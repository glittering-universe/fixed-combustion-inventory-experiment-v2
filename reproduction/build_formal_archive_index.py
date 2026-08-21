#!/usr/bin/env python3
"""Build the lightweight index for the completed formal v2 experiment."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiment_control.result_layout import result_leaf


MATRIX = ROOT / "matrix/frozen_experiment_matrix.json"
SUMMARY = ROOT / "实验结果/00_总汇总与索引/aggregate_summary_v2.json"
RUN_SCORES = ROOT / "实验结果/00_总汇总与索引/run_scores_v2.csv"
REPRODUCTION_MANIFEST = ROOT / "reproduction/reproduction_manifest.json"
OUTPUT = ROOT / "archives/formal-experiment-v2.0.0"
AGENT_RUNNERS = {"generic_agent", "full_agent", "ablation_agent"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    matrix = load_json(MATRIX)
    summary = load_json(SUMMARY)
    rows = sorted(matrix["rows"], key=lambda item: int(item["sequence"]))
    if len(rows) != 150 or summary.get("sealed_runs_evaluated") != 150:
        raise RuntimeError("formal archive index requires a complete 150-run evaluation")

    run_rows: list[dict[str, Any]] = []
    session_rows: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    for row in rows:
        run_dir = result_leaf(ROOT, row)
        manifest_path = run_dir / "run_manifest.json"
        seal_path = run_dir / "experiment_seal.json"
        metrics_path = run_dir / "logs/execution_metrics.json"
        for required in (manifest_path, seal_path, metrics_path):
            if not required.is_file():
                raise FileNotFoundError(f"missing formal artifact: {required}")

        session_id = ""
        session_path = run_dir / "session_export/session.jsonl"
        if row.get("runner") in AGENT_RUNNERS:
            if not session_path.is_file():
                raise FileNotFoundError(f"missing Agent session export: {session_path}")
            session = load_json(session_path)
            session_id = str(session.get("id") or "")
            if not session_id or session_id in session_ids:
                raise RuntimeError(f"missing or duplicate formal session id: {row['run_id']}")
            session_ids.add(session_id)
            session_rows.append({
                "run_id": row["run_id"],
                "session_id": session_id,
                "method": row["method"],
                "target": row["target"],
                "session_export": str(session_path.relative_to(ROOT)),
                "session_export_sha256": sha256(session_path),
            })

        run_rows.append({
            "sequence": row["sequence"],
            "experiment": row["experiment"],
            "run_id": row["run_id"],
            "method": row["method"],
            "target": row["target"],
            "input_variant": row["input_variant"],
            "scale_percent": row.get("scale_percent"),
            "repetition": row.get("repetition"),
            "runner": row["runner"],
            "run_directory": str(run_dir.relative_to(ROOT)),
            "experiment_seal_sha256": sha256(seal_path),
            "run_manifest_sha256": sha256(manifest_path),
            "execution_metrics_sha256": sha256(metrics_path),
            "session_id": session_id,
        })

    if len(session_rows) != 94:
        raise RuntimeError(f"expected 94 formal Agent sessions, found {len(session_rows)}")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(
        OUTPUT / "formal_run_index.csv",
        [
            "sequence", "experiment", "run_id", "method", "target", "input_variant",
            "scale_percent", "repetition", "runner", "run_directory",
            "experiment_seal_sha256", "run_manifest_sha256", "execution_metrics_sha256",
            "session_id",
        ],
        run_rows,
    )
    write_csv(
        OUTPUT / "hermes_session_index.csv",
        ["run_id", "session_id", "method", "target", "session_export", "session_export_sha256"],
        session_rows,
    )
    archive_index = {
        "archive_version": "formal-experiment-v2.0.0",
        "git_tag": "formal-experiment-v2.0.0",
        "status": "formal_complete",
        "matrix_rows": len(rows),
        "machine_runs": matrix["machine_runs"],
        "human_runs": matrix["human_runs"],
        "agent_sessions": len(session_rows),
        "experiments": dict(sorted(Counter(row["experiment"] for row in rows).items())),
        "methods": dict(sorted(Counter(row["method"] for row in rows).items())),
        "aggregate_summary": str(SUMMARY.relative_to(ROOT)),
        "aggregate_summary_sha256": sha256(SUMMARY),
        "run_scores": str(RUN_SCORES.relative_to(ROOT)),
        "run_scores_sha256": sha256(RUN_SCORES),
        "reproduction_manifest": str(REPRODUCTION_MANIFEST.relative_to(ROOT)),
        "reproduction_manifest_sha256": sha256(REPRODUCTION_MANIFEST),
        "physical_result_delivery": "GitHub Release assets; see ARCHIVE_README.md and SHA256SUMS in the release",
    }
    (OUTPUT / "formal_archive_index.json").write_text(
        json.dumps(archive_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(archive_index, ensure_ascii=False))


if __name__ == "__main__":
    main()
