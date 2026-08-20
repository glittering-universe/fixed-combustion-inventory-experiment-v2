#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${1:-${SCRIPT_DIR:h}}"
ROOT="${ROOT:A}"
VENV="$ROOT/.venv"
REQUIREMENTS="$ROOT/requirements.lock.txt"
BOOTSTRAP_PYTHON="${PYTHON_BOOTSTRAP:-python3}"

if [[ ! -f "$REQUIREMENTS" ]]; then
  print -u2 -r -- "缺少冻结依赖清单：$REQUIREMENTS"
  exit 2
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  command "$BOOTSTRAP_PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install \
  --disable-pip-version-check \
  --requirement "$REQUIREMENTS"

"$VENV/bin/python" - <<'PY'
import openpyxl, psutil, yaml, pypdf, xlsxwriter
print("项目虚拟环境已就绪")
PY
