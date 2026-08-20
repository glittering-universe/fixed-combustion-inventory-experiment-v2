#!/usr/bin/env python3
"""Run the fixed-mapping deterministic baseline outside every Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
BASELINE = ROOT / "baseline" / "simple_deterministic" / "simple_inventory.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(pid: int) -> dict[str, float]:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return {"rss": 0, "user": 0, "system": 0, "read": 0, "write": 0}
    totals = {"rss": 0.0, "user": 0.0, "system": 0.0, "read": 0.0, "write": 0.0}
    for process in processes:
        try:
            totals["rss"] += process.memory_info().rss
            cpu = process.cpu_times()
            totals["user"] += cpu.user
            totals["system"] += cpu.system
            io = process.io_counters()
            totals["read"] += io.read_bytes
            totals["write"] += io.write_bytes
        except (psutil.Error, AttributeError):
            continue
    return totals


def verify_inputs(manifest: dict[str, object]) -> None:
    for spec in manifest["inputs"]:
        path = Path(spec["path"])
        if not path.is_file() or sha256(path) != spec["sha256"]:
            raise RuntimeError(f"input hash verification failed: {path}")
    identity_path = manifest.get("source_identity_index_path")
    identity_hash = manifest.get("source_identity_index_hash")
    if identity_path:
        path = Path(str(identity_path))
        if not path.is_file() or (identity_hash and sha256(path) != identity_hash):
            raise RuntimeError(f"input hash verification failed: {path}")


def seal_experiment(run_dir: Path, manifest: dict[str, object], metrics_path: Path) -> Path:
    files = []
    for relative_root in ("outputs", "session_export", "logs"):
        root = run_dir / relative_root
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            if path.is_file():
                files.append({"path": str(path.relative_to(run_dir)), "sha256": sha256(path), "bytes": path.stat().st_size})
    for name in ("run_manifest.json",):
        path = run_dir / name
        if path.is_file():
            files.append({"path": name, "sha256": sha256(path), "bytes": path.stat().st_size})
    input_hashes = {
        str(Path(spec["path"]).relative_to(run_dir)): spec["sha256"]
        for spec in manifest["inputs"]
    }
    identity_path = manifest.get("source_identity_index_path")
    if identity_path:
        path = Path(str(identity_path))
        input_hashes[str(path.relative_to(run_dir))] = str(manifest.get("source_identity_index_hash") or sha256(path))
    seal = {
        "seal_version": "2.0.0",
        "run_id": manifest["run_id"],
        "method_bundle_hash": manifest["method_bundle_hash"],
        "input_hashes": input_hashes,
        "execution_metrics_sha256": sha256(metrics_path),
        "files": files,
    }
    destination = run_dir / "experiment_seal.json"
    destination.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_package", type=Path)
    args = parser.parse_args()
    run_dir = args.run_package.resolve()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("experiment_method") != "deterministic_program":
        raise SystemExit("runner only accepts deterministic_program")
    verify_inputs(manifest)
    started = time.perf_counter()
    maxima = {"rss": 0.0, "user": 0.0, "system": 0.0, "read": 0.0, "write": 0.0}
    process = subprocess.Popen(
        [str(PYTHON), str(BASELINE), str(run_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.perf_counter() + 3600
    while process.poll() is None:
        current = snapshot(process.pid)
        for key in maxima:
            maxima[key] = max(maxima[key], current[key])
        if time.perf_counter() > deadline:
            process.terminate()
            raise TimeoutError("simple deterministic baseline exceeded 3600 seconds")
        time.sleep(0.1)
    assert process.stdout is not None and process.stderr is not None
    stdout = process.stdout.read()
    stderr = process.stderr.read()
    if process.returncode:
        raise RuntimeError(stderr.strip() or stdout.strip())
    verify_inputs(manifest)
    baseline_result = json.loads(stdout)
    output_bytes = sum(path.stat().st_size for path in (run_dir / "outputs").rglob("*") if path.is_file())
    input_artifact_bytes = sum(Path(spec["path"]).stat().st_size for spec in manifest["inputs"])
    io_supported = bool(maxima["read"] or maxima["write"])
    metrics = {
        "wall_seconds": time.perf_counter() - started,
        "child_user_cpu_seconds": maxima["user"],
        "child_system_cpu_seconds": maxima["system"],
        "peak_rss_bytes": int(maxima["rss"]),
        "read_bytes": int(maxima["read"]) if io_supported else None,
        "write_bytes": int(maxima["write"]) if io_supported else None,
        "io_byte_counter_supported": io_supported,
        "input_artifact_bytes": input_artifact_bytes,
        "output_bytes": output_bytes,
        "api_requests": None,
        "input_tokens": None,
        "output_tokens": None,
        "stage_results": [{
            "stage": "fixed_mapping_lookup_direct_calculation",
            "status": "completed",
            "result": baseline_result,
        }],
    }
    metrics_path = run_dir / "logs" / "execution_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal_path = seal_experiment(run_dir, manifest, metrics_path)
    print(json.dumps({"run_id": manifest["run_id"], "metrics": metrics, "experiment_seal_path": str(seal_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
