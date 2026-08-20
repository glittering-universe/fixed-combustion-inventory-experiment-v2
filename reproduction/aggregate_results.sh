#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${1:-${SCRIPT_DIR:h}}"
ROOT="${ROOT:A}"
PYTHON="$ROOT/.venv/bin/python"

"$PYTHON" "$ROOT/reproduction/copy_human_inputs.py" "$ROOT" "$ROOT" --skip-existing-complete
"$PYTHON" "$ROOT/evaluation/aggregate_experiment_results_v2.py" \
  --reference-package "$ROOT/reference/frozen/v2.0.0"
print -r -- "汇总结果：$ROOT/实验结果/00_总汇总与索引"
