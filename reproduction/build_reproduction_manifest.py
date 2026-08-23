#!/usr/bin/env python3
"""Hash every dependency of the raw-input/DNE-v2.1 reproduction entrypoints."""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reproduction" / "reproduction_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


FILES = [
    "requirements.lock.txt",
    "reproduction/bootstrap_environment.sh",
    "reproduction/README.md",
    "reproduction/check_environment.py",
    "reproduction/copy_human_inputs.py",
    "reproduction/run_all_machine_experiments.sh",
    "reproduction/run_one_machine_experiment.sh",
    "reproduction/aggregate_results.sh",
    "reproduction/build_evaluation_revision_index.py",
    "reproduction/build_formal_archive_index.py",
    "reproduction/build_reproduction_manifest.py",
    "reproduction/tests/test_reproduction_v2.py",
    "method_package/full_prompt.md",
    "method_package/generic_agent_prompt.md",
    "method_package/ablation_prompt.md",
    "method_package/input_adapter.json",
    "method_package/scenarios.json",
    "experiment_control/source_identity.py",
    "experiment_control/prepare_raw_inputs.py",
    "experiment_control/write_deidentified_inputs.mjs",
    "experiment_control/generate_b_variants.py",
    "experiment_control/generate_b2_injections.py",
    "experiment_control/generate_scale_inputs.py",
    "experiment_control/validate_input_variants.py",
    "experiment_control/prepare_run.py",
    "experiment_control/run_hermes_session.py",
    "experiment_control/execute_experiment_matrix.py",
    "experiment_control/result_layout.py",
    "experiment_control/run_stage_sequence.py",
    "experiment_control/freeze_method_packages.py",
    "experiment_control/build_experiment_matrix.py",
    "experiment_control/seal_formal_setup.py",
    "inputs/prepared/raw_input_manifest.json",
    "inputs/prepared/source_identity_index.json",
    "human_baseline/README.md",
    "human_baseline/normalize_human_v2.py",
    "human_baseline/normalized_v2/normalization_index.json",
    "evaluation/metric_spec_v2.json",
    "evaluation/metric_spec_v2_1.json",
    "evaluation/score_dne.py",
    "evaluation/aggregate_experiment_results_v2.py",
    "evaluation/README.md",
    "evaluation/README_DNE_V2.md",
    "evaluation/README_DNE_V2_1.md",
    "evaluation/evaluate_sealed_run.py",
    "evaluation/normalize_run_output.py",
    "evaluation/compare_normalized_runs.py",
    "evaluation/tests/test_dne_scoring.py",
    "evaluation/tests/test_dne_aggregation.py",
    "evaluation/tests/test_normalize_generic_output.py",
    "evaluation/tests/test_seal_boundary_v2.py",
    "evaluation/tests/test_specialized_normalization.py",
    "baseline/simple_deterministic/README.md",
    "baseline/simple_deterministic/simple_inventory.py",
    "baseline/simple_deterministic/lookups/fixed_fuel_mapping.csv",
    "baseline/simple_deterministic/lookups/fixed_combustion_mapping.csv",
    "baseline/simple_deterministic/lookups/fixed_control_keywords.csv",
    "baseline/simple_deterministic/lookups/fixed_industrial_factors.csv",
    "baseline/simple_deterministic/lookups/fixed_power_factors.csv",
    "baseline/simple_deterministic/lookups/fixed_coal_parameters.csv",
    "baseline/simple_deterministic/tests/test_simple_inventory.py",
    "reference/build_reference_package.py",
    "reference/finalize_reference_package.py",
    "reference/validate_reference_package.py",
    "reference/compare_run_to_reference.py",
    "reference/tests/test_reference_v2.py",
    "reference/tests/test_reference_comparator_v2.py",
    "experiment_control/tests/test_input_variants_v2.py",
    "experiment_control/tests/test_raw_input_packages.py",
    "human_baseline/tests/test_normalize_human_v2.py",
    "plugin/fixed-combustion-inventory/tests/test_anomaly_audit_regressions.py",
    "plugin/fixed-combustion-inventory/tests/test_source_domain.py",
    "reference/frozen/v2.0.0/package_lock.json",
    "rules/frozen/v1.0.1/rule_package_lock.json",
    "matrix/frozen_experiment_matrix.json",
    "matrix/frozen_experiment_matrix.csv",
    "matrix/formal_setup_lock.json",
    "method_package/frozen_manifest.json",
]

EXTERNAL_FILES = ["1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf"]


def build_payload(root: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    root = root.resolve()
    entries = []
    missing = []
    for relative in FILES:
        path = root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        entries.append({"path": relative, "sha256": sha256(path), "bytes": path.stat().st_size})
    external = []
    for name in EXTERNAL_FILES:
        path = root.parent / name
        if not path.is_file():
            missing.append(f"../{name}")
            continue
        external.append({"path": f"../{name}", "sha256": sha256(path), "bytes": path.stat().st_size})
    if missing and not allow_missing:
        raise FileNotFoundError(f"reproduction dependency is missing: {missing[0]}")
    return {
        "manifest_version": "2.1.0",
        "purpose": "fixed-combustion raw-input A-D runs with post-experiment DNE-v2.1 evaluation",
        "input_contract": "raw_base_tables_v2",
        "evaluation_contract": "DNE_v2.1",
        "formal_setup_evaluation_contract": "DNE_v2",
        "reference_package": "reference/frozen/v2.0.0",
        "runtime": {
            "python": "./.venv/bin/python",
            "python_version": platform.python_version(),
            "environment": "repository_local_virtual_environment",
        },
        "files": entries,
        "external_files": external,
        "missing_files": missing,
    }


def main() -> None:
    payload = build_payload(ROOT)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT),
        "file_count": len(payload["files"]),
        "sha256": sha256(OUTPUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
