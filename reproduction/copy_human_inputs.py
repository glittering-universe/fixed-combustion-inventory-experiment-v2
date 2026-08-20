#!/usr/bin/env python3
"""Materialize normalized expert-led v2 packages into the physical result tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


NORMALIZED_FILES = (
    "source_decisions.csv",
    "calculation_totals.csv",
    "exceptions.csv",
    "normalization_notes.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_human_packages(
    source_root: Path,
    target_root: Path,
    *,
    skip_existing_complete: bool = False,
) -> dict[str, Any]:
    """Copy derived human v2 files only; preserved original workbooks stay put."""
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    normalized_root = source_root / "human_baseline" / "normalized_v2"
    index_path = normalized_root / "normalization_index.json"
    if not index_path.is_file():
        raise FileNotFoundError(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("package_count") != 14 or not index.get("preserved_originals_unchanged"):
        raise RuntimeError("human baseline v2 index is incomplete or does not preserve originals")

    sys.path.insert(0, str(source_root / "experiment_control"))
    from result_layout import result_leaf  # noqa: PLC0415

    matrix = json.loads((source_root / "matrix" / "frozen_experiment_matrix.json").read_text(encoding="utf-8"))
    human_rows = [row for row in matrix["rows"] if row.get("runner") == "user_supplied"]
    packages_by_output = {str(item["output"]): item for item in index["packages"]}
    copied = []
    for row in human_rows:
        relative = result_leaf(source_root, row).relative_to(source_root)
        source_dir = normalized_root / relative
        package = packages_by_output.get(relative.as_posix())
        if package is None:
            raise RuntimeError(f"human v2 index lacks matrix package: {row['run_id']}")
        destination = result_leaf(target_root, row)
        destination.mkdir(parents=True, exist_ok=True)
        protected = [destination / name for name in (*NORMALIZED_FILES, "run_manifest.json", "experiment_seal.json")]
        protected.append(destination / "logs" / "execution_metrics.json")
        existing = [path for path in protected if path.exists()]
        if existing:
            if skip_existing_complete and all(path.is_file() for path in protected):
                copied.append({"run_id": row["run_id"], "destination": str(relative), "status": "existing_complete"})
                continue
            raise FileExistsError(f"refuses to overwrite existing human result files: {existing[0]}")
        for name in NORMALIZED_FILES:
            source = source_dir / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, destination / name)

        notes = json.loads((source_dir / "normalization_notes.json").read_text(encoding="utf-8"))
        timing = notes["timing"]
        manifest = {
            **row,
            "experiment_method": "expert_led",
            "input_contract": "raw_base_tables_v2",
            "human_normalization_contract": "human-baseline-normalization-v2.0.0",
            "user_supplied": True,
            "sealed": True,
            "seconds_per_record": timing["seconds_per_candidate_record"],
            "record_count": timing["candidate_record_count"],
            "timing_basis": timing["basis"],
            "preserved_original_workbook": package["workbook"],
            "preserved_original_workbook_sha256": index["original_workbook_hashes"][package["workbook"]],
        }
        _write_json(destination / "run_manifest.json", manifest)
        _write_json(destination / "logs" / "execution_metrics.json", {
            "run_id": row["run_id"],
            "status": "user_supplied_complete",
            "wall_seconds": timing["total_seconds"],
            "seconds_per_record": timing["seconds_per_candidate_record"],
            "record_count": timing["candidate_record_count"],
            "timing_basis": timing["basis"],
            "usage": {"api_calls": None, "cost_status": "not_applicable"},
        })
        metrics_path = destination / "logs" / "execution_metrics.json"
        sealed_files = [
            destination / name for name in NORMALIZED_FILES
        ] + [
            destination / "run_manifest.json",
            metrics_path,
        ]
        _write_json(destination / "experiment_seal.json", {
            "seal_version": "2.0.0",
            "run_id": row["run_id"],
            "method": "expert_led",
            "method_bundle_hash": row.get("method_bundle_hash"),
            "status": "sealed_user_supplied",
            "input_hashes": {},
            "execution_metrics_sha256": sha256(metrics_path),
            "files": [
                {
                    "path": str(path.relative_to(destination)),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path in sealed_files
            ],
            "preserved_original_workbook_sha256": index["original_workbook_hashes"][package["workbook"]],
        })
        copied.append({"run_id": row["run_id"], "destination": str(relative)})

    if len(copied) != 14:
        raise RuntimeError(f"expected 14 normalized human packages, copied {len(copied)}")
    return {
        "status": "ok",
        "human_runs": len(copied),
        "normalization_contract": "human-baseline-normalization-v2.0.0",
        "preserved_originals_unchanged": True,
        "packages": copied,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("target_root", type=Path)
    parser.add_argument("--skip-existing-complete", action="store_true")
    args = parser.parse_args()
    print(json.dumps(copy_human_packages(
        args.source_root,
        args.target_root,
        skip_existing_complete=args.skip_existing_complete,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
