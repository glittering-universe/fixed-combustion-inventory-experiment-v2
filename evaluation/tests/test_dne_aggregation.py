#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import csv
import copy
import json
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "evaluation" / "aggregate_experiment_results_v2.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("aggregate_experiment_results_v2", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DneAggregationTests(unittest.TestCase):
    def test_active_spec_v2_2_fixes_N_and_preserves_D_E(self) -> None:
        spec = json.loads(MODULE.METRIC_SPEC.read_text(encoding="utf-8"))
        prior = json.loads((ROOT / "evaluation" / "metric_spec_v2_1.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["version"], "2.2.0")
        self.assertEqual(spec["supersedes"], "2.1.0")
        self.assertEqual(MODULE.OUTPUT_SUFFIX, "v2_2")
        self.assertEqual(spec["dimensions"]["D"], prior["dimensions"]["D"])
        self.assertEqual(spec["dimensions"]["E"], prior["dimensions"]["E"])
        self.assertEqual(spec["core_weights"], prior["core_weights"])
        self.assertEqual(spec["dimensions"]["N"]["required_population"], "reference_defined_per_method")

    def test_metric_spec_v2_1_freezes_hierarchical_D_without_quality_gate(self) -> None:
        spec = json.loads((ROOT / "evaluation" / "metric_spec_v2_1.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["version"], "2.1.0")
        self.assertEqual(spec["supersedes"], "2.0.0")
        self.assertEqual(spec["core_weights"], {"D": 0.35, "N": 0.35, "E": 0.30})
        self.assertFalse(spec["independent_quality_gate"])
        self.assertEqual(
            spec["dimensions"]["D"]["formula"],
            "mean_available(D_regular,D_exception)",
        )
        self.assertEqual(spec["dimensions"]["D"]["component_weights"], {
            "D_regular": 0.5,
            "D_exception": 0.5,
        })

    def test_a_summary_uses_component_medians_and_rebuilds_hierarchical_D(self) -> None:
        run_scores = [
            {"D": {
                 "score": 0.6, "D_regular": 1.0, "D_exception": 0.2,
                 "regular": {"passed": 10, "applicable": 10, "not_applicable": 0},
                 "exception": {"passed": 1, "applicable": 5, "not_applicable": 0},
                 "raw_counts": {"passed": 1001, "applicable": 1005, "not_applicable": 0, "total": 1005},
                 "legacy_atom_micro_score": 1001 / 1005,
                 "diagnostics": {"detection_f1": 0.2, "root_cause_macro_jaccard": 0.4, "exception_disposition_accuracy": 0.6},
             },
             "N": {"score": 0.6, "passed": 6, "applicable": 10}, "E": 0.5, "record_count": 10},
            {"D": {
                 "score": 0.8, "D_regular": 1.0, "D_exception": 0.6,
                 "regular": {"passed": 10, "applicable": 10, "not_applicable": 0},
                 "exception": {"passed": 3, "applicable": 5, "not_applicable": 0},
                 "raw_counts": {"passed": 1003, "applicable": 1005, "not_applicable": 0, "total": 1005},
                 "legacy_atom_micro_score": 1003 / 1005,
                 "diagnostics": {"detection_f1": 0.6, "root_cause_macro_jaccard": 0.8, "exception_disposition_accuracy": 1.0},
             },
             "N": {"score": 0.8, "passed": 8, "applicable": 10}, "E": 0.7, "record_count": 10},
            {"D": {
                 "score": 0.7, "D_regular": 0.8, "D_exception": 0.6,
                 "regular": {"passed": 8, "applicable": 10, "not_applicable": 0},
                 "exception": {"passed": 3, "applicable": 5, "not_applicable": 0},
                 "raw_counts": {"passed": 803, "applicable": 1005, "not_applicable": 0, "total": 1005},
                 "legacy_atom_micro_score": 803 / 1005,
                 "diagnostics": {"detection_f1": 0.6, "root_cause_macro_jaccard": 0.6, "exception_disposition_accuracy": 0.8},
             },
             "N": {"score": 0.7, "passed": 7, "applicable": 10}, "E": 0.6, "record_count": 10},
        ]
        summary = MODULE.summarize_a_repetitions(run_scores)
        self.assertAlmostEqual(summary["D"]["D_regular"], 1.0)
        self.assertAlmostEqual(summary["D"]["D_exception"], 0.6)
        self.assertAlmostEqual(summary["D"]["score"], 0.8)
        self.assertEqual(summary["D"]["regular"]["passed"], 28)
        self.assertEqual(summary["D"]["exception"]["applicable"], 15)
        self.assertAlmostEqual(summary["D"]["diagnostics"]["detection_f1"], 0.6)
        self.assertAlmostEqual(summary["N"]["score"], 0.7)
        self.assertAlmostEqual(summary["E"], 0.6)
        self.assertAlmostEqual(summary["EICPI_core_0_100"], 70.5)

    def test_target_aggregation_never_pools_raw_D_atoms(self) -> None:
        target_rows = {
            "INDUSTRIAL": {
                "D": {"score": 0.5, "D_regular": 1.0, "D_exception": 0.0},
                "N": {"score": 0.8, "passed": 8000, "applicable": 10000},
                "E": 0.6, "record_count": 4025,
            },
            "POWER": {
                "D": {"score": 1.0, "D_regular": 1.0, "D_exception": 1.0},
                "N": {"score": 1.0, "passed": 1000, "applicable": 1000},
                "E": 1.0, "record_count": 367,
            },
        }
        result = MODULE.aggregate_a_targets(target_rows)
        self.assertAlmostEqual(result["macro"]["D_regular"], 1.0)
        self.assertAlmostEqual(result["macro"]["D_exception"], 0.5)
        self.assertAlmostEqual(result["macro"]["D"], 0.75)
        self.assertEqual(result["micro"]["D"], result["macro"]["D"])
        self.assertEqual(result["micro"]["D_aggregation"], "hierarchical_component_macro")

    def test_only_reviewed_exception_terminals_are_conjoined_and_removed_from_regular_D(self) -> None:
        Atom = namedtuple("Atom", "atom_id kind passed detail", defaults=[""])
        selected = {"exceptions": [
            {
                "source_id": "SRC-E", "requires_user_judgment": "true",
                "affected_pollutants": '["SO2","NOx"]',
            },
            {
                "source_id": "SRC-NOTICE", "requires_user_judgment": "false",
                "affected_pollutants": '["PM10"]',
            },
        ]}
        dispositions, terminal_ids = MODULE.reviewed_exception_dispositions(selected, [
            Atom("terminal:SRC-E::SO2", "terminal_disposition", True),
            Atom("terminal:SRC-E::NOx", "terminal_disposition", False),
            Atom("terminal:SRC-NOTICE::PM10", "terminal_disposition", False),
        ])
        self.assertEqual(dispositions, {"SRC-E": False})
        self.assertEqual(terminal_ids, {
            "terminal:SRC-E::SO2",
            "terminal:SRC-E::NOx",
        })

    def test_only_experiment_a_enters_core_ranking(self) -> None:
        rows = [
            {"experiment": "A", "method": "full", "target": "INDUSTRIAL", "score": 1},
            {"experiment": "B", "method": "full", "target": "INDUSTRIAL", "score": 0},
            {"experiment": "D", "method": "without_admission", "target": "INDUSTRIAL", "score": 0},
        ]
        primary, independent = MODULE.partition_experiments(rows)
        self.assertEqual([row["experiment"] for row in primary], ["A"])
        self.assertEqual(set(independent), {"B1", "B2", "C", "D"})
        self.assertEqual([row["experiment"] for row in independent["D"]], ["D"])

    def test_expected_and_actual_pm10_controls_use_the_same_relevant_signature(self) -> None:
        selected = {
            "items": [{
                "source_id": "SRC-1", "pollutant": "PM10",
                "applicability_mask": '{"control":true}',
                "expected_control_efficiency": "", "expected_fine_efficiency": "0.8",
                "expected_coarse_efficiency": "0.6",
            }],
            "totals": [{
                "source_id": "SRC-1", "pollutant": "PM10", "expected_status": "calculated",
                "expected_generation_t": "2", "expected_emission_t": "1",
            }],
        }
        expected = MODULE.expected_semantics(selected)
        actual = MODULE.actual_semantics([{
            "source_id": "SRC-1", "target": "INDUSTRIAL", "pollutant": "PM10",
            "status": "calculated", "method_category": "", "activity_record": "[]",
            "parameter_record": "[]",
            "control_record": '[{"efficiency":0,"fine_efficiency":0.8,"coarse_efficiency":0.6}]',
            "generation_t": "2", "emission_t": "1", "method": "full",
        }])
        self.assertEqual(
            expected["SRC-1::PM10"]["control_signatures"],
            actual["SRC-1::PM10"]["control_signatures"],
        )

    def test_expected_method_category_parses_reference_json_array(self) -> None:
        selected = {
            "items": [{
                "source_id": "SRC-1",
                "pollutant": "NOx",
                "applicability_mask": '{"method":true}',
                "expected_method_category": '["emission_factor_method"]',
            }],
            "totals": [{
                "source_id": "SRC-1",
                "pollutant": "NOx",
                "expected_status": "calculated",
                "expected_generation_t": "1",
                "expected_emission_t": "1",
            }],
        }
        expected = MODULE.expected_semantics(selected)
        self.assertEqual(expected["SRC-1::NOx"]["method_ids"], ["emission_factor_method"])

    def test_b1_semantic_retention_ignores_irrelevant_pm10_general_efficiency(self) -> None:
        fields = [
            "source_id", "target", "pollutant", "status", "method_category", "activity_record",
            "parameter_record", "control_record", "generation_t", "emission_t", "standard_reference",
            "reason_codes", "minimum_recalculation_complete", "complete_process_record", "run_id", "method",
        ]
        base = {
            "source_id": "SRC-1", "target": "INDUSTRIAL", "pollutant": "PM10", "status": "calculated",
            "method_category": '["constant"]', "activity_record": '[{"value":1000,"unit":"kg"}]',
            "parameter_record": '[{"mode":"constant","value":2,"unit":"g/kg"}]',
            "generation_t": "0.002", "emission_t": "0.001", "standard_reference": "E.7",
            "reason_codes": "[]", "minimum_recalculation_complete": "true",
            "complete_process_record": "true", "run_id": "r", "method": "full",
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ("left.csv", "right.csv")]
            for path, efficiency in zip(paths, (0, None)):
                row = dict(base)
                row["control_record"] = json.dumps([{
                    "efficiency": efficiency, "fine_efficiency": 0.8, "coarse_efficiency": 0.6,
                }])
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader(); writer.writerow(row)
            result = MODULE.compare_normalized_v2(*paths)
        self.assertEqual(result["passed_units"], 1)
        self.assertEqual(result["agreement_rate"], 1.0)

    def test_human_v2_adapter_maps_reported_total_and_target_specific_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"run_id": "H", "target": "INDUSTRIAL"}), encoding="utf-8"
            )
            with (run_dir / "calculation_totals.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                fields = ["source_id", "target", "pollutant", "status", "generation_t", "emission_t", "reason_code"]
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
                writer.writerow({"source_id": "SRC-I", "target": "INDUSTRIAL", "pollutant": "SO2", "status": "reported_total", "generation_t": 2, "emission_t": 1})
            with (run_dir / "source_decisions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                fields = ["source_id", "target", "human_scope_decision"]
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
                writer.writerow({"source_id": "SRC-I", "target": "INDUSTRIAL", "human_scope_decision": "include"})
                writer.writerow({"source_id": "SRC-P", "target": "INDUSTRIAL", "human_scope_decision": "not_selected_for_target"})
            normalized = MODULE.normalize_path(run_dir)
            rows = MODULE.read_csv(normalized)
            destinations = MODULE.actual_destinations(run_dir, rows)
        self.assertEqual(rows[0]["status"], "calculated")
        self.assertTrue(destinations["SRC-I"]["selected_for_target"])
        self.assertFalse(destinations["SRC-P"]["selected_for_target"])

    def test_human_observation_overlay_uses_corrected_values_and_preserves_old_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "sealed"
            corrected = Path(tmp) / "corrected"
            (original / "evaluation").mkdir(parents=True)
            (corrected / "evaluation").mkdir(parents=True)
            old_cache = original / "evaluation" / "normalized_items.csv"
            old_cache.write_text("old sealed observation", encoding="utf-8")
            (corrected / "evaluation" / "normalized_items.csv").write_text("stale cache", encoding="utf-8")
            (corrected / "calculation_totals.csv").write_text(
                "source_id,target,pollutant,status,generation_t,emission_t,reason_code\n"
                "SRC-I,INDUSTRIAL,VOC,reported_total,0.002544,0.002544,\n",
                encoding="utf-8",
            )
            (corrected / "source_decisions.csv").write_text(
                "source_id,target,human_scope_decision\nSRC-I,INDUSTRIAL,include\n", encoding="utf-8",
            )
            manifest = {"run_id": "A-IND-HUM", "target": "INDUSTRIAL"}
            path = MODULE.normalize_path(corrected, manifest=manifest, refresh=True)
            rows = MODULE.read_csv(path)
            destinations = MODULE.actual_destinations(corrected, rows, manifest=manifest)
            self.assertEqual(rows[0]["emission_t"], "0.002544")
            self.assertEqual(rows[0]["run_id"], "A-IND-HUM")
            self.assertEqual(rows[0]["complete_process_record"], "NA")
            self.assertEqual(destinations["SRC-I"]["run_target"], "INDUSTRIAL")
            self.assertEqual(old_cache.read_text(encoding="utf-8"), "old sealed observation")
            self.assertFalse((corrected / "run_manifest.json").exists())

    def test_human_overlay_rejects_changed_original_or_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals = root / "originals"
            originals.mkdir()
            source = originals / "human.xlsx"
            source.write_bytes(b"original workbook")
            package = root / "run"
            package.mkdir()
            derived = {}
            for name in ("source_decisions.csv", "calculation_totals.csv", "exceptions.csv", "normalization_notes.json"):
                path = package / name
                path.write_bytes(b"checked export")
                derived[name] = {"sha256": MODULE.sha256(path), "bytes": path.stat().st_size}
            index = {
                "schema_version": "human-baseline-normalization-v2.1.0",
                "preserved_originals_unchanged": True,
                "preserved_original_root": str(originals),
                "original_workbook_hashes": {"human.xlsx": MODULE.sha256(source)},
                "packages": [{"output": "run", "workbook": "human.xlsx", "target": "INDUSTRIAL", "derived_files": derived}],
            }
            (root / "normalization_index.json").write_text(json.dumps(index), encoding="utf-8")
            packages = MODULE.load_human_normalization(root)
            self.assertEqual(packages["run"]["directory"], package)
            (package / "calculation_totals.csv").write_bytes(b"changed export")
            with self.assertRaisesRegex(ValueError, "export hash"):
                MODULE.load_human_normalization(root)
            (package / "calculation_totals.csv").write_bytes(b"checked export")
            source.write_bytes(b"changed original")
            with self.assertRaisesRegex(ValueError, "original workbook hash"):
                MODULE.load_human_normalization(root)

    def test_boundary_failure_is_recorded_for_agent_without_rejecting_valid_seal(self) -> None:
        class SealHelper:
            audit_calls = 0

            @staticmethod
            def verify_experiment_seal(run_dir: Path, manifest: dict) -> dict:
                return {"status": "pass", "failures": []}

            @classmethod
            def method_boundary_audit(cls, run_dir: Path, manifest: dict) -> dict:
                cls.audit_calls += 1
                return {
                    "status": "fail",
                    "failures": ["FULL_REQUIRED_STAGE_MISSING"],
                    "warnings": ["EXPLORATION_OVERHEAD_OBSERVED"],
                }

        diagnostic = MODULE.verify_seal_and_collect_boundary_diagnostic(
            {
                "run_id": "A-IND-FULL-R1", "method": "full", "target": "INDUSTRIAL",
                "input_variant": "B0", "scale_percent": 100,
                "method_bundle_hash": "method-hash", "runner": "full_agent",
            },
            Path("/unused"),
            {
                "run_id": "A-IND-FULL-R1", "experiment_method": "full",
                "target": "INDUSTRIAL", "input_variant": "B0", "scale_percent": 100,
                "method_bundle_hash": "method-hash",
            },
            SealHelper,
        )
        self.assertEqual(SealHelper.audit_calls, 1)
        self.assertEqual(diagnostic["status"], "fail")
        self.assertEqual(diagnostic["failures"], ["FULL_REQUIRED_STAGE_MISSING"])
        self.assertEqual(diagnostic["warnings"], ["EXPLORATION_OVERHEAD_OBSERVED"])

    def test_invalid_seal_still_hard_fails_before_boundary_audit(self) -> None:
        class SealHelper:
            audit_calls = 0

            @staticmethod
            def verify_experiment_seal(run_dir: Path, manifest: dict) -> dict:
                return {"status": "fail", "failures": ["SEALED_FILE_HASH_MISMATCH"]}

            @classmethod
            def method_boundary_audit(cls, run_dir: Path, manifest: dict) -> dict:
                cls.audit_calls += 1
                return {"status": "pass"}

        with self.assertRaisesRegex(RuntimeError, "sealed integrity failed"):
            MODULE.verify_seal_and_collect_boundary_diagnostic(
                {"run_id": "A-IND-FULL-R1", "runner": "full_agent"},
                Path("/unused"),
                {"experiment_method": "full"},
                SealHelper,
            )
        self.assertEqual(SealHelper.audit_calls, 0)

    def test_frozen_matrix_row_must_match_the_sealed_run_manifest(self) -> None:
        class SealHelper:
            @staticmethod
            def verify_experiment_seal(run_dir: Path, manifest: dict) -> dict:
                return {"status": "pass", "failures": []}

            @staticmethod
            def method_boundary_audit(run_dir: Path, manifest: dict) -> dict:
                return {"status": "pass", "failures": [], "warnings": []}

        row = {
            "run_id": "A-IND-FULL-R1",
            "method": "full",
            "target": "INDUSTRIAL",
            "input_variant": "B0",
            "scale_percent": 100,
            "method_bundle_hash": "frozen-method-hash",
            "runner": "full_agent",
        }
        manifest = {
            "run_id": "A-IND-FULL-R1",
            "experiment_method": "full",
            "target": "INDUSTRIAL",
            "input_variant": "B0",
            "scale_percent": 100,
            "method_bundle_hash": "frozen-method-hash",
        }
        field_mapping = {
            "run_id": "run_id",
            "method": "experiment_method",
            "target": "target",
            "input_variant": "input_variant",
            "scale_percent": "scale_percent",
            "method_bundle_hash": "method_bundle_hash",
        }
        for matrix_field, manifest_field in field_mapping.items():
            with self.subTest(field=matrix_field):
                mismatched = dict(manifest)
                mismatched[manifest_field] = f"wrong-{manifest_field}"
                with self.assertRaisesRegex(RuntimeError, "matrix-manifest binding failed"):
                    MODULE.verify_seal_and_collect_boundary_diagnostic(
                        row, Path("/unused"), mismatched, SealHelper,
                    )

    def test_malformed_boundary_audit_result_becomes_non_gating_diagnostic(self) -> None:
        class SealHelper:
            @staticmethod
            def verify_experiment_seal(run_dir: Path, manifest: dict) -> dict:
                return {"status": "pass", "failures": []}

            @staticmethod
            def method_boundary_audit(run_dir: Path, manifest: dict) -> None:
                return None

        diagnostic = MODULE.verify_seal_and_collect_boundary_diagnostic(
            {
                "run_id": "A-IND-FULL-R1", "method": "full", "target": "INDUSTRIAL",
                "input_variant": "B0", "scale_percent": 100,
                "method_bundle_hash": "method-hash", "runner": "full_agent",
            },
            Path("/unused"),
            {
                "run_id": "A-IND-FULL-R1", "experiment_method": "full",
                "target": "INDUSTRIAL", "input_variant": "B0", "scale_percent": 100,
                "method_bundle_hash": "method-hash",
            },
            SealHelper,
        )
        self.assertEqual(diagnostic["status"], "audit_error")
        self.assertEqual(diagnostic["failures"], ["METHOD_BOUNDARY_AUDIT_ERROR"])

    def test_boundary_summary_is_diagnostic_only_and_covers_agent_runs(self) -> None:
        scores = {
            "R-FULL": {
                "D": {"score": 1.0}, "N": {"score": 1.0}, "E": 0.8,
                "method_boundary": {"status": "pass", "failures": [], "warnings": []},
            },
            "R-GEN": {
                "D": {"score": 0.4}, "N": {"score": 0.3}, "E": 0.2,
                "method_boundary": {
                    "status": "fail",
                    "failures": ["GENERIC_USED_FIXED_COMBUSTION_CAPABILITY"],
                    "warnings": ["EXPLORATION_OVERHEAD_OBSERVED"],
                },
            },
            "R-DET": {
                "D": {"score": 0.9}, "N": {"score": 0.9}, "E": 1.0,
                "method_boundary": {
                    "status": "not_applicable", "failures": [], "warnings": [],
                },
            },
        }
        rows = [
            {"run_id": "R-FULL", "runner": "full_agent", "method": "full", "target": "INDUSTRIAL"},
            {"run_id": "R-GEN", "runner": "generic_agent", "method": "generic_tool_agent", "target": "POWER"},
            {"run_id": "R-DET", "runner": "deterministic", "method": "deterministic_program", "target": "POWER"},
        ]
        before = copy.deepcopy(scores)
        diagnostic = MODULE.summarize_method_boundary_diagnostics(rows, scores)
        self.assertEqual(scores, before, "diagnostic aggregation must not alter D/N/E")
        self.assertEqual(diagnostic["agent_run_count"], 2)
        self.assertEqual(diagnostic["audited_agent_run_count"], 2)
        self.assertEqual(diagnostic["status_counts"], {"fail": 1, "pass": 1})
        self.assertEqual(
            diagnostic["failure_code_counts"],
            {"GENERIC_USED_FIXED_COMBUSTION_CAPABILITY": 1},
        )
        self.assertEqual(
            diagnostic["warning_code_counts"],
            {"EXPLORATION_OVERHEAD_OBSERVED": 1},
        )
        self.assertEqual({item["run_id"] for item in diagnostic["runs"]}, {"R-FULL", "R-GEN"})

    def test_audit_error_and_not_available_do_not_count_as_completed_audits(self) -> None:
        rows = [
            {"run_id": "PASS", "runner": "full_agent", "method": "full", "target": "INDUSTRIAL"},
            {"run_id": "FAIL", "runner": "generic_agent", "method": "generic_tool_agent", "target": "POWER"},
            {"run_id": "ERROR", "runner": "full_agent", "method": "full", "target": "POWER"},
            {"run_id": "MISSING", "runner": "generic_agent", "method": "generic_tool_agent", "target": "INDUSTRIAL"},
        ]
        scores = {
            "PASS": {"method_boundary": {"status": "pass", "failures": [], "warnings": []}},
            "FAIL": {"method_boundary": {"status": "fail", "failures": ["OUTSIDE_PATH"], "warnings": []}},
            "ERROR": {"method_boundary": {"status": "audit_error", "failures": ["AUDIT_ERROR"], "warnings": []}},
            "MISSING": {"method_boundary": {"status": "not_available", "failures": [], "warnings": ["NO_SESSION"]}},
        }
        diagnostic = MODULE.summarize_method_boundary_diagnostics(rows, scores)
        self.assertEqual(diagnostic["agent_run_count"], 4)
        self.assertEqual(diagnostic["audited_agent_run_count"], 2)
        self.assertFalse(diagnostic["all_agent_runs_audited"])
        self.assertEqual(
            diagnostic["status_counts"],
            {"audit_error": 1, "fail": 1, "not_available": 1, "pass": 1},
        )

    def test_frozen_matrix_expects_boundary_diagnostics_for_all_94_agent_runs(self) -> None:
        rows = MODULE.load_json(MODULE.MATRIX)["rows"]
        agent_rows = [row for row in rows if row.get("runner") in MODULE.AGENT_RUNNERS]
        scores = {
            row["run_id"]: {
                "method_boundary": {
                    "status": "fail" if index < 4 else "pass",
                    "failures": ["BOUNDARY_VIOLATION"] if index < 4 else [],
                    "warnings": [],
                },
            }
            for index, row in enumerate(agent_rows)
        }
        diagnostic = MODULE.summarize_method_boundary_diagnostics(rows, scores)
        self.assertEqual(diagnostic["agent_run_count"], 94)
        self.assertEqual(diagnostic["audited_agent_run_count"], 94)
        self.assertTrue(diagnostic["all_agent_runs_audited"])
        self.assertEqual(diagnostic["status_counts"], {"fail": 4, "pass": 90})

    def test_run_csv_exports_boundary_status_failures_and_warnings(self) -> None:
        score = {
            "experiment": "A", "run_id": "A-IND-FULL-R1", "method": "full",
            "target": "INDUSTRIAL", "input_variant": "B0", "scale_percent": 100,
            "repetition": 1,
            "D": {
                "score": 0.75, "D_regular": 1.0, "D_exception": 0.5,
                "raw_counts": {"passed": 10, "applicable": 10, "not_applicable": 0},
                "legacy_atom_micro_score": 1.0,
                "diagnostics": {
                    "detection_f1": 0.5,
                    "root_cause_macro_jaccard": 0.25,
                    "exception_disposition_accuracy": 0.75,
                },
            },
            "N": {"score": 1.0, "passed": 10, "applicable": 10, "not_applicable": 0},
            "E": 0.8, "EICPI_core_0_100": 94.0, "record_count": 10,
            "seconds_per_record": 0.1,
            "method_boundary": {
                "status": "fail",
                "failures": ["FULL_REQUIRED_STAGE_MISSING"],
                "warnings": ["EXPLORATION_OVERHEAD_OBSERVED"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_scores_v2.csv"
            MODULE.write_run_csv(path, [score])
            exported = MODULE.read_csv(path)[0]
        self.assertEqual(exported["method_boundary_status"], "fail")
        self.assertEqual(
            json.loads(exported["method_boundary_failures"]),
            ["FULL_REQUIRED_STAGE_MISSING"],
        )
        self.assertEqual(
            json.loads(exported["method_boundary_warnings"]),
            ["EXPLORATION_OVERHEAD_OBSERVED"],
        )
        self.assertEqual(float(exported["D_regular"]), 1.0)
        self.assertEqual(float(exported["D_exception"]), 0.5)
        self.assertEqual(float(exported["D_exception_detection_f1"]), 0.5)
        self.assertEqual(float(exported["D_root_cause_macro_jaccard"]), 0.25)
        self.assertEqual(float(exported["D_exception_disposition_accuracy"]), 0.75)

    def test_five_class_macro_f1_penalizes_missed_and_misclassified_anomalies(self) -> None:
        classes = [
            "ACTIVITY_MISSING",
            "POLLUTANT_PARAMETER_MISSING",
            "SOURCE_RELATION_MISSING",
            "HARD_CONSTRAINT_CONFLICT",
            "NO_ANOMALY",
        ]
        expected = classes
        predicted = [
            "ACTIVITY_MISSING",
            "ACTIVITY_MISSING",
            "SOURCE_RELATION_MISSING",
            "UNRESOLVED_OR_MISCLASSIFIED",
            "NO_ANOMALY",
        ]
        result = MODULE.multiclass_macro_f1(expected, predicted, classes)
        self.assertAlmostEqual(result["by_class"]["ACTIVITY_MISSING"]["f1"], 2 / 3)
        self.assertEqual(result["by_class"]["POLLUTANT_PARAMETER_MISSING"]["f1"], 0.0)
        self.assertEqual(result["by_class"]["SOURCE_RELATION_MISSING"]["f1"], 1.0)
        self.assertEqual(result["by_class"]["HARD_CONSTRAINT_CONFLICT"]["f1"], 0.0)
        self.assertEqual(result["by_class"]["NO_ANOMALY"]["f1"], 1.0)
        self.assertAlmostEqual(result["macro_f1"], (2 / 3 + 0 + 1 + 0 + 1) / 5)

    def test_b2_uses_only_new_roots_and_joint_disposition_on_eligible_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            reference.mkdir()
            fields = [
                "plan_class", "target", "root_cause", "source_id",
                "expected_affected_pollutants", "baseline_statuses", "b2_statuses",
                "scoring_eligible",
            ]
            designed = [
                ["positive_injection", "INDUSTRIAL", "ACTIVITY_MISSING", "SRC-A", '["SO2"]', '{"SO2":"calculated"}', '{"SO2":"information_insufficient"}', "true"],
                ["positive_injection", "INDUSTRIAL", "POLLUTANT_PARAMETER_MISSING", "SRC-P", '["SO2"]', '{"SO2":"calculated"}', '{"SO2":"information_insufficient"}', "true"],
                ["negative_control", "INDUSTRIAL", "NO_INJECTION_CONTROL", "SRC-N", "[]", "{}", "{}", "true"],
                ["preexisting_problem", "INDUSTRIAL", "PREEXISTING_INPUT_PROBLEM", "SRC-X", "[]", "{}", "{}", "false"],
            ]
            with (reference / "expected_injection_outcomes.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle); writer.writerow(fields); writer.writerows(designed)

            normalized_fields = ["source_id", "pollutant", *MODULE.SEMANTIC_FIELDS]
            b0_path, b2_path = root / "b0.csv", root / "b2.csv"
            b0_rows, b2_rows = [], []
            for source_id in ("SRC-A", "SRC-P", "SRC-N", "SRC-X"):
                for pollutant in MODULE.POLLUTANTS:
                    base = {
                        "source_id": source_id, "pollutant": pollutant,
                        "status": "calculated", "method_category": "[]",
                        "activity_record": "[]", "parameter_record": "[]", "control_record": "[]",
                        "generation_t": "1", "emission_t": "1",
                        "standard_reference": "R", "reason_codes": "[]",
                    }
                    b0_rows.append(dict(base))
                    injected = dict(base)
                    if source_id == "SRC-A" and pollutant == "SO2":
                        injected.update(status="information_insufficient", generation_t="", emission_t="")
                    # SRC-P intentionally keeps the old terminal disposition.
                    b2_rows.append(injected)
            for path, data in ((b0_path, b0_rows), (b2_path, b2_rows)):
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=normalized_fields)
                    writer.writeheader(); writer.writerows(data)

            def semantic_totals(data: list[dict[str, str]], expected: bool) -> dict[str, dict[str, object]]:
                result = {}
                for item in data:
                    key = f"{item['source_id']}::{item['pollutant']}"
                    if expected:
                        result[key] = {
                            "source_id": item["source_id"], "pollutant": item["pollutant"],
                            "expected_status": item["status"],
                            "expected_generation_t": item["generation_t"],
                            "expected_emission_t": item["emission_t"],
                            "control_signatures": [],
                        }
                    else:
                        result[key] = {
                            "source_id": item["source_id"], "pollutant": item["pollutant"],
                            "status": item["status"], "generation_t": item["generation_t"],
                            "emission_t": item["emission_t"], "control_signatures": [],
                        }
                return result

            selected_exceptions = [
                {"source_id": "SRC-A", "canonical_root_cause": "activity_level_missing", "injection_root_cause": "ACTIVITY_MISSING", "requires_user_judgment": "true"},
                {"source_id": "SRC-P", "canonical_root_cause": "coal_parameter_missing", "injection_root_cause": "POLLUTANT_PARAMETER_MISSING", "requires_user_judgment": "true"},
            ]
            b0_context = {
                "actual": semantic_totals(b0_rows, False),
                "actual_exception_groups": {("SRC-A", "natural_existing_issue")},
            }
            b2_context = {
                "expected": semantic_totals(b2_rows, True),
                "actual": semantic_totals(b2_rows, False),
                "expected_sources": {source_id: {} for source_id in ("SRC-A", "SRC-P", "SRC-N", "SRC-X")},
                "expected_exception_groups": set(),
                "actual_exception_groups": {
                    ("SRC-A", "natural_existing_issue"),
                    ("SRC-A", "activity_level_missing"),
                    ("SRC-P", "coal_parameter_missing"),
                    ("SRC-N", "source_relationship_missing"),
                },
                "selected": {"exceptions": selected_exceptions},
            }
            rows = [{
                "run_id": "B2", "method": "full", "target": "INDUSTRIAL",
                "sequence": 2, "input_variant": "B2",
            }]
            b0_rows_matrix = [{
                "run_id": "B0", "method": "full", "target": "INDUSTRIAL",
                "sequence": 1, "input_variant": "B0",
            }]
            scores = {
                "B0": {"normalized_output": str(b0_path)},
                "B2": {
                    "normalized_output": str(b2_path),
                    "D": {"score": 0.5}, "N": {"score": 0.5},
                },
            }
            report = MODULE.b2_report(
                rows, scores, {"B0": b0_context, "B2": b2_context}, reference, b0_rows_matrix,
            )

        self.assertEqual(report["eligible_records"], 3)
        self.assertEqual(report["excluded_preexisting_problem"], 1)
        run = report["runs"][0]
        outcomes = {item["source_id"]: item for item in run["outcomes"]}
        self.assertEqual(outcomes["SRC-A"]["new_actual_root_causes_vs_B0"], ["activity_level_missing"])
        self.assertEqual(outcomes["SRC-A"]["predicted_class"], "ACTIVITY_MISSING")
        self.assertEqual(outcomes["SRC-P"]["predicted_class"], "UNRESOLVED_OR_MISCLASSIFIED")
        self.assertNotEqual(outcomes["SRC-N"]["predicted_class"], "NO_ANOMALY")
        self.assertEqual(run["five_class_classification"]["eligible_records"], 3)
        self.assertIn("overall_reference_D_diagnostic", run)
        self.assertNotIn("D", run)
        self.assertNotIn("root_cause", run)
        self.assertNotIn("targeted_detection", run)

    def test_ablation_report_separates_regular_and_exception_deltas(self) -> None:
        rows = [
            {"run_id": "FULL", "method": "full", "target": "INDUSTRIAL", "sequence": 1},
            {"run_id": "ABL", "method": "without_rule_gate", "target": "INDUSTRIAL", "sequence": 2},
        ]
        scores = {
            "FULL": {
                "D": {"score": 0.9, "D_regular": 1.0, "D_exception": 0.8},
                "N": {"score": 1.0}, "seconds_per_record": 1.0,
            },
            "ABL": {
                "D": {"score": 0.5, "D_regular": 0.8, "D_exception": 0.2},
                "N": {"score": 0.7}, "seconds_per_record": 1.2,
            },
        }
        report = MODULE.d_report(rows, scores)
        ablation = next(item for item in report["entries"] if item["run_id"] == "ABL")
        self.assertAlmostEqual(ablation["delta_regular"], -0.2)
        self.assertAlmostEqual(ablation["delta_exception"], -0.6)


if __name__ == "__main__":
    unittest.main()
