#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
DEFAULT_ROOT="${SCRIPT_DIR:h}"

if [[ $# -lt 1 || $# -gt 2 ]]; then
  print -u2 -r -- "用法：$0 RUN_ID [实验根目录]"
  exit 2
fi

RUN_ID="$1"
ROOT="${2:-$DEFAULT_ROOT}"
ROOT="${ROOT:A}"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  "$ROOT/reproduction/bootstrap_environment.sh" "$ROOT"
fi

"$PYTHON" "$ROOT/reproduction/check_environment.py" --root "$ROOT"
"$PYTHON" "$ROOT/experiment_control/execute_experiment_matrix.py" --run-id "$RUN_ID"
print -r -- "单次运行请求结束：$RUN_ID"
