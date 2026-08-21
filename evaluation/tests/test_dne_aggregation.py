#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import csv
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "evaluation" / "aggregate_experiment_results_v2.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("aggregate_experiment_results_v2", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DneAggregationTests(unittest.TestCase):
    def test_metric_spec_freezes_three_dimensions_without_quality_gate(self) -> None:
        spec = json.loads((ROOT / "evaluation" / "metric_spec_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["version"], "2.0.0")
        self.assertEqual(spec["core_weights"], {"D": 0.35, "N": 0.35, "E": 0.30})
        self.assertFalse(spec["independent_quality_gate"])

    def test_a_summary_uses_medians_and_keeps_atom_denominators(self) -> None:
        run_scores = [
            {"D": {"score": 0.8, "passed": 8, "applicable": 10},
             "N": {"score": 0.6, "passed": 6, "applicable": 10}, "E": 0.5, "record_count": 10},
            {"D": {"score": 1.0, "passed": 10, "applicable": 10},
             "N": {"score": 0.8, "passed": 8, "applicable": 10}, "E": 0.7, "record_count": 10},
            {"D": {"score": 0.9, "passed": 9, "applicable": 10},
             "N": {"score": 0.7, "passed": 7, "applicable": 10}, "E": 0.6, "record_count": 10},
        ]
        summary = MODULE.summarize_a_repetitions(run_scores)
        self.assertAlmostEqual(summary["D"]["score"], 0.9)
        self.assertEqual(summary["D"]["passed"], 27)
        self.assertEqual(summary["D"]["applicable"], 30)
        self.assertAlmostEqual(summary["N"]["score"], 0.7)
        self.assertAlmostEqual(summary["E"], 0.6)
        self.assertAlmostEqual(summary["EICPI_core_0_100"], 74.0)

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
            "D": {"score": 1.0, "passed": 10, "applicable": 10, "not_applicable": 0},
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


if __name__ == "__main__":
    unittest.main()
