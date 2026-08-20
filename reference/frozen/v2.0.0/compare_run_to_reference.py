#!/usr/bin/env python3
"""Compare one sealed run with the frozen public reference semantics.

The comparator is outside every participating method.  It reads a sealed run
only after execution, aligns public source/component/pollutant IDs, applies the
per-item mask, and never asks a method to reproduce Full's private rule IDs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_REFERENCE = HERE / "frozen" / "v2.0.0"
POLLUTANT = {"SO2": "SO2", "NOX": "NOx", "NOx": "NOx", "CO": "CO",
             "VOCS": "VOC", "VOC": "VOC", "PM10": "PM10", "PM25": "PM2.5",
             "PM2.5": "PM2.5", "BC": "BC", "OC": "OC", "NH3": "NH3"}
FOUNDATION_FLAGS = {
    "SOURCE_RELATION_MISSING", "SOURCE_RELATION_CONFLICT", "ACTIVITY_OUT_OF_RANGE",
    "SULFUR_OUT_OF_RANGE", "ASH_OUT_OF_RANGE", "CAPACITY_OUT_OF_RANGE",
    "PM_SIZE_CONSTRAINT_VIOLATION", "NEGATIVE_RESULT", "EFFICIENCY_OUT_OF_RANGE",
    "LOW_NOX_FIELD_CONFLICT",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_reference(reference: Path) -> None:
    manifest = json.loads((reference / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((reference / "validation_report.json").read_text(encoding="utf-8"))
    audit = (reference / "subagent_audit.md").read_text(encoding="utf-8")
    lock = json.loads((reference / "package_lock.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "audited_frozen" or validation.get("status") != "pass" or "终审结论：通过" not in audit:
        raise RuntimeError("reference package is not audited_frozen")
    if (lock.get("status") != "audited_frozen" or
            lock.get("version") != manifest.get("version") or
            lock.get("package_name") != manifest.get("package_name")):
        raise RuntimeError("reference package lock metadata mismatch")
    locked_files = lock.get("files")
    if not isinstance(locked_files, dict) or not locked_files:
        raise RuntimeError("reference package lock has no file coverage")
    actual_files = {path.name for path in reference.iterdir() if path.is_file() and path.name != "package_lock.json"}
    if set(locked_files) != actual_files:
        raise RuntimeError("reference package lock file coverage mismatch")
    for name, record in locked_files.items():
        path = reference / name
        if not path.is_file() or path.stat().st_size != record.get("bytes") or sha256(path) != record.get("sha256"):
            raise RuntimeError(f"reference package lock mismatch: {name}")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_public_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(compact_json(value).encode('utf-8')).hexdigest()[:20]}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(1e-6, abs(expected) * 1e-6)


def normalized_factor_unit(value: Any) -> str:
    text = str(value or "").strip().replace("³", "3").replace("m^3", "m3")
    if text.startswith("g/kg"):
        return "g/kg"
    if text.startswith("g/m3"):
        return "g/m3"
    return text


def normalized_efficiency(value: Any) -> float | None:
    result = finite_number(value)
    if result is None:
        return None
    return result / 100.0 if 1.0 < result <= 100.0 else result


def normalized_method(mode: Any) -> str:
    text = str(mode or "").strip()
    aliases = {
        "constant": "emission_factor_method", "capacity_lookup": "emission_factor_method",
        "产排污系数法": "emission_factor_method", "排放系数法": "emission_factor_method",
        "emission_factor": "emission_factor_method", "coefficient_method": "emission_factor_method",
        "coal_sulfur_balance": "material_balance_method",
        "coal_particle_balance": "material_balance_method", "物料衡算法": "material_balance_method",
        "material_balance": "material_balance_method",
        "ammonia_slip": "ammonia_slip_factor_method", "氨逃逸系数法": "ammonia_slip_factor_method",
        "empirical_zero": "authorized_empirical_disposition",
    }
    return aliases.get(text, text)


def standard_reference_ids(values: list[str]) -> list[str]:
    result: set[str] = set()
    for value in values:
        text = str(value or "")
        for token, identifier in (
            ("A.1", "T/CSES144-2024:A.1"), ("C.1", "T/CSES144-2024:C.1"),
            ("C.2", "T/CSES144-2024:C.2"), ("C.3", "T/CSES144-2024:C.3"),
            ("D.1", "T/CSES144-2024:D.1"), ("E.1", "T/CSES144-2024:E.1"),
            ("E.7", "T/CSES144-2024:E.7"),
        ):
            if token in text:
                result.add(identifier)
        if "公式(3)" in text:
            result.add("T/CSES144-2024:FORMULA-3")
        if any(token in text for token in ("公式(4)", "公式(5)", "公式(6)")):
            result.add("T/CSES144-2024:FORMULA-4-6")
        if any(token in text.lower() for token in ("实施协议", "冻结实施规则", "经验缺失处置规则", "operational", "protocol")):
            result.add("EXPERIMENT-PROTOCOL:V4")
    return sorted(result)


def flags(*values: Any) -> set[str]:
    result: set[str] = set()
    for value in values:
        decoded = parse_json(value, None)
        if isinstance(decoded, list):
            result.update(str(item) for item in decoded if item)
        elif value:
            result.update(part.strip() for part in re.split(r"\s*[|;]\s*", str(value)) if part.strip())
    return result


def status_from(action: str, item_flags: set[str], calculation_status: str) -> str:
    if item_flags & FOUNDATION_FLAGS:
        return "source_data_invalid"
    if action in {"withhold", "withhold_missing", "block_foundation"}:
        return "information_insufficient"
    if action in {"not_applicable", "not_involved"}:
        return "not_involved"
    if action == "allow_calculation" or calculation_status == "calculated":
        return "calculated"
    return "information_insufficient"


def specialized_items(run_dir: Path) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(run_dir / "run.sqlite3")
    connection.row_factory = sqlite3.Row
    query = """
        SELECT i.item_id,i.source_id,i.fuel_id,i.item_type,i.pollutant,i.dependency_id,
               a.action,a.reason_code AS admission_reason,a.flags_json AS admission_flags,
               r.activity_value,r.activity_unit,r.mode,r.factor_value,r.factor_unit,
               r.parameter_id,r.source_section,r.source_page,r.control_technology,
               r.control_efficiency,r.control_fine_efficiency,r.control_coarse_efficiency,
               r.flags_json AS rule_flags,c.generation_t,c.emission_t,c.calculation_status
        FROM calculation_items i
        LEFT JOIN admission_results a ON a.item_id=i.item_id
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        LEFT JOIN calculations c ON c.item_id=i.item_id
    """
    raw = {row["item_id"]: dict(row) for row in connection.execute(query)}
    connection.close()
    trace_path = run_dir / "trace" / "complete_calculation_process.jsonl"
    trace_records: dict[str, dict[str, Any]] = {}
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                trace = json.loads(line)
                trace_records[trace["item_id"]] = trace
    observed: dict[str, dict[str, Any]] = {}
    for row in raw.values():
        source_id = row["source_id"]
        pollutant = POLLUTANT.get(row["pollutant"], row["pollutant"])
        if row["fuel_id"]:
            component_key = str(row["fuel_id"]).removeprefix(f"FUEL-{source_id}-")
        else:
            component_key = "nh3_slip"
        evaluation_id = f"{source_id}::{component_key}::{pollutant}"
        item_flags = flags(row.get("admission_flags"), row.get("rule_flags"))
        status = status_from(str(row.get("action") or ""), item_flags, str(row.get("calculation_status") or ""))
        parameter_rows = [row]
        if pollutant == "PM10" and row.get("dependency_id") in raw:
            parameter_rows.append(raw[str(row["dependency_id"])])
        parameter_eval: list[dict[str, Any]] = []
        modes: list[str] = []
        for index, parameter in enumerate(parameter_rows):
            mode = normalized_method(parameter.get("mode"))
            if mode:
                modes.append(mode)
            role = "primary" if index == 0 else "pm25_pair_for_pm10"
            refs = standard_reference_ids([f"{parameter.get('source_section') or ''}|{parameter.get('source_page') or ''}"])
            public = {
                "role": role, "method": mode, "value": finite_number(parameter.get("factor_value")),
                "unit": normalized_factor_unit(parameter.get("factor_unit")), "standard_reference_ids": refs,
            }
            parameter_eval.append(public)
        method_id = stable_public_id("METHOD", sorted(set(modes))) if modes else ""
        parameter_ids = [stable_public_id("PARAM", public) for public in parameter_eval]
        controls = {
            "efficiency": normalized_efficiency(row.get("control_efficiency")),
            "fine_efficiency": normalized_efficiency(row.get("control_fine_efficiency")),
            "coarse_efficiency": normalized_efficiency(row.get("control_coarse_efficiency")),
        }
        minimum = status != "calculated" or all(row.get(field) not in (None, "") for field in (
            "activity_value", "activity_unit", "mode", "factor_value", "factor_unit", "generation_t", "emission_t",
        ))
        trace = trace_records.get(row["item_id"])
        trace_common = ("source_path", "source_sheet", "source_row", "action", "reason_code", "flags_json", "payload_json", "target", "pollutant")
        trace_numeric = ("activity_value", "activity_unit", "mode", "factor_value", "factor_unit", "generation_t", "emission_t")
        complete_trace = bool(trace) and all(trace.get(field) not in (None, "") for field in trace_common)
        if status == "calculated":
            complete_trace = complete_trace and all(trace.get(field) not in (None, "") for field in trace_numeric)
        observed[evaluation_id] = {
            "evaluation_item_id": evaluation_id, "source_id": source_id, "pollutant": pollutant,
            "status": status, "method_evaluation_id": method_id,
            "activity_value": row.get("activity_value"), "activity_unit": row.get("activity_unit"),
            "parameter_evaluation_ids": parameter_ids, "control": controls,
            "generation_t": row.get("generation_t") if status == "calculated" else "",
            "emission_t": row.get("emission_t") if status == "calculated" else "",
            "reason_codes": sorted(item_flags | {str(row.get("admission_reason") or "")}),
            "minimum_recalculation_complete": minimum,
            "complete_process_record": complete_trace,
        }
    return observed


def generic_items(run_dir: Path, expected: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    raw = read_csv(run_dir / "outputs" / "generic_inventory_items.csv")
    expected_components: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in expected:
        expected_components[(row["source_id"], row["pollutant"])].append(row["evaluation_item_id"])
    observed: dict[str, dict[str, Any]] = {}
    for row in raw:
        pollutant = POLLUTANT.get(row.get("pollutant", ""), row.get("pollutant", ""))
        candidates = expected_components[(row.get("source_id", ""), pollutant)]
        evaluation_id = candidates[0] if len(candidates) == 1 else f"{row.get('source_id','')}::aggregate_only::{pollutant}"
        mode = normalized_method(row.get("method"))
        method_id = stable_public_id("METHOD", [mode]) if mode else ""
        public_parameter = {
            "role": "primary", "method": mode, "value": finite_number(row.get("parameter_value")),
            "unit": normalized_factor_unit(row.get("parameter_unit")),
            "standard_reference_ids": standard_reference_ids([row.get("standard_reference", "")]),
        }
        calculated = row.get("status") == "calculated"
        minimum = (not calculated) or all(row.get(field, "") not in ("", None) for field in (
            "method", "activity_value", "activity_unit", "parameter_value", "parameter_unit",
            "control_efficiency", "generation_t", "emission_t",
        ))
        observed[evaluation_id] = {
            "evaluation_item_id": evaluation_id, "source_id": row.get("source_id", ""), "pollutant": pollutant,
            "status": row.get("status", ""), "method_evaluation_id": method_id,
            "parameter_evaluation_ids": [stable_public_id("PARAM", public_parameter)],
            "control": {"efficiency": normalized_efficiency(row.get("control_efficiency")), "fine_efficiency": None, "coarse_efficiency": None},
            "generation_t": row.get("generation_t", ""), "emission_t": row.get("emission_t", ""),
            "reason_codes": [row.get("reason_code", "")] if row.get("reason_code") else [],
            "minimum_recalculation_complete": minimum, "complete_process_record": False,
        }
    return observed


def select_reference(reference: Path, manifest: dict[str, Any]) -> tuple[str, list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    variant = "B2" if str(manifest.get("input_variant", "")).upper() == "B2" else "B0"
    scope = set(
        manifest.get("evaluation_scope_ids")
        or manifest.get("scope_ids")
        or manifest.get("candidate_ids")
        or []
    )
    target = manifest.get("target")
    items = [row for row in read_csv(reference / "expected_items.csv")
             if row["variant"] == variant and (not scope or row["source_id"] in scope) and row["target"] == target]
    totals = [row for row in read_csv(reference / "expected_source_pollutant_totals.csv")
              if row["variant"] == variant and (not scope or row["source_id"] in scope) and row["target"] == target]
    exceptions = [row for row in read_csv(reference / "expected_exceptions.csv")
                  if row["variant"] == variant and (not scope or row["source_id"] in scope) and row["target"] == target]
    return variant, items, totals, exceptions


def actual_exception_groups(run_dir: Path, equivalence: dict[str, Any]) -> set[tuple[str, str]]:
    """Normalize the method's final user handoff to source×root-cause groups."""
    path = run_dir / "outputs" / "最终异常清单.csv"
    code_map = equivalence.get("codes", {})
    groups: set[tuple[str, str]] = set()
    if path.is_file():
        for row in read_csv(path):
            source_id = row.get("源ID") or row.get("source_id") or ""
            raw_codes = flags(row.get("规则标志"), row.get("准入原因"), row.get("reason_code"))
            for raw_code in raw_codes:
                code = raw_code.split(":", 1)[0]
                mapping = code_map.get(code)
                if mapping:
                    if mapping.get("requires_user_judgment"):
                        groups.add((source_id, mapping["canonical_root_cause"]))
                elif code and code not in {"RULE_CHAIN_COMPLETE", "CALCULATED"}:
                    groups.add((source_id, f"unmapped::{code}"))
    generic_path = run_dir / "outputs" / "generic_exception_list.csv"
    if generic_path.is_file():
        generic_rules = equivalence.get("generic_root_rules", [])
        for row in read_csv(generic_path):
            text = row.get("root_cause", "")
            direct = code_map.get(text) or code_map.get(text.split(":", 1)[0])
            if direct:
                root = direct["canonical_root_cause"]
                requires_user = bool(direct.get("requires_user_judgment"))
            else:
                matched = next((rule for rule in generic_rules if re.search(rule["regex"], text, re.IGNORECASE)), None)
                root = matched["canonical_root_cause"] if matched else "unmapped::generic_natural_language_exception"
                requires_user = bool(matched.get("requires_user_judgment")) if matched else True
            if not requires_user:
                continue
            source_ids = set(re.findall(
                r"SRC-(?:RAW-[A-F0-9]{20}|(?:IND|PWR)-\d{6})",
                row.get("source_id", ""),
            ))
            for source_id in source_ids:
                groups.add((source_id, root))
    return groups


def total_status(statuses: list[str]) -> str:
    values = set(statuses)
    if "source_data_invalid" in values:
        return "source_data_invalid"
    if "information_insufficient" in values:
        return "information_insufficient"
    if values == {"not_involved"}:
        return "not_involved"
    return "calculated" if values == {"calculated"} else "information_insufficient"


def activity_evaluation_id(value: Any, unit: Any) -> str:
    number = finite_number(value)
    text = str(unit or "").strip().replace("³", "3")
    if number is not None and text in {"万立方米", "万m3"}:
        number, text = number * 10_000.0, "m3"
    elif number is not None and text in {"吨", "t"}:
        number, text = number * 1_000.0, "kg"
    text = "kg" if text in {"kg_coal", "kg煤"} else "m3" if text in {"立方米", "m^3"} else text
    public = {"value": number, "unit": text}
    return stable_public_id("ACTIVITY", public) if number is not None else ""


def control_evaluation_id(control: dict[str, Any]) -> str:
    normalized = {
        "efficiency": normalized_efficiency(control.get("efficiency")),
        "fine_efficiency": normalized_efficiency(control.get("fine_efficiency")),
        "coarse_efficiency": normalized_efficiency(control.get("coarse_efficiency")),
    }
    return stable_public_id("CONTROL", normalized)


def observed_component_totals(observed: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observed.values():
        groups[(row["source_id"], row["pollutant"])].append(row)
    totals: dict[str, dict[str, Any]] = {}
    for (source_id, pollutant), members in groups.items():
        status = total_status([member["status"] for member in members])
        calculated = status == "calculated"
        totals[f"{source_id}::{pollutant}"] = {
            "source_id": source_id,
            "pollutant": pollutant,
            "status": status,
            "activity_ids": [activity_evaluation_id(member.get("activity_value"), member.get("activity_unit")) for member in members if member.get("activity_value") not in (None, "")],
            "method_ids": [member["method_evaluation_id"] for member in members if member["method_evaluation_id"]],
            "parameter_ids": [identifier for member in members for identifier in member["parameter_evaluation_ids"]],
            "control_ids": sorted({control_evaluation_id(member["control"]) for member in members}),
            "generation_t": sum(float(member["generation_t"]) for member in members) if calculated else "",
            "emission_t": sum(float(member["emission_t"]) for member in members) if calculated else "",
            "minimum_recalculation_complete": calculated and all(member["minimum_recalculation_complete"] for member in members),
        }
    return totals


def generic_total_items(run_dir: Path) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for row in read_csv(run_dir / "outputs" / "generic_inventory_items.csv"):
        source_id = row.get("source_id", "")
        pollutant = POLLUTANT.get(row.get("pollutant", ""), row.get("pollutant", ""))
        mode = normalized_method(row.get("method"))
        refs = standard_reference_ids([row.get("standard_reference", "")])
        public_parameter = {
            "role": "primary", "method": mode, "value": finite_number(row.get("parameter_value")),
            "unit": normalized_factor_unit(row.get("parameter_unit")), "standard_reference_ids": refs,
        }
        control_value = normalized_efficiency(row.get("control_efficiency"))
        if control_value is None and str(row.get("control_method") or "").strip() in {"无", "none", "None"}:
            control_value = 0.0
        control = {"efficiency": control_value, "fine_efficiency": None, "coarse_efficiency": None}
        calculated = row.get("status") == "calculated"
        minimum = (not calculated) or all(row.get(field, "") not in ("", None) for field in (
            "method", "activity_value", "activity_unit", "parameter_value", "parameter_unit",
            "generation_t", "emission_t",
        ))
        minimum = minimum and control_value is not None
        totals[f"{source_id}::{pollutant}"] = {
            "source_id": source_id, "pollutant": pollutant, "status": row.get("status", ""),
            "activity_ids": [activity_evaluation_id(row.get("activity_value"), row.get("activity_unit"))],
            "method_ids": [stable_public_id("METHOD", [mode])] if mode else [],
            "parameter_ids": [stable_public_id("PARAM", public_parameter)] if mode else [],
            "control_ids": [control_evaluation_id(control)],
            "generation_t": row.get("generation_t", "") if calculated else "",
            "emission_t": row.get("emission_t", "") if calculated else "",
            "minimum_recalculation_complete": minimum,
        }
    return totals


def expected_total_semantics(expected_items: list[dict[str, str]], expected_totals: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in expected_items:
        groups[(row["source_id"], row["pollutant"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    for total in expected_totals:
        key = (total["source_id"], total["pollutant"])
        members = groups[key]
        activity_ids: list[str] = []
        method_ids: list[str] = []
        parameter_ids: list[str] = []
        control_ids: set[str] = set()
        for member in members:
            mask = parse_json(member["applicability_mask"], {})
            if mask.get("activity"):
                for activity in parse_json(member["expected_activity_record"], []):
                    activity_ids.append(activity_evaluation_id(activity.get("value"), activity.get("unit")))
            if mask.get("method") and member["method_evaluation_id"]:
                method_ids.append(member["method_evaluation_id"])
            if mask.get("parameter"):
                parameter_ids.extend(parse_json(member["parameter_evaluation_ids"], []))
            if mask.get("control"):
                control = {
                    "efficiency": finite_number(member["expected_control_efficiency"]),
                    "fine_efficiency": finite_number(member["expected_fine_efficiency"]),
                    "coarse_efficiency": finite_number(member["expected_coarse_efficiency"]),
                }
                control_ids.add(control_evaluation_id(control))
        result[f"{total['source_id']}::{total['pollutant']}"] = {
            **total, "activity_ids": activity_ids, "method_ids": method_ids,
            "parameter_ids": parameter_ids, "control_ids": sorted(control_ids),
        }
    return result


def compare(run_dir: Path, reference: Path, *, allow_candidate: bool = False) -> dict[str, Any]:
    if not allow_candidate:
        verify_frozen_reference(reference)
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    variant, expected, expected_totals, expected_exceptions = select_reference(reference, manifest)
    run_output_failure = False
    if (run_dir / "run.sqlite3").is_file():
        observed_components = specialized_items(run_dir)
        observed_totals = observed_component_totals(observed_components)
        extractor = "sealed_sqlite_item_level"
    elif (run_dir / "outputs" / "generic_inventory_items.csv").is_file():
        observed_components = generic_items(run_dir, expected)
        observed_totals = generic_total_items(run_dir)
        extractor = "generic_source_pollutant_contract"
    else:
        observed_components = {}
        observed_totals = {}
        extractor = "no_supported_output_run_failure"
        run_output_failure = True
    expected_by_id = {row["evaluation_item_id"]: row for row in expected}
    expected_component_ids, observed_component_ids = set(expected_by_id), set(observed_components)
    component_missing = sorted(expected_component_ids - observed_component_ids)
    component_extra = sorted(observed_component_ids - expected_component_ids)
    expected_common = expected_total_semantics(expected, expected_totals)
    expected_common_ids, observed_common_ids = set(expected_common), set(observed_totals)
    missing = sorted(expected_common_ids - observed_common_ids)
    extra = sorted(observed_common_ids - expected_common_ids)
    equivalence = json.loads((reference / "reason_code_equivalence.json").read_text(encoding="utf-8"))
    expected_user_group_rows = [row for row in expected_exceptions if row["requires_user_judgment"] == "true"]
    expected_user_groups = {(row["source_id"], row["canonical_root_cause"]) for row in expected_user_group_rows}
    actual_user_groups = actual_exception_groups(run_dir, equivalence)
    expected_total_roots: dict[str, set[str]] = defaultdict(set)
    actual_source_roots: dict[str, set[str]] = defaultdict(set)
    for row in expected_user_group_rows:
        for pollutant in parse_json(row["affected_pollutants"], []):
            expected_total_roots[f"{row['source_id']}::{pollutant}"].add(row["canonical_root_cause"])
    for source_id, root in actual_user_groups:
        actual_source_roots[source_id].add(root)
    diagnostics: list[dict[str, Any]] = []
    total_passes: dict[str, bool] = {}
    q_results: dict[str, bool] = {
        total_id: False for total_id, row in expected_common.items() if row["expected_status"] == "calculated"
    }
    s_pass = terminal_legal = 0
    reference_numeric = sum(row["expected_status"] == "calculated" for row in expected_common.values())
    unsupported_numeric = 1 if run_output_failure else 0
    critical_errors = 1 if run_output_failure else 0
    disposition_passes: dict[str, bool] = {}
    result_passes: dict[str, bool] = {}
    for total_id, ref in expected_common.items():
        actual = observed_totals.get(total_id)
        failures: list[str] = []
        if actual is None:
            failures.append("missing_item")
        else:
            terminal_legal += actual["status"] in {"calculated", "not_involved", "information_insufficient", "source_data_invalid"}
            if actual["status"] != ref["expected_status"]:
                failures.append("status")
                if ref["expected_status"] != "calculated" and actual["status"] == "calculated":
                    unsupported_numeric += 1
            standard_failures: list[str] = []
            if ref["expected_status"] == "calculated":
                if set(actual["control_ids"]) != set(ref["control_ids"]):
                    standard_failures.append("control")
            expected_roots = expected_total_roots.get(total_id, set())
            if expected_roots:
                actual_roots = actual_source_roots.get(ref["source_id"], set())
                if not expected_roots.issubset(actual_roots):
                    standard_failures.append("exception_root")
            failures.extend(standard_failures)
            standard_ok = actual["status"] == ref["expected_status"] and not standard_failures
            if standard_ok:
                s_pass += 1
            if ref["expected_status"] == "calculated":
                generation = finite_number(actual["generation_t"])
                emission = finite_number(actual["emission_t"])
                if generation is None or emission is None:
                    failures.append("numeric_missing")
                else:
                    expected_generation = float(ref["expected_generation_t"])
                    expected_emission = float(ref["expected_emission_t"])
                    numeric_ok = close_enough(generation, expected_generation) and close_enough(emission, expected_emission)
                    hard_ok = generation >= 0 and emission >= 0 and emission <= generation + max(1e-6, abs(generation) * 1e-6)
                    if numeric_ok and hard_ok:
                        q_results[total_id] = True
                    else:
                        failures.append("numeric_or_hard_constraint")
                        critical_errors += 1
            elif actual["generation_t"] not in ("", None) or actual["emission_t"] not in ("", None):
                failures.append("forbidden_numeric")
                unsupported_numeric += 1
            if actual["status"] != ref["expected_status"] or standard_failures:
                critical_errors += 1
        if failures and len(diagnostics) < 200:
            diagnostics.append({"evaluation_total_id": total_id, "failures": failures})
        total_passes[total_id] = not failures
        disposition_passes[total_id] = bool(
            actual is not None and actual["status"] == ref["expected_status"] and
            (ref["expected_status"] == "calculated" or
             (actual["generation_t"] in ("", None) and actual["emission_t"] in ("", None)))
        )
        result_passes[total_id] = bool(
            disposition_passes[total_id] and
            (ref["expected_status"] != "calculated" or q_results.get(total_id, False))
        )

    for source_id in {row["source_id"] for row in expected_common.values()}:
        pm10_id, pm25_id = f"{source_id}::PM10", f"{source_id}::PM2.5"
        pm10, pm25 = observed_totals.get(pm10_id), observed_totals.get(pm25_id)
        if pm10_id in q_results and pm25_id in q_results and pm10 and pm25:
            pm10_generation = finite_number(pm10["generation_t"])
            pm25_generation = finite_number(pm25["generation_t"])
            if pm10_generation is None or pm25_generation is None or pm25_generation > pm10_generation + max(1e-6, abs(pm10_generation) * 1e-6):
                q_results[pm10_id] = False
                critical_errors += 1

    trace_required = 0
    trace_pass = 0
    for item_id, ref in expected_by_id.items():
        mask = parse_json(ref["applicability_mask"], {})
        if mask.get("trace"):
            trace_required += 1
            actual = observed_components.get(item_id)
            trace_pass += bool(actual and actual.get("complete_process_record"))

    minimum_ids = {
        item_id for item_id, ref in expected_by_id.items()
        if ref.get("minimum_recalculation_complete") == "true"
    }
    minimum_required = len(minimum_ids)
    minimum_pass = sum(
        bool(observed_components.get(item_id, {}).get("minimum_recalculation_complete"))
        for item_id in minimum_ids
    )

    item_denominator = len(expected_common)
    s = s_pass / item_denominator if item_denominator else None
    a1 = terminal_legal / item_denominator if item_denominator else None
    missing_user_groups = expected_user_groups - actual_user_groups
    group_recall = (len(expected_user_groups & actual_user_groups) / len(expected_user_groups)) if expected_user_groups else 1.0
    burden_score = 1.0 - min(1.0, len(actual_user_groups) / item_denominator) if item_denominator else None
    a2 = (group_recall + burden_score) / 2.0 if burden_score is not None else None
    a = (a1 + a2) / 2.0 if a1 is not None and a2 is not None else None
    q = sum(q_results.values()) / reference_numeric if reference_numeric else None
    t = trace_pass / trace_required if trace_required else None
    b2_diagnostics: dict[str, Any] | str = "not_applicable"
    if variant == "B2":
        injection_rows = [
            row for row in read_csv(reference / "expected_injection_outcomes.csv")
            if row["target"] == manifest.get("target") and (
                not set(manifest.get("evaluation_scope_ids") or manifest.get("scope_ids") or manifest.get("candidate_ids") or [])
                or row["source_id"] in set(manifest.get("evaluation_scope_ids") or manifest.get("scope_ids") or manifest.get("candidate_ids") or [])
            )
        ]
        eligible_rows = [row for row in injection_rows if row["scoring_eligible"] == "true"]
        negative_rows = [row for row in eligible_rows if row["root_cause"] == "UNIQUE_DEFAULT_NEGATIVE_CONTROL"]

        targeted_results = []
        for row in eligible_rows:
            affected = set(parse_json(row["expected_affected_pollutants"], []))
            source_ids = {pollutant: f"{row['source_id']}::{pollutant}" for pollutant in POLLUTANT.values()}
            unaffected = set(POLLUTANT.values()) - affected
            affected_disposition_pass = all(disposition_passes.get(source_ids[pollutant], False) for pollutant in affected)
            unaffected_result_pass = (
                all(result_passes.get(source_ids[pollutant], False) for pollutant in unaffected)
                if unaffected else None
            )
            injection_roots = {
                exception["canonical_root_cause"] for exception in expected_exceptions
                if exception["source_id"] == row["source_id"] and exception["injection_root_cause"] == row["root_cause"]
                and exception["requires_user_judgment"] == "true"
            }
            root_pass = injection_roots.issubset(actual_source_roots.get(row["source_id"], set()))
            targeted_results.append({
                "source_id": row["source_id"], "root_cause": row["root_cause"],
                "affected_disposition_correct": affected_disposition_pass,
                "unaffected_items_reference_correct": unaffected_result_pass,
                "root_cause_group_correct": root_pass,
                "targeted_detection_correct": affected_disposition_pass and root_pass,
                "whole_source_reference_correct": all(total_passes.get(source_ids[pollutant], False) for pollutant in POLLUTANT.values()),
            })
        positive_results = [result for result in targeted_results if result["root_cause"] != "UNIQUE_DEFAULT_NEGATIVE_CONTROL"]
        eligible_pass = sum(result["targeted_detection_correct"] for result in positive_results)
        negative_pass = sum(
            result["whole_source_reference_correct"] for result in targeted_results
            if result["root_cause"] == "UNIQUE_DEFAULT_NEGATIVE_CONTROL"
        )
        unaffected_results = [
            result["unaffected_items_reference_correct"] for result in targeted_results
            if result["unaffected_items_reference_correct"] is not None
        ]
        b2_diagnostics = {
            "designed_records_in_scope": len(injection_rows),
            "eligible_targeted_records": len(eligible_rows),
            "excluded_no_isolated_baseline_effect": len(injection_rows) - len(eligible_rows),
            "eligible_positive_injections": len(positive_results),
            "targeted_detection_correct": eligible_pass,
            "targeted_detection_accuracy": eligible_pass / len(positive_results) if positive_results else None,
            "affected_disposition_accuracy": sum(result["affected_disposition_correct"] for result in positive_results) / len(positive_results) if positive_results else None,
            "root_cause_group_accuracy": sum(result["root_cause_group_correct"] for result in positive_results) / len(positive_results) if positive_results else None,
            "unaffected_items_reference_correctness": sum(unaffected_results) / len(unaffected_results) if unaffected_results else None,
            "unaffected_items_diagnostic_records": len(unaffected_results),
            "unaffected_result_preservation_vs_method_B0": "not_computable_single_run",
            "negative_control_records": len(negative_rows),
            "negative_control_whole_source_reference_correct_count": negative_pass,
            "negative_control_whole_source_reference_accuracy": negative_pass / len(negative_rows) if negative_rows else None,
            "negative_control_unchanged_vs_method_B0": "not_computable_single_run",
        }
    report = {
        "evaluation_version": "reference-comparator-1.0.0",
        "run_id": manifest.get("run_id"), "method": manifest.get("experiment_method"),
        "target": manifest.get("target"), "reference_variant": variant, "extractor": extractor,
        "reference_package_version": json.loads((reference / "manifest.json").read_text(encoding="utf-8"))["version"],
        "denominators": {"expected_source_pollutant_items": item_denominator, "reference_numeric_items": reference_numeric,
                         "trace_component_items": trace_required,
                         "minimum_recalculation_component_items": minimum_required,
                         "expected_user_judgment_groups": len(expected_user_groups),
                         "actual_user_judgment_groups": len(actual_user_groups)},
        "alignment": {"observed_items": len(observed_totals), "missing_items": len(missing), "extra_items": len(extra),
                      "missing_examples": missing[:20], "extra_examples": extra[:20]},
        "component_structure_diagnostics": {
            "expected_component_items": len(expected_component_ids),
            "observed_component_items": len(observed_component_ids),
            "missing_component_items": len(component_missing),
            "extra_component_items": len(component_extra),
            "missing_examples": component_missing[:20], "extra_examples": component_extra[:20],
        },
        "formal_quality_gates": {
            "record_destination_complete": "pass" if not missing and not extra else "fail",
            "unsupported_dispositions_zero": "pass" if unsupported_numeric == 0 else "fail",
            "critical_errors_zero": "pass" if critical_errors == 0 else "fail",
            "minimum_recalculation_complete": "pass" if minimum_pass == minimum_required else "fail",
        },
        "formal_metrics": {
            "S_standard_implementation": s,
            "A1_automatic_legal_terminal_state": a1,
            "A2_user_judgment_score": a2,
            "A_automation_and_human_burden": a,
            "Q_numeric_validity": q,
            "T_complete_process_record_coverage": t,
            "R": "not_computable_single_run", "E1": "not_computable_single_variant",
            "E2": "not_computable_without_independent_L_U", "EICPI": "not_computable",
        },
        "exception_group_diagnostics": {
            "expected_user_groups": len(expected_user_groups),
            "actual_user_groups": len(actual_user_groups),
            "missing_expected_groups": len(expected_user_groups - actual_user_groups),
            "extra_actual_groups": len(actual_user_groups - expected_user_groups),
            "expected_group_recall": group_recall,
            "reported_group_burden_score": burden_score,
            "missing_examples": sorted(f"{source}|{root}" for source, root in expected_user_groups - actual_user_groups)[:20],
            "extra_examples": sorted(f"{source}|{root}" for source, root in actual_user_groups - expected_user_groups)[:20],
        },
        "B2_targeted_diagnostics": b2_diagnostics,
        "diagnostic_examples": diagnostics,
        "notes": [
            "Formal S/A/Q use the common source×pollutant layer; component alignment is a separate structure diagnostic used by the relevant ablation.",
            "Activity, method and parameter multisets remain component diagnostics and do not enter cross-method common S.",
            "A2 averages required root-group recall and the normalized reported-group burden, so omission cannot receive full credit.",
            "Internal Full parameter IDs are not scored.",
            "EICPI remains not computable until independently pre-specified time thresholds and cross-run metrics are available.",
        ],
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_package", type=Path)
    parser.add_argument("--reference-package", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-candidate", action="store_true", help="Audit-only: bypass final frozen lock gate.")
    args = parser.parse_args()
    report = compare(args.run_package.resolve(), args.reference_package.resolve(), allow_candidate=args.allow_candidate)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
