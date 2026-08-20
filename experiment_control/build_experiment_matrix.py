#!/usr/bin/env python3
"""Freeze the protocol-approved A-D run matrix without executing it."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "matrix"
METHODS = json.loads((ROOT / "method_package" / "frozen_manifest.json").read_text(encoding="utf-8"))["method_hashes"]
SEED = "fixed-combustion-raw-base-table-matrix-v2"
MACHINE = ("deterministic_program", "generic_tool_agent", "full")
TARGETS = ("INDUSTRIAL", "POWER")
SHORT = {"deterministic_program": "DET", "generic_tool_agent": "GEN", "full": "FULL", "expert_led": "HUM"}


def scenario_for(method: str) -> str:
    if method == "deterministic_program":
        return "simple_deterministic"
    return "generic_tool_agent" if method == "generic_tool_agent" else "full"


def stable_shuffle(rows: list[dict[str, object]], label: str) -> list[dict[str, object]]:
    result = list(rows)
    seed = int(hashlib.sha256(f"{SEED}|{label}".encode()).hexdigest()[:16], 16)
    random.Random(seed).shuffle(result)
    return result


def row(experiment: str, run_id: str, method: str, scenario: str, target: str,
        scale: int, variant: str, repetition: int | None, order_group: str,
        user_supplied: bool = False) -> dict[str, object]:
    return {
        "experiment": experiment,
        "run_id": run_id,
        "method": method,
        "scenario": scenario,
        "target": target,
        "scale_percent": scale,
        "input_variant": variant,
        "repetition": repetition,
        "order_group": order_group,
        "method_bundle_hash": METHODS.get(method),
        "runner": "user_supplied" if user_supplied else (
            "deterministic" if method == "deterministic_program" else
            "generic_agent" if method == "generic_tool_agent" else
            "full_agent" if scenario == "full" else "ablation_agent"
        ),
        "status": "provided" if user_supplied else "pending",
    }


def build_matrix() -> dict[str, object]:
    rows: list[dict[str, object]] = []

    # Experiment A: both full datasets, three machine repetitions; human data later.
    for repetition in (1, 2, 3):
        for target in TARGETS:
            group = f"A-R{repetition}-{target}"
            batch = [row("A", f"A-{target[:3]}-{SHORT[m]}-R{repetition}", m,
                         scenario_for(m),
                         target, 100, "B0", repetition, group) for m in MACHINE]
            rows.extend(stable_shuffle(batch, group))
    for target in TARGETS:
        rows.append(row("A", f"A-{target[:3]}-HUM", "expert_led", "expert_led",
                        target, 100, "B0", None, "A-HUM", True))

    # Experiment B: B0 + four full structural perturbations + B2, one run each.
    for variant in ("B0", "S1", "S2", "S3", "S4", "B2"):
        for target in TARGETS:
            group = f"B-{variant}-{target}"
            batch = [row("B", f"B-{variant}-{target[:3]}-{SHORT[m]}", m,
                         scenario_for(m),
                         target, 100, variant, 1, group) for m in MACHINE]
            rows.extend(stable_shuffle(batch, group))
            rows.append(row("B", f"B-{variant}-{target[:3]}-HUM", "expert_led", "expert_led",
                            target, 100, variant, None, group, True))

    # Experiment C: nested 25/50/75/100% scopes, three repetitions.
    for repetition in (1, 2, 3):
        for scale in (25, 50, 75, 100):
            for target in TARGETS:
                group = f"C-R{repetition}-{scale}-{target}"
                batch = [row("C", f"C-{scale}-{target[:3]}-{SHORT[m]}-R{repetition}", m,
                             scenario_for(m),
                             target, scale, "B0", repetition, group) for m in MACHINE]
                rows.extend(stable_shuffle(batch, group))

    # Experiment D: Full plus four single-component ablations, both full datasets.
    d_versions = ("full", "w_o_calculation_item_structure", "w_o_executable_rule_gate",
                  "agent_managed_admission", "w_o_complete_trace")
    for target in TARGETS:
        group = f"D-{target}"
        batch = [row("D", f"D-{target[:3]}-{scenario.upper()}", scenario, scenario,
                     target, 100, "B0", 1, group) for scenario in d_versions]
        rows.extend(stable_shuffle(batch, group))

    # The row helper expresses user supplied state through runner/status; keep the public matrix compact.
    for index, record in enumerate(rows, start=1):
        record["sequence"] = index
    if len({record["run_id"] for record in rows}) != len(rows):
        raise RuntimeError("duplicate run_id in experiment matrix")
    machine_rows = [record for record in rows if record["runner"] != "user_supplied"]
    human_rows = [record for record in rows if record["runner"] == "user_supplied"]
    if len(machine_rows) != 136:
        raise RuntimeError(f"unexpected machine run count: {len(machine_rows)}")

    return {
        "matrix_version": "2.0.0",
        "input_contract": "raw_base_tables_v2",
        "seed": SEED,
        "machine_runs": len(machine_rows),
        "human_runs": len(human_rows),
        "rows": rows,
    }


def main() -> None:
    payload = build_matrix()
    rows = payload["rows"]
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "frozen_experiment_matrix.json"
    csv_path = OUT / "frozen_experiment_matrix.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"json": str(json_path), "csv": str(csv_path),
                      "machine_runs": payload["machine_runs"], "human_runs": payload["human_runs"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
