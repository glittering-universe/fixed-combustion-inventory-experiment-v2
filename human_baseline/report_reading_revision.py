"""Report a human-reading correction separately from experimental observations."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "实验结果" / "00_总汇总与索引"
AUDIT = ROOT / "human_baseline" / "reading_audit_v2_1"
NORMALIZED = ROOT / "human_baseline" / "normalized_v2_1"
OLD = "v2_2"
NEW = "v2_2_human_v2_1"
METHODS = {
    "full": "Full",
    "deterministic_program": "简单确定性脚本",
    "generic_tool_agent": "通用工具Agent",
    "expert_led": "既有专家主导型（历史业务流程对照）",
}
TARGETS = {"INDUSTRIAL": "工业锅炉", "POWER": "火电热力"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path, values):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    audit = load(AUDIT / "verification_report.json")
    assert audit["status"] == "pass", "Original-cell reconciliation must pass."
    old_summary = load(SUMMARY / f"aggregate_summary_{OLD}.json")
    new_summary = load(SUMMARY / f"aggregate_summary_{NEW}.json")
    old_runs = {row["run_id"]: row for row in rows(SUMMARY / f"run_scores_{OLD}.csv")}
    new_runs = {row["run_id"]: row for row in rows(SUMMARY / f"run_scores_{NEW}.csv")}
    assert old_runs.keys() == new_runs.keys()
    machine_ids = [key for key, row in new_runs.items() if row["method"] != "expert_led"]
    human_ids = [key for key, row in new_runs.items() if row["method"] == "expert_led"]
    changed_machine = [key for key in machine_ids if old_runs[key] != new_runs[key]]
    assert not changed_machine, changed_machine
    fixed_human_fields = ["D", "D_regular", "D_exception", "E", "seconds_per_record", "record_count", "N_applicable", "N_NA"]
    changed_nonreading_fields = [
        f"{key}:{field}" for key in human_ids for field in fixed_human_fields
        if old_runs[key][field] != new_runs[key][field]
    ]
    assert not changed_nonreading_fields, changed_nonreading_fields
    comparison = []
    for method, name in METHODS.items():
        previous = old_summary["experiment_A_EICPI_core"]["by_method_macro_and_micro"][method]["macro"]
        current = new_summary["experiment_A_EICPI_core"]["by_method_macro_and_micro"][method]["macro"]
        comparison.append({
            "method": method, "method_name": name,
            "comparison_status": "historical_control_task_conditions_unconfirmed" if method == "expert_led" else "current_machine_task",
            "D_before": previous["D"], "D_after": current["D"],
            "N_before": previous["N"], "N_after": current["N"],
            "E_before": previous["E"], "E_after": current["E"],
            "EICPI_core_before": previous["EICPI_core_0_100"], "EICPI_core_after": current["EICPI_core_0_100"],
            "E_basis": "existing_relative_time_anchors_preserved; human_11.52_seconds_per_record_measurement_scope_unconfirmed",
        })
    write_csv(AUDIT / "experiment_A_comparison.csv", comparison)
    human_comparison = [{
        "run_id": key, "experiment": new_runs[key]["experiment"], "target": new_runs[key]["target"],
        "variant": new_runs[key]["input_variant"],
        "status": "historical_control_pending_task_evidence" if new_runs[key]["experiment"] == "A" else "legacy_not_current_raw_v2_human_trial",
        **{f"{field}_{version}": record[key][field]
           for field in ("D", "N", "N_passed", "N_applicable", "E", "EICPI_core_0_100", "seconds_per_record")
           for version, record in (("before", old_runs), ("after", new_runs))},
    } for key in human_ids]
    write_csv(AUDIT / "human_run_comparison.csv", human_comparison)

    index = load(NORMALIZED / "normalization_index.json")
    originals = Path(index["preserved_original_root"])
    raw_manifest = load(ROOT / "inputs" / "prepared" / "raw_input_manifest.json")
    current_raw_hashes = {w["source_sha256"] for w in raw_manifest["workbooks"]}
    conditions = []
    for package in index["packages"]:
        if "02_实验A_" not in package["output"]:
            continue
        original_manifest_path = originals / package["output"] / "run_manifest.json"
        original_manifest = load(original_manifest_path)
        notes = load(NORMALIZED / package["output"] / "normalization_notes.json")
        decisions = rows(NORMALIZED / package["output"] / "source_decisions.csv")
        included = [row for row in decisions if row["human_scope_decision"] == "include"]
        conditions.append({
            "target": package["target"], "inventory_years": sorted({row["inventory_year"] for row in included}),
            "submitted_source_count": len(included),
            "current_evaluation_candidate_count": notes["candidate_source_count"],
            "original_registered_scope_count": len(original_manifest.get("scope_ids", [])),
            "source_mapping_methods": dict(Counter(row["mapping_method"] for row in included)),
            "original_manifest_path": str(original_manifest_path.relative_to(ROOT)),
            "original_manifest_inputs": original_manifest.get("inputs", []),
            "adapter_raw_hashes_match_current_machine_source_files": {v["sha256"] for v in notes["raw_inputs"].values()} == current_raw_hashes,
            "conclusion": "Year and recoverable record lineage are consistent; original human input bundle and technical-task requirements are unverified. Preserve as a historical workflow control pending task evidence.",
            "timing": notes["timing"],
        })
    write_json(AUDIT / "experiment_A_condition_audit.json", conditions)
    checks = {
        "status": "pass", "scoring_version": new_summary["evaluation_version"],
        "normalization_contract": index["schema_version"],
        "sealed_runs_reevaluated": len(new_runs), "machine_run_count": len(machine_ids),
        "all_machine_score_rows_unchanged": not changed_machine,
        "human_package_count": len(human_ids),
        "human_D_E_time_scope_and_N_denominators_unchanged": not changed_nonreading_fields,
        "original_cell_verification": audit,
        "old_score_csv_sha256": digest(SUMMARY / f"run_scores_{OLD}.csv"),
        "new_score_csv_sha256": digest(SUMMARY / f"run_scores_{NEW}.csv"),
        "normalization_index_sha256": digest(NORMALIZED / "normalization_index.json"),
        "scoring_implementation_sha256": digest(ROOT / "evaluation" / "score_dne.py"),
        "aggregation_implementation_sha256": digest(ROOT / "evaluation" / "aggregate_experiment_results_v2.py"),
        "machine_or_human_experiments_rerun": False,
    }
    write_json(AUDIT / "evaluation_revision_checks.json", checks)
    score_lines = []
    for target, target_name in TARGETS.items():
        key = f"expert_led|{target}"
        before = old_summary["experiment_A_EICPI_core"]["by_method_target"][key]
        after = new_summary["experiment_A_EICPI_core"]["by_method_target"][key]
        score_lines.append(f"| {target_name} | {100*before['N']['score']:.2f}% | {100*after['N']['score']:.2f}% | {before['EICPI_core_0_100']:.2f} | {after['EICPI_core_0_100']:.2f} |")
    human = next(row for row in comparison if row["method"] == "expert_led")
    score_lines.append(f"| 两类等权汇总 | {100*human['N_before']:.2f}% | {100*human['N_after']:.2f}% | {human['EICPI_core_before']:.2f} | {human['EICPI_core_after']:.2f} |")
    method_lines = [f"| {row['method_name']} | {100*row['D_after']:.2f}% | {100*row['N_after']:.2f}% | {100*row['E_after']:.2f}% | {row['EICPI_core_after']:.2f} |" for row in comparison]
    text = f"""# 人工结果读取与评价修正报告

## 修正结果

原人工工作簿保持不变，修正的是程序对产生量和最终排放量的读取。14份原表逐单元复核通过；重新评价150个封存结果，其中136个机器结果的全部评分表字段保持一致。没有重新开展人工核算、调用Hermes或重跑机器实验。

读取与规范化7项回归测试、评价61项测试全部通过，包括表头换序、空白与零、原始行映射、旧缓存保留，以及删除必需错误结果不能提高N的测试。

读取器原先按`SO₂、NOx、CO、VOCs、PM₁₀、PM₂.₅、BC、OC、NH₃`套列，实际原表顺序为`SO₂、NOx、CO、PM₁₀、PM₂.₅、BC、OC、VOCs、NH₃`。因此PM₁₀、PM₂.₅、BC、OC、VOCs五种污染物的两类结果被串列。现在逐个识别原始表头，并以治理字段分隔产生量与最终排放量区块；区块内调整列序也能读取。

| 污染物 | 工业产生量 | 工业排放量 | 火电热力产生量 | 火电热力排放量 |
|---|---|---|---|---|
| SO₂ | BX | CN | CA | CQ |
| NOx | BY | CO | CB | CR |
| CO | BZ | CP | CC | CS |
| PM₁₀ | CA | CQ | CD | CT |
| PM₂.₅ | CB | CR | CE | CU |
| BC | CC | CS | CF | CV |
| OC | CD | CT | CG | CW |
| VOCs | CE | CU | CH | CX |
| NH₃ | CF | CV | CI | CY |

这些列号是原表的独立审计对照，不是读取器的固定取列规则。

## 原表与导出核对

共核对{audit['workbook_count']}份工作簿、{audit['column_checks']}个结果列、{audit['source_cell_checks']:,}个单元格；数值与空白不一致数为{audit['mismatched_cells']}，全部列合计一致，原始有效行与导出的“原行号×污染物”一一对应。此次更正使{audit['changed_generation_or_emission_values']:,}个已导出值回到其正确污染物列；这是读取解释修正，不是修改原人工答案。该数包含不同实验包中的重复交付，不表示独立人工错误或独立源记录数。

工业锅炉仍保留4,160条、火电热力仍保留426条原人工纳入决定；旧B2火电工作簿的15条附加观察行也原样保留。原工作簿、旧规范化文件、原封存及其既有过程材料均保留。新文件另存于`human_baseline/normalized_v2_1/`，保留原Excel行号、工作表、原始列名、单元格位置、原值和空白/数值类型。

新增加的单元格定位是本次读取程序的追溯记录，不计为人工当时已记录的核算过程。原人工未提供的参数选择、规则引用或中间步骤仍按现行评价定义处理，没有事后补写。

## 使用修复后的N重新评价

指标仍为D/N/E v2.2.0：应输出对象和PM配对检查由参照确定，缺失必需数值计失败。与修正前比较时使用同一版评分函数、同一参照包及相同的D、E规则。14个人工包的N分母、D、E、源范围和原耗时全部保持一致。

| 原人工结果 | 修正前N | 修正后N | 修正前EICPI | 修正后EICPI |
|---|---:|---:|---:|---:|
{chr(10).join(score_lines)}

实验A两类源等权汇总如下。人工一行是历史业务流程的诊断对照，任务同条件尚未证实；不能据此直接声称严格同条件的人工优劣。

| 方法 | D | N | E | EICPI_core |
|---|---:|---:|---:|---:|
{chr(10).join(method_lines)}

D和N表示与冻结规则参照实现的一致程度，不是与独立实测排放真值的误差。本次没有修正规则包或参照包，已有领域审计问题仍须另外处理。

## 实验A任务条件

核算年份已确认一致：工业4,160条、火电热力426条均为2022年；全部纳入记录可追溯至基102记录。规范化适配器所用原始基表哈希与当前机器输入的来源哈希一致，但这只能证明当前对齐的数据来源，不能代替“当时人工实际收到哪些文件”的证据。

工业4,160条及火电422条通过原始特征签名对应；火电另4条采用既有回退匹配。逐条核查显示，人工Excel第418—421行对应基102第5873—5876行，差别仅为燃料二“其他燃料”附加了人工判断文字，其他用于对齐的特征一致。该人工判断文字及对应结果均保留。

原人工交付清单的`inputs`只登记了完成后的目标工作簿，未登记其当时使用的原始输入包和技术任务说明。旧登记范围为4,025/367条，当前机器任务为每个源类从5,922个候选设备开始；旧登记数量本身也不足以推断人工实际任务范围。因此现阶段按历史业务流程对照保留，并将同条件认定列为待确认。若后续提供当时输入和任务要求，确认条件相同，应直接使用保留的原答案；筛选差异和计算差异继续作为实验表现，不能据参照改写答案。

## 计时与尚缺材料

保留用户给出的`11.52秒/条`。现有文件未说明实际计时的样本数、“条”的对象是候选设备还是纳入设备，以及是否覆盖读取、筛选、核算和复核。旧登记按4,025/367条外推为46,368/4,227.84秒；当前评价沿用按5,922个候选源外推的68,221.44秒/目标任务。以上总耗时都是按单条率推算，不是新测得的全任务耗时；本次没有改动这些评价输入。E沿用既有相对时间锚点，故人工E=0是归一化端点，不表示实际效率为零。全表E及总分仍需结合计时口径的这一限制解释。

只需补充与拟报告结论对应的材料：

- 实验A：当时人工使用的输入文件/可核验来源，以及核算年份、范围和技术要求的任务说明。现有答案保留。
- 新B1：若报告人工在S1—S4原始基表扰动上的表现，需要真实人员处理对应输入并交付答案，能够对应到`inputs/experiment_b/S1—S4/variant_manifest.json`中的输入版本。现有旧人工包保留，不能仅凭重新规范化认定其已完成新任务。
- 新B2：若报告人工在当前缺失/冲突注入上的表现，需要真实人员处理当前B2输入并交付答案、实际处置记录；输入对应`inputs/experiment_b/B2/variant_manifest.json`。现有旧B2答案不填充为这次实验结果。
- 若报告人工过程可复核性或效率，需要当时实际留下的过程材料及计时样本、起止边界。没有记录的部分如实记缺失；本次新增读取轨迹不充作原人工轨迹。

另外生成的`expert_workflow/`数据不纳入本次原人工交付评价。当前B1/B2汇总继续保留旧人工包不可比标记。

## 文件与复现

- `column_reconciliation.csv`：逐工作簿、逐污染物、逐区块的数值/空白计数及合计。
- `row_coverage.csv`、`cell_mismatches.csv`、`verification_report.json`：原行覆盖、单元格差异和复核结果。
- `experiment_A_comparison.csv`、`human_run_comparison.csv`：方法比较和14个人工包的前后评价。
- `experiment_A_condition_audit.json`：年份、原交付输入登记、行映射和计时证据。
- `evaluation_revision_checks.json`：136个机器评分行不变及人工分母、D/E/耗时不变检查。
- 当前汇总：`实验结果/00_总汇总与索引/*_{NEW}.*`。旧评价文件保留。

在实验目录运行以下命令即可复现；已有规范化目录默认不覆盖。前三步均不调用Hermes或重算原工作簿。

```bash
.venv/bin/python -B human_baseline/normalize_human_v2.py
.venv/bin/python -B human_baseline/verify_human_exports.py
.venv/bin/python -B evaluation/aggregate_experiment_results_v2.py --reference-package reference/frozen/v2.0.0 --human-normalization-root human_baseline/normalized_v2_1
.venv/bin/python -B human_baseline/report_reading_revision.py
```
"""
    (AUDIT / "人工结果读取与评价修正报告.md").write_text(text, encoding="utf-8")
    print(json.dumps({"status": "pass", "report": str(AUDIT / "人工结果读取与评价修正报告.md"), "machine_scores_unchanged": len(machine_ids), "human_packages": len(human_ids)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
