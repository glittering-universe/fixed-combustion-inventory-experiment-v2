#!/usr/bin/env python3
"""Launch one isolated, Desktop-visible Hermes experiment session."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parents[1]
PROFILE = "fixed-combustion-inventory"
PROFILE_HOME = Path(f"/Users/wushuo/.hermes/profiles/{PROFILE}")
STATE_DB = PROFILE_HOME / "state.db"
HERMES_ALIAS = Path(f"/Users/wushuo/.local/bin/{PROFILE}")
def session_ids() -> set[str]:
    connection = sqlite3.connect(STATE_DB)
    try:
        try:
            return {row[0] for row in connection.execute("SELECT id FROM sessions")}
        except sqlite3.OperationalError:
            return set()
    finally:
        connection.close()


def session_row(session_id: str) -> dict[str, object]:
    connection = sqlite3.connect(STATE_DB)
    connection.row_factory = sqlite3.Row
    try:
        try:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        return dict(row) if row else {}
    finally:
        connection.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs(manifest: dict[str, object]) -> None:
    for spec in manifest["inputs"]:
        path = Path(spec["path"])
        if not path.is_file() or sha256(path) != spec["sha256"]:
            raise RuntimeError(f"input hash verification failed: {path}")
    for path_key, hash_key in (
        ("source_identity_index_path", "source_identity_index_hash"),
        ("standard_pdf_path", "standard_pdf_hash"),
    ):
        raw_path = manifest.get(path_key)
        if not raw_path:
            continue
        path = Path(str(raw_path))
        expected = manifest.get(hash_key)
        if not path.is_file() or (expected and sha256(path) != expected):
            raise RuntimeError(f"input hash verification failed: {path}")


def process_snapshot(pid: int) -> dict[str, float]:
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


def export_session(session_id: str, destination: Path) -> dict[str, object]:
    completed = subprocess.run(
        [str(HERMES_ALIAS), "sessions", "export", str(destination), "--format", "jsonl",
         "--session-id", session_id, "--redact", "--yes"],
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "path": str(destination),
        "return_code": completed.returncode,
        "sha256": sha256(destination) if destination.is_file() else None,
        "stderr": completed.stderr.strip(),
    }


def seal_experiment_run(run_dir: Path, manifest: dict[str, object], metrics_path: Path) -> Path:
    database = run_dir / "run.sqlite3"
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    files = []
    for relative_root in ("outputs", "scripts", "session_export", "trace", "logs"):
        root = run_dir / relative_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files.append({"path": str(path.relative_to(run_dir)), "sha256": sha256(path), "bytes": path.stat().st_size})
    for name in ("run_manifest.json", "sealed_manifest.json", "run.sqlite3"):
        path = run_dir / name
        if path.is_file():
            files.append({"path": name, "sha256": sha256(path), "bytes": path.stat().st_size})
    input_hashes = {str(Path(spec["path"]).relative_to(run_dir)): spec["sha256"] for spec in manifest["inputs"]}
    for path_key, hash_key in (
        ("source_identity_index_path", "source_identity_index_hash"),
        ("standard_pdf_path", "standard_pdf_hash"),
    ):
        raw_path = manifest.get(path_key)
        if raw_path:
            path = Path(str(raw_path))
            input_hashes[str(path.relative_to(run_dir))] = str(manifest.get(hash_key) or sha256(path))
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
    parser.add_argument("--mode", choices=("full", "generic", "ablation"), required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    run_dir = args.run_package.resolve()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    prompt_name = {
        "full": "full_prompt.md",
        "generic": "generic_agent_prompt.md",
        "ablation": "ablation_prompt.md",
    }[args.mode]
    prompt_file = ROOT / "method_package" / prompt_name
    task = prompt_file.read_text(encoding="utf-8") + "\n\n本次运行信息：\n" + json.dumps(
        {
            "run_id": manifest["run_id"],
            "run_package_path": str(run_dir),
            "scenario": manifest["scenario"],
            "method_bundle_hash": manifest["method_bundle_hash"],
            "candidate_scope": (
                "实验C的中性候选子集已在run_manifest.json中冻结；工具调用时省略candidate_ids"
                if manifest.get("candidate_ids")
                else "本运行处理全部原始基102候选，不提供目标成员名单"
            ),
            "target": manifest["target"],
            "scale_percent": manifest["scale_percent"],
            "input_variant": manifest["input_variant"],
        },
        ensure_ascii=False,
        indent=2,
    )
    usage_path = run_dir / "logs" / "hermes_usage.json"
    stdout_path = run_dir / "logs" / "hermes_final.txt"
    stderr_path = run_dir / "logs" / "hermes_stderr.txt"
    verify_inputs(manifest)
    before_ids = session_ids()
    started = time.perf_counter()
    command = [
        str(HERMES_ALIAS),
        "--oneshot",
        task,
        "--usage-file",
        str(usage_path),
        "--model",
        "deepseek-v4-pro",
        "--provider",
        "custom",
        "--reasoning",
        "high",
        "--accept-hooks",
    ]
    if args.mode == "full":
        command.extend(["-t", "fixed_combustion_inventory,skills"])
    elif args.mode == "generic":
        command.extend(["-t", "terminal,file,code_execution,skills"])
    else:
        command.extend(["-t", "fixed_combustion_inventory,skills"])
    maxima = {"rss": 0.0, "user": 0.0, "system": 0.0, "read": 0.0, "write": 0.0}
    execution_environment = dict(os.environ)
    execution_environment["FIXED_COMBUSTION_PYTHON"] = str(ROOT / ".venv" / "bin" / "python")
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=str(run_dir),
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=execution_environment,
        )
        deadline = started + 10800
        while process.poll() is None:
            snapshot = process_snapshot(process.pid)
            for key in maxima:
                maxima[key] = max(maxima[key], snapshot[key])
            if time.perf_counter() > deadline:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise TimeoutError(f"Hermes run exceeded 10800 seconds: {manifest['run_id']}")
            time.sleep(0.5)
        return_code = process.returncode
    elapsed = time.perf_counter() - started
    verify_inputs(manifest)
    after_ids = session_ids()
    new_ids = sorted(after_ids - before_ids)
    if len(new_ids) != 1:
        raise RuntimeError(f"expected exactly one new Hermes session, observed {len(new_ids)}")
    session_id = new_ids[0]
    subprocess.run([str(HERMES_ALIAS), "sessions", "rename", session_id, args.title], text=True, capture_output=True, check=False)
    session_export = export_session(session_id, run_dir / "session_export" / "session.jsonl")
    session_export_path = Path(str(session_export.get("path") or ""))
    if session_export.get("return_code") != 0 or not session_export_path.is_file() or session_export_path.stat().st_size == 0:
        raise RuntimeError("Hermes session export failed; run is not sealable")
    usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.is_file() else {}
    output_bytes = sum(path.stat().st_size for path in (run_dir / "outputs").rglob("*") if path.is_file())
    input_artifact_bytes = sum(Path(spec["path"]).stat().st_size for spec in manifest["inputs"])
    if manifest.get("standard_pdf_path"):
        input_artifact_bytes += Path(manifest["standard_pdf_path"]).stat().st_size
    io_supported = bool(maxima["read"] or maxima["write"])
    session_metadata = session_row(session_id) if session_id else {}
    metrics = {
        "return_code": return_code,
        "wall_seconds": elapsed,
        "child_user_cpu_seconds": maxima["user"],
        "child_system_cpu_seconds": maxima["system"],
        "peak_rss_bytes": int(maxima["rss"]),
        "read_bytes": int(maxima["read"]) if io_supported else None,
        "write_bytes": int(maxima["write"]) if io_supported else None,
        "io_byte_counter_supported": io_supported,
        "input_artifact_bytes": input_artifact_bytes,
        "output_bytes": output_bytes,
        "session_id": session_id,
        "session": session_metadata,
        "session_export": session_export,
        "usage": usage,
        "api_requests": usage.get("api_calls") or session_metadata.get("api_call_count"),
        "tool_calls": session_metadata.get("tool_call_count"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "final_response_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    metrics_path = run_dir / "logs" / "execution_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal_path = seal_experiment_run(run_dir, manifest, metrics_path)
    print(json.dumps({"run_id": manifest["run_id"], "session_id": session_id, "return_code": return_code, "metrics_path": str(metrics_path), "experiment_seal_path": str(seal_path)}, ensure_ascii=False))
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
