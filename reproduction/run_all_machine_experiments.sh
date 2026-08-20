#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
SOURCE_ROOT="${SCRIPT_DIR:h}"

usage() {
  print -r -- "用法："
  print -r -- "  $0 --resume"
  print -r -- "  $0 --fresh-dir /绝对路径/新的复现实验目录"
}

if [[ $# -eq 1 && "$1" == "--resume" ]]; then
  ROOT="$SOURCE_ROOT"
elif [[ $# -eq 2 && "$1" == "--fresh-dir" ]]; then
  ROOT="${2:A}"
  if [[ -e "$ROOT" && -n "$(find "$ROOT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    print -u2 -r -- "目标目录不是空目录，已停止：$ROOT"
    exit 2
  fi
  mkdir -p "$ROOT"
  rsync -a \
    --exclude '/.git/' \
    --exclude '/.venv/' \
    --exclude '/实验结果/' \
    --exclude '/matrix/execution_state.jsonl' \
    --exclude '/matrix/run_console/' \
    --exclude '/matrix/controller_logs/' \
    --exclude '/documentation/rendered/' \
    --exclude '/documentation/实验记录.docx' \
    "$SOURCE_ROOT/" "$ROOT/"
  mkdir -p "$ROOT/实验结果/00_总汇总与索引" "$ROOT/matrix/run_console"

  STANDARD_NAME="1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf"
  SOURCE_STANDARD="${SOURCE_ROOT:h}/$STANDARD_NAME"
  TARGET_STANDARD="${ROOT:h}/$STANDARD_NAME"
  if [[ ! -f "$SOURCE_STANDARD" ]]; then
    print -u2 -r -- "缺少标准原文，已停止：$SOURCE_STANDARD"
    exit 2
  fi
  if [[ -f "$TARGET_STANDARD" ]]; then
    if ! cmp -s "$SOURCE_STANDARD" "$TARGET_STANDARD"; then
      print -u2 -r -- "目标目录上级存在同名但不同内容的标准文件，已停止：$TARGET_STANDARD"
      exit 2
    fi
  else
    cp "$SOURCE_STANDARD" "$TARGET_STANDARD"
  fi
else
  usage
  exit 2
fi

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  "$ROOT/reproduction/bootstrap_environment.sh" "$ROOT"
fi

"$PYTHON" "$ROOT/reproduction/check_environment.py" --root "$ROOT"
"$PYTHON" "$ROOT/reproduction/copy_human_inputs.py" "$SOURCE_ROOT" "$ROOT" --skip-existing-complete

for experiment in A B C D; do
  "$PYTHON" "$ROOT/experiment_control/execute_experiment_matrix.py" --experiment "$experiment"
done

"$PYTHON" "$ROOT/evaluation/aggregate_experiment_results_v2.py" \
  --reference-package "$ROOT/reference/frozen/v2.0.0"
print -r -- "复现实验运行完毕：$ROOT"
