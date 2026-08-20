#!/usr/bin/env python3
"""Normalize sealed method outputs to one source-by-pollutant evaluation table."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


POLLUTANT = {"SO2": "SO2", "NOX": "NOx", "NOx": "NOx", "CO": "CO",
             "VOCS": "VOC", "VOC": "VOC", "PM10": "PM10", "PM25": "PM2.5",
             "PM2.5": "PM2.5", "BC": "BC", "OC": "OC", "NH3": "NH3"}
ORDER = ("SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC", "NH3")
FOUNDATION_FLAGS = {
    "SOURCE_RELATION_MISSING", "SOURCE_RELATION_CONFLICT", "ACTIVITY_OUT_OF_RANGE",
    "SULFUR_OUT_OF_RANGE", "ASH_OUT_OF_RANGE", "CAPACITY_OUT_OF_RANGE",
    "PM_SIZE_CONSTRAINT_VIOLATION", "NEGATIVE_RESULT", "EFFICIENCY_OUT_OF_RANGE",
    "LOW_NOX_FIELD_CONFLICT",
}
FIELDS = (
    "source_id", "target", "pollutant", "status", "method_category", "activity_record",
    "parameter_record", "control_record", "generation_t", "emission_t", "standard_reference",
    "reason_codes", "minimum_recalculation_complete", "complete_process_record", "run_id", "method",
)


def compact_json(values: object) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_flags(*values: object) -> set[str]:
    result: set[str] = set()
    for value in values:
        if not value:
            continue
        try:
            decoded = json.loads(str(value))
            if isinstance(decoded, list):
                result.update(str(item) for item in decoded)
        except json.JSONDecodeError:
            result.update(part.strip() for part in str(value).split(";") if part.strip())
    return result


def is_number(value: object) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def json_object_array(value: object) -> tuple[list[dict[str, object]], bool]:
    """Parse one formal component array without silently accepting a scalar."""
    if value in (None, ""):
        return [], False
    try:
        decoded = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return [], False
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        return [], False
    return decoded, True


def component_record_complete(
    pollutant: str,
    activities: list[dict[str, object]],
    parameters: list[dict[str, object]],
    controls: list[dict[str, object]],
    *,
    require_component_ids: bool,
) -> bool:
    if not activities or not (len(activities) == len(parameters) == len(controls)):
        return False
    if not all(item.get("value") not in (None, "") and item.get("unit") not in (None, "") for item in activities):
        return False
    if not all(item.get("value") not in (None, "") and item.get("unit") not in (None, "") for item in parameters):
        return False
    if require_component_ids:
        activity_ids = [item.get("component_id") for item in activities]
        parameter_ids = [item.get("component_id") for item in parameters]
        control_ids = [item.get("component_id") for item in controls]
        if any(value in (None, "") for value in activity_ids) or not (
            activity_ids == parameter_ids == control_ids
        ):
            return False
    if pollutant == "PM10":
        return all(
            item.get("fine_efficiency") not in (None, "")
            and item.get("coarse_efficiency") not in (None, "")
            for item in controls
        )
    return all(item.get("efficiency") not in (None, "") for item in controls)


def generic_rows(run_dir: Path, manifest: dict[str, object]) -> list[dict[str, object]]:
    path = run_dir / "outputs" / "generic_inventory_items.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.DictReader(handle))
    result = []
    formal_contract_required = manifest.get("input_contract") == "raw_base_tables_v2"
    for row in raw:
        status = row.get("status", "")
        calculated = status == "calculated"
        formal_requested = any(row.get(field) not in (None, "") for field in (
            "activity_record_json", "parameter_record_json", "control_record_json"
        ))
        if formal_requested:
            activities, activities_valid = json_object_array(row.get("activity_record_json"))
            parameters, parameters_valid = json_object_array(row.get("parameter_record_json"))
            controls, controls_valid = json_object_array(row.get("control_record_json"))
            records_valid = activities_valid and parameters_valid and controls_valid
        else:
            activities = [{"value": row.get("activity_value", ""), "unit": row.get("activity_unit", "")}]
            parameters = [{"value": row.get("parameter_value", ""), "unit": row.get("parameter_unit", "")}]
            controls = [{"method": row.get("control_method", ""), "efficiency": row.get("control_efficiency", "")}]
            records_valid = not formal_contract_required
        pollutant = POLLUTANT.get(row.get("pollutant", ""), row.get("pollutant", ""))
        minimum = (not calculated) or (
            row.get("method", "") not in ("", None)
            and records_valid
            and component_record_complete(
                pollutant,
                activities,
                parameters,
                controls,
                require_component_ids=formal_contract_required or formal_requested,
            )
            and row.get("generation_t", "") not in ("", None)
            and row.get("emission_t", "") not in ("", None)
        )
        result.append({
            "source_id": row.get("source_id", ""),
            "target": row.get("target", manifest["target"]),
            "pollutant": pollutant,
            "status": status,
            "method_category": row.get("method", ""),
            "activity_record": compact_json(activities),
            "parameter_record": compact_json(parameters),
            "control_record": compact_json(controls),
            "generation_t": row.get("generation_t", ""),
            "emission_t": row.get("emission_t", ""),
            "standard_reference": row.get("standard_reference", ""),
            "reason_codes": row.get("reason_code", ""),
            "minimum_recalculation_complete": minimum,
            # The generic contract has no physical source locations, normalization actions,
            # revision links, or item-level admission/quality records.
            "complete_process_record": False,
            "run_id": manifest["run_id"],
            "method": manifest["experiment_method"],
        })
    return result


def baseline_rows(run_dir: Path, manifest: dict[str, object]) -> list[dict[str, object]]:
    """Normalize the baseline's minimal numeric record without inventing lineage."""
    path = run_dir / "outputs" / "baseline_inventory_items.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.DictReader(handle))
    result = []
    for row in raw:
        status = row.get("status", "")
        try:
            activities = json.loads(row.get("activity_record_json") or "[]")
            parameters = json.loads(row.get("parameter_record_json") or "[]")
            controls = json.loads(row.get("control_record_json") or "[]")
        except json.JSONDecodeError:
            activities, parameters, controls = [], [], []
        calculated = status == "calculated"
        minimum = (not calculated) or (
            bool(activities)
            and len(activities) == len(parameters) == len(controls)
            and all(item.get("value") not in (None, "") and item.get("unit") for item in activities)
            and all(item.get("value") not in (None, "") and item.get("unit") for item in parameters)
            and row.get("generation_t", "") not in (None, "")
            and row.get("emission_t", "") not in (None, "")
        )
        result.append({
            "source_id": row.get("source_id", ""),
            "target": row.get("target", manifest["target"]),
            "pollutant": POLLUTANT.get(row.get("pollutant", ""), row.get("pollutant", "")),
            "status": status,
            "method_category": row.get("method", ""),
            "activity_record": compact_json(activities),
            "parameter_record": compact_json(parameters),
            "control_record": compact_json(controls),
            "generation_t": row.get("generation_t", ""),
            "emission_t": row.get("emission_t", ""),
            "standard_reference": row.get("standard_reference", ""),
            "reason_codes": row.get("reason_code", ""),
            "minimum_recalculation_complete": minimum,
            "complete_process_record": False,
            "run_id": manifest["run_id"],
            "method": manifest["experiment_method"],
        })
    return result


def specialized_rows(run_dir: Path, manifest: dict[str, object]) -> list[dict[str, object]]:
    connection = sqlite3.connect(run_dir / "run.sqlite3")
    connection.row_factory = sqlite3.Row
    query = """
        SELECT i.item_id,i.source_id,i.pollutant,i.item_type,i.fuel_id,
               a.action,a.reason_code AS admission_reason,a.flags_json AS admission_flags,
               r.normalized_fuel,r.normalized_technology,r.activity_value,r.activity_unit,
               r.activity_formula,r.parameter_id,r.mode,r.factor_value,r.factor_unit,r.factor_formula,
               r.source_section,r.source_page,r.control_technology,r.control_parameter_ids,
               r.control_efficiency,r.control_fine_efficiency,r.control_coarse_efficiency,
               r.flags_json AS rule_flags,
               c.generation_t,c.emission_t,c.generation_formula,c.emission_formula,c.calculation_status
        FROM calculation_items i
        LEFT JOIN admission_results a ON a.item_id=i.item_id
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        LEFT JOIN calculations c ON c.item_id=i.item_id
        ORDER BY i.source_id,i.pollutant,i.item_id
    """
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for raw in connection.execute(query):
        row = dict(raw)
        grouped[(row["source_id"], POLLUTANT.get(row["pollutant"], row["pollutant"]))].append(row)
    target_by_source = {row["source_id"]: row["target"] for row in connection.execute("SELECT source_id,target FROM device_sources")}
    connection.close()

    trace_records: dict[str, dict[str, object]] = {}
    trace_path = run_dir / "trace" / "complete_calculation_process.jsonl"
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                trace_records[record["item_id"]] = record

    result = []
    for (source_id, pollutant), items in sorted(grouped.items(), key=lambda pair: (pair[0][0], ORDER.index(pair[0][1]))):
        actions = [str(item.get("action") or "") for item in items]
        flags = set().union(*(parse_flags(item.get("admission_flags"), item.get("rule_flags")) for item in items))
        if flags & FOUNDATION_FLAGS:
            status = "source_data_invalid"
        elif any(action in {"withhold", "withhold_missing", "block_foundation"} for action in actions):
            status = "information_insufficient"
        elif actions and all(action in {"not_applicable", "not_involved"} for action in actions):
            status = "not_involved"
        elif any(action == "allow_calculation" for action in actions):
            status = "calculated"
        else:
            status = "information_insufficient"

        calculated_items = [item for item in items if item.get("calculation_status") == "calculated"]
        record_items = calculated_items if status == "calculated" else items
        generation = sum(float(item["generation_t"]) for item in calculated_items if item.get("generation_t") is not None)
        emission = sum(float(item["emission_t"]) for item in calculated_items if item.get("emission_t") is not None)
        if status != "calculated":
            generation_value: object = ""
            emission_value: object = ""
        else:
            generation_value = generation
            emission_value = emission
        activities = [{"fuel_id": item.get("fuel_id"), "value": item.get("activity_value"),
                       "unit": item.get("activity_unit"), "formula": item.get("activity_formula")} for item in record_items]
        parameters = [{"item_type": item.get("item_type"), "parameter_id": item.get("parameter_id"),
                       "mode": item.get("mode"), "value": item.get("factor_value"),
                       "unit": item.get("factor_unit"), "formula": item.get("factor_formula")} for item in record_items]
        controls = [{"technology": item.get("control_technology"), "parameter_ids": item.get("control_parameter_ids"),
                     "efficiency": item.get("control_efficiency"), "fine_efficiency": item.get("control_fine_efficiency"),
                     "coarse_efficiency": item.get("control_coarse_efficiency")} for item in record_items]
        minimum = True
        for item in items:
            if item.get("action") != "allow_calculation":
                continue
            # The minimum recalculation record requires the method/formula and
            # actual value/unit used.  A parameter ID is intentionally not
            # mandatory: rule-authorized empirical zero paths (for example,
            # missing denitrification information -> zero NH3 slip with flag)
            # have no catalog parameter ID but remain exactly reproducible.
            required = (item.get("activity_value"), item.get("activity_unit"), item.get("mode"),
                        item.get("factor_value"), item.get("factor_unit"), item.get("factor_formula"),
                        item.get("generation_t"), item.get("emission_t"), item.get("generation_formula"), item.get("emission_formula"))
            if any(value in (None, "") for value in required):
                minimum = False
        trace_complete = all(item["item_id"] in trace_records for item in items)
        if trace_complete:
            required_trace = {"source_path", "source_sheet", "source_row", "activity_formula", "parameter_id",
                              "source_section", "action", "reason_code", "generation_formula", "emission_formula"}
            trace_complete = all(required_trace.issubset(trace_records[item["item_id"]]) for item in items)
        result.append({
            "source_id": source_id,
            "target": target_by_source.get(source_id, manifest["target"]),
            "pollutant": pollutant,
            "status": status,
            "method_category": compact_json(sorted({str(item.get("mode") or "") for item in record_items})),
            "activity_record": compact_json(activities),
            "parameter_record": compact_json(parameters),
            "control_record": compact_json(controls),
            "generation_t": generation_value,
            "emission_t": emission_value,
            "standard_reference": compact_json(sorted({f"{item.get('source_section') or ''}|{item.get('source_page') or ''}" for item in record_items})),
            "reason_codes": compact_json(sorted({str(item.get("admission_reason") or "") for item in items} | flags)),
            "minimum_recalculation_complete": minimum,
            "complete_process_record": trace_complete,
            "run_id": manifest["run_id"],
            "method": manifest["experiment_method"],
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_package.resolve()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    output_status = "normalized"
    if (run_dir / "outputs" / "baseline_inventory_items.csv").is_file():
        rows = baseline_rows(run_dir, manifest)
    elif (run_dir / "outputs" / "generic_inventory_items.csv").is_file():
        rows = generic_rows(run_dir, manifest)
    elif (run_dir / "run.sqlite3").is_file():
        rows = specialized_rows(run_dir, manifest)
    else:
        # A sealed task failure is still an evaluable experimental outcome.
        # Preserve the fixed downstream denominator by emitting a valid empty
        # normalized table instead of terminating the batch evaluator.
        rows = []
        output_status = "no_supported_output_run_failure"
    destination = args.output or (run_dir / "evaluation" / "normalized_items.csv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "run_id": manifest["run_id"], "output": str(destination),
        "row_count": len(rows), "status": output_status,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
