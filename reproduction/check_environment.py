#!/usr/bin/env python3
"""Verify the frozen raw-input/DNE-v2 environment without changing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


RETIRED_DEPENDENCIES = {
    "inputs/prepared/scope_catalog.json": "retired scope membership dependency",
    "evaluation/final_time_thresholds.json": "retired absolute time threshold dependency",
    "evaluation/aggregate_experiment_results.py": "retired six-dimension aggregate evaluator",
    "evaluation/normalize_human_outputs.py": "retired human placeholder normalizer",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def contract_errors(manifest: dict[str, Any]) -> list[str]:
    errors = []
    if manifest.get("manifest_version") != "2.0.0":
        errors.append("reproduction manifest is not version 2.0.0")
    if manifest.get("input_contract") != "raw_base_tables_v2":
        errors.append("input contract is not raw_base_tables_v2")
    if manifest.get("evaluation_contract") != "DNE_v2":
        errors.append("evaluation contract is not DNE_v2")
    paths = {str(item.get("path") or "") for item in manifest.get("files", [])}
    for retired, description in RETIRED_DEPENDENCIES.items():
        if retired in paths:
            errors.append(description)
    if "inputs/prepared/raw_input_manifest.json" not in paths:
        errors.append("raw input manifest is not frozen")
    if "evaluation/aggregate_experiment_results_v2.py" not in paths:
        errors.append("DNE v2 aggregate evaluator is not frozen")
    return errors


def inspect_environment(root: Path) -> dict[str, Any]:
    root = root.resolve()
    required = {
        "formal setup lock": root / "matrix" / "formal_setup_lock.json",
        "experiment matrix": root / "matrix" / "frozen_experiment_matrix.json",
        "method manifest": root / "method_package" / "frozen_manifest.json",
        "reproduction manifest": root / "reproduction" / "reproduction_manifest.json",
        "raw input manifest": root / "inputs" / "prepared" / "raw_input_manifest.json",
        "source identity index": root / "inputs" / "prepared" / "source_identity_index.json",
        "DNE metric specification": root / "evaluation" / "metric_spec_v2.json",
        "reference v2 lock": root / "reference" / "frozen" / "v2.0.0" / "package_lock.json",
        "human v2 index": root / "human_baseline" / "normalized_v2" / "normalization_index.json",
    }
    errors = [f"missing required file: {label}: {path}" for label, path in required.items() if not path.is_file()]
    checks: list[dict[str, Any]] = []
    if errors:
        return {"status": "failed", "root": str(root), "errors": errors, "checks": checks}

    lock = load_json(required["formal setup lock"])
    reproduction = load_json(required["reproduction manifest"])
    matrix = load_json(required["experiment matrix"])
    method = load_json(required["method manifest"])
    errors.extend(contract_errors(reproduction))
    for field, expected in (
        ("setup_version", "2.0.0"),
        ("input_contract", "raw_base_tables_v2"),
        ("evaluation_contract", "DNE_v2"),
        ("reference_package", "reference/frozen/v2.0.0"),
        ("runtime_python", ".venv/bin/python"),
    ):
        if lock.get(field) != expected:
            errors.append(f"formal setup {field} is {lock.get(field)!r}, expected {expected!r}")

    for item in lock.get("files", []):
        path = root / item["path"]
        passed = path.is_file() and sha256(path) == item["sha256"]
        checks.append({"name": f"setup:{item['path']}", "status": "pass" if passed else "fail"})
        if not passed:
            errors.append(f"formal setup hash mismatch: {item['path']}")
    for item in reproduction.get("files", []):
        path = root / item["path"]
        passed = path.is_file() and sha256(path) == item["sha256"]
        checks.append({"name": f"reproduction:{item['path']}", "status": "pass" if passed else "fail"})
        if not passed:
            errors.append(f"reproduction dependency mismatch: {item['path']}")
    for item in reproduction.get("external_files", []):
        path = (root / item["path"]).resolve()
        passed = path.is_file() and sha256(path) == item["sha256"]
        checks.append({"name": f"external:{item['path']}", "path": str(path), "status": "pass" if passed else "fail"})
        if not passed:
            errors.append(f"external dependency mismatch: {item['path']}")
    if reproduction.get("missing_files"):
        errors.append(f"reproduction manifest contains missing files: {reproduction['missing_files'][:3]}")

    rows = matrix.get("rows", [])
    machine = [row for row in rows if row.get("runner") != "user_supplied"]
    human = [row for row in rows if row.get("runner") == "user_supplied"]
    if matrix.get("matrix_version") != "2.0.0" or matrix.get("input_contract") != "raw_base_tables_v2":
        errors.append("experiment matrix is not the raw-input v2 matrix")
    if len(machine) != 136 or len(human) != 14:
        errors.append(f"matrix counts are machine={len(machine)}, human={len(human)}; expected 136/14")
    if any("scope_ids" in row for row in rows):
        errors.append("matrix still contains target scope_ids")

    raw = load_json(required["raw input manifest"])
    raw_entries = raw.get("workbooks", [])
    if raw.get("input_form") != "three_raw_environmental_base_tables" or len(raw_entries) != 6:
        errors.append("raw input manifest must contain six workbook artifacts from three base tables")
    for item in raw_entries:
        filename = Path(str(item.get("output_path") or "")).name
        path = root / "inputs" / "prepared" / filename
        passed = path.is_file() and sha256(path) == item.get("output_sha256")
        checks.append({"name": f"raw:{filename}", "status": "pass" if passed else "fail"})
        if not passed:
            errors.append(f"raw workbook mismatch: {filename}")
    identity = load_json(required["source identity index"])
    candidate_ids = [str(value) for value in identity.get("candidate_ids", [])]
    if len(candidate_ids) != 5922 or len(candidate_ids) != len(set(candidate_ids)):
        errors.append("source identity index does not contain 5,922 unique candidate IDs")

    for variant in ("S1", "S2", "S3", "S4", "B2"):
        variant_dir = root / "inputs" / "experiment_b" / variant
        workbooks = list(variant_dir.glob("*.xlsx"))
        if len(workbooks) != 6:
            errors.append(f"variant {variant} has {len(workbooks)} workbooks, expected 6")
        if not (variant_dir / "source_identity_index.json").is_file():
            errors.append(f"variant {variant} lacks source_identity_index.json")
        if not (variant_dir / "variant_manifest.json").is_file():
            errors.append(f"variant {variant} lacks variant_manifest.json")
        if variant == "B2" and not (variant_dir / "injection_manifest.json").is_file():
            errors.append("variant B2 lacks injection_manifest.json")

    human_index = load_json(required["human v2 index"])
    if human_index.get("package_count") != 14 or not human_index.get("preserved_originals_unchanged"):
        errors.append("human baseline v2 index is incomplete")
    original_root = root / "human_baseline" / "original_packages"
    for relative, expected in human_index.get("original_workbook_hashes", {}).items():
        path = original_root / relative
        if not path.is_file() or sha256(path) != expected:
            errors.append(f"preserved human original mismatch: {relative}")

    metric = load_json(required["DNE metric specification"])
    if metric.get("version") != "2.0.0" or metric.get("independent_quality_gate") is not False:
        errors.append("DNE metric specification is not the approved v2 no-gate contract")

    reference_dir = root / "reference" / "frozen" / "v2.0.0"
    reference_lock = load_json(required["reference v2 lock"])
    if reference_lock.get("version") != "2.0.0" or reference_lock.get("status") != "audited_frozen":
        errors.append("reference package v2 is not audited_frozen")
    for relative, expected in reference_lock.get("files", {}).items():
        path = reference_dir / relative
        if not path.is_file() or sha256(path) != expected.get("sha256"):
            errors.append(f"reference v2 file mismatch: {relative}")

    for entry in method.get("method_files", []):
        path = root / "method_package" / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            errors.append(f"method package file mismatch: {entry['path']}")
    profile_name = lock.get("profile", "fixed-combustion-inventory")
    home = Path(os.path.expanduser("~"))
    profile_home = home / ".hermes" / "profiles" / profile_name
    runtime = {
        "repository Python": root / ".venv" / "bin" / "python",
        "Hermes profile": profile_home / "profile.yaml",
        "Hermes session database": profile_home / "state.db",
        "Hermes launcher": home / ".local" / "bin" / profile_name,
        "Hermes Python": home / ".hermes" / "hermes-agent" / "venv" / "bin" / "python",
        "installed experiment Plugin": profile_home / "plugins" / "fixed-combustion-inventory",
    }
    for label, path in runtime.items():
        passed = path.exists()
        checks.append({"name": label, "path": str(path), "status": "pass" if passed else "fail"})
        if not passed:
            errors.append(f"missing runtime dependency: {label}: {path}")
    repository_python = runtime["repository Python"]
    if repository_python.is_file():
        probe = subprocess.run(
            [str(repository_python), "-c", "import openpyxl,psutil,yaml; print('ok')"],
            text=True, capture_output=True, check=False,
        )
        if probe.returncode or probe.stdout.strip() != "ok":
            errors.append("repository .venv cannot import openpyxl, psutil and yaml")

    installed_plugin = runtime["installed experiment Plugin"]
    for entry in method.get("plugin", []):
        local = root / "plugin" / "fixed-combustion-inventory" / entry["path"]
        installed = installed_plugin / entry["path"]
        if not local.is_file() or sha256(local) != entry["sha256"]:
            errors.append(f"local Plugin mismatch: {entry['path']}")
        if not installed.is_file() or sha256(installed) != entry["sha256"]:
            errors.append(f"installed Plugin mismatch: {entry['path']}")
    for entry in method.get("rules", []):
        path = root / "rules" / "frozen" / "v1.0.1" / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            errors.append(f"frozen rule mismatch: {entry['path']}")
    for entry in method.get("deterministic_baseline", []):
        path = root / "baseline" / "simple_deterministic" / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            errors.append(f"deterministic baseline mismatch: {entry['path']}")

    return {
        "status": "pass" if not errors else "failed",
        "root": str(root),
        "profile": profile_name,
        "model": method.get("model"),
        "reasoning_effort": method.get("reasoning_effort"),
        "input_contract": "raw_base_tables_v2",
        "evaluation_contract": "DNE_v2",
        "reference_package": "reference/frozen/v2.0.0",
        "machine_matrix_rows": len(machine),
        "human_runs": len(human),
        "candidate_sources": len(candidate_ids),
        "errors": errors,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = inspect_environment(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
