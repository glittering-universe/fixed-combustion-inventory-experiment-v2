#!/usr/bin/env python3
"""Canonical physical layout for experiment run packages and summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TARGET_NAMES = {
    "INDUSTRIAL": "01_工业锅炉",
    "POWER": "02_火电热力",
}

METHOD_NAMES = {
    "full": "01_Full完整方法",
    "deterministic_program": "02_确定性程序",
    "generic_tool_agent": "03_通用工具调用型Agent",
    "expert_led": "04_既有专家主导型",
}

VARIANT_NAMES = {
    "B0": "00_B0原始输入",
    "S1": "01_S1表头形式扰动",
    "S2": "02_S2等价别名扰动",
    "S3": "03_S3列与工作表顺序扰动",
    "S4": "04_S4组合扰动",
    "B2": "05_B2受控缺失与冲突注入",
}

ABLATION_NAMES = {
    "full": "00_Full参照",
    "w_o_calculation_item_structure": "01_去掉污染物核算项结构",
    "w_o_executable_rule_gate": "02_去掉可执行规则门控",
    "agent_managed_admission": "03_Agent管理核算准入",
    "w_o_complete_trace": "04_去掉完整核算过程记录",
}

EXPERIMENT_NAMES = {
    "A": "02_实验A_端到端清单编制",
    "B": "03_实验B_输入不变性与异常处置",
    "C": "04_实验C_规模与资源表现",
    "D": "05_实验D_方法组成贡献",
}


def results_root(root: Path) -> Path:
    return root / "实验结果"


def summary_dir(root: Path) -> Path:
    return results_root(root) / "00_总汇总与索引"


def pilot_dir(root: Path) -> Path:
    return results_root(root) / "90_试运行与开发验证"


def result_leaf(root: Path, row: dict[str, Any]) -> Path:
    """Return the only physical storage path for a matrix row."""
    destination = results_root(root)
    experiment = row["experiment"]
    target = TARGET_NAMES[row["target"]]
    method = row["method"]
    repetition = row.get("repetition")
    run_label = f"R{repetition}" if repetition is not None else "结果"

    if experiment == "A":
        return destination / EXPERIMENT_NAMES[experiment] / target / METHOD_NAMES[method] / run_label
    if experiment == "B":
        if row["input_variant"] == "B2":
            return (
                destination / EXPERIMENT_NAMES[experiment] / "02_B2受控缺失与冲突注入"
                / target / METHOD_NAMES[method] / run_label
            )
        return (
            destination / EXPERIMENT_NAMES[experiment] / "01_B1整体输入扰动"
            / VARIANT_NAMES[row["input_variant"]] / target / METHOD_NAMES[method] / run_label
        )
    if experiment == "C":
        scale = f"{int(row['scale_percent']):03d}%规模"
        return destination / EXPERIMENT_NAMES[experiment] / scale / target / METHOD_NAMES[method] / run_label
    if experiment == "D":
        return destination / EXPERIMENT_NAMES[experiment] / ABLATION_NAMES[method] / target / "R1"
    raise ValueError(f"Unsupported experiment: {experiment}")


def load_matrix(root: Path) -> dict[str, Any]:
    path = root / "matrix" / "frozen_experiment_matrix.json"
    return json.loads(path.read_text(encoding="utf-8"))


def matrix_row(root: Path, run_id: str) -> dict[str, Any]:
    for row in load_matrix(root)["rows"]:
        if row["run_id"] == run_id:
            return row
    raise KeyError(f"Unknown run_id: {run_id}")


def run_dir(root: Path, run_id: str) -> Path:
    return result_leaf(root, matrix_row(root, run_id))
