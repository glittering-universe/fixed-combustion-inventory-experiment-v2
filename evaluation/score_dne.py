#!/usr/bin/env python3
"""D/N/E v2 scoring primitives for the fixed-combustion experiment.

The module contains no experiment discovery and writes no files.  Its public
functions operate on already normalized, sealed observations.  This keeps the
metric implementation independent from every participating method.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, NamedTuple


CORE_WEIGHTS = {"D": 0.35, "N": 0.35, "E": 0.30}
CALCULATED = "calculated"


class Atom(NamedTuple):
    """One independently applicable scoring decision.

    ``passed=None`` means that the atom is not observable/applicable for this
    method and therefore does not enter the denominator.
    """

    atom_id: str
    kind: str
    passed: bool | None
    detail: str = ""


def finite_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(1e-6, abs(expected) * 1e-6)


def normalized_efficiency(value: Any) -> float | None:
    number = finite_number(value)
    if number is None:
        return None
    if 1.0 < number <= 100.0:
        number /= 100.0
    return round(number, 12)


def pollutant_control_signature(pollutant: str, control: dict[str, Any]) -> tuple[float | None, ...]:
    """Return only the control fields that can affect ``pollutant``.

    PM10 is the sum of fine and coarse fractions, while PM2.5/BC/OC use the
    fine-particle efficiency.  SO2/NOx and the remaining pollutants use the
    general pollutant-specific efficiency.  Irrelevant populated fields are
    deliberately ignored.
    """

    if pollutant == "PM10":
        return (
            normalized_efficiency(control.get("fine_efficiency")),
            normalized_efficiency(control.get("coarse_efficiency")),
        )
    if pollutant in {"PM2.5", "BC", "OC"}:
        fine = control.get("fine_efficiency")
        if fine in (None, ""):
            fine = control.get("efficiency")
        return (normalized_efficiency(fine),)
    return (normalized_efficiency(control.get("efficiency")),)


def summarize_atoms(atoms: Iterable[Atom]) -> dict[str, Any]:
    materialized = list(atoms)
    applicable = [atom for atom in materialized if atom.passed is not None]
    passed = sum(atom.passed is True for atom in applicable)
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({atom.kind for atom in materialized}):
        members = [atom for atom in materialized if atom.kind == kind]
        scored = [atom for atom in members if atom.passed is not None]
        kind_passed = sum(atom.passed is True for atom in scored)
        by_kind[kind] = {
            "passed": kind_passed,
            "applicable": len(scored),
            "not_applicable": len(members) - len(scored),
            "score": kind_passed / len(scored) if scored else None,
        }
    return {
        "passed": passed,
        "applicable": len(applicable),
        "not_applicable": len(materialized) - len(applicable),
        "score": passed / len(applicable) if applicable else None,
        "by_kind": by_kind,
    }


def summarize_domain_decisions(
    atoms: Iterable[Atom],
    expected_exception_groups: set[tuple[str, str]],
    actual_exception_groups: set[tuple[str, str]],
    exception_disposition_by_source: dict[str, bool | None],
    exception_terminal_atom_ids: set[str],
) -> dict[str, Any]:
    """Summarize routine decisions and exception handling as equal components.

    Routine atoms and exception sources have deliberately separate
    denominators.  This prevents a large number of routine calculations from
    hiding a method's inability to detect or correctly handle a small number
    of exceptional sources.  ``exception_terminal_atom_ids`` contains exact
    terminal atom IDs whose outcome is already represented by the source-level
    exception disposition and therefore must not be counted twice.
    """

    materialized = list(atoms)
    legacy = summarize_atoms(materialized)
    exception_terminal_ids = {str(atom_id) for atom_id in exception_terminal_atom_ids}
    regular_atoms = [
        atom for atom in materialized
        if atom.kind != "exception_root_cause" and atom.atom_id not in exception_terminal_ids
    ]
    regular = summarize_atoms(regular_atoms)

    expected_by_source: dict[str, set[str]] = defaultdict(set)
    actual_by_source: dict[str, set[str]] = defaultdict(set)
    for source_id, root_cause in expected_exception_groups:
        expected_by_source[str(source_id)].add(str(root_cause))
    for source_id, root_cause in actual_exception_groups:
        actual_by_source[str(source_id)].add(str(root_cause))

    expected_sources = set(expected_by_source)
    actual_sources = set(actual_by_source)
    exception_sources = sorted(expected_sources | actual_sources)
    exception_passed = 0
    failed_source_examples: list[dict[str, Any]] = []
    for source_id in exception_sources:
        expected_roots = expected_by_source.get(source_id, set())
        actual_roots = actual_by_source.get(source_id, set())
        roots_exact = expected_roots == actual_roots
        disposition_required = source_id in expected_sources
        disposition_pass = exception_disposition_by_source.get(source_id) is True
        passed = roots_exact and (disposition_pass if disposition_required else False)
        exception_passed += int(passed)
        if not passed and len(failed_source_examples) < 30:
            failed_source_examples.append({
                "source_id": source_id,
                "expected_root_causes": sorted(expected_roots),
                "actual_root_causes": sorted(actual_roots),
                "disposition_passed": disposition_pass if disposition_required else None,
            })

    exception_score = exception_passed / len(exception_sources) if exception_sources else None
    components = [value for value in (regular["score"], exception_score) if value is not None]
    combined_score = statistics.mean(components) if components else None

    intersection = expected_sources & actual_sources
    if not expected_sources and not actual_sources:
        detection_precision = detection_recall = detection_f1 = None
    else:
        detection_precision = len(intersection) / len(actual_sources) if actual_sources else None
        detection_recall = len(intersection) / len(expected_sources) if expected_sources else None
        if detection_precision is None or detection_recall is None:
            detection_f1 = 0.0
        elif detection_precision + detection_recall == 0:
            detection_f1 = 0.0
        else:
            detection_f1 = 2 * detection_precision * detection_recall / (detection_precision + detection_recall)

    jaccards: list[float] = []
    for source_id in sorted(intersection):
        expected_roots = expected_by_source[source_id]
        actual_roots = actual_by_source[source_id]
        union = expected_roots | actual_roots
        jaccards.append(len(expected_roots & actual_roots) / len(union) if union else 1.0)
    root_macro_jaccard = statistics.mean(jaccards) if jaccards else None
    disposition_accuracy = (
        sum(exception_disposition_by_source.get(source_id) is True for source_id in expected_sources)
        / len(expected_sources)
        if expected_sources else None
    )

    return {
        "score": combined_score,
        "D_regular": regular["score"],
        "D_exception": exception_score,
        "regular": regular,
        "exception": {
            "passed": exception_passed,
            "applicable": len(exception_sources),
            "not_applicable": 0,
            "score": exception_score,
            "failed_source_count": len(exception_sources) - exception_passed,
            "failed_source_examples": failed_source_examples,
        },
        "legacy_atom_micro_score": legacy["score"],
        "passed": legacy["passed"],
        "applicable": legacy["applicable"],
        "not_applicable": legacy["not_applicable"],
        "by_kind": legacy["by_kind"],
        "raw_counts": {
            "passed": legacy["passed"],
            "applicable": legacy["applicable"],
            "not_applicable": legacy["not_applicable"],
            "total": len(materialized),
        },
        "diagnostics": {
            "detection_precision": detection_precision,
            "detection_recall": detection_recall,
            "detection_f1": detection_f1,
            "root_cause_macro_jaccard": root_macro_jaccard,
            "exception_disposition_accuracy": disposition_accuracy,
        },
    }


def _actual_target(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("target") or value.get("actual_target") or value.get("disposition") or ""
    text = str(value) if value not in (None, "") else ""
    aliases = {
        "include_industrial_boiler": "INDUSTRIAL",
        "include_power_heat": "POWER",
        "exclude_outside_two_source_categories": "EXCLUDE",
    }
    return aliases.get(text, text) or None


def _source_destination_pass(expected_target: str, value: Any) -> bool:
    if isinstance(value, dict) and "selected_for_target" in value:
        run_target = str(value.get("run_target") or "")
        return bool(value["selected_for_target"]) == (expected_target == run_target)
    actual_target = _actual_target(value)
    return actual_target == expected_target if actual_target is not None else False


def score_decision_atoms(
    expected_totals: dict[str, dict[str, Any]],
    actual_totals: dict[str, dict[str, Any]],
    *,
    expected_sources: dict[str, dict[str, Any]],
    actual_destinations: dict[str, Any],
    expected_exception_groups: set[tuple[str, str]],
    actual_exception_groups: set[tuple[str, str]],
    method: str,
) -> list[Atom]:
    """Score professional decisions without treating absent human traces as errors."""

    atoms: list[Atom] = []
    for source_id, expected in sorted(expected_sources.items()):
        expected_target = str(expected.get("expected_target") or expected.get("target") or "")
        atoms.append(Atom(
            f"source:{source_id}", "source_destination",
            _source_destination_pass(expected_target, actual_destinations.get(source_id)),
        ))

    human = method == "expert_led"
    for total_id, expected in sorted(expected_totals.items()):
        actual = actual_totals.get(total_id)
        expected_status = str(expected.get("expected_status") or "")
        atoms.append(Atom(
            f"terminal:{total_id}", "terminal_disposition",
            bool(actual and str(actual.get("status") or "") == expected_status),
            f"expected={expected_status};actual={str((actual or {}).get('status') or 'missing')}",
        ))
        if expected_status != CALCULATED:
            continue

        expected_methods = sorted(str(item) for item in expected.get("method_ids", []) if item)
        expected_parameters = sorted(str(item) for item in expected.get("parameter_ids", []) if item)
        actual_methods = sorted(str(item) for item in (actual or {}).get("method_ids", []) if item)
        actual_parameters = sorted(str(item) for item in (actual or {}).get("parameter_ids", []) if item)
        basis_applicable = bool(expected_methods or expected_parameters)
        basis_pass: bool | None
        if not basis_applicable or human:
            basis_pass = None
        else:
            basis_pass = actual_methods == expected_methods and actual_parameters == expected_parameters
        atoms.append(Atom(
            f"basis:{total_id}",
            "calculation_basis",
            basis_pass,
            f"expected_methods={expected_methods};actual_methods={actual_methods};expected_parameters={expected_parameters};actual_parameters={actual_parameters}",
        ))

        expected_controls = sorted(
            (tuple(item) for item in expected.get("control_signatures", [])), key=repr
        )
        actual_controls = sorted(
            (tuple(item) for item in (actual or {}).get("control_signatures", [])), key=repr
        )
        control_pass: bool | None
        if not expected_controls or human:
            control_pass = None
        else:
            control_pass = actual_controls == expected_controls
        atoms.append(Atom(f"control:{total_id}", "control_application", control_pass))

    for source_id, root_cause in sorted(expected_exception_groups | actual_exception_groups):
        expected = (source_id, root_cause) in expected_exception_groups
        actual = (source_id, root_cause) in actual_exception_groups
        atoms.append(Atom(
            f"exception:{source_id}:{root_cause}", "exception_root_cause", expected == actual,
            f"expected={expected};actual={actual}",
        ))
    return atoms


def score_numeric_atoms(
    expected_totals: dict[str, dict[str, Any]],
    actual_totals: dict[str, dict[str, Any]],
    *,
    method: str,
) -> list[Atom]:
    """Score required numeric outputs against a reference-defined population.

    Missing required values fail the applicable checks. Only a non-calculated
    reference item, or the predeclared human-process exception, makes a numeric
    check inapplicable; an observed omission cannot remove it from the denominator.
    """

    atoms: list[Atom] = []
    for total_id, expected in sorted(expected_totals.items()):
        actual = actual_totals.get(total_id)
        expected_status = str(expected.get("expected_status") or "")
        actual_generation = finite_number((actual or {}).get("generation_t"))
        actual_emission = finite_number((actual or {}).get("emission_t"))
        if expected_status == CALCULATED:
            complete = bool(
                actual
                and str(actual.get("status") or "") == CALCULATED
                and actual_generation is not None
                and actual_emission is not None
            )
        else:
            complete = bool(
                actual
                and actual_generation is None
                and actual_emission is None
            )
        atoms.append(Atom(f"complete:{total_id}", "result_completeness", complete))

        if expected_status != CALCULATED:
            atoms.append(Atom(f"value:{total_id}", "reference_value", None))
            atoms.append(Atom(f"recalc:{total_id}", "internal_recalculation", None))
            continue

        expected_generation = finite_number(expected.get("expected_generation_t"))
        expected_emission = finite_number(expected.get("expected_emission_t"))
        value_pass = bool(
            actual_generation is not None
            and actual_emission is not None
            and expected_generation is not None
            and expected_emission is not None
            and close_enough(actual_generation, expected_generation)
            and close_enough(actual_emission, expected_emission)
        )
        atoms.append(Atom(f"value:{total_id}", "reference_value", value_pass))

        if method == "expert_led":
            recalc_pass = None
        else:
            recorded = (actual or {}).get("recalculation_pass")
            recalc_pass = bool(recorded) if recorded is not None else False
            recalc_pass = bool(
                recalc_pass
                and actual_generation is not None
                and actual_emission is not None
                and actual_generation >= 0
                and actual_emission >= 0
                and actual_emission <= actual_generation + max(1e-6, abs(actual_generation) * 1e-6)
            )
        atoms.append(Atom(f"recalc:{total_id}", "internal_recalculation", recalc_pass))

    for total_id, actual in sorted(actual_totals.items()):
        if total_id in expected_totals:
            continue
        numeric = finite_number(actual.get("generation_t")) is not None or finite_number(actual.get("emission_t")) is not None
        atoms.append(Atom(
            f"extra:{total_id}", "result_completeness", False,
            "unexpected_numeric_row" if numeric else "unexpected_blank_row",
        ))

    expected_pm_pairs: dict[str, dict[str, str]] = defaultdict(dict)
    for total_id, expected in expected_totals.items():
        if str(expected.get("expected_status") or "") != CALCULATED:
            continue
        source_id = str(expected.get("source_id") or total_id.split("::", 1)[0])
        pollutant = str(expected.get("pollutant") or total_id.rsplit("::", 1)[-1])
        if pollutant in {"PM10", "PM2.5"}:
            expected_pm_pairs[source_id][pollutant] = total_id
    for source_id, pair in sorted(expected_pm_pairs.items()):
        if "PM10" not in pair or "PM2.5" not in pair:
            continue
        pm10 = actual_totals.get(pair["PM10"]) or {}
        pm25 = actual_totals.get(pair["PM2.5"]) or {}
        ten_generation = finite_number(pm10.get("generation_t"))
        fine_generation = finite_number(pm25.get("generation_t"))
        ten_emission = finite_number(pm10.get("emission_t"))
        fine_emission = finite_number(pm25.get("emission_t"))
        atoms.append(Atom(
            f"pm-order:{source_id}", "internal_recalculation",
            bool(
                None not in (ten_generation, fine_generation, ten_emission, fine_emission)
                and fine_generation <= ten_generation + max(1e-6, abs(ten_generation) * 1e-6)
                and fine_emission <= ten_emission + max(1e-6, abs(ten_emission) * 1e-6)
            ),
        ))
    return atoms


def relative_time_efficiency(
    method_seconds_per_record: float | None,
    human_seconds_per_record: float | None,
    deterministic_seconds_per_record: float | None,
) -> float | None:
    """Log-normalize time between empirical human (0) and script (1) anchors."""

    values = (method_seconds_per_record, human_seconds_per_record, deterministic_seconds_per_record)
    if any(value is None or value <= 0 for value in values):
        return None
    method_time, human_time, deterministic_time = (float(value) for value in values)
    if human_time <= deterministic_time:
        return None
    raw = math.log(human_time / method_time) / math.log(human_time / deterministic_time)
    return max(0.0, min(1.0, raw))


def core_score(d_score: float | None, n_score: float | None, e_score: float | None) -> float | None:
    if any(value is None for value in (d_score, n_score, e_score)):
        return None
    return 100.0 * (
        CORE_WEIGHTS["D"] * float(d_score)
        + CORE_WEIGHTS["N"] * float(n_score)
        + CORE_WEIGHTS["E"] * float(e_score)
    )


def aggregate_targets(target_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return equal-source macro and atom/record-weighted micro summaries."""

    rows = list(target_rows.values())
    macro: dict[str, float | None] = {}
    micro: dict[str, float | None] = {}
    for dimension in ("D", "N"):
        scores = [row[dimension].get("score") for row in rows if row.get(dimension)]
        scores = [float(value) for value in scores if value is not None]
        macro[dimension] = statistics.mean(scores) if scores else None
        passed = sum(int(row[dimension].get("passed", 0)) for row in rows if row.get(dimension))
        applicable = sum(int(row[dimension].get("applicable", 0)) for row in rows if row.get(dimension))
        micro[dimension] = passed / applicable if applicable else None
    e_values = [float(row["E"]) for row in rows if row.get("E") is not None]
    macro["E"] = statistics.mean(e_values) if e_values else None
    e_weight = sum(int(row.get("record_count", 0)) for row in rows if row.get("E") is not None)
    micro["E"] = (
        sum(float(row["E"]) * int(row.get("record_count", 0)) for row in rows if row.get("E") is not None) / e_weight
        if e_weight else None
    )
    macro["EICPI_core_0_100"] = core_score(macro["D"], macro["N"], macro["E"])
    micro["EICPI_core_0_100"] = core_score(micro["D"], micro["N"], micro["E"])
    return {"macro": macro, "micro": micro}


def atom_rows(atoms: Iterable[Atom]) -> list[dict[str, Any]]:
    return [
        {"atom_id": atom.atom_id, "kind": atom.kind, "passed": atom.passed, "detail": atom.detail}
        for atom in atoms
    ]


def parse_json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    if value in (None, ""):
        return []
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    return []


def _normalized_activity(value: Any, unit: Any) -> tuple[float | None, str | None]:
    number = finite_number(value)
    text = str(unit or "").strip().lower().replace("³", "3").replace(" ", "")
    if number is None:
        return None, None
    if text in {"kg", "kg_coal", "kg煤"}:
        return number, "kg"
    if text in {"t", "吨", "吨燃料"}:
        return number * 1000.0, "kg"
    if text == "万吨":
        return number * 10_000_000.0, "kg"
    if text in {"m3", "立方米"}:
        return number, "m3"
    if text in {"万m3", "万立方米"}:
        return number * 10_000.0, "m3"
    return None, None


def _factor_basis(unit: Any) -> str | None:
    text = str(unit or "").strip().lower().replace("³", "3").replace(" ", "")
    if text.startswith("g/kg"):
        return "kg"
    if text.startswith("g/m3"):
        return "m3"
    return None


def independent_recalculation(
    row: dict[str, Any],
    all_actual_totals: dict[str, dict[str, Any]],
) -> bool | None:
    """Recalculate one normalized source-pollutant row from its recorded inputs."""

    if str(row.get("status") or "") != CALCULATED:
        return None
    activities = parse_json_list(row.get("activity_record"))
    parameters = parse_json_list(row.get("parameter_record"))
    controls = parse_json_list(row.get("control_record"))
    if not activities and not parameters and not controls:
        return None
    if not (len(activities) == len(parameters) == len(controls)) or not activities:
        return False

    pollutant = str(row.get("pollutant") or "")
    generation = 0.0
    emission = 0.0
    pm25_parameters: list[dict[str, Any]] = []
    if pollutant == "PM10":
        source_id = str(row.get("source_id") or "")
        pm25 = all_actual_totals.get(f"{source_id}::PM2.5")
        if pm25:
            pm25_parameters = parse_json_list(pm25.get("parameter_record"))

    for index, (activity, parameter, control) in enumerate(zip(activities, parameters, controls)):
        amount, activity_basis = _normalized_activity(activity.get("value"), activity.get("unit"))
        factor = finite_number(parameter.get("value"))
        factor_basis = _factor_basis(parameter.get("unit"))
        if amount is None or factor is None or activity_basis != factor_basis:
            return False
        item_generation = amount * factor / 1_000_000.0
        generation += item_generation
        signature = pollutant_control_signature(pollutant, control)
        if pollutant == "PM10":
            if len(pm25_parameters) <= index:
                return False
            fine_factor = finite_number(pm25_parameters[index].get("value"))
            fine_efficiency, coarse_efficiency = signature
            if fine_factor is None or fine_efficiency is None or coarse_efficiency is None:
                return False
            fine_generation = amount * fine_factor / 1_000_000.0
            emission += fine_generation * (1.0 - fine_efficiency)
            emission += (item_generation - fine_generation) * (1.0 - coarse_efficiency)
        else:
            efficiency = signature[0]
            if efficiency is None:
                return False
            emission += item_generation * (1.0 - efficiency)

    actual_generation = finite_number(row.get("generation_t"))
    actual_emission = finite_number(row.get("emission_t"))
    if actual_generation is None or actual_emission is None:
        return False
    return close_enough(actual_generation, generation) and close_enough(actual_emission, emission)
