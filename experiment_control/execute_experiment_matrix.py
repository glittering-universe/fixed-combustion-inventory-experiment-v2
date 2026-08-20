#!/usr/bin/env python3
"""Execute the frozen experiment matrix sequentially in isolated run packages."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from result_layout import result_leaf


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "matrix" / "frozen_experiment_matrix.json"
PREPARE = ROOT / "experiment_control" / "prepare_run.py"
RUN_AGENT = ROOT / "experiment_control" / "run_hermes_session.py"
RUN_DETERMINISTIC = ROOT / "experiment_control" / "run_stage_sequence.py"
PYTHON = ROOT / ".venv" / "bin" / "python"
HERMES_PYTHON = PYTHON


def append_state(record: dict[str, object]) -> None:
    path = ROOT / "matrix" / "execution_state.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=("A", "B", "C", "D"))
    parser.add_argument("--run-id")
    parser.add_argument("--method")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--continue-on-infrastructure-failure", action="store_true")
    args = parser.parse_args()
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))["rows"]
    selected = [row for row in matrix if row["runner"] != "user_supplied"]
    if args.experiment:
        selected = [row for row in selected if row["experiment"] == args.experiment]
    if args.run_id:
        selected = [row for row in selected if row["run_id"] == args.run_id]
    if args.method:
        selected = [row for row in selected if row["method"] == args.method]
    if args.limit is not None:
        selected = selected[:args.limit]

    console_dir = ROOT / "matrix" / "run_console"
    console_dir.mkdir(parents=True, exist_ok=True)
    for record in selected:
        run_id = record["run_id"]
        run_dir = result_leaf(ROOT, record)
        if (run_dir / "experiment_seal.json").is_file():
            append_state({"run_id": run_id, "status": "already_sealed"})
            continue
        if run_dir.exists():
            append_state({"run_id": run_id, "status": "blocked_existing_unsealed", "path": str(run_dir)})
            if args.continue_on_infrastructure_failure:
                continue
            raise RuntimeError(f"existing unsealed run directory: {run_dir}")
        prepare = [str(PYTHON), str(PREPARE), "--run-id", run_id, "--method", record["method"],
                   "--scenario", record["scenario"], "--target", record["target"],
                   "--scale", str(record["scale_percent"]), "--variant", record["input_variant"],
                   "--export-mode", "machine", "--output", str(run_dir)]
        prepared = subprocess.run(prepare, text=True, capture_output=True, check=False)
        if prepared.returncode:
            append_state({"run_id": run_id, "status": "prepare_failed", "stderr": prepared.stderr})
            if args.continue_on_infrastructure_failure:
                continue
            raise RuntimeError(f"run preparation failed for {run_id}: {prepared.stderr}")

        if record["runner"] == "deterministic":
            command = [str(HERMES_PYTHON), str(RUN_DETERMINISTIC), str(run_dir)]
        else:
            mode = "generic" if record["runner"] == "generic_agent" else (
                "full" if record["runner"] == "full_agent" else "ablation")
            title = f"实验{record['experiment']}｜{record['method']}｜{record['target']}｜{record['scale_percent']}%｜{record['input_variant']}"
            command = [str(HERMES_PYTHON), str(RUN_AGENT), str(run_dir), "--mode", mode, "--title", title]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        (console_dir / f"{run_id}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (console_dir / f"{run_id}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        sealed = (run_dir / "experiment_seal.json").is_file()
        status = "sealed" if sealed and completed.returncode == 0 else "sealed_task_failure" if sealed else "infrastructure_failure_unsealed"
        append_state({"run_id": run_id, "status": status,
                      "return_code": completed.returncode, "run_package_path": str(run_dir)})
        if not sealed and not args.continue_on_infrastructure_failure:
            raise RuntimeError(f"run did not produce a seal: {run_id}")

    missing = [record["run_id"] for record in selected if not (result_leaf(ROOT, record) / "experiment_seal.json").is_file()]
    if missing and not args.continue_on_infrastructure_failure:
        raise RuntimeError(f"selected matrix rows are not fully sealed: {missing[:10]}")


if __name__ == "__main__":
    main()
