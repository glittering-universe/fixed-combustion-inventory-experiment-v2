from __future__ import annotations

import hashlib
import importlib.util
import csv
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "evaluation/evaluate_sealed_run.py"
SPEC = importlib.util.spec_from_file_location("evaluate_sealed_run_v2", MODULE_PATH)
assert SPEC and SPEC.loader
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session_with_tools(path: Path, tools: list[str]) -> None:
    messages = []
    for index, name in enumerate(tools):
        messages.append({
            "id": index,
            "role": "assistant",
            "tool_calls": [{
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({"name": name, "arguments": {}}),
                }
            }],
        })
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "S", "messages": messages}), encoding="utf-8")


class SealV2Tests(unittest.TestCase):
    def test_relative_input_hashes_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "inputs").mkdir()
            (run / "logs").mkdir()
            source = run / "inputs/raw.xlsx"
            source.write_bytes(b"raw")
            metrics = run / "logs/execution_metrics.json"
            metrics.write_text("{}", encoding="utf-8")
            manifest = {
                "run_id": "R",
                "method_bundle_hash": "M",
                "inputs": [{"path": str(source), "sha256": sha(source)}],
            }
            seal = {
                "seal_version": "2.0.0",
                "run_id": "R",
                "method_bundle_hash": "M",
                "input_hashes": {"inputs/raw.xlsx": sha(source)},
                "execution_metrics_sha256": sha(metrics),
                "files": [{"path": "logs/execution_metrics.json", "sha256": sha(metrics), "bytes": metrics.stat().st_size}],
            }
            (run / "experiment_seal.json").write_text(json.dumps(seal), encoding="utf-8")
            self.assertEqual(EVALUATOR.verify_experiment_seal(run, manifest)["status"], "pass")

    def test_raw_v2_structural_scope_comes_from_source_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "outputs").mkdir()
            decisions = run / "outputs/source_decisions.csv"
            with decisions.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=("source_id", "decided_target", "disposition"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_id": "SRC-RAW-00000000000000000001",
                        "decided_target": "INDUSTRIAL",
                        "disposition": "include",
                    }
                )
            normalized = run / "normalized.csv"
            fields = (
                "source_id", "target", "pollutant", "status", "generation_t", "emission_t",
                "minimum_recalculation_complete", "complete_process_record", "activity_record",
                "parameter_record", "control_record",
            )
            with normalized.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for pollutant in EVALUATOR.POLLUTANTS:
                    writer.writerow(
                        {
                            "source_id": "SRC-RAW-00000000000000000001",
                            "target": "INDUSTRIAL",
                            "pollutant": pollutant,
                            "status": "information_insufficient",
                            "generation_t": "",
                            "emission_t": "",
                            "minimum_recalculation_complete": "false",
                            "complete_process_record": "false",
                            "activity_record": "[]",
                            "parameter_record": "[]",
                            "control_record": "[]",
                        }
                    )
            report = EVALUATOR.structural_checks(
                run,
                {"target": "INDUSTRIAL", "experiment_method": "full"},
                normalized,
            )
            self.assertEqual(report["missing_count"], 0)
            self.assertIn(
                "EXPECTED_SCOPE_DERIVED_FROM_TARGET_NEUTRAL_SOURCE_DECISIONS",
                report["diagnostics"],
            )


class MethodBoundaryV2Tests(unittest.TestCase):
    def test_full_allows_exploration_but_requires_ordered_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            session_with_tools(
                run / "session_export/session.jsonl",
                ["skill_view", *EVALUATOR.FULL_REQUIRED_STAGE_TOOLS],
            )
            report = EVALUATOR.method_boundary_audit(run, {"experiment_method": "full", "inputs": []})
            self.assertEqual(report["status"], "pass", report)

    def test_ablation_cannot_call_the_removed_full_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            required = list(EVALUATOR.ABLATION_REQUIRED_STAGE_TOOLS["w_o_complete_trace"])
            required[-1] = "archive_complete_calculation_process"
            session_with_tools(run / "session_export/session.jsonl", required)
            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "w_o_complete_trace", "inputs": []},
            )
            self.assertEqual(report["status"], "fail")
            self.assertIn("ABLATION_REQUIRED_STAGE_MISSING", report["failures"])
            self.assertIn("ABLATION_FORBIDDEN_STAGE_USED", report["failures"])


if __name__ == "__main__":
    unittest.main()
