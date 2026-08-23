#!/usr/bin/env python3
"""Index the post-experiment DNE-v2.1 evaluation without duplicating run data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_DIR = ROOT / "实验结果/00_总汇总与索引"
SUMMARY = SUMMARY_DIR / "aggregate_summary_v2_1.json"
RUN_SCORES = SUMMARY_DIR / "run_scores_v2_1.csv"
METRIC_SPEC = ROOT / "evaluation/metric_spec_v2_1.json"
METRIC_README = ROOT / "evaluation/README_DNE_V2_1.md"
REPRODUCTION_MANIFEST = ROOT / "reproduction/reproduction_manifest.json"
OUTPUT = ROOT / "archives/formal-evaluation-v2.1.0"
CURRENT_EVALUATION = SUMMARY_DIR / "CURRENT_EVALUATION.md"
REPORTS = (
    "experiment_B1_v2_1.json",
    "experiment_B2_v2_1.json",
    "experiment_C_v2_1.json",
    "experiment_D_v2_1.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def main() -> None:
    summary = load_json(SUMMARY)
    if (
        summary.get("evaluation_version") != "2.1.0"
        or summary.get("sealed_runs_evaluated") != 150
        or summary.get("pending_runs") != 0
    ):
        raise RuntimeError("formal evaluation v2.1 is incomplete")
    metric = load_json(METRIC_SPEC)
    if metric.get("revision_type") != "post-experiment_protocol_revision":
        raise RuntimeError("metric revision provenance is missing")

    files = [
        file_record(SUMMARY),
        file_record(RUN_SCORES),
        file_record(METRIC_SPEC),
        file_record(METRIC_README),
        file_record(CURRENT_EVALUATION),
        file_record(OUTPUT / "README.md"),
        file_record(OUTPUT / "VALIDATION.md"),
    ]
    files.extend(file_record(SUMMARY_DIR / name) for name in REPORTS)
    files.append(file_record(REPRODUCTION_MANIFEST))
    payload = {
        "archive_version": "formal-evaluation-v2.1.0",
        "git_tag": "formal-evaluation-v2.1.0",
        "status": "formal_complete",
        "revision_type": "post_experiment_evaluation_protocol_revision",
        "supersedes_evaluation_version": "2.0.0",
        "formal_runs_reexecuted": False,
        "sealed_runs_reused": 150,
        "source_experiment_tag": "formal-experiment-v2.0.0",
        "source_experiment_release": (
            "https://github.com/glittering-universe/"
            "fixed-combustion-inventory-experiment-v2/releases/tag/formal-experiment-v2.0.0"
        ),
        "files": files,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "evaluation_revision_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
