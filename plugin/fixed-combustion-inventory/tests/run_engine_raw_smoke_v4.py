#!/usr/bin/env python3
"""Full raw-base-table engine regression through deterministic calculation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT / "plugin/fixed-combustion-inventory/engine/workbook_engine.py"
PYTHON = ROOT / ".venv/bin/python"
RULE = ROOT / "rules/frozen/v1.0.1"
ADAPTER = ROOT / "method_package/input_adapter.json"
PREPARED = ROOT / "inputs/prepared"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("INDUSTRIAL", "POWER"), default="POWER")
    args = parser.parse_args()
    lock = json.loads((RULE / "rule_package_lock.json").read_text(encoding="utf-8"))
    raw_manifest = json.loads((PREPARED / "raw_input_manifest.json").read_text(encoding="utf-8"))
    inputs = []
    for entry in raw_manifest["workbooks"]:
        path = Path(entry["output_path"])
        inputs.append(
            {
                "tag": entry["source_tag"],
                "role": entry["role"],
                "path": str(path),
                "sheet": entry["sheet"],
                "sha256": file_hash(path),
            }
        )
    with tempfile.TemporaryDirectory(prefix="fixed-combustion-raw-smoke-") as temporary:
        run = Path(temporary)
        manifest = {
            "run_id": "engine-raw-smoke-v4",
            "experiment_method": "full",
            "scenario": "full",
            "target": args.target,
            "export_mode": "machine",
            "method_bundle_hash": "engine-raw-smoke-v4",
            "rule_package_path": str(RULE),
            "rule_package_hash": lock["package_hash"],
            "input_adapter_path": str(ADAPTER),
            "input_adapter_hash": file_hash(ADAPTER),
            "inputs": inputs,
        }
        (run / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run / "outputs").mkdir()
        stages = [
            "adapt_environmental_workbooks",
            "build_device_emission_sources",
            "generate_pollutant_calculation_items",
            "execute_tcses_calculation_rules",
            "control_calculation_admission",
            "calculate_fixed_combustion_emissions",
            "validate_and_export_fixed_combustion_inventory",
            "archive_complete_calculation_process",
        ]
        summaries = []
        for stage in stages:
            request = {
                "run_package_path": str(run),
                "run_id": manifest["run_id"],
                "scenario": manifest["scenario"],
                "method_bundle_hash": manifest["method_bundle_hash"],
                "stage": stage,
            }
            completed = subprocess.run(
                [str(PYTHON), str(ENGINE)],
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=1800,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr or completed.stdout)
            summaries.append(json.loads(completed.stdout))

        connection = sqlite3.connect(run / "run.sqlite3")
        decision_counts = dict(
            connection.execute(
                "SELECT decided_target,COUNT(*) FROM source_decisions GROUP BY decided_target"
            ).fetchall()
        )
        raw_count = connection.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0]
        included = connection.execute("SELECT COUNT(*) FROM device_sources").fetchone()[0]
        wrong_4412 = connection.execute(
            """
            SELECT COUNT(*) FROM device_sources d
            WHERE json_extract(d.payload_json,'$.industry_code')='4412'
              AND d.department<>'电力生产'
            """
        ).fetchone()[0]
        wrong_4412_nox = connection.execute(
            """
            SELECT COUNT(*)
            FROM calculation_items i
            JOIN device_sources d ON d.source_id=i.source_id
            JOIN rule_results r ON r.item_id=i.item_id
            WHERE json_extract(d.payload_json,'$.industry_code')='4412'
              AND i.pollutant='NOX'
              AND r.normalized_fuel='天然气'
              AND (r.parameter_id NOT LIKE 'D-EP-%' OR ABS(r.factor_value-4.1)>1e-12)
            """
        ).fetchone()[0]
        connection.close()

        decisions_path = run / "outputs/source_decisions.csv"
        items_path = run / "outputs/calculation_items.csv"
        totals_path = run / "outputs/source_pollutant_totals.csv"

        assert raw_count == 5922, raw_count
        assert decision_counts == {"EXCLUDE": 1261, "INDUSTRIAL": 4046, "POWER": 615}, decision_counts
        assert included == (615 if args.target == "POWER" else 4046), included
        assert wrong_4412 == 0, wrong_4412
        assert wrong_4412_nox == 0, wrong_4412_nox
        assert decisions_path.is_file() and sum(1 for _ in decisions_path.open(encoding="utf-8-sig")) == 5923
        assert items_path.is_file()
        assert totals_path.is_file()
        comparison_path = run / "evaluation/reference_comparison.json"
        comparison_path.parent.mkdir(parents=True, exist_ok=True)
        compared = subprocess.run(
            [
                str(PYTHON),
                str(ROOT / "reference/compare_run_to_reference.py"),
                str(run),
                "--reference-package",
                str(ROOT / "reference/frozen/v2.0.0"),
                "--output",
                str(comparison_path),
            ],
            text=True,
            capture_output=True,
            timeout=1800,
            check=False,
        )
        if compared.returncode:
            raise RuntimeError(compared.stderr or compared.stdout)
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        aggregate_path = ROOT / "evaluation/aggregate_experiment_results_v2.py"
        sys.path.insert(0, str(aggregate_path.parent))
        aggregate_spec = importlib.util.spec_from_file_location("raw_smoke_dne", aggregate_path)
        assert aggregate_spec and aggregate_spec.loader
        aggregate_module = importlib.util.module_from_spec(aggregate_spec)
        aggregate_spec.loader.exec_module(aggregate_module)
        helper = aggregate_module.load_reason_module(ROOT / "reference/frozen/v2.0.0")
        dne_score, dne_context = aggregate_module.evaluate_one(
            {
                "run_id": manifest["run_id"],
                "experiment": "A",
                "method": "full",
                "target": args.target,
                "input_variant": "B0",
                "scale_percent": 100,
                "repetition": 1,
            },
            run,
            ROOT / "reference/frozen/v2.0.0",
            helper,
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "raw_count": raw_count,
                    "decision_counts": decision_counts,
                    "included_count": included,
                    "wrong_4412_department": wrong_4412,
                    "wrong_4412_natural_gas_nox": wrong_4412_nox,
                    "reference_comparison": comparison,
                    "dne_score": dne_score,
                    "dne_failed_atoms": [
                        {"atom_id": atom.atom_id, "kind": atom.kind, "detail": atom.detail}
                        for atom in [*dne_context["d_atoms"], *dne_context["n_atoms"]]
                        if atom.passed is False
                    ],
                    "actual_exception_rows": list(
                        csv.DictReader(
                            (run / "outputs" / "最终异常清单.csv").open(
                                encoding="utf-8-sig", newline=""
                            )
                        )
                    ),
                    "stages": summaries,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
