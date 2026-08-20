#!/usr/bin/env python3
"""Create one isolated fixed-combustion experiment run package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METHOD_MANIFEST = ROOT / "method_package" / "frozen_manifest.json"
RULE_PATH = ROOT / "rules" / "frozen" / "v1.0.1"
RULE_LOCK = RULE_PATH / "rule_package_lock.json"
INPUT_ADAPTER = ROOT / "method_package" / "input_adapter.json"
DETERMINISTIC_BASELINE = ROOT / "baseline" / "simple_deterministic"
STANDARD_PDF = ROOT.parent / "1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


RAW_INPUTS = (
    ("B102-2002", "device_fuel_base", "基102-2002_原始结构脱敏输入.xlsx"),
    ("B101-2001", "enterprise_master", "基101-2001_原始结构脱敏输入_分片01-of04.xlsx"),
    ("B101-2001", "enterprise_master", "基101-2001_原始结构脱敏输入_分片02-of04.xlsx"),
    ("B101-2001", "enterprise_master", "基101-2001_原始结构脱敏输入_分片03-of04.xlsx"),
    ("B101-2001", "enterprise_master", "基101-2001_原始结构脱敏输入_分片04-of04.xlsx"),
    ("B101-2002", "control_facility_base", "基101-2002_原始结构脱敏输入.xlsx"),
)


def input_directory(variant: str, experiment: str, scale: int) -> Path:
    if experiment == "C":
        return ROOT / "inputs" / "scales" / str(scale)
    return ROOT / "inputs" / "prepared" if variant == "B0" else ROOT / "inputs" / "experiment_b" / variant


def source_inputs(variant: str, experiment: str = "", scale: int = 100) -> list[dict[str, str]]:
    variant_dir = input_directory(variant, experiment, scale)
    result = []
    role_totals: dict[tuple[str, str], int] = {}
    for tag, role, _ in RAW_INPUTS:
        role_totals[(tag, role)] = role_totals.get((tag, role), 0) + 1
    role_seen: dict[tuple[str, str], int] = {}
    for tag, role, filename in RAW_INPUTS:
        path = variant_dir / filename
        key = (tag, role)
        role_seen[key] = role_seen.get(key, 0) + 1
        result.append({
            "tag": tag,
            "role": role,
            "shard_index": role_seen[key],
            "shard_count": role_totals[key],
            "path": str(path),
            "sheet": "Sheet1",
            "schema_mode": "raw_structure",
            "sha256": sha256(path),
        })
    return result


def identity_index_path(variant: str, experiment: str = "", scale: int = 100) -> Path:
    if experiment == "C":
        return ROOT / "inputs" / "scales" / str(scale) / "source_identity_index.json"
    variant_path = ROOT / "inputs" / "experiment_b" / variant / "source_identity_index.json"
    if variant != "B0" and variant_path.is_file():
        return variant_path
    return ROOT / "inputs" / "prepared" / "source_identity_index.json"


def neutral_candidate_ids(index_path: Path, scale: int) -> list[str]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    candidates = [str(value) for value in index.get("candidate_ids", [])]
    if len(candidates) != len(set(candidates)):
        raise ValueError("source identity index contains duplicate candidate ids")
    if not candidates:
        raise ValueError("source identity index contains no candidate ids")
    ordered = sorted(
        candidates,
        key=lambda value: hashlib.sha256(f"fixed-combustion-neutral-scale-v2|{value}".encode("utf-8")).hexdigest(),
    )
    count = (len(ordered) * scale + 99) // 100
    return ordered[:count]


def isolated_read_only_copy(source: Path, destination: Path) -> None:
    """Materialize a run-local copy so one method cannot mutate frozen inputs."""

    shutil.copy2(source, destination)
    destination.chmod(0o444)


def materialize_inputs(
    run_dir: Path,
    variant: str,
    include_standard: bool,
    experiment: str,
    scale: int,
) -> tuple[list[dict[str, str]], str | None, dict[str, str]]:
    input_dir = run_dir / "inputs"
    input_dir.mkdir()
    localized = []
    for spec in source_inputs(variant, experiment, scale):
        source = Path(spec["path"])
        destination = input_dir / source.name
        isolated_read_only_copy(source, destination)
        localized.append({**spec, "path": str(destination), "sha256": sha256(destination)})
    identity_source = identity_index_path(variant, experiment, scale)
    identity_destination = input_dir / "source_identity_index.json"
    isolated_read_only_copy(identity_source, identity_destination)
    identity_spec = {
        "path": str(identity_destination),
        "sha256": sha256(identity_destination),
        "identity_contract": "source_identity_v1",
    }
    standard_path = None
    if include_standard:
        destination = input_dir / "T_CSES_144-2024.pdf"
        isolated_read_only_copy(STANDARD_PDF, destination)
        standard_path = str(destination)
    return localized, standard_path, identity_spec


def prepare_run_package(args: argparse.Namespace) -> dict[str, object]:
    method_manifest = json.loads(METHOD_MANIFEST.read_text(encoding="utf-8"))
    methods = method_manifest["method_hashes"]
    if args.method not in methods:
        raise SystemExit(f"unknown method: {args.method}")
    lock = json.loads(RULE_LOCK.read_text(encoding="utf-8"))
    run_dir = (args.output or (ROOT / "runs" / args.run_id)).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "outputs").mkdir()
    (run_dir / "logs").mkdir()
    (run_dir / "session_export").mkdir()
    experiment = str(getattr(args, "experiment", "") or str(args.run_id).split("-", 1)[0]).upper()
    input_specs, standard_path, identity_spec = materialize_inputs(
        run_dir,
        args.variant,
        args.method == "generic_tool_agent",
        experiment,
        int(args.scale),
    )
    manifest: dict[str, object] = {
        "run_id": args.run_id,
        "experiment_method": args.method,
        "scenario": args.scenario,
        "target": args.target,
        "scale_percent": int(args.scale),
        "input_variant": args.variant,
        "input_contract": "raw_base_tables_v2",
        "export_mode": args.export_mode,
        "method_bundle_hash": methods[args.method],
        "inputs": input_specs,
        "source_identity_index_path": identity_spec["path"],
        "source_identity_index_hash": identity_spec["sha256"],
        "source_identity_contract": identity_spec["identity_contract"],
        "profile": "fixed-combustion-inventory",
        "model": "deepseek-v4-pro",
        "reasoning_effort": "high",
        "sealed": False,
    }
    if experiment == "C":
        manifest["candidate_ids"] = neutral_candidate_ids(
            identity_index_path(args.variant, experiment, int(args.scale)),
            100,
        )
        manifest["candidate_selection"] = "target_neutral_nested_hash_sample_v2"
    if args.method == "generic_tool_agent":
        manifest["standard_pdf_path"] = standard_path
        manifest["standard_pdf_hash"] = sha256(Path(str(standard_path)))
        manifest["input_boundary"] = "only run_manifest.json and files under this run package"
    elif args.method == "deterministic_program":
        manifest.update({
            "deterministic_baseline_path": str(DETERMINISTIC_BASELINE),
            "deterministic_baseline_hash": method_manifest["deterministic_baseline_hash"],
            "baseline_definition": "fixed_mapping_plus_fixed_lookup_plus_direct_calculation",
        })
    else:
        manifest.update({
            "rule_package_path": str(RULE_PATH),
            "rule_package_hash": lock["package_hash"],
            "input_adapter_path": str(INPUT_ADAPTER),
            "input_adapter_hash": sha256(INPUT_ADAPTER),
        })
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--target", choices=("INDUSTRIAL", "POWER"), required=True)
    parser.add_argument("--scale", choices=("25", "50", "75", "100"), default="100")
    parser.add_argument("--variant", default="B0")
    parser.add_argument("--export-mode", choices=("machine", "presentation"), default="machine")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = prepare_run_package(args)
    run_dir = (args.output or (ROOT / "runs" / args.run_id)).resolve()
    print(json.dumps({"run_package_path": str(run_dir), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
