# 人工基线读取与规范化

`original_packages/` 是用户提供的 14 个既有专家主导型结果包，只读保留，不得覆盖。

`normalize_human_v2.py` 联合读取：

1. 基 102 表 2023102002：5,922 条原始候选设备记录；
2. 基 101 表 2023102001：企业主表；
3. 基 101 表 2023102002：废气治理设施明细。

候选源 ID 直接复用 `experiment_control/source_identity.py` 的目标无关契约：企业键按统一社会信用代码、组织机构代码、企业名称依次回退，再与基 102 序号和统计年份共同生成 `SRC-RAW-*`。人工表内的 4,160 条工业锅炉和 426 条火电热力记录保留为人工范围决策，不改写为本文规则包的分类。

每个人工结果包输出四个文件：

- `source_decisions.csv`：面向 5,922 个候选源的目标类纳入/未纳入决策；
- `calculation_totals.csv`：人工工作簿中可观察的污染物产生量和排放量总值；
- `exceptions.csv`：源映射失败或人工总值不可观察的记录；
- `normalization_notes.json`：输入哈希、计数、时间边界和规范化策略。

活动水平、参数来源、治理工艺应用、规则编号和中间计算过程若未由人工包提供，一律记为 `NA`；不从独立参照包或原始基表补造人工过程。基 101 治理表只用于上下文完整性计数，不用于补写人工控制轨迹。

用户提供的计时原述为 `11.52秒/条`。现有交付中缺少实际计时样本数、记录粒度和阶段覆盖说明。当前评价既有口径将该单条率用于5,922个候选源，外推为 `68,221.44 s/目标任务`；这是外推假设，不是完整任务实测耗时。本次读取修正保持原计时输入不变。

当前读取版本为 `human-baseline-normalization-v2.1.0`。产生量和最终排放量逐个匹配原表表头，原表九污染物顺序为 SO₂、NOx、CO、PM₁₀、PM₂.₅、BC、OC、VOCs、NH₃。新导出保留原行号、工作表、单元格、原表头、原值与空白/数值类型。这些定位是适配器生成的追溯信息，不作为人工当时的过程记录。

`normalized_v2/`保留为历史导出；`normalized_v2_1/`为修正后的读取结果。原封存包不覆盖，评价通过显式指定新导出目录读取更正值。详见[读取与评价修正报告](reading_audit_v2_1/人工结果读取与评价修正报告.md)。

实验A的年份和记录来源可核验，但当时人工收到的输入包及技术任务要求尚缺证明，暂作为历史业务流程对照；筛选和计算答案保持原样。新B1/B2人工表现需有真实人员针对当前输入的交付，旧人工包重新导出不构成新实验。

## 执行

```bash
.venv/bin/python -B human_baseline/normalize_human_v2.py
.venv/bin/python -B human_baseline/verify_human_exports.py
.venv/bin/python -B evaluation/aggregate_experiment_results_v2.py --reference-package reference/frozen/v2.0.0 --human-normalization-root human_baseline/normalized_v2_1
.venv/bin/python -B human_baseline/report_reading_revision.py
```

若已存在 `human_baseline/normalized_v2_1/`，规范化脚本默认停止。重新评价可以从第三条命令开始；它不重跑机器或人工实验。

## 回归测试

```bash
python3 -m unittest human_baseline.tests.test_normalize_human_v2 -v
```

回归测试锁定 5,922 个候选 ID 唯一、4,160/426 个人工范围选择、代表性原始行映射、表头取列、空白与零的区分，以及 14 个输出包的四文件契约。评价测试另行验证更正读取覆盖旧缓存时保持原封存材料不变。
