#!/usr/bin/env python3
"""Validate the independent professional reference package."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import argparse
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "frozen" / "v1.0.1"
BUILDER = HERE / "build_reference_package.py"
WORKSPACE = HERE.parent.parent
REQUIRED = (
    "manifest.json", "expected_sources.csv", "expected_items.csv", "expected_source_pollutant_totals.csv",
    "expected_exceptions.csv", "tolerances.json", "evaluation_protocol.json", "reason_code_equivalence.json",
)
POLLUTANTS = ("SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC", "NH3")
FORBIDDEN_DEPENDENCY_PARTS = ("实验结果", "method_package", "plugin", "matrix", "run_console", "controller_logs")
FORBIDDEN_LEGACY_HEADERS = {
    "SO2（t）", "NOx（t）", "CO（t）", "PM10（t）", "PM2.5（t）", "BC（t）", "OC（t）", "VOC（t）", "NH3（t）",
    "二氧化硫产生量（吨）", "二氧化硫排放量（吨）", "氮氧化物产生量（吨）", "氮氧化物排放量（吨）",
    "颗粒物产生量（吨）", "颗粒物排放量（吨）", "挥发性有机物产生量（千克）", "挥发性有机物排放量（千克）",
    "脱硫去除效率（%）", "脱硝去除效率（%）", "PMC去除效率（%）", "PM10去除效率（%）", "PM2.5去除效率（%）",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(name: str) -> list[dict[str, str]]:
    with (PACKAGE / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def close_enough(actual: float, expected: float, absolute: float = 1e-6, relative: float = 1e-6) -> bool:
    return abs(actual - expected) <= max(absolute, abs(expected) * relative)


def validate() -> dict[str, Any]:
    failures: list[str] = []
    notices: list[str] = []
    checks: dict[str, Any] = {}
    for name in REQUIRED:
        if not (PACKAGE / name).is_file():
            failures.append(f"REQUIRED_FILE_MISSING:{name}")
    if failures:
        return {"status": "fail", "failures": failures, "notices": notices, "checks": checks}

    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    sources = read_csv("expected_sources.csv")
    items = read_csv("expected_items.csv")
    totals = read_csv("expected_source_pollutant_totals.csv")
    exceptions = read_csv("expected_exceptions.csv")
    injections = json.loads((HERE.parent / "inputs" / "experiment_b" / "B2" / "injection_manifest.json").read_text(encoding="utf-8"))["records"]

    # File and dependency integrity.
    for name, record in manifest.get("generated_files", {}).items():
        path = PACKAGE / name
        if not path.is_file() or sha256(path) != record.get("sha256") or path.stat().st_size != record.get("bytes"):
            failures.append(f"GENERATED_FILE_HASH_OR_SIZE_MISMATCH:{name}")
    lock_path = PACKAGE / "package_lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if (lock.get("status") != "audited_frozen" or
                lock.get("version") != manifest.get("version") or
                lock.get("package_name") != manifest.get("package_name")):
            failures.append("PACKAGE_LOCK_STATUS_INVALID")
        for name, record in lock.get("files", {}).items():
            path = PACKAGE / name
            if not path.is_file() or sha256(path) != record.get("sha256") or path.stat().st_size != record.get("bytes"):
                failures.append(f"PACKAGE_LOCK_MISMATCH:{name}")
        unlocked = sorted(
            path.name for path in PACKAGE.iterdir()
            if path.is_file() and path.name != "package_lock.json" and path.name not in lock.get("files", {})
        )
        if unlocked:
            failures.append("PACKAGE_LOCK_UNCOVERED_FILES:" + "+".join(unlocked))
        checks["package_lock_files"] = len(lock.get("files", {}))
    elif manifest.get("status") == "audited_frozen":
        failures.append("PACKAGE_LOCK_MISSING_FOR_FROZEN_STATUS")
    for record in manifest.get("dependencies", []):
        logical = record.get("logical_path", "")
        if logical.startswith("workspace/"):
            path = WORKSPACE / logical.removeprefix("workspace/")
        elif logical.startswith("experiment/"):
            path = HERE.parent / logical.removeprefix("experiment/")
        else:
            failures.append(f"DEPENDENCY_LOGICAL_PATH_INVALID:{logical}")
            continue
        if not path.is_file() or sha256(path) != record.get("sha256") or path.stat().st_size != record.get("bytes"):
            failures.append(f"DEPENDENCY_HASH_OR_SIZE_MISMATCH:{path.name}")
        if any(part in path.parts for part in FORBIDDEN_DEPENDENCY_PARTS):
            failures.append(f"FORBIDDEN_METHOD_OUTPUT_DEPENDENCY:{path}")
    checks["dependency_count"] = len(manifest.get("dependencies", []))

    # Static proof that the independent calculator does not address legacy calculated columns.
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    used_forbidden = sorted(FORBIDDEN_LEGACY_HEADERS & literals)
    if used_forbidden:
        failures.append("LEGACY_CALCULATED_INPUT_COLUMN_REFERENCED:" + "+".join(used_forbidden))
    checks["legacy_calculated_columns_referenced"] = used_forbidden

    # Shape, key and scope checks.
    source_keys = [(row["variant"], row["source_id"]) for row in sources]
    item_keys = [(row["variant"], row["evaluation_item_id"]) for row in items]
    total_keys = [(row["variant"], row["evaluation_total_id"]) for row in totals]
    if len(source_keys) != len(set(source_keys)):
        failures.append("DUPLICATE_SOURCE_KEY")
    if len(item_keys) != len(set(item_keys)):
        failures.append("DUPLICATE_ITEM_KEY")
    if len(total_keys) != len(set(total_keys)):
        failures.append("DUPLICATE_TOTAL_KEY")
    if len(sources) != 9172:
        failures.append(f"SOURCE_ROW_COUNT:{len(sources)}")
    if len(items) != 80624:
        failures.append(f"ITEM_ROW_COUNT:{len(items)}")
    if len(totals) != 79056:
        failures.append(f"TOTAL_ROW_COUNT:{len(totals)}")
    source_count = Counter((row["variant"], row["expected_target"]) for row in sources)
    expected_source_count = {
        ("B0", "INDUSTRIAL"): 4025, ("B0", "POWER"): 367, ("B0", "EXCLUDE"): 194,
        ("B2", "INDUSTRIAL"): 4025, ("B2", "POWER"): 367, ("B2", "EXCLUDE"): 194,
    }
    if source_count != Counter(expected_source_count):
        failures.append(f"SOURCE_SCOPE_COUNT_MISMATCH:{dict(source_count)}")
    item_count = Counter((row["variant"], row["target"]) for row in items)
    expected_item_count = {
        ("B0", "INDUSTRIAL"): 4091 * 8 + 4025, ("B0", "POWER"): 399 * 8 + 367,
        ("B2", "INDUSTRIAL"): 4091 * 8 + 4025, ("B2", "POWER"): 399 * 8 + 367,
    }
    if item_count != Counter(expected_item_count):
        failures.append(f"ITEM_SCOPE_COUNT_MISMATCH:{dict(item_count)}")
    pollutants_by_source: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in items:
        pollutants_by_source[(row["variant"], row["source_id"])].add(row["pollutant"])
    if any(value != set(POLLUTANTS) for value in pollutants_by_source.values()):
        failures.append("POLLUTANT_SET_INCOMPLETE")
    expected_by_source = {(row["variant"], row["source_id"]): int(row["expected_item_count"]) for row in sources if row["expected_target"] != "EXCLUDE"}
    actual_by_source = Counter((row["variant"], row["source_id"]) for row in items)
    if expected_by_source != dict(actual_by_source):
        failures.append("SOURCE_ITEM_COUNT_MISMATCH")
    checks["source_counts"] = {f"{key[0]}:{key[1]}": value for key, value in sorted(source_count.items())}
    checks["item_counts"] = {f"{key[0]}:{key[1]}": value for key, value in sorted(item_count.items())}

    # Status, blank policy and independent arithmetic recomputation.
    allowed_status = {"calculated", "not_involved", "information_insufficient", "source_data_invalid"}
    by_key = {(row["variant"], row["evaluation_item_id"]): row for row in items}
    by_total_key = {(row["variant"], row["evaluation_total_id"]): row for row in totals}
    recomputed = 0
    status_counts = Counter()
    for row in items:
        status = row["expected_status"]
        status_counts[(row["variant"], row["target"], status)] += 1
        if status not in allowed_status:
            failures.append(f"INVALID_STATUS:{status}")
            continue
        generation_text, emission_text = row["expected_generation_t"], row["expected_emission_t"]
        try:
            mask = json.loads(row["applicability_mask"])
        except json.JSONDecodeError:
            failures.append(f"APPLICABILITY_MASK_PARSE_ERROR:{row['item_id']}")
            continue
        required_mask = {"status", "activity", "method", "parameter", "control", "value", "exception", "minimum_recalculation", "trace"}
        if set(mask) != required_mask or any(not isinstance(value, bool) for value in mask.values()):
            failures.append(f"APPLICABILITY_MASK_INVALID:{row['item_id']}")
        if status != "calculated":
            if generation_text or emission_text:
                failures.append(f"WITHHELD_VALUE_PRESENT:{row['item_id']}")
            continue
        try:
            generation = float(generation_text)
            emission = float(emission_text)
            activities = json.loads(row["expected_activity_record"])
            parameters = json.loads(row["expected_parameter_record"])
            control = json.loads(row["expected_control_record"])
        except (ValueError, TypeError, json.JSONDecodeError):
            failures.append(f"CALCULATED_RECORD_PARSE_ERROR:{row['item_id']}")
            continue
        if not math.isfinite(generation) or not math.isfinite(emission) or generation < 0 or emission < 0:
            failures.append(f"CALCULATED_VALUE_INVALID:{row['item_id']}")
            continue
        primary_parameters = [parameter for parameter in parameters if parameter.get("role", "primary") == "primary"]
        if len(activities) != 1 or len(primary_parameters) != 1:
            failures.append(f"ACTIVITY_PRIMARY_PARAMETER_ALIGNMENT:{row['item_id']}")
            continue
        try:
            expected_generation = float(activities[0]["value"]) * float(primary_parameters[0]["value"]) / 1_000_000.0
        except (TypeError, ValueError, KeyError):
            failures.append(f"MINIMUM_RECALCULATION_COMPONENT_INVALID:{row['item_id']}")
            continue
        if not close_enough(generation, expected_generation):
            failures.append(f"GENERATION_RECALCULATION_MISMATCH:{row['item_id']}")
        if row["pollutant"] == "PM10":
            paired = [parameter for parameter in parameters if parameter.get("role") == "pm25_pair_for_pm10"]
            try:
                intermediate = json.loads(row["expected_intermediate_record"])
            except json.JSONDecodeError:
                intermediate = {}
            if len(paired) != 1 or "fine_generation_t" not in intermediate or "coarse_generation_t" not in intermediate:
                failures.append(f"PM25_PAIR_RECORD_INCOMPLETE:{row['item_id']}")
                continue
            fine_generation = float(activities[0]["value"]) * float(paired[0]["value"]) / 1_000_000.0
            coarse_generation = generation - fine_generation
            if not close_enough(float(intermediate["fine_generation_t"]), fine_generation) or not close_enough(float(intermediate["coarse_generation_t"]), coarse_generation):
                failures.append(f"PM_INTERMEDIATE_MISMATCH:{row['item_id']}")
            fine = float(control["fine_efficiency"])
            coarse = float(control["coarse_efficiency"])
            expected_emission = fine_generation * (1 - fine) + coarse_generation * (1 - coarse)
            if fine_generation > generation + max(1e-6, abs(generation) * 1e-6):
                failures.append(f"PM_SIZE_CONSTRAINT:{row['item_id']}")
        else:
            expected_emission = generation * (1 - float(control["efficiency"]))
        if not close_enough(emission, expected_emission):
            failures.append(f"EMISSION_RECALCULATION_MISMATCH:{row['item_id']}")
        if emission > generation + max(1e-6, abs(generation) * 1e-6):
            failures.append(f"EMISSION_GREATER_THAN_GENERATION:{row['item_id']}")
        recomputed += 1
    checks["status_counts"] = {f"{key[0]}:{key[1]}:{key[2]}": value for key, value in sorted(status_counts.items())}
    checks["recomputed_calculated_items"] = recomputed

    representative_expectations = {
        ("B0", "SRC-IND-000002::fuel_1::NOx"): (0.044308, 0.044308),
        ("B0", "SRC-IND-000661::fuel_1::SO2"): (7.124598, 5.6996784),
        ("B0", "SRC-IND-000661::fuel_1::PM10"): (4.8357, 2.226114495),
        ("B0", "SRC-IND-000117::fuel_1::NOx"): (37.786297, 37.786297),
    }
    representative_results: list[dict[str, Any]] = []
    for key, expected in representative_expectations.items():
        row = by_key.get(key)
        passed = bool(
            row and row["expected_status"] == "calculated"
            and close_enough(float(row["expected_generation_t"]), expected[0])
            and close_enough(float(row["expected_emission_t"]), expected[1])
        )
        representative_results.append({"key": list(key), "expected": list(expected), "passed": passed})
        if not passed:
            failures.append(f"REPRESENTATIVE_CHECK_FAILED:{key}")
    checks["representative_formula_checks"] = representative_results

    # B2 must equal B0 outside the 30 controlled records.
    injected_keys = {(record["source_id"], record["target"]) for record in injections}
    noninjected_differences = 0
    semantic_fields = [
        "expected_status", "method_evaluation_id", "expected_generation_t", "expected_emission_t",
        "expected_activity_record", "parameter_evaluation_ids", "control_evaluation_id",
        "expected_reason_codes", "standard_reference_ids",
    ]
    for row in items:
        if row["variant"] != "B0" or (row["source_id"], row["target"]) in injected_keys:
            continue
        counterpart = by_key[("B2", row["evaluation_item_id"])]
        if any(row[field] != counterpart[field] for field in semantic_fields):
            noninjected_differences += 1
    if noninjected_differences:
        failures.append(f"B2_NONINJECTED_SEMANTIC_DIFFERENCES:{noninjected_differences}")
    checks["b2_noninjected_semantic_differences"] = noninjected_differences

    # Injection-specific expectations are evaluated on source-pollutant totals.
    negative_control_failures = 0
    injection_coverage = Counter()
    total_semantic_fields = ("expected_status", "expected_generation_t", "expected_emission_t", "expected_reason_codes")
    for injection in injections:
        source_id, target, root_cause = injection["source_id"], injection["target"], injection["root_cause"]
        injection_coverage[(target, root_cause)] += 1
        for pollutant in POLLUTANTS:
            total_id = f"{source_id}::{pollutant}"
            b0 = by_total_key[("B0", total_id)]
            b2 = by_total_key[("B2", total_id)]
            if root_cause == "UNIQUE_DEFAULT_NEGATIVE_CONTROL":
                if any(b0[field] != b2[field] for field in total_semantic_fields):
                    negative_control_failures += 1
            elif root_cause == "SOURCE_RELATION_CONFLICT":
                if b2["expected_status"] != "source_data_invalid":
                    failures.append(f"B2_SOURCE_RELATION_NOT_INVALID:{source_id}:{pollutant}")
            elif root_cause == "ACTIVITY_MISSING" and pollutant != "NH3":
                if b2["expected_status"] not in {"information_insufficient", "source_data_invalid"}:
                    failures.append(f"B2_ACTIVITY_MISSING_NOT_WITHHELD:{source_id}:{pollutant}")
            elif root_cause == "POLLUTANT_PARAMETER_MISSING" and pollutant in {"SO2", "PM10", "PM2.5", "BC", "OC"}:
                if b2["expected_status"] == "calculated":
                    failures.append(f"B2_POLLUTANT_PARAMETER_NOT_BLOCKED:{source_id}:{pollutant}")
            elif root_cause == "HARD_CONSTRAINT_CONFLICT" and pollutant in {"PM10", "PM2.5", "BC", "OC"}:
                if b2["expected_status"] == "calculated":
                    failures.append(f"B2_HARD_CONSTRAINT_NOT_BLOCKED:{source_id}:{pollutant}")
    if negative_control_failures:
        failures.append(f"B2_NEGATIVE_CONTROL_CHANGED:{negative_control_failures}")
    expected_coverage = Counter({(target, cause): 3 for target in ("INDUSTRIAL", "POWER") for cause in (
        "ACTIVITY_MISSING", "POLLUTANT_PARAMETER_MISSING", "SOURCE_RELATION_CONFLICT",
        "HARD_CONSTRAINT_CONFLICT", "UNIQUE_DEFAULT_NEGATIVE_CONTROL",
    )})
    if injection_coverage != expected_coverage:
        failures.append(f"B2_INJECTION_COVERAGE:{dict(injection_coverage)}")
    checks["b2_injection_coverage"] = {f"{key[0]}:{key[1]}": value for key, value in sorted(injection_coverage.items())}
    checks["b2_negative_control_differences"] = negative_control_failures
    injection_outcomes = read_csv("expected_injection_outcomes.csv")
    injection_validity = Counter(row["injection_validity"] for row in injection_outcomes)
    if injection_validity != Counter({"valid": 26, "invalid": 4}):
        failures.append(f"B2_INJECTION_VALIDITY_SUMMARY:{dict(injection_validity)}")
    eligible = sum(row["scoring_eligible"] == "true" for row in injection_outcomes)
    if eligible != 26 or any(row["scoring_eligible"] == "true" and row["injection_validity"] != "valid" for row in injection_outcomes):
        failures.append(f"B2_INJECTION_SCORING_ELIGIBILITY:{eligible}")
    checks["b2_injection_validity"] = dict(injection_validity)

    # Exception completeness: every non-calculated item must belong to at
    # least one canonical root group; grouping intentionally removes repeated
    # source-pollutant notifications.
    exception_group_keys = [(row["variant"], row["exception_group_id"]) for row in exceptions]
    if len(exception_group_keys) != len(set(exception_group_keys)):
        failures.append("DUPLICATE_EXCEPTION_GROUP_ID")
    exception_item_ids: set[tuple[str, str]] = set()
    for row in exceptions:
        try:
            affected = json.loads(row["affected_item_ids"])
        except json.JSONDecodeError:
            failures.append(f"EXCEPTION_AFFECTED_ITEMS_PARSE:{row['exception_group_id']}")
            continue
        exception_item_ids.update((row["variant"], item_id) for item_id in affected)
    missing_exception = sum(
        row["expected_status"] != "calculated" and (row["variant"], row["evaluation_item_id"]) not in exception_item_ids
        for row in items
    )
    if missing_exception:
        failures.append(f"NONCALCULATED_EXCEPTION_NOT_UNIQUE:{missing_exception}")
    checks["exception_rows"] = len(exceptions)
    checks["noncalculated_exception_failures"] = missing_exception

    # Source-pollutant totals must be reproducible from component items, and
    # no partial sum may be emitted when any component is withheld.
    component_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in items:
        component_groups[(row["variant"], f"{row['source_id']}::{row['pollutant']}")].append(row)
    total_failures = 0
    for row in totals:
        members = component_groups[(row["variant"], row["evaluation_total_id"])]
        if not members:
            total_failures += 1
            continue
        statuses = {member["expected_status"] for member in members}
        expected_status = "source_data_invalid" if "source_data_invalid" in statuses else "information_insufficient" if "information_insufficient" in statuses else "not_involved" if statuses == {"not_involved"} else "calculated" if statuses == {"calculated"} else "information_insufficient"
        if row["expected_status"] != expected_status:
            total_failures += 1
            continue
        if expected_status == "calculated":
            generation = sum(float(member["expected_generation_t"]) for member in members)
            emission = sum(float(member["expected_emission_t"]) for member in members)
            if not close_enough(float(row["expected_generation_t"]), generation) or not close_enough(float(row["expected_emission_t"]), emission):
                total_failures += 1
        elif row["expected_generation_t"] or row["expected_emission_t"]:
            total_failures += 1
    if total_failures:
        failures.append(f"SOURCE_POLLUTANT_TOTAL_FAILURES:{total_failures}")
    checks["source_pollutant_total_recalculation_failures"] = total_failures

    if manifest.get("reference_timing") != "post_execution_output_isolated" or manifest.get("pre_registered") is not False:
        failures.append("REFERENCE_TIMING_DISCLOSURE_INVALID")
    notices.append("This package is output-isolated but was created after machine runs were sealed; it must not be described as pre-registered.")
    return {"status": "pass" if not failures else "fail", "failures": failures[:200], "failure_count": len(failures), "notices": notices, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Optional explicit report path; default validation is read-only.")
    args = parser.parse_args()
    report = validate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
