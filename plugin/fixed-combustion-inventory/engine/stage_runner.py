"""Dispatch a batch stage to the deterministic workbook engine."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_PROJECT_PYTHON = _PLUGIN_DIR.parents[1] / ".venv" / "bin" / "python"
_DEFAULT_PYTHON = Path(
    "/Users/wushuo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
)
_RUNNER = _PLUGIN_DIR / "engine" / "workbook_engine.py"


def run(stage_name: str, args: dict[str, Any]) -> dict[str, Any]:
    run_path = Path(str(args.get("run_package_path", ""))).expanduser().resolve()
    if not run_path.is_dir():
        raise ValueError(f"run package does not exist: {run_path}")
    default_python = _PROJECT_PYTHON if _PROJECT_PYTHON.is_file() else _DEFAULT_PYTHON
    python = Path(os.environ.get("FIXED_COMBUSTION_PYTHON", str(default_python)))
    if not python.is_file():
        raise RuntimeError(f"workbook runtime unavailable: {python}")
    request = dict(args)
    request["stage"] = stage_name
    request["run_package_path"] = str(run_path)
    completed = subprocess.run(
        [str(python), str(_RUNNER)],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise RuntimeError(detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid workbook-engine response: {completed.stdout[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("workbook-engine response is not an object")
    return payload
