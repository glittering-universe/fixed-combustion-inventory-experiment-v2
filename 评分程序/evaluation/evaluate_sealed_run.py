#!/usr/bin/env python3
"""Run reference-independent checks on one sealed experiment package.

This evaluator deliberately separates checks that can be made from the sealed
run itself from claims that require the user-supplied reference package.  It
never assigns a formal S/Q score or the reference-dependent quality gates when
that package is absent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


POLLUTANTS = ("SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC", "NH3")
ALLOWED_STATUSES = {
    "calculated",
    "not_involved",
    "information_insufficient",
    "source_data_invalid",
}
POLLUTANT_ALIASES = {
    "SO2": "SO2", "NOX": "NOx", "NOx": "NOx", "CO": "CO",
    "VOCS": "VOC", "VOC": "VOC", "PM10": "PM10", "PM25": "PM2.5",
    "PM2.5": "PM2.5", "BC": "BC", "OC": "OC", "NH3": "NH3",
}

FULL_REQUIRED_STAGE_TOOLS = (
    "adapt_environmental_workbooks",
    "build_device_emission_sources",
    "generate_pollutant_calculation_items",
    "execute_tcses_calculation_rules",
    "control_calculation_admission",
    "calculate_fixed_combustion_emissions",
    "validate_and_export_fixed_combustion_inventory",
    "archive_complete_calculation_process",
)

# These tools replace a formal Full stage with an ablation-specific path. Read-only
# option summaries and general tool discovery are exploration, not forbidden use.
FULL_FORBIDDEN_STAGE_TOOLS = {
    "generate_device_pollutant_slots",
    "record_agent_rule_choices",
    "record_agent_admission_decisions",
    "archive_minimal_calculation_process",
}

ABLATION_REQUIRED_STAGE_TOOLS = {
    "w_o_calculation_item_structure": (
        "adapt_environmental_workbooks", "build_device_emission_sources",
        "generate_device_pollutant_slots", "execute_tcses_calculation_rules",
        "control_calculation_admission", "calculate_fixed_combustion_emissions",
        "validate_and_export_fixed_combustion_inventory", "archive_complete_calculation_process",
    ),
    "w_o_executable_rule_gate": (
        "adapt_environmental_workbooks", "build_device_emission_sources",
        "generate_pollutant_calculation_items", "record_agent_rule_choices",
        "control_calculation_admission", "calculate_fixed_combustion_emissions",
        "validate_and_export_fixed_combustion_inventory", "archive_complete_calculation_process",
    ),
    "agent_managed_admission": (
        "adapt_environmental_workbooks", "build_device_emission_sources",
        "generate_pollutant_calculation_items", "execute_tcses_calculation_rules",
        "record_agent_admission_decisions", "calculate_fixed_combustion_emissions",
        "validate_and_export_fixed_combustion_inventory", "archive_complete_calculation_process",
    ),
    "w_o_complete_trace": (
        "adapt_environmental_workbooks", "build_device_emission_sources",
        "generate_pollutant_calculation_items", "execute_tcses_calculation_rules",
        "control_calculation_admission", "calculate_fixed_combustion_emissions",
        "validate_and_export_fixed_combustion_inventory", "archive_minimal_calculation_process",
    ),
}

ALL_SPECIALIZED_STAGE_TOOLS = set(FULL_REQUIRED_STAGE_TOOLS) | FULL_FORBIDDEN_STAGE_TOOLS

TRUSTED_PYTHON_EXECUTABLES = {
    str(Path.home() / ".hermes/hermes-agent/venv/bin/python3"),
    str(Path(__file__).resolve().parents[1] / ".venv/bin/python"),
    str(Path(__file__).resolve().parents[1] / ".venv/bin/python3"),
}

# Evaluator-owned derivatives. Early expert-led packages listed these files in
# their seal alongside the immutable human workbook. Re-evaluation may
# legitimately regenerate them, so their evaluator-version hashes are not used
# as evidence that the original human artifact changed.
REEVALUATION_DERIVATIVE_PATHS = {
    "evaluation/normalized_items.csv",
    "evaluation/reference_independent_report.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite_number(value: str) -> float | None:
    if value in ("", None):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def as_fraction(value: Any) -> float | None:
    number = finite_number(value)
    if number is None:
        return None
    return number / 100.0 if number > 1 else number


def normalized_activity(value: Any, unit: str) -> tuple[float | None, str | None]:
    number = finite_number(value)
    if number is None:
        return None, None
    compact = str(unit or "").strip().lower().replace("³", "3").replace(" ", "")
    if compact in {"kg", "kg_coal", "kg煤"}:
        return number, "kg"
    if compact in {"t", "吨", "吨燃料"}:
        return number * 1000.0, "kg"
    if compact == "万吨":
        return number * 10_000_000.0, "kg"
    if compact in {"m3", "立方米"}:
        return number, "m3"
    if compact in {"万m3", "万立方米"}:
        return number * 10_000.0, "m3"
    return None, None


def parameter_basis(unit: str) -> str | None:
    compact = str(unit or "").strip().lower().replace("³", "3").replace(" ", "")
    if compact.startswith("g/kg"):
        return "kg"
    if compact.startswith("g/m3"):
        return "m3"
    return None


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(1e-6, abs(expected) * 1e-6)


def specialized_item_groups(run_dir: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Load item-level admission, rule and calculation records when available."""
    database = run_dir / "run.sqlite3"
    if not database.is_file():
        return {}
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT i.item_id,i.source_id,i.pollutant,i.fuel_id,
               a.action,a.reason_code,
               r.activity_value,r.activity_unit,r.factor_value,r.factor_unit,
               r.control_efficiency,r.control_fine_efficiency,r.control_coarse_efficiency,
               c.generation_t,c.emission_t,c.calculation_status
        FROM calculation_items i
        LEFT JOIN admission_results a ON a.item_id=i.item_id
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        LEFT JOIN calculations c ON c.item_id=i.item_id
        ORDER BY i.source_id,i.pollutant,i.item_id
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    try:
        for raw in connection.execute(query):
            item = dict(raw)
            pollutant = POLLUTANT_ALIASES.get(str(item.get("pollutant") or ""), str(item.get("pollutant") or ""))
            grouped[(str(item.get("source_id") or ""), pollutant)].append(item)
    finally:
        connection.close()
    return grouped


def minimum_recalculation_checks(
    rows: list[dict[str, str]], run_dir: Path, method: str
) -> dict[str, Any]:
    """Recompute only calculation-admitted items and audit excluded items separately."""
    by_key = {(row["source_id"], row["pollutant"]): row for row in rows}
    item_groups = specialized_item_groups(run_dir)
    failures: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    checked = 0
    allow_items_checked = 0
    not_applicable_items_checked = 0

    def fail(code: str, row: dict[str, str], detail: str = "") -> None:
        failures[code] += 1
        if len(examples) < 20:
            examples.append({"source_id": row["source_id"], "pollutant": row["pollutant"], "code": code, "detail": detail})

    # Expert-led workbooks have no item-level admission contract. Their final
    # values are still compared with the independent reference package, while
    # missing field/parameter lineage is reflected by the T metric and minimum
    # process-record gate rather than by inventing an implicit calculation item.
    if method == "expert_led":
        return {
            "status": "pass",
            "calculated_rows": sum(row.get("status") == "calculated" for row in rows),
            "recomputed_rows": 0,
            "allow_calculation_items_checked": 0,
            "not_applicable_items_checked": 0,
            "failure_counts": {},
            "examples": [],
            "note": "expert_led_has_no_item_level_admission; final values use reference comparison and missing process evidence lowers T",
        }

    if item_groups:
        for key, explicit_items in item_groups.items():
            if key not in by_key:
                continue
            normalized_row = by_key.get(key, {"source_id": key[0], "pollutant": key[1]})
            for item in explicit_items:
                if item.get("action") != "not_applicable":
                    continue
                not_applicable_items_checked += 1
                if not str(item.get("reason_code") or "").strip():
                    fail("NOT_APPLICABLE_REASON_MISSING", normalized_row, str(item.get("item_id") or ""))
                if item.get("calculation_status") == "calculated":
                    fail("NOT_APPLICABLE_INCLUDED_IN_CALCULATION", normalized_row, str(item.get("item_id") or ""))

    for row in rows:
        if row.get("status") != "calculated":
            continue
        explicit_items = item_groups.get((row["source_id"], row["pollutant"]))
        if explicit_items is not None:
            calculation_items = [item for item in explicit_items if item.get("action") == "allow_calculation"]
            activities = [
                {"value": item.get("activity_value"), "unit": item.get("activity_unit"), "fuel_id": item.get("fuel_id")}
                for item in calculation_items
            ]
            parameters = [
                {"value": item.get("factor_value"), "unit": item.get("factor_unit"), "fuel_id": item.get("fuel_id")}
                for item in calculation_items
            ]
            controls = [
                {
                    "efficiency": item.get("control_efficiency"),
                    "fine_efficiency": item.get("control_fine_efficiency"),
                    "coarse_efficiency": item.get("control_coarse_efficiency"),
                    "fuel_id": item.get("fuel_id"),
                }
                for item in calculation_items
            ]
            allow_items_checked += len(calculation_items)
        else:
            try:
                activities = json.loads(row["activity_record"])
                parameters = json.loads(row["parameter_record"])
                controls = json.loads(row["control_record"])
            except (json.JSONDecodeError, TypeError) as error:
                fail("MINIMUM_RECORD_JSON_INVALID", row, str(error))
                continue
            if isinstance(activities, dict):
                activities = [activities]
            if isinstance(parameters, dict):
                parameters = [parameters]
            if isinstance(controls, dict):
                controls = [controls]
            if not (len(activities) == len(parameters) == len(controls)):
                fail("MINIMUM_RECORD_ALIGNMENT_INVALID", row, f"{len(activities)}/{len(parameters)}/{len(controls)}")
                continue
            allow_items_checked += len(activities)

        expected_generation = 0.0
        expected_emission = 0.0
        row_recomputable = True
        for index, (activity, parameter, control) in enumerate(zip(activities, parameters, controls)):
            amount, activity_basis = normalized_activity(activity.get("value"), activity.get("unit", ""))
            factor = finite_number(parameter.get("value"))
            factor_basis = parameter_basis(parameter.get("unit", ""))
            if amount is None or factor is None or activity_basis is None or factor_basis != activity_basis:
                fail("MINIMUM_RECORD_UNIT_OR_VALUE_INVALID", row, f"item={index}")
                row_recomputable = False
                break
            item_generation = amount * factor / 1_000_000.0
            expected_generation += item_generation
            if row["pollutant"] == "PM10" and control.get("fine_efficiency") is not None and control.get("coarse_efficiency") is not None:
                fine_factor = None
                if explicit_items is not None:
                    pm25_items = item_groups.get((row["source_id"], "PM2.5"), [])
                    paired = next(
                        (
                            item for item in pm25_items
                            if item.get("action") == "allow_calculation"
                            and item.get("fuel_id") == activity.get("fuel_id")
                        ),
                        None,
                    )
                    fine_factor = finite_number(paired.get("factor_value")) if paired else None
                else:
                    pm25_row = by_key.get((row["source_id"], "PM2.5"))
                    if pm25_row:
                        pm25_parameters = json.loads(pm25_row["parameter_record"])
                        if isinstance(pm25_parameters, dict):
                            pm25_parameters = [pm25_parameters]
                        fine_factor = finite_number(pm25_parameters[index].get("value")) if index < len(pm25_parameters) else None
                if fine_factor is None:
                    fail("MINIMUM_RECORD_PM25_PAIR_MISSING", row, f"item={index}")
                    row_recomputable = False
                    break
                fine_efficiency = as_fraction(control.get("fine_efficiency"))
                coarse_efficiency = as_fraction(control.get("coarse_efficiency"))
                if fine_factor is None or fine_efficiency is None or coarse_efficiency is None:
                    fail("MINIMUM_RECORD_PM10_COMPONENT_MISSING", row, f"item={index}")
                    row_recomputable = False
                    break
                fine_generation = amount * fine_factor / 1_000_000.0
                coarse_generation = item_generation - fine_generation
                expected_emission += fine_generation * (1 - fine_efficiency) + coarse_generation * (1 - coarse_efficiency)
            else:
                efficiency = as_fraction(control.get("efficiency"))
                if efficiency is None:
                    fail("MINIMUM_RECORD_CONTROL_INVALID", row, f"item={index}")
                    row_recomputable = False
                    break
                expected_emission += item_generation * (1 - efficiency)

        if not row_recomputable:
            continue
        actual_generation = finite_number(row.get("generation_t"))
        actual_emission = finite_number(row.get("emission_t"))
        if actual_generation is None or actual_emission is None:
            fail("MINIMUM_RECORD_RESULT_INVALID", row)
            continue
        checked += 1
        if not close_enough(actual_generation, expected_generation):
            fail("MINIMUM_RECALCULATION_GENERATION_MISMATCH", row, f"actual={actual_generation};expected={expected_generation}")
        if not close_enough(actual_emission, expected_emission):
            fail("MINIMUM_RECALCULATION_EMISSION_MISMATCH", row, f"actual={actual_emission};expected={expected_emission}")

    return {
        "status": "pass" if not failures else "fail",
        "calculated_rows": sum(row.get("status") == "calculated" for row in rows),
        "recomputed_rows": checked,
        "allow_calculation_items_checked": allow_items_checked,
        "not_applicable_items_checked": not_applicable_items_checked,
        "failure_counts": dict(failures),
        "examples": examples,
    }


def check(condition: bool, failures: list[str], code: str) -> None:
    if not condition:
        failures.append(code)


def verify_experiment_seal(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    seal_path = run_dir / "experiment_seal.json"
    failures: list[str] = []
    if not seal_path.is_file():
        return {"status": "fail", "failures": ["EXPERIMENT_SEAL_MISSING"]}
    seal = load_json(seal_path)
    check(seal.get("run_id") == manifest.get("run_id"), failures, "SEAL_RUN_ID_MISMATCH")
    check(
        seal.get("method_bundle_hash") == manifest.get("method_bundle_hash"),
        failures,
        "SEAL_METHOD_HASH_MISMATCH",
    )
    for source in manifest.get("inputs", []):
        original_path = Path(source["path"])
        path = original_path
        if not path.is_file():
            relocated = run_dir / "inputs" / original_path.name
            if relocated.is_file():
                path = relocated
        expected = source.get("sha256", "")
        check(path.is_file(), failures, f"INPUT_MISSING:{path.name}")
        if path.is_file():
            actual = sha256(path)
            check(actual == expected, failures, f"INPUT_HASH_MISMATCH:{path.name}")
            try:
                relative_key = str(path.relative_to(run_dir))
            except ValueError:
                relative_key = str(original_path)
            sealed_hash = (
                seal.get("input_hashes", {}).get(relative_key)
                or seal.get("input_hashes", {}).get(str(original_path))
            )
            check(sealed_hash == expected, failures, f"SEAL_INPUT_HASH_MISMATCH:{path.name}")
    for path_key, hash_key in (
        ("source_identity_index_path", "source_identity_index_hash"),
        ("standard_pdf_path", "standard_pdf_hash"),
    ):
        raw_path = manifest.get(path_key)
        if not raw_path:
            continue
        original_path = Path(str(raw_path))
        path = original_path if original_path.is_file() else run_dir / "inputs" / original_path.name
        expected = str(manifest.get(hash_key) or "")
        check(path.is_file(), failures, f"INPUT_MISSING:{path.name}")
        if path.is_file():
            actual = sha256(path)
            if expected:
                check(actual == expected, failures, f"INPUT_HASH_MISMATCH:{path.name}")
            sealed_hash = seal.get("input_hashes", {}).get(str(path.relative_to(run_dir)))
            check(sealed_hash == (expected or actual), failures, f"SEAL_INPUT_HASH_MISMATCH:{path.name}")
    metrics_path = run_dir / "logs" / "execution_metrics.json"
    check(metrics_path.is_file(), failures, "EXECUTION_METRICS_MISSING")
    if metrics_path.is_file():
        check(
            sha256(metrics_path) == seal.get("execution_metrics_sha256"),
            failures,
            "EXECUTION_METRICS_HASH_MISMATCH",
        )
    ignored_derivatives: list[str] = []
    for item in seal.get("files", []):
        if item["path"] in REEVALUATION_DERIVATIVE_PATHS:
            ignored_derivatives.append(item["path"])
            continue
        path = run_dir / item["path"]
        check(path.is_file(), failures, f"SEALED_FILE_MISSING:{item['path']}")
        if path.is_file():
            check(sha256(path) == item.get("sha256"), failures, f"SEALED_FILE_HASH_MISMATCH:{item['path']}")
            check(path.stat().st_size == item.get("bytes"), failures, f"SEALED_FILE_SIZE_MISMATCH:{item['path']}")
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "ignored_reevaluation_derivatives": ignored_derivatives,
    }


def ensure_normalized(run_dir: Path) -> Path:
    output = run_dir / "evaluation" / "normalized_items.csv"
    normalizer = Path(__file__).with_name("normalize_run_output.py")
    subprocess.run([sys.executable, str(normalizer), str(run_dir), "--output", str(output)], check=True)
    return output


def workbook_qa(run_dir: Path) -> dict[str, Any]:
    qa_files = sorted((run_dir / "outputs").glob("machine_qa_*.json"))
    if not qa_files:
        return {
            "status": "not_available",
            "formula_error_count": None,
            "external_link_count": None,
            "files": [],
        }
    formula_errors = 0
    external_links = 0
    details = []
    for path in qa_files:
        data = load_json(path)
        formula_errors += int(data.get("formula_error_count", 0))
        external_links += int(data.get("external_link_count", 0))
        details.append({"path": str(path.relative_to(run_dir)), **data})
    return {
        "status": "pass" if formula_errors == 0 and external_links == 0 else "fail",
        "formula_error_count": formula_errors,
        "external_link_count": external_links,
        "files": details,
    }


def argument_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in argument_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in argument_strings(item)]
    return []


def trusted_python_used_as_program(arguments: str, occurrence_start: int) -> bool:
    """Allow only an exact trusted interpreter token used to execute Python."""
    for executable in TRUSTED_PYTHON_EXECUTABLES:
        if not arguments.startswith(executable, occurrence_start):
            continue
        tail = arguments[occurrence_start + len(executable):]
        if re.match(r"\s+(?:-c\b|--version\b|[^\s;&|]+\.py\b)", tail):
            return True
        escaped = re.escape(executable)
        shell_loop = re.search(
            rf"\bfor\s+(\w+)\s+in\s+[^;]*{escaped}(?=\s|;)[^;]*;\s*do\b.*\$\1\s+"
            rf"(?:-c\b|--version\b|[^\s;&|]+\.py\b)",
            arguments,
            flags=re.DOTALL,
        )
        if shell_loop:
            return True
        assignments = re.findall(
            rf"\b([A-Za-z_]\w*)\s*=\s*['\"]{escaped}['\"]",
            arguments,
        )
        for variable in assignments:
            if re.search(
                rf"subprocess\.(?:run|Popen|call|check_call|check_output)\s*\(\s*"
                rf"\[\s*{re.escape(variable)}\s*,\s*['\"](?:-c|--version)['\"]",
                arguments,
            ):
                return True
        if re.search(
            rf"subprocess\.(?:run|Popen|call|check_call|check_output)\s*\(\s*"
            rf"\[\s*['\"]{escaped}['\"]\s*,\s*['\"](?:-c|--version)['\"]",
            arguments,
        ):
            return True
    return False


def tool_call_outcome(result_message: dict[str, Any] | None) -> str:
    """Classify a recorded call without treating a rejected tool name as access."""
    if result_message is None:
        return "unknown"
    content = result_message.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if re.search(r"Tool ['\"][^'\"]+['\"] does not exist\b", text, flags=re.IGNORECASE):
        return "not_executed"
    try:
        payload = json.loads(text) if isinstance(text, str) else content
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return "failed"
        status = str(payload.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure"}:
            return "failed"
        if payload.get("error") not in (None, "", False):
            return "failed"
        if "exit_code" in payload and payload.get("exit_code") not in (None, 0):
            return "failed"
        if payload.get("success") is True or status in {"success", "complete", "completed", "pass", "ok"}:
            return "success"
        if "exit_code" in payload and payload.get("exit_code") == 0:
            return "success"
    return "success"


def ordered_stage_chain_positions(invoked_tools: list[str], required: tuple[str, ...]) -> list[int] | None:
    """Return one complete ordered subsequence, if the successful call stream has one."""
    positions: list[int] = []
    cursor = 0
    for stage in required:
        try:
            position = invoked_tools.index(stage, cursor)
        except ValueError:
            return None
        positions.append(position)
        cursor = position + 1
    return positions


def method_boundary_audit(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    session_path = run_dir / "session_export" / "session.jsonl"
    method = manifest.get("experiment_method")
    if not session_path.is_file():
        return {
            "status": "not_applicable" if method == "deterministic_program" else "not_available",
            "reason": "no_agent_session_export",
        }
    session = json.loads(session_path.read_text(encoding="utf-8"))
    result_messages = {
        str(message.get("tool_call_id")): message
        for message in session.get("messages", [])
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    tool_counts: Counter[str] = Counter()
    viewed_skills: list[str] = []
    fixed_capability_calls: list[str] = []
    invoked_tools: list[str] = []
    executed_invoked_tools: list[str] = []
    successful_invoked_tools: list[str] = []
    exploration_calls: list[str] = []
    outside_run_paths: list[str] = []
    warnings: list[str] = []
    allowed_prefixes = {str(run_dir)}
    for source in manifest.get("inputs", []):
        original = str(source.get("path") or "")
        marker = "/inputs/"
        if marker in original:
            allowed_prefixes.add(original.split(marker, 1)[0])
    for message in session.get("messages", []):
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "")
            tool_counts[name] += 1
            effective_name = name
            parsed_arguments: dict[str, Any] | None = None
            try:
                candidate = json.loads(arguments)
                if isinstance(candidate, dict):
                    parsed_arguments = candidate
            except json.JSONDecodeError:
                parsed_arguments = None
            call_id = str(call.get("id") or call.get("call_id") or "")
            outcome = tool_call_outcome(result_messages.get(call_id))
            if outcome == "not_executed":
                warnings.append("NONEXISTENT_TOOL_CALL_NOT_EXECUTED")
            invocation_recorded = False
            if name == "tool_call" and parsed_arguments and parsed_arguments.get("name"):
                effective_name = str(parsed_arguments["name"])
                invoked_tools.append(effective_name)
                invocation_recorded = True
            elif name not in {"skill_view", "tool_describe"}:
                invoked_tools.append(effective_name)
                invocation_recorded = True
            if invocation_recorded and outcome != "not_executed":
                executed_invoked_tools.append(effective_name)
            if invocation_recorded and outcome == "success":
                successful_invoked_tools.append(effective_name)
            elif invocation_recorded and outcome == "failed" and effective_name in ALL_SPECIALIZED_STAGE_TOOLS:
                warnings.append("FAILED_STAGE_ATTEMPT_IGNORED")
            if name == "skill_view":
                try:
                    viewed_skills.append(str(json.loads(arguments).get("name") or ""))
                except json.JSONDecodeError:
                    viewed_skills.append(arguments)
            if name in {"skill_view", "tool_describe", "tool_search", "skills_list"} or effective_name.startswith("summarize_"):
                exploration_calls.append(effective_name)
            capability_text = f"{name}\n{arguments}".lower()
            if outcome != "not_executed" and "fixed-combustion-inventory" in capability_text:
                fixed_capability_calls.append(name)
            if outcome == "not_executed":
                continue
            path_texts = argument_strings(parsed_arguments) if parsed_arguments is not None else [arguments]
            for path_text in path_texts:
                for match in re.finditer(r"/Users/wushuo/", path_text):
                    if trusted_python_used_as_program(path_text, match.start()):
                        continue
                    if not any(path_text.startswith(prefix, match.start()) for prefix in allowed_prefixes):
                        outside_run_paths.append(path_text[match.start():match.start() + 180])

    failures: list[str] = []
    if method == "generic_tool_agent":
        if fixed_capability_calls:
            failures.append("GENERIC_USED_FIXED_COMBUSTION_CAPABILITY")
        if outside_run_paths:
            failures.append("GENERIC_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE")
    elif method == "full":
        missing_stages = [stage for stage in FULL_REQUIRED_STAGE_TOOLS if stage not in successful_invoked_tools]
        stage_positions = ordered_stage_chain_positions(successful_invoked_tools, FULL_REQUIRED_STAGE_TOOLS)
        if missing_stages:
            failures.append("FULL_REQUIRED_STAGE_MISSING")
        elif stage_positions is None:
            failures.append("FULL_REQUIRED_STAGE_ORDER_INVALID")
        forbidden = sorted(set(executed_invoked_tools) & FULL_FORBIDDEN_STAGE_TOOLS)
        if forbidden:
            failures.append("FULL_FORBIDDEN_CAPABILITY_USED")
        if outside_run_paths:
            failures.append("FULL_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE")
        # A complete, ordered stage chain is the no-bypass proof. Skill views,
        # tool discovery and harmless read-only inspection are exploration cost.
    elif method in ABLATION_REQUIRED_STAGE_TOOLS:
        required = ABLATION_REQUIRED_STAGE_TOOLS[str(method)]
        missing = [stage for stage in required if stage not in successful_invoked_tools]
        positions = ordered_stage_chain_positions(successful_invoked_tools, required)
        if missing:
            failures.append("ABLATION_REQUIRED_STAGE_MISSING")
        elif positions is None:
            failures.append("ABLATION_REQUIRED_STAGE_ORDER_INVALID")
        forbidden = sorted((set(executed_invoked_tools) & ALL_SPECIALIZED_STAGE_TOOLS) - set(required))
        if forbidden:
            failures.append("ABLATION_FORBIDDEN_STAGE_USED")
        if outside_run_paths:
            failures.append("ABLATION_ACCESSED_PATH_OUTSIDE_RUN_PACKAGE")
    required_for_method = (
        FULL_REQUIRED_STAGE_TOOLS if method == "full"
        else ABLATION_REQUIRED_STAGE_TOOLS.get(str(method), ())
    )
    forbidden_for_method = (
        sorted(set(executed_invoked_tools) & FULL_FORBIDDEN_STAGE_TOOLS) if method == "full"
        else sorted((set(executed_invoked_tools) & ALL_SPECIALIZED_STAGE_TOOLS) - set(required_for_method))
        if method in ABLATION_REQUIRED_STAGE_TOOLS else []
    )
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "warnings": list(dict.fromkeys(warnings)),
        "tool_counts": dict(tool_counts),
        "viewed_skills": viewed_skills,
        "invoked_tools": invoked_tools,
        "successful_invoked_tools": successful_invoked_tools,
        "required_stage_tools": list(required_for_method),
        "forbidden_stage_tools": forbidden_for_method,
        "exploration_call_count": len(exploration_calls),
        "exploration_calls": exploration_calls,
        "exploration_calls_have_independent_DNE_penalty": False,
        "exploration_elapsed_time_included_in_wall_seconds": True,
        "exploration_scoring_note": (
            "Exploration calls do not incur an independent D/N/E penalty; their elapsed time "
            "remains included in the end-to-end wall_seconds used by E."
        ),
        "fixed_capability_call_count": len(fixed_capability_calls),
        "outside_run_path_count": len(outside_run_paths),
        "outside_run_path_examples": outside_run_paths[:10],
        "system_prompt_hash": session.get("system_prompt_hash"),
        "session_id": session.get("id"),
    }


def structural_checks(run_dir: Path, manifest: dict[str, Any], normalized: Path) -> dict[str, Any]:
    with normalized.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    failures: list[str] = []
    diagnostics: list[str] = []
    scope_ids = list(manifest.get("scope_ids") or [])
    if not scope_ids:
        decision_path = next(
            (
                path for path in (
                    run_dir / "outputs" / "source_decisions.csv",
                    run_dir / "source_decisions.csv",
                )
                if path.is_file()
            ),
            None,
        )
        if decision_path is None:
            failures.append("SOURCE_DECISION_TABLE_MISSING")
        else:
            with decision_path.open(encoding="utf-8-sig", newline="") as handle:
                scope_ids = [
                    row.get("source_id", "")
                    for row in csv.DictReader(handle)
                    if row.get("decided_target") == manifest.get("target")
                    or row.get("target") == manifest.get("target")
                    and row.get("disposition") in {"include", "selected", "included"}
                ]
            diagnostics.append("EXPECTED_SCOPE_DERIVED_FROM_TARGET_NEUTRAL_SOURCE_DECISIONS")
    expected_keys = {(source_id, pollutant) for source_id in scope_ids for pollutant in POLLUTANTS}
    observed_keys = [(row.get("source_id", ""), row.get("pollutant", "")) for row in rows]
    observed_set = set(observed_keys)
    duplicates = sorted(key for key, count in Counter(observed_keys).items() if count > 1)
    missing = sorted(expected_keys - observed_set)
    unexpected = sorted(observed_set - expected_keys)
    invalid_status = sorted({row.get("status", "") for row in rows} - ALLOWED_STATUSES)
    target_mismatch = [
        (row.get("source_id", ""), row.get("pollutant", ""), row.get("target", ""))
        for row in rows
        if row.get("target", "") != manifest["target"]
    ]

    check(not duplicates, failures, "DUPLICATE_SOURCE_POLLUTANT_ROWS")
    check(not missing, failures, "SCOPE_ROWS_MISSING")
    check(not unexpected, failures, "UNEXPECTED_SCOPE_ROWS")
    check(not invalid_status, failures, "INVALID_TERMINAL_STATUS")
    check(not target_mismatch, failures, "TARGET_MISMATCH")

    calculated_count = 0
    nonnumeric_count = 0
    negative_count = 0
    emission_gt_generation_count = 0
    forbidden_numeric_count = 0
    minimum_missing_count = 0
    complete_trace_count = 0
    by_source: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    status_counts: Counter[str] = Counter()
    for row in rows:
        status = row.get("status", "")
        status_counts[status] += 1
        source_id = row.get("source_id", "")
        pollutant = row.get("pollutant", "")
        by_source[source_id][pollutant] = row
        if str(row.get("complete_process_record", "")).lower() == "true":
            complete_trace_count += 1
        generation = finite_number(row.get("generation_t", ""))
        emission = finite_number(row.get("emission_t", ""))
        if status == "calculated":
            calculated_count += 1
            if generation is None or emission is None:
                nonnumeric_count += 1
                continue
            if generation < 0 or emission < 0:
                negative_count += 1
            if emission > generation + max(1e-12, abs(generation) * 1e-12):
                emission_gt_generation_count += 1
            if str(row.get("minimum_recalculation_complete", "")).lower() != "true":
                minimum_missing_count += 1
        elif generation is not None or emission is not None:
            forbidden_numeric_count += 1

    pm_relation_failures = 0
    for pollutants in by_source.values():
        pm10 = pollutants.get("PM10")
        pm25 = pollutants.get("PM2.5")
        if not pm10 or not pm25 or pm10.get("status") != "calculated" or pm25.get("status") != "calculated":
            continue
        for field in ("generation_t", "emission_t"):
            ten = finite_number(pm10.get(field, ""))
            fine = finite_number(pm25.get(field, ""))
            if ten is not None and fine is not None and fine > ten + max(1e-12, abs(ten) * 1e-12):
                pm_relation_failures += 1

    for code, count in (
        ("CALCULATED_VALUE_MISSING_OR_NONNUMERIC", nonnumeric_count),
        ("NEGATIVE_CALCULATED_VALUE", negative_count),
        ("EMISSION_EXCEEDS_GENERATION", emission_gt_generation_count),
        ("NONCALCULATED_ROW_HAS_NUMERIC_RESULT", forbidden_numeric_count),
        ("MINIMUM_RECALCULATION_RECORD_MISSING", minimum_missing_count),
        ("PM25_EXCEEDS_PM10", pm_relation_failures),
    ):
        if count:
            failures.append(f"{code}:{count}")

    if manifest.get("experiment_method") == "generic_tool_agent" and complete_trace_count:
        diagnostics.append("GENERIC_METHOD_UNEXPECTED_COMPLETE_TRACE")

    independent_recalculation = minimum_recalculation_checks(
        rows, run_dir, str(manifest.get("experiment_method") or "")
    )
    if independent_recalculation["status"] != "pass":
        failures.append("INDEPENDENT_MINIMUM_RECALCULATION_FAILED")

    total_expected = len(expected_keys)
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "diagnostics": diagnostics,
        "expected_rows": total_expected,
        "observed_rows": len(rows),
        "unique_rows": len(observed_set),
        "duplicate_count": len(duplicates),
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "invalid_statuses": invalid_status,
        "target_mismatch_count": len(target_mismatch),
        "status_counts": dict(status_counts),
        "calculated_count": calculated_count,
        "minimum_recalculation_complete_count": calculated_count - minimum_missing_count,
        "complete_process_record_count": complete_trace_count,
        "complete_process_record_coverage": (complete_trace_count / total_expected) if total_expected else None,
        "independent_minimum_recalculation": independent_recalculation,
        "numeric_checks": {
            "missing_or_nonnumeric": nonnumeric_count,
            "negative": negative_count,
            "emission_exceeds_generation": emission_gt_generation_count,
            "noncalculated_with_numeric": forbidden_numeric_count,
            "pm25_exceeds_pm10": pm_relation_failures,
        },
        "examples": {
            "duplicates": duplicates[:10],
            "missing": missing[:10],
            "unexpected": unexpected[:10],
            "target_mismatch": target_mismatch[:10],
        },
    }


def reference_status(reference_package: Path | None) -> dict[str, Any]:
    required = (
        "manifest.json", "expected_sources.csv", "expected_items.csv", "expected_source_pollutant_totals.csv",
        "expected_exceptions.csv", "tolerances.json", "validation_report.json", "subagent_audit.md", "package_lock.json",
        "compare_run_to_reference.py",
    )
    if reference_package is None:
        return {
            "status": "not_computable",
            "reason": "reference_package_not_supplied",
            "required_files": list(required),
        }
    missing = [name for name in required if not (reference_package / name).is_file()]
    if missing:
        return {"status": "not_computable", "reason": "reference_package_incomplete", "missing_files": missing}
    manifest = load_json(reference_package / "manifest.json")
    validation = load_json(reference_package / "validation_report.json")
    audit = (reference_package / "subagent_audit.md").read_text(encoding="utf-8")
    if manifest.get("status") != "audited_frozen" or validation.get("status") != "pass" or "终审结论：通过" not in audit:
        return {"status": "not_computable", "reason": "reference_package_not_audited_frozen"}
    return {"status": "available", "path": str(reference_package.resolve())}


def execute_reference_comparator(run_dir: Path, reference_package: Path) -> dict[str, Any]:
    comparator_path = reference_package / "compare_run_to_reference.py"
    if not comparator_path.is_file():
        raise FileNotFoundError(f"reference comparator missing: {comparator_path}")
    spec = importlib.util.spec_from_file_location("fixed_combustion_reference_comparator", comparator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reference comparator: {comparator_path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module.compare(run_dir, reference_package)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_package", type=Path)
    parser.add_argument("--reference-package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_package.resolve()
    manifest = load_json(run_dir / "run_manifest.json")
    normalized = ensure_normalized(run_dir)
    structural = structural_checks(run_dir, manifest, normalized)
    seal = verify_experiment_seal(run_dir, manifest)
    qa = workbook_qa(run_dir)
    boundary = method_boundary_audit(run_dir, manifest)
    reference = reference_status(args.reference_package.resolve() if args.reference_package else None)
    reference_comparison = None
    if reference["status"] == "available":
        reference_comparison = execute_reference_comparator(run_dir, args.reference_package.resolve())

    minimum_gate = "pass" if (
        structural["numeric_checks"]["missing_or_nonnumeric"] == 0
        and structural["independent_minimum_recalculation"]["status"] == "pass"
        and not any(failure.startswith("MINIMUM_RECALCULATION_RECORD_MISSING") for failure in structural["failures"])
    ) else "fail"
    reference_gates = reference_comparison["formal_quality_gates"] if reference_comparison else None
    reference_metrics = reference_comparison["formal_metrics"] if reference_comparison else None
    report = {
        "evaluation_version": "1.2.0",
        "run_id": manifest["run_id"],
        "method": manifest["experiment_method"],
        "target": manifest["target"],
        "scale_percent": manifest["scale_percent"],
        "input_variant": manifest["input_variant"],
        "method_bundle_hash": manifest["method_bundle_hash"],
        "sealed_integrity": seal,
        "structural_and_arithmetic_checks": structural,
        "workbook_formula_checks": qa,
        "method_boundary_audit": boundary,
        "reference_package": reference,
        "reference_comparison": reference_comparison,
        "formal_quality_gates": {
            "record_destination_complete": reference_gates["record_destination_complete"] if reference_gates else "not_computable",
            "unsupported_dispositions_zero": reference_gates["unsupported_dispositions_zero"] if reference_gates else "not_computable",
            "critical_errors_zero": reference_gates["critical_errors_zero"] if reference_gates else "not_computable",
            "minimum_recalculation_complete": (
                "pass" if minimum_gate == "pass" and (not reference_gates or reference_gates["minimum_recalculation_complete"] == "pass") else "fail"
            ),
        },
        "formal_metrics": {
            "S": reference_metrics["S_standard_implementation"] if reference_metrics else "not_computable",
            "A1": reference_metrics["A1_automatic_legal_terminal_state"] if reference_metrics else structural["observed_rows"] / structural["expected_rows"] if structural["expected_rows"] else None,
            "A2": reference_metrics["A2_user_judgment_score"] if reference_metrics else "not_computable",
            "A": reference_metrics["A_automation_and_human_burden"] if reference_metrics else "not_computable",
            "Q": reference_metrics["Q_numeric_validity"] if reference_metrics else "not_computable",
            "T": reference_metrics["T_complete_process_record_coverage"] if reference_metrics else structural["complete_process_record_coverage"],
            "R": "not_computable_single_run",
            "E1": "not_computable_single_variant",
            "E2": "raw_time_only",
            "EICPI": "not_computable",
        },
    }
    destination = args.output or (run_dir / "evaluation" / "reference_independent_report.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "run_id": manifest["run_id"],
        "output": str(destination),
        "integrity": seal["status"],
        "structural": structural["status"],
        "reference": reference["status"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
