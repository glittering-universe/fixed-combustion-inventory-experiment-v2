# 人工基线 v2 规范化

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

人工时间不按最终纳入的 4,160/426 条记录缩减。新任务要求每个目标源类都从 5,922 个候选源开始筛选，因此 E 维度的人工时间按用户提供的 `11.52 s/候选条` 外推为 `68,221.44 s/目标任务`，并标记为 `extrapolated_from_user_supplied_per_candidate_rate`。

## 执行

```bash
python3 human_baseline/normalize_human_v2.py
```

若已存在 `human_baseline/normalized_v2/`，脚本默认停止；明确需要重建时使用 `--replace`。

## 回归测试

```bash
python3 -m unittest human_baseline.tests.test_normalize_human_v2 -v
```

回归测试锁定 5,922 个候选 ID 唯一、4,160/426 个人工范围选择、代表性原始行映射，以及 14 个输出包的四文件契约。
