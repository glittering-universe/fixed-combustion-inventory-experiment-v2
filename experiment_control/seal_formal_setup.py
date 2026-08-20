#!/usr/bin/env python3
"""Seal the raw-input/DNE-v2 formal setup before any experiment execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_setup_files(root: Path, *, allow_missing: bool = False) -> list[Path]:
    root = root.resolve()
    required = [
        root / "method_package" / "frozen_manifest.json",
        root / "matrix" / "frozen_experiment_matrix.json",
        root / "matrix" / "frozen_experiment_matrix.csv",
        root / "inputs" / "prepared" / "raw_input_manifest.json",
        root / "inputs" / "prepared" / "source_identity_index.json",
        root / "rules" / "frozen" / "v1.0.1" / "rule_package_lock.json",
        root / "evaluation" / "metric_spec_v2.json",
        root / "reference" / "frozen" / "v2.0.0" / "package_lock.json",
        root / "human_baseline" / "normalized_v2" / "normalization_index.json",
        root / "requirements.lock.txt",
        root / "reproduction" / "bootstrap_environment.sh",
    ]
    files = list(required)
    files.extend(sorted((root / "inputs" / "prepared").glob("*.xlsx")))
    for variant in ("S1", "S2", "S3", "S4", "B2"):
        variant_dir = root / "inputs" / "experiment_b" / variant
        files.extend(sorted(path for path in variant_dir.glob("*.xlsx") if not path.name.startswith(".")))
        files.extend(sorted(path for path in variant_dir.glob("*.json") if not path.name.startswith(".")))
    normalized = root / "human_baseline" / "normalized_v2"
    files.extend(sorted(path for path in normalized.rglob("*") if path.is_file()))
    originals = root / "human_baseline" / "original_packages"
    files.extend(sorted(originals.rglob("*.xlsx")))

    unique = sorted(set(files), key=lambda path: path.as_posix())
    missing = [path for path in unique if not path.is_file()]
    if missing and not allow_missing:
        raise FileNotFoundError(f"formal setup dependency is missing: {missing[0]}")
    return [path for path in unique if path.is_file()]


def build_lock(root: Path) -> dict[str, object]:
    files = collect_setup_files(root)
    return {
        "setup_version": "2.0.0",
        "profile": "fixed-combustion-inventory",
        "input_contract": "raw_base_tables_v2",
        "evaluation_contract": "DNE_v2",
        "reference_package": "reference/frozen/v2.0.0",
        "runtime_python": ".venv/bin/python",
        "files": [
            {"path": str(path.relative_to(root)), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in files
        ],
    }


def main() -> None:
    payload = build_lock(ROOT)
    destination = ROOT / "matrix" / "formal_setup_lock.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(destination),
        "file_count": len(payload["files"]),
        "sha256": sha256(destination),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
