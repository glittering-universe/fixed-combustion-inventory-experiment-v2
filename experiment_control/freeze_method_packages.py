#!/usr/bin/env python3
"""Freeze reproducible method-package manifests without copying secrets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METHOD_DIR = ROOT / "method_package"
PLUGIN_DIR = ROOT / "plugin" / "fixed-combustion-inventory"
RULE_DIR = ROOT / "rules" / "frozen" / "v1.0.1"
BASELINE_DIR = ROOT / "baseline" / "simple_deterministic"
HUMAN_DIR = ROOT / "human_baseline"
PROFILE_DIR = Path("/Users/wushuo/.hermes/profiles/fixed-combustion-inventory")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and "__pycache__" not in path.parts
        and not any(part.startswith("smoke_run") for part in path.parts)
        and ".inspect" not in path.name
    )


def entry(path: Path, label_root: Path) -> dict[str, str]:
    return {
        "path": str(path.relative_to(label_root)),
        "sha256": sha256(path),
    }


def canonical_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> None:
    plugin_files = selected_files(PLUGIN_DIR, (".py", ".mjs", ".md", ".yaml", ".json"))
    rule_files = selected_files(RULE_DIR, (".yaml", ".yml", ".csv", ".json", ".jsonl", ".md"))
    baseline_files = selected_files(BASELINE_DIR, (".py", ".csv", ".json", ".md"))
    human_files = [
        HUMAN_DIR / "README.md",
        HUMAN_DIR / "normalize_human_v2.py",
        HUMAN_DIR / "normalized_v2" / "normalization_index.json",
    ]
    method_files = selected_files(METHOD_DIR, (".md", ".json"))
    profile_files = [PROFILE_DIR / "config.yaml", PROFILE_DIR / "SOUL.md", PROFILE_DIR / "profile.yaml"]

    inventory = {
        "package_version": "2.0.0",
        "input_contract": "raw_base_tables_v2",
        "evaluation_contract": "DNE_v2",
        "hermes_profile": "fixed-combustion-inventory",
        "model": "deepseek-v4-pro",
        "reasoning_effort": "high",
        "plugin": [entry(path, PLUGIN_DIR) for path in plugin_files],
        "rules": [entry(path, RULE_DIR) for path in rule_files],
        "deterministic_baseline": [entry(path, BASELINE_DIR) for path in baseline_files],
        "expert_baseline": [entry(path, HUMAN_DIR) for path in human_files if path.is_file()],
        "method_files": [entry(path, METHOD_DIR) for path in method_files if path.name != "frozen_manifest.json"],
        "profile": [entry(path, PROFILE_DIR) for path in profile_files if path.is_file()],
    }
    common_hash = canonical_hash(inventory)
    harness_hash = canonical_hash({
        "package_version": "2.0.0",
        "hermes_profile": "fixed-combustion-inventory",
        "model": "deepseek-v4-pro",
        "reasoning_effort": "high",
        "profile": inventory["profile"],
    })
    specialized_hash = canonical_hash({
        "plugin": inventory["plugin"],
        "rules": inventory["rules"],
        "input_adapter": sha256(METHOD_DIR / "input_adapter.json"),
    })
    baseline_hash = canonical_hash({
        "definition": "fixed_mapping_plus_fixed_lookup_plus_direct_calculation",
        "files": inventory["deterministic_baseline"],
    })
    expert_hash = canonical_hash({
        "definition": "existing_expert_led_inventory_compilation",
        "files": inventory["expert_baseline"],
        "candidate_seconds_per_record": 11.52,
        "candidate_record_count_per_target_task": 5922,
        "timing_basis": "extrapolated_from_user_supplied_per_candidate_rate",
    })
    scenarios = json.loads((METHOD_DIR / "scenarios.json").read_text(encoding="utf-8"))
    methods = {
        "deterministic_program": canonical_hash({"baseline": baseline_hash, "orchestrator": "single_direct_script"}),
        "generic_tool_agent": canonical_hash({"harness": harness_hash, "prompt": sha256(METHOD_DIR / "generic_agent_prompt.md"), "specialized_plugin": False}),
        "full": canonical_hash({"harness": harness_hash, "specialized": specialized_hash, "prompt": sha256(METHOD_DIR / "full_prompt.md"), "scenario": scenarios["full"]}),
        "expert_led": canonical_hash({
            "expert_baseline": expert_hash,
            "normalization": "read_only_preserved_workbook_to_raw_candidate_evaluation_contract_v2",
        }),
    }
    for scenario, assembly in scenarios.items():
        if scenario == "full":
            continue
        methods[scenario] = canonical_hash({"harness": harness_hash, "specialized": specialized_hash,
                                            "prompt": sha256(METHOD_DIR / "ablation_prompt.md"), "scenario": assembly})
    manifest = {**inventory, "common_hash": common_hash, "harness_hash": harness_hash,
                "specialized_hash": specialized_hash, "deterministic_baseline_hash": baseline_hash,
                "expert_baseline_hash": expert_hash,
                "method_hashes": methods}
    output = METHOD_DIR / "frozen_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "common_hash": common_hash, "method_hashes": methods}, ensure_ascii=False))


if __name__ == "__main__":
    main()
