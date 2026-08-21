#!/usr/bin/env python3
"""Aggregate the formal experiment with the three-dimensional D/N/E metric.

Experiment A alone supplies the primary EICPI_core comparison.  B1, B2, C,
and D are emitted as independent diagnostic reports and never enter the core
score.  The evaluator reads sealed packages; it does not alter their contents.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import statistics
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from compare_normalized_runs import (
    FIELDS as SEMANTIC_FIELDS,
    decode as decode_semantic,
    read_rows as read_normalized_rows,
    semantic_equal,
)
from score_dne import (
    aggregate_targets,
    core_score,
    independent_recalculation,
    pollutant_control_signature,
    relative_time_efficiency,
    score_decision_atoms,
    score_numeric_atoms,
    summarize_atoms,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiment_control"))
from result_layout import result_leaf, summary_dir  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "matrix" / "frozen_experiment_matrix.json"
METRIC_SPEC = ROOT / "evaluation" / "metric_spec_v2.json"
POLLUTANTS = ("SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC", "NH3")
AGENT_RUNNERS = frozenset({"generic_agent", "full_agent", "ablation_agent"})
MATRIX_MANIFEST_BINDINGS = (
    ("run_id", "run_id"),
    ("method", "experiment_method"),
    ("target", "target"),
    ("input_variant", "input_variant"),
    ("scale_percent", "scale_percent"),
    ("method_bundle_hash", "method_bundle_hash"),
)
METHOD_ALIASES = {
    "constant": "emission_factor_method",
    "capacity_lookup": "emission_factor_method",
    "产排污系数法": "emission_factor_method",
    "排放系数法": "emission_factor_method",
    "emission_factor": "emission_factor_method",
    "coefficient_method": "emission_factor_method",
    "coal_sulfur_balance": "material_balance_method",
    "coal_particle_balance": "material_balance_method",
    "物料衡算法": "material_balance_method",
    "material_balance": "material_balance_method",
    "ammonia_slip": "ammonia_slip_factor_method",
    "constant_sum": "ammonia_slip_factor_method",
    "氨逃逸系数法": "ammonia_slip_factor_method",
    "empirical_zero": "authorized_empirical_disposition",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def normalized_method(value: Any) -> str:
    text = str(value or "").strip()
    return METHOD_ALIASES.get(text, text)


def factor_unit(value: Any) -> str:
    text = str(value or "").strip().replace("³", "3").replace("m^3", "m3")
    if text.startswith("g/kg"):
        return "g/kg"
    if text.startswith("g/m3"):
        return "g/m3"
    return text


def canonical_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return round(number, 12)


def parameter_signature(role: str, method: Any, value: Any, unit: Any) -> str:
    del method  # method is scored independently; do not require hidden item-to-method pairing
    return json.dumps(
        [role, canonical_number(value), factor_unit(unit)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def normalize_path(run_dir: Path) -> Path:
    destination = run_dir / "evaluation" / "normalized_items.csv"
    if destination.is_file():
        return destination
    human_totals = next(
        (path for path in (run_dir / "calculation_totals.csv", run_dir / "outputs" / "calculation_totals.csv") if path.is_file()),
        None,
    )
    if human_totals is not None:
        rows = []
        for row in read_csv(human_totals):
            status = "calculated" if row.get("status") == "reported_total" else "information_insufficient"
            rows.append({
                "source_id": row.get("source_id", ""), "target": row.get("target", ""),
                "pollutant": row.get("pollutant", ""), "status": status,
                "method_category": "", "activity_record": "[]", "parameter_record": "[]",
                "control_record": "[]", "generation_t": row.get("generation_t", ""),
                "emission_t": row.get("emission_t", ""), "standard_reference": "",
                "reason_codes": row.get("reason_code", ""),
                "minimum_recalculation_complete": "NA", "complete_process_record": "NA",
                "run_id": load_json(run_dir / "run_manifest.json").get("run_id", ""), "method": "expert_led",
            })
        destination.parent.mkdir(parents=True, exist_ok=True)
        fields = (
            "source_id", "target", "pollutant", "status", "method_category", "activity_record",
            "parameter_record", "control_record", "generation_t", "emission_t", "standard_reference",
            "reason_codes", "minimum_recalculation_complete", "complete_process_record", "run_id", "method",
        )
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader(); writer.writerows(rows)
        return destination
    subprocess.run(
        [sys.executable, str(ROOT / "evaluation" / "normalize_run_output.py"), str(run_dir)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return destination


def execution_metrics(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "logs" / "execution_metrics.json"
    return load_json(path) if path.is_file() else {}


def run_seconds_per_record(run_dir: Path, record_count: int) -> float | None:
    manifest = load_json(run_dir / "run_manifest.json")
    direct = manifest.get("seconds_per_record")
    if direct not in (None, ""):
        return float(direct)
    metrics = execution_metrics(run_dir)
    direct = metrics.get("seconds_per_record")
    if direct not in (None, ""):
        return float(direct)
    wall = metrics.get("wall_seconds")
    if wall in (None, "") or record_count <= 0:
        return None
    return float(wall) / record_count


def reference_rows(reference: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    variant = "B2" if str(manifest.get("input_variant") or "").upper() == "B2" else "B0"
    target = str(manifest.get("target") or "")
    explicit_scope = set(
        manifest.get("evaluation_candidate_ids")
        or manifest.get("candidate_ids")
        or []
    )
    all_sources = [row for row in read_csv(reference / "expected_sources.csv") if row["variant"] == variant]
    if explicit_scope:
        sources = [row for row in all_sources if row["source_id"] in explicit_scope]
    else:
        sources = all_sources
    target_ids = {
        row["source_id"] for row in sources
        if row.get("expected_target") == target
    }
    items = [
        row for row in read_csv(reference / "expected_items.csv")
        if row["variant"] == variant and row["target"] == target
        and (not explicit_scope or row["source_id"] in explicit_scope)
    ]
    totals = [
        row for row in read_csv(reference / "expected_source_pollutant_totals.csv")
        if row["variant"] == variant and row["target"] == target
        and (not explicit_scope or row["source_id"] in explicit_scope)
    ]
    exceptions = [
        row for row in read_csv(reference / "expected_exceptions.csv")
        if row["variant"] == variant and row["target"] == target
        and (not explicit_scope or row["source_id"] in explicit_scope)
    ]
    return {
        "sources": sources,
        "items": items,
        "totals": totals,
        "exceptions": exceptions,
        "target_ids": [{"source_id": source_id} for source_id in sorted(target_ids)],
    }


def expected_semantics(selected: dict[str, list[dict[str, str]]]) -> dict[str, dict[str, Any]]:
    members: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in selected["items"]:
        members[(row["source_id"], row["pollutant"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    for total in selected["totals"]:
        key = (total["source_id"], total["pollutant"])
        method_ids: set[str] = set()
        parameter_ids: set[str] = set()
        control_signatures: set[tuple[float | None, ...]] = set()
        for item in members.get(key, []):
            mask = parse_json(item.get("applicability_mask"), {})
            if mask.get("method"):
                raw_method = item.get("expected_method_category") or ""
                decoded_methods = parse_json(raw_method, None)
                if isinstance(decoded_methods, list):
                    method_ids.update(normalized_method(method) for method in decoded_methods if method)
                elif raw_method:
                    method_ids.add(normalized_method(raw_method))
            if mask.get("parameter"):
                parameter_records = parse_json(item.get("expected_parameter_evaluation_record"), [])
                if isinstance(parameter_records, dict):
                    parameter_records = [parameter_records]
                for parameter in parameter_records:
                    parameter_ids.add(parameter_signature(
                        str(parameter.get("role") or "primary"), parameter.get("method"),
                        parameter.get("value"), parameter.get("unit"),
                    ))
            # NH3 is an independent SCR/SNCR ammonia-slip calculation basis,
            # not a combustion-removal-efficiency decision.
            if mask.get("control") and total["pollutant"] != "NH3":
                control_signatures.add(pollutant_control_signature(total["pollutant"], {
                    "efficiency": item.get("expected_control_efficiency"),
                    "fine_efficiency": item.get("expected_fine_efficiency"),
                    "coarse_efficiency": item.get("expected_coarse_efficiency"),
                }))
        result[f"{total['source_id']}::{total['pollutant']}"] = {
            **total,
            "method_ids": sorted(method_ids),
            "parameter_ids": sorted(parameter_ids),
            "control_signatures": sorted(control_signatures, key=repr),
        }
    return result


def actual_destinations(run_dir: Path, normalized_rows: list[dict[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    manifest = load_json(run_dir / "run_manifest.json")
    for human_decisions in (run_dir / "source_decisions.csv", run_dir / "outputs" / "source_decisions.csv"):
        for row in read_csv(human_decisions):
            source_id = row.get("source_id", "")
            decision = row.get("human_scope_decision", "")
            if source_id and decision in {"include", "not_selected_for_target"}:
                result[source_id] = {
                    "selected_for_target": decision == "include",
                    "run_target": manifest.get("target"),
                }
            elif source_id and (row.get("decided_target") or row.get("actual_target")):
                result[source_id] = str(row.get("decided_target") or row.get("actual_target"))
    explicit = run_dir / "outputs" / "source_dispositions.csv"
    for row in read_csv(explicit):
        source_id = row.get("source_id", "")
        target = row.get("actual_target") or row.get("target") or row.get("disposition") or ""
        if source_id and target:
            result[source_id] = target
    database = run_dir / "run.sqlite3"
    if database.is_file():
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "source_decisions" in tables:
                for row in connection.execute("SELECT source_id,decided_target FROM source_decisions"):
                    result[str(row["source_id"])] = str(row["decided_target"])
            if "device_sources" in tables:
                for row in connection.execute("SELECT source_id,target FROM device_sources"):
                    result[str(row["source_id"])] = str(row["target"])
            if "excluded_records" in tables:
                for row in connection.execute("SELECT raw_id FROM excluded_records"):
                    raw_id = str(row["raw_id"])
                    source_id = raw_id.replace("RAW-", "SRC-", 1)
                    result[source_id] = "EXCLUDE"
        finally:
            connection.close()
    for row in normalized_rows:
        if row.get("source_id") and row.get("target"):
            result.setdefault(row["source_id"], row["target"])
    return result


def _actual_basis(row: dict[str, str], pm25_row: dict[str, str] | None) -> tuple[list[str], list[str]]:
    parameters = parse_json(row.get("parameter_record"), [])
    if isinstance(parameters, dict):
        parameters = [parameters]
    methods: set[str] = set()
    signatures: set[str] = set()
    raw_method_value = row.get("method_category") or row.get("method") or ""
    decoded_methods = parse_json(raw_method_value, None)
    if isinstance(decoded_methods, list):
        methods.update(normalized_method(item) for item in decoded_methods if item)
    else:
        methods.update(
            normalized_method(item) for item in re.split(r"\s*[;|]\s*", str(raw_method_value)) if item
        )
    for parameter in parameters:
        method = parameter.get("mode") or ""
        if method:
            methods.add(normalized_method(method))
        signatures.add(parameter_signature("primary", method, parameter.get("value"), parameter.get("unit")))
    if row.get("pollutant") == "PM10" and pm25_row:
        paired = parse_json(pm25_row.get("parameter_record"), [])
        if isinstance(paired, dict):
            paired = [paired]
        for parameter in paired:
            method = parameter.get("mode") or pm25_row.get("method_category") or pm25_row.get("method") or ""
            signatures.add(parameter_signature("pm25_pair_for_pm10", method, parameter.get("value"), parameter.get("unit")))
    methods.discard("")
    return sorted(methods), sorted(signatures)


def actual_semantics(normalized_rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    raw = {f"{row['source_id']}::{row['pollutant']}": row for row in normalized_rows}
    result: dict[str, dict[str, Any]] = {}
    for total_id, row in raw.items():
        pm25 = raw.get(f"{row['source_id']}::PM2.5")
        method_ids, parameter_ids = _actual_basis(row, pm25)
        controls = parse_json(row.get("control_record"), [])
        if isinstance(controls, dict):
            controls = [controls]
        control_signatures = sorted(
            {pollutant_control_signature(row["pollutant"], control) for control in controls}, key=repr
        )
        result[total_id] = {
            **row,
            "method_ids": method_ids,
            "parameter_ids": parameter_ids,
            "control_signatures": control_signatures,
        }
    for total_id, row in result.items():
        minimum = str(raw.get(total_id, {}).get("minimum_recalculation_complete", "")).strip().lower()
        recalculated = independent_recalculation(row, result)
        row["recalculation_pass"] = False if minimum in {"false", "0", "no"} else recalculated
    return result


def load_reason_module(reference: Path):
    path = reference / "compare_run_to_reference.py"
    if not path.is_file():
        path = ROOT / "reference" / "compare_run_to_reference.py"
    spec = importlib.util.spec_from_file_location("dne_reference_helpers", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_seal_module():
    path = ROOT / "evaluation" / "evaluate_sealed_run.py"
    spec = importlib.util.spec_from_file_location("dne_seal_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load seal verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _diagnostic_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def verify_seal_and_collect_boundary_diagnostic(
    row: dict[str, Any],
    run_dir: Path,
    manifest: dict[str, Any],
    seal_helper: Any,
) -> dict[str, Any]:
    """Hard-check the seal and collect non-gating Agent-boundary diagnostics."""

    seal_status = seal_helper.verify_experiment_seal(run_dir, manifest)
    if seal_status.get("status") != "pass":
        raise RuntimeError(
            f"sealed integrity failed for {row['run_id']}: {seal_status.get('failures')}"
        )
    binding_failures = [
        {
            "matrix_field": matrix_field,
            "manifest_field": manifest_field,
            "matrix_value": row.get(matrix_field),
            "manifest_value": manifest.get(manifest_field),
        }
        for matrix_field, manifest_field in MATRIX_MANIFEST_BINDINGS
        if (
            row.get(matrix_field) != manifest.get(manifest_field)
            or type(row.get(matrix_field)) is not type(manifest.get(manifest_field))
        )
    ]
    if binding_failures:
        raise RuntimeError(
            f"matrix-manifest binding failed for {row['run_id']}: "
            f"{json.dumps(binding_failures, ensure_ascii=False, separators=(',', ':'))}"
        )
    if row.get("runner") not in AGENT_RUNNERS:
        return {
            "status": "not_applicable",
            "failures": [],
            "warnings": [],
            "reason": "non_agent_method",
            "diagnostic_only": True,
            "enters_DNE": False,
        }
    try:
        raw = seal_helper.method_boundary_audit(run_dir, manifest)
        if not isinstance(raw, dict):
            raise TypeError("method boundary audit did not return a mapping")
        result = dict(raw)
        result["status"] = str(raw.get("status") or "not_available")
        result["failures"] = _diagnostic_values(raw.get("failures"))
        result["warnings"] = _diagnostic_values(raw.get("warnings"))
        if raw.get("reason") and str(raw["reason"]) not in result["warnings"]:
            result["warnings"].append(str(raw["reason"]))
    except Exception as exc:  # diagnostic collection must not become a score gate
        return {
            "status": "audit_error",
            "failures": ["METHOD_BOUNDARY_AUDIT_ERROR"],
            "warnings": [f"{type(exc).__name__}: {exc}"],
            "diagnostic_only": True,
            "enters_DNE": False,
        }
    result["diagnostic_only"] = True
    result["enters_DNE"] = False
    return result


def summarize_method_boundary_diagnostics(
    rows: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Summarize Agent method-boundary observations without changing D/N/E."""

    agent_rows = [row for row in rows if row.get("runner") in AGENT_RUNNERS]
    status_counts: dict[str, int] = defaultdict(int)
    failure_counts: dict[str, int] = defaultdict(int)
    warning_counts: dict[str, int] = defaultdict(int)
    run_diagnostics: list[dict[str, Any]] = []
    completed_statuses = {"pass", "fail"}
    for row in agent_rows:
        score = scores.get(row["run_id"])
        if score is None or "method_boundary" not in score:
            continue
        diagnostic = score["method_boundary"]
        status = str(diagnostic.get("status") or "not_available")
        failures = _diagnostic_values(diagnostic.get("failures"))
        warnings = _diagnostic_values(diagnostic.get("warnings"))
        status_counts[status] += 1
        for code in failures:
            failure_counts[code] += 1
        for code in warnings:
            warning_counts[code] += 1
        run_diagnostics.append({
            "run_id": row["run_id"],
            "runner": row.get("runner"),
            "method": row.get("method"),
            "target": row.get("target"),
            "status": status,
            "failures": failures,
            "warnings": warnings,
        })
    return {
        "diagnostic_only": True,
        "enters_DNE": False,
        "interpretation": (
            "Method-boundary audit is reported for Agent runs as an independent diagnostic; "
            "it is not an additional quality gate and does not change D, N, E, or EICPI_core."
        ),
        "agent_run_count": len(agent_rows),
        "audited_agent_run_count": sum(
            item["status"] in completed_statuses for item in run_diagnostics
        ),
        "all_agent_runs_audited": (
            len(run_diagnostics) == len(agent_rows)
            and all(item["status"] in completed_statuses for item in run_diagnostics)
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "runs_with_failures": sum(bool(item["failures"]) for item in run_diagnostics),
        "runs_with_warnings": sum(bool(item["warnings"]) for item in run_diagnostics),
        "failure_code_counts": dict(sorted(failure_counts.items())),
        "warning_code_counts": dict(sorted(warning_counts.items())),
        "runs": run_diagnostics,
    }


def exception_groups(
    run_dir: Path,
    reference: Path,
    selected: dict[str, list[dict[str, str]]],
    helper: Any,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    expected = {
        (row["source_id"], row["canonical_root_cause"])
        for row in selected["exceptions"] if row.get("requires_user_judgment") == "true"
    }
    equivalence_path = reference / "reason_code_equivalence.json"
    actual: set[tuple[str, str]] = set()
    if helper and equivalence_path.is_file():
        actual = helper.actual_exception_groups(run_dir, load_json(equivalence_path))
        target_ids = {row["source_id"] for row in selected["target_ids"]}
        actual = {(source_id, root) for source_id, root in actual if source_id in target_ids}
    target_ids = {row["source_id"] for row in selected["target_ids"]}
    has_primary_exception_contract = (
        (run_dir / "outputs" / "最终异常清单.csv").is_file()
        or (run_dir / "outputs" / "generic_exception_list.csv").is_file()
    )
    if not has_primary_exception_contract:
        equivalence = load_json(equivalence_path) if equivalence_path.is_file() else {}
        code_map = equivalence.get("codes", {})
        for human_exceptions in (run_dir / "exceptions.csv", run_dir / "outputs" / "exceptions.csv"):
            for row in read_csv(human_exceptions):
                source_id = row.get("source_id", "")
                category = row.get("canonical_root_cause") or row.get("root_cause") or row.get("reason_code") or row.get("category", "")
                mapping = code_map.get(str(category).split(":", 1)[0]) if category else None
                if mapping and not mapping.get("requires_user_judgment"):
                    continue
                root = mapping.get("canonical_root_cause") if mapping else f"unmapped::{category}"
                if source_id in target_ids and category:
                    actual.add((source_id, root))
    return expected, actual


def evaluate_one(
    row: dict[str, Any],
    run_dir: Path,
    reference: Path,
    helper: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(run_dir / "run_manifest.json")
    normalized = normalize_path(run_dir)
    normalized_rows = read_csv(normalized)
    selected = reference_rows(reference, manifest)
    expected = expected_semantics(selected)
    actual = actual_semantics(normalized_rows)
    expected_sources = {item["source_id"]: item for item in selected["sources"]}
    destinations = actual_destinations(run_dir, normalized_rows)
    expected_exceptions, actual_exceptions = exception_groups(run_dir, reference, selected, helper)
    d_atoms = score_decision_atoms(
        expected, actual,
        expected_sources=expected_sources,
        actual_destinations=destinations,
        expected_exception_groups=expected_exceptions,
        actual_exception_groups=actual_exceptions,
        method=row["method"],
    )
    n_atoms = score_numeric_atoms(expected, actual, method=row["method"])
    d_summary, n_summary = summarize_atoms(d_atoms), summarize_atoms(n_atoms)
    process_values = [
        str(item.get("complete_process_record", "")).strip().lower()
        for item in normalized_rows
        if str(item.get("complete_process_record", "")).strip().lower() not in {"", "na", "none"}
    ]
    process_record_coverage = (
        sum(value in {"true", "1", "yes"} for value in process_values) / len(process_values)
        if process_values else None
    )
    record_count = sum(item.get("expected_target") == row["target"] for item in selected["sources"])
    score = {
        "run_id": row["run_id"], "experiment": row["experiment"], "method": row["method"],
        "target": row["target"], "input_variant": row["input_variant"],
        "scale_percent": row["scale_percent"], "repetition": row.get("repetition"),
        "D": d_summary, "N": n_summary, "E": None, "EICPI_core_0_100": None,
        "record_count": record_count,
        "seconds_per_record": run_seconds_per_record(run_dir, record_count),
        "process_record_coverage": process_record_coverage,
        "normalized_output": str(normalized),
    }
    context = {
        "expected": expected, "actual": actual, "expected_sources": expected_sources,
        "expected_exception_groups": expected_exceptions, "actual_exception_groups": actual_exceptions,
        "d_atoms": d_atoms, "n_atoms": n_atoms, "selected": selected,
    }
    return score, context


def summarize_a_repetitions(run_scores: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"run_count": len(run_scores)}
    for dimension in ("D", "N"):
        values = [item[dimension]["score"] for item in run_scores if item[dimension]["score"] is not None]
        result[dimension] = {
            "score": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "passed": sum(int(item[dimension]["passed"]) for item in run_scores),
            "applicable": sum(int(item[dimension]["applicable"]) for item in run_scores),
            "not_applicable": sum(int(item[dimension].get("not_applicable", 0)) for item in run_scores),
        }
    e_values = [item["E"] for item in run_scores if item.get("E") is not None]
    result["E"] = statistics.median(e_values) if e_values else None
    result["E_range"] = {"min": min(e_values), "max": max(e_values)} if e_values else None
    result["record_count"] = max((int(item["record_count"]) for item in run_scores), default=0)
    result["EICPI_core_0_100"] = core_score(result["D"]["score"], result["N"]["score"], result["E"])
    result["run_ids"] = [item["run_id"] for item in run_scores if item.get("run_id")]
    return result


def partition_experiments(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    primary = [row for row in rows if row.get("experiment") == "A"]
    reports: dict[str, list[dict[str, Any]]] = {"B1": [], "B2": [], "C": [], "D": []}
    for row in rows:
        experiment = row.get("experiment")
        if experiment == "B":
            reports["B2" if row.get("input_variant") == "B2" else "B1"].append(row)
        elif experiment in {"C", "D"}:
            reports[experiment].append(row)
    return primary, reports


def distribution(values: list[Any]) -> dict[str, Any]:
    numbers = [float(value) for value in values if value not in (None, "")]
    return {
        "count": len(numbers),
        "median": statistics.median(numbers) if numbers else None,
        "min": min(numbers) if numbers else None,
        "max": max(numbers) if numbers else None,
    }


def compare_normalized_v2(left_path: Path, right_path: Path) -> dict[str, Any]:
    """Compare method-internal semantics using pollutant-relevant controls."""

    left_rows, right_rows = read_csv(left_path), read_csv(right_path)
    left_raw = {(row["source_id"], row["pollutant"]): row for row in left_rows}
    right_raw = {(row["source_id"], row["pollutant"]): row for row in right_rows}
    left, right = actual_semantics(left_rows), actual_semantics(right_rows)
    keys = sorted(set(left) | set(right))
    mismatch: dict[str, int] = defaultdict(int)
    examples = []
    exact_failed = 0

    def activities(row: dict[str, str]) -> list[str]:
        values = parse_json(row.get("activity_record"), [])
        if isinstance(values, dict):
            values = [values]
        return sorted(json.dumps(
            [canonical_number(item.get("value")), str(item.get("unit") or "").replace("³", "3")],
            ensure_ascii=False, separators=(",", ":"),
        ) for item in values)

    for total_id in keys:
        if total_id not in left or total_id not in right:
            mismatch["row_presence"] += 1
            exact_failed += 1
            if len(examples) < 30:
                examples.append({"evaluation_total_id": total_id, "fields": ["row_presence"]})
            continue
        lrow, rrow = left[total_id], right[total_id]
        lraw = left_raw[(lrow["source_id"], lrow["pollutant"])]
        rraw = right_raw[(rrow["source_id"], rrow["pollutant"])]
        failures = []
        pairs = {
            "status": (lrow.get("status"), rrow.get("status")),
            "method": (lrow.get("method_ids"), rrow.get("method_ids")),
            "activity": (activities(lraw), activities(rraw)),
            "parameter": (lrow.get("parameter_ids"), rrow.get("parameter_ids")),
            "control": (lrow.get("control_signatures"), rrow.get("control_signatures")),
            "standard_reference": (decode_semantic(lraw.get("standard_reference", "")), decode_semantic(rraw.get("standard_reference", ""))),
            "reason_codes": (decode_semantic(lraw.get("reason_codes", "")), decode_semantic(rraw.get("reason_codes", ""))),
        }
        for field, (left_value, right_value) in pairs.items():
            if not semantic_equal(left_value, right_value):
                failures.append(field)
                mismatch[field] += 1
        for field in ("generation_t", "emission_t"):
            if not semantic_equal(lrow.get(field, ""), rrow.get(field, "")):
                failures.append(field)
                mismatch[field] += 1
        if failures and len(examples) < 30:
            examples.append({"evaluation_total_id": total_id, "fields": failures})
        if failures:
            exact_failed += 1
    total = len(keys)
    return {
        "left": str(left_path), "right": str(right_path),
        "total_units": total, "passed_units": total - exact_failed, "failed_units": exact_failed,
        "agreement_rate": (total - exact_failed) / total if total else None,
        "mismatch_by_field": dict(mismatch), "mismatch_examples": examples,
    }


def b1_report(rows: list[dict[str, Any]], row_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    legacy_human = [row["run_id"] for row in rows if row.get("method") == "expert_led"]
    comparable_rows = [row for row in rows if row.get("method") != "expert_led"]
    index = {(row["method"], row["target"], row["input_variant"]): row for row in comparable_rows}
    comparisons = []
    for method, target, variant in sorted(index):
        if variant not in {"S1", "S2", "S3", "S4"}:
            continue
        baseline = index.get((method, target, "B0"))
        perturbed = index[(method, target, variant)]
        if not baseline:
            continue
        result = compare_normalized_v2(
            Path(row_by_id[baseline["run_id"]]["normalized_output"]),
            Path(row_by_id[perturbed["run_id"]]["normalized_output"]),
        )
        comparisons.append({
            "method": method, "target": target, "variant": variant,
            "baseline_run_id": baseline["run_id"], "perturbed_run_id": perturbed["run_id"], **result,
        })
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in comparisons:
        groups[item["method"]].append(item)
    by_method = {}
    for method, items in sorted(groups.items()):
        total = sum(int(item["total_units"]) for item in items)
        passed = sum(int(item["passed_units"]) for item in items)
        by_method[method] = {
            "pair_count": len(items), "passed_units": passed, "total_units": total,
            "semantic_retention": passed / total if total else None,
        }
    return {
        "experiment": "B1", "enters_EICPI_core": False,
        "interpretation": "single frozen run per perturbation; descriptive input invariance, not a causal estimate",
        "legacy_human_runs_not_comparable_to_raw_v2_perturbations": legacy_human,
        "by_method": by_method, "comparisons": comparisons,
    }


def _total_reference_correct(expected: dict[str, Any] | None, actual: dict[str, Any] | None) -> bool:
    if not expected or not actual or actual.get("status") != expected.get("expected_status"):
        return False
    if expected.get("control_signatures") and actual.get("control_signatures") != expected.get("control_signatures"):
        return False
    if expected.get("expected_status") != "calculated":
        return actual.get("generation_t") in (None, "") and actual.get("emission_t") in (None, "")
    try:
        generation_ok = abs(float(actual["generation_t"]) - float(expected["expected_generation_t"])) <= max(1e-6, abs(float(expected["expected_generation_t"])) * 1e-6)
        emission_ok = abs(float(actual["emission_t"]) - float(expected["expected_emission_t"])) <= max(1e-6, abs(float(expected["expected_emission_t"])) * 1e-6)
    except (TypeError, ValueError, KeyError):
        return False
    return generation_ok and emission_ok


def b2_report(
    rows: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    reference: Path,
    b0_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    injection_manifest = read_csv(reference / "expected_injection_outcomes.csv")
    b0_index = {(row["method"], row["target"]): row for row in b0_rows if row.get("input_variant") == "B0"}
    runs = []
    for row in sorted(rows, key=lambda item: item["sequence"]):
        if row.get("method") == "expert_led":
            runs.append({
                "run_id": row["run_id"],
                "method": row["method"],
                "target": row["target"],
                "status": "not_comparable_legacy_human_result_did_not_process_raw_v2_injections",
            })
            continue
        context = contexts[row["run_id"]]
        expected, actual = context["expected"], context["actual"]
        expected_groups, actual_groups = context["expected_exception_groups"], context["actual_exception_groups"]
        source_ids = set(context["expected_sources"])
        designed = [
            item for item in injection_manifest
            if item.get("target") == row["target"] and item.get("source_id") in source_ids
        ]
        eligible = [item for item in designed if item.get("scoring_eligible") == "true"]
        baseline = b0_index.get((row["method"], row["target"]))
        baseline_rows = (
            read_normalized_rows(Path(scores[baseline["run_id"]]["normalized_output"])) if baseline else {}
        )
        injected_rows = read_normalized_rows(Path(scores[row["run_id"]]["normalized_output"]))

        def unchanged(source_id: str, pollutants: set[str]) -> bool | None:
            if not baseline:
                return None
            for pollutant in pollutants:
                key = (source_id, pollutant)
                left, right = baseline_rows.get(key), injected_rows.get(key)
                if left is None or right is None:
                    return False
                if any(
                    not semantic_equal(decode_semantic(left.get(field, "")), decode_semantic(right.get(field, "")))
                    for field in SEMANTIC_FIELDS
                ):
                    return False
            return True

        outcomes = []
        for item in eligible:
            source_id = item["source_id"]
            affected = set(parse_json(item.get("expected_affected_pollutants"), []))
            positive = item.get("plan_class") != "negative_control"
            affected_disposition = all(
                actual.get(f"{source_id}::{pollutant}", {}).get("status")
                == expected.get(f"{source_id}::{pollutant}", {}).get("expected_status")
                for pollutant in affected
            )
            expected_injected_roots = {
                exception["canonical_root_cause"] for exception in context["selected"]["exceptions"]
                if exception["source_id"] == source_id
                and exception.get("injection_root_cause") == item.get("root_cause")
                and exception.get("requires_user_judgment") == "true"
            }
            actual_roots = {root for source, root in actual_groups if source == source_id}
            root_correct = expected_injected_roots.issubset(actual_roots)
            unaffected = set(POLLUTANTS) - affected
            unaffected_correct = all(
                _total_reference_correct(expected.get(f"{source_id}::{pollutant}"), actual.get(f"{source_id}::{pollutant}"))
                for pollutant in unaffected
            )
            whole_source = all(
                _total_reference_correct(expected.get(f"{source_id}::{pollutant}"), actual.get(f"{source_id}::{pollutant}"))
                for pollutant in POLLUTANTS
            )
            outcomes.append({
                "source_id": source_id, "positive_injection": positive,
                "affected_disposition_correct": affected_disposition,
                "root_cause_group_correct": root_correct,
                "targeted_detection_correct": affected_disposition and root_correct,
                "unaffected_items_reference_correct": unaffected_correct,
                "whole_source_reference_correct": whole_source,
                "unaffected_items_unchanged_vs_B0": unchanged(source_id, unaffected),
                "whole_source_unchanged_vs_B0": unchanged(source_id, set(POLLUTANTS)),
            })
        positive = [item for item in outcomes if item["positive_injection"]]
        negative = [item for item in outcomes if not item["positive_injection"]]
        def ratio(items: list[dict[str, Any]], field: str) -> dict[str, Any]:
            applicable = [item for item in items if item.get(field) is not None]
            numerator = sum(bool(item[field]) for item in applicable)
            return {"correct": numerator, "total": len(applicable), "rate": numerator / len(applicable) if applicable else None}
        runs.append({
            "run_id": row["run_id"], "method": row["method"], "target": row["target"],
            "designed_records": len(designed), "eligible_records": len(eligible),
            "excluded_preexisting_problem": len(designed) - len(eligible),
            "positive_injections": len(positive), "negative_controls": len(negative),
            "affected_disposition": ratio(positive, "affected_disposition_correct"),
            "root_cause": ratio(positive, "root_cause_group_correct"),
            "targeted_detection": ratio(positive, "targeted_detection_correct"),
            "unaffected_reference_correctness": ratio(positive, "unaffected_items_reference_correct"),
            "unaffected_unchanged_vs_method_B0": ratio(positive, "unaffected_items_unchanged_vs_B0"),
            "negative_control_whole_source": ratio(negative, "whole_source_reference_correct"),
            "negative_control_unchanged_vs_method_B0": ratio(negative, "whole_source_unchanged_vs_B0"),
            "D": scores[row["run_id"]]["D"], "N": scores[row["run_id"]]["N"],
            "outcomes": outcomes,
        })
    return {"experiment": "B2", "enters_EICPI_core": False, "runs": runs}


def c_report(rows: list[dict[str, Any]], scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["method"], row["target"], int(row["scale_percent"]))].append(row)
    entries = []
    for (method, target, scale), members in sorted(groups.items()):
        values = [scores[row["run_id"]] for row in members]
        entries.append({
            "method": method, "target": target, "scale_percent": scale,
            "run_count": len(values),
            "seconds_per_record": distribution([item["seconds_per_record"] for item in values]),
            "wall_seconds": distribution([execution_metrics(result_leaf(ROOT, row)).get("wall_seconds") for row in members]),
            "peak_rss_bytes": distribution([execution_metrics(result_leaf(ROOT, row)).get("peak_rss_bytes") for row in members]),
        })
    return {"experiment": "C", "enters_EICPI_core": False, "entries": entries}


def d_report(rows: list[dict[str, Any]], scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    full = {
        target: scores[row["run_id"]]
        for row in rows if row["method"] == "full" for target in [row["target"]]
    }
    entries = []
    for row in sorted(rows, key=lambda item: item["sequence"]):
        score = scores[row["run_id"]]
        reference = full.get(row["target"])
        entries.append({
            "run_id": row["run_id"], "method": row["method"], "target": row["target"],
            "D": score["D"], "N": score["N"],
            "delta_D_vs_full": (
                score["D"]["score"] - reference["D"]["score"]
                if reference and score["D"]["score"] is not None and reference["D"]["score"] is not None else None
            ),
            "delta_N_vs_full": (
                score["N"]["score"] - reference["N"]["score"]
                if reference and score["N"]["score"] is not None and reference["N"]["score"] is not None else None
            ),
            "seconds_per_record": score["seconds_per_record"],
            "process_record_coverage": score.get("process_record_coverage"),
        })
    return {
        "experiment": "D", "enters_EICPI_core": False,
        "total_EICPI_core": "not_calculated_by_design",
        "interpretation": "one frozen run per variant; component contribution diagnostic, not causal effect estimate",
        "entries": entries,
    }


def write_run_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "experiment", "run_id", "method", "target", "input_variant", "scale_percent", "repetition",
        "D", "D_passed", "D_applicable", "D_NA", "N", "N_passed", "N_applicable", "N_NA",
        "E", "EICPI_core_0_100", "record_count", "seconds_per_record",
        "method_boundary_status", "method_boundary_failures", "method_boundary_warnings",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            boundary = row.get("method_boundary") or {}
            writer.writerow({
                **{name: row.get(name) for name in ("experiment", "run_id", "method", "target", "input_variant", "scale_percent", "repetition", "E", "EICPI_core_0_100", "record_count", "seconds_per_record")},
                "D": row["D"]["score"], "D_passed": row["D"]["passed"], "D_applicable": row["D"]["applicable"], "D_NA": row["D"]["not_applicable"],
                "N": row["N"]["score"], "N_passed": row["N"]["passed"], "N_applicable": row["N"]["applicable"], "N_NA": row["N"]["not_applicable"],
                "method_boundary_status": boundary.get("status", "not_available"),
                "method_boundary_failures": json.dumps(
                    _diagnostic_values(boundary.get("failures")), ensure_ascii=False, separators=(",", ":"),
                ),
                "method_boundary_warnings": json.dumps(
                    _diagnostic_values(boundary.get("warnings")), ensure_ascii=False, separators=(",", ":"),
                ),
            })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-package", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=summary_dir(ROOT))
    parser.add_argument("--allow-partial", action="store_true", help="Development only: aggregate sealed subset.")
    args = parser.parse_args()
    reference = args.reference_package.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix = load_json(MATRIX)
    missing_seals = [
        row["run_id"] for row in matrix["rows"]
        if not (result_leaf(ROOT, row) / "experiment_seal.json").is_file()
    ]
    if missing_seals and not args.allow_partial:
        raise RuntimeError(
            f"formal aggregation requires all {len(matrix['rows'])} rows sealed; missing {len(missing_seals)}: {missing_seals[:10]}"
        )
    helper = load_reason_module(reference)
    if helper and hasattr(helper, "verify_frozen_reference"):
        helper.verify_frozen_reference(reference)
    seal_helper = load_seal_module()

    sealed_rows = []
    scores: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for row in sorted(matrix["rows"], key=lambda item: item["sequence"]):
        run_dir = result_leaf(ROOT, row)
        if not (run_dir / "experiment_seal.json").is_file():
            continue
        manifest = load_json(run_dir / "run_manifest.json")
        boundary_status = verify_seal_and_collect_boundary_diagnostic(
            row, run_dir, manifest, seal_helper,
        )
        score, context = evaluate_one(row, run_dir, reference, helper)
        score["method_boundary"] = boundary_status
        sealed_rows.append(row)
        scores[row["run_id"]] = score
        if row["experiment"] == "B" and row["input_variant"] == "B2":
            contexts[row["run_id"]] = context

    primary_rows, independent_rows = partition_experiments(sealed_rows)
    anchors = {}
    for target in ("INDUSTRIAL", "POWER"):
        human = [scores[row["run_id"]]["seconds_per_record"] for row in primary_rows if row["target"] == target and row["method"] == "expert_led"]
        deterministic = [scores[row["run_id"]]["seconds_per_record"] for row in primary_rows if row["target"] == target and row["method"] == "deterministic_program"]
        human = [value for value in human if value is not None]
        deterministic = [value for value in deterministic if value is not None]
        anchors[target] = {
            "human_seconds_per_record": statistics.median(human) if human else None,
            "deterministic_seconds_per_record": statistics.median(deterministic) if deterministic else None,
        }
    for row in primary_rows:
        score = scores[row["run_id"]]
        anchor = anchors[row["target"]]
        score["E"] = relative_time_efficiency(
            score["seconds_per_record"], anchor["human_seconds_per_record"], anchor["deterministic_seconds_per_record"],
        )
        score["EICPI_core_0_100"] = core_score(score["D"]["score"], score["N"]["score"], score["E"])

    a_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in primary_rows:
        a_groups[(row["method"], row["target"])].append(scores[row["run_id"]])
    by_method_target = {
        f"{method}|{target}": {"method": method, "target": target, **summarize_a_repetitions(items)}
        for (method, target), items in sorted(a_groups.items())
    }
    by_method = {}
    for method in sorted({method for method, _ in a_groups}):
        target_rows = {
            target: by_method_target[f"{method}|{target}"]
            for target in ("INDUSTRIAL", "POWER") if f"{method}|{target}" in by_method_target
        }
        by_method[method] = aggregate_targets(target_rows)

    b1 = b1_report(independent_rows["B1"], scores)
    b2 = b2_report(independent_rows["B2"], scores, contexts, reference, independent_rows["B1"])
    c = c_report(independent_rows["C"], scores)
    d = d_report(independent_rows["D"], scores)
    method_boundary_diagnostics = summarize_method_boundary_diagnostics(
        matrix["rows"], scores,
    )
    summary = {
        "evaluation_version": "2.0.0",
        "metric_spec": str(METRIC_SPEC.relative_to(ROOT)),
        "metric_spec_sha256": sha256(METRIC_SPEC),
        "matrix_version": matrix.get("matrix_version"),
        "sealed_runs_evaluated": len(sealed_rows),
        "pending_runs": len(matrix["rows"]) - len(sealed_rows),
        "independent_quality_gate": False,
        "reference_interpretation": load_json(METRIC_SPEC)["reference_interpretation"],
        "reference_package_version": load_json(reference / "manifest.json").get("version"),
        "experiment_A_EICPI_core": {
            "anchors": anchors,
            "by_method_target": by_method_target,
            "by_method_macro_and_micro": by_method,
        },
        "independent_reports": {
            "B1": "experiment_B1_v2.json", "B2": "experiment_B2_v2.json",
            "C": "experiment_C_v2.json", "D": "experiment_D_v2.json",
        },
        "independent_diagnostics": {
            "method_boundary": method_boundary_diagnostics,
        },
    }
    write_run_csv(output_dir / "run_scores_v2.csv", [scores[row["run_id"]] for row in sealed_rows])
    for name, payload in (("B1", b1), ("B2", b2), ("C", c), ("D", d)):
        (output_dir / f"experiment_{name}_v2.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "aggregate_summary_v2.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "evaluation_version": "2.0.0", "sealed_runs_evaluated": len(sealed_rows),
        "pending_runs": len(matrix["rows"]) - len(sealed_rows), "output_dir": str(output_dir),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
