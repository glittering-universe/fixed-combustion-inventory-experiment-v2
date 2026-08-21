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


def session_with_tools(
    path: Path,
    tools: list[str],
    *,
    outcomes: list[dict[str, object] | str] | None = None,
) -> None:
    messages = []
    for index, name in enumerate(tools):
        call_id = f"call-{index}"
        messages.append({
            "id": index * 2,
            "role": "assistant",
            "tool_calls": [{
                "id": call_id,
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({"name": name, "arguments": {}}),
                }
            }],
        })
        outcome = outcomes[index] if outcomes is not None else {"success": True, "status": "complete"}
        messages.append({
            "id": index * 2 + 1,
            "role": "tool",
            "tool_call_id": call_id,
            "content": outcome if isinstance(outcome, str) else json.dumps(outcome),
        })
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "S", "messages": messages}), encoding="utf-8")


def session_with_function_call(
    path: Path,
    name: str,
    arguments: dict[str, object],
    result: dict[str, object] | str,
) -> None:
    call_id = "call-0"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "id": "S",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": call_id,
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result if isinstance(result, str) else json.dumps(result),
                },
            ],
        }),
        encoding="utf-8",
    )


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
    def test_generic_allows_trusted_python_only_as_execution_program(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            trusted_python = Path.home() / ".hermes/hermes-agent/venv/bin/python3"
            call_id = "trusted-python"
            session = {
                "id": "S",
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": call_id,
                            "function": {
                                "name": "terminal",
                                "arguments": json.dumps({
                                    "command": f'cd "{run}" && {trusted_python} scripts/check.py',
                                }),
                            },
                        }],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"exit_code": 0, "error": None, "output": "ok"}),
                    },
                ],
            }
            session_path = run / "session_export/session.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(json.dumps(session), encoding="utf-8")

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "pass", report)
            self.assertEqual(report["outside_run_path_count"], 0, report)

    def test_generic_allows_trusted_python_selected_then_executed_in_shell_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            trusted_python = Path.home() / ".hermes/hermes-agent/venv/bin/python3"
            session_with_function_call(
                run / "session_export/session.jsonl",
                "terminal",
                {
                    "command": (
                        f"for py in /usr/bin/python3 {trusted_python}; "
                        'do $py -c "import sys; print(sys.version)"; done'
                    ),
                },
                {"exit_code": 0, "error": None, "output": "ok"},
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "pass", report)

    def test_generic_allows_trusted_python_variable_as_subprocess_program(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            trusted_python = ROOT / ".venv/bin/python"
            session_with_function_call(
                run / "session_export/session.jsonl",
                "execute_code",
                {
                    "code": (
                        f'venv_py = "{trusted_python}"\n'
                        'subprocess.run([venv_py, "-c", "import openpyxl"], check=False)'
                    ),
                },
                {"status": "success", "exit_code": 0, "output": ""},
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "pass", report)

    def test_generic_does_not_allow_parent_directory_browsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            session_with_function_call(
                run / "session_export/session.jsonl",
                "terminal",
                {"command": f'ls "{ROOT}"'},
                {"exit_code": 0, "error": None, "output": "evaluation"},
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "fail", report)
            self.assertIn("GENERIC_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE", report["failures"])

    def test_generic_does_not_allow_external_pip_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            trusted_python = Path.home() / ".hermes/hermes-agent/venv/bin/python3"
            session_with_function_call(
                run / "session_export/session.jsonl",
                "terminal",
                {"command": f"{trusted_python} -m pip install pandas"},
                {"exit_code": 0, "error": None, "output": "installed"},
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "fail", report)
            self.assertIn("GENERIC_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE", report["failures"])

    def test_generic_does_not_allow_trusted_python_path_prefix_lookalike(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            trusted_python = Path.home() / ".hermes/hermes-agent/venv/bin/python3"
            session_with_function_call(
                run / "session_export/session.jsonl",
                "terminal",
                {
                    "command": (
                        f"for py in {trusted_python}-untrusted; "
                        'do $py -c "print(1)"; done'
                    ),
                },
                {"exit_code": 0, "error": None, "output": ""},
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "fail", report)
            self.assertIn("GENERIC_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE", report["failures"])

    def test_failed_nonexistent_tool_call_is_warning_not_actual_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            session_with_function_call(
                run / "session_export/session.jsonl",
                "terminal",
                {"command": f'ls "{ROOT}"'},
                "Tool 'terminal' does not exist. Available tools: tool_call, tool_search",
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "generic_tool_agent", "inputs": []},
            )

            self.assertEqual(report["status"], "pass", report)
            self.assertEqual(report["outside_run_path_count"], 0, report)
            self.assertIn("NONEXISTENT_TOOL_CALL_NOT_EXECUTED", report["warnings"])

    def test_ablation_accepts_complete_ordered_success_chain_after_failed_stage_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            required = list(EVALUATOR.ABLATION_REQUIRED_STAGE_TOOLS["w_o_calculation_item_structure"])
            tools = ["build_device_emission_sources", *required]
            outcomes: list[dict[str, object]] = [
                {"success": False, "status": "error", "error": "upstream stage incomplete"},
                *({"success": True, "status": "complete"} for _ in required),
            ]
            session_with_tools(
                run / "session_export/session.jsonl",
                tools,
                outcomes=outcomes,
            )

            report = EVALUATOR.method_boundary_audit(
                run,
                {"experiment_method": "w_o_calculation_item_structure", "inputs": []},
            )

            self.assertEqual(report["status"], "pass", report)
            self.assertIn("FAILED_STAGE_ATTEMPT_IGNORED", report["warnings"])

    def test_full_allows_exploration_but_requires_ordered_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            session_with_tools(
                run / "session_export/session.jsonl",
                ["skill_view", *EVALUATOR.FULL_REQUIRED_STAGE_TOOLS],
            )
            report = EVALUATOR.method_boundary_audit(run, {"experiment_method": "full", "inputs": []})
            self.assertEqual(report["status"], "pass", report)
            self.assertFalse(report["exploration_calls_have_independent_DNE_penalty"])
            self.assertTrue(report["exploration_elapsed_time_included_in_wall_seconds"])
            self.assertNotIn("exploration_included_in_EICPI", report)

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
