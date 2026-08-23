# 固定燃烧源清单原始基表实验 v2 / 评价 v2.1

本仓库是重新设计后的唯一活动实验版本。所有机器方法从同一组答案隔离的原始结构环境统计基表开始，对基102中的5,922条设备候选自主完成源类判定、排放核算、异常处置和结果输出。

## 活动实验边界

- 原始候选：基102设备明细5,922条。
- 关联数据：基101企业主表18,734条、基101治理设施43,902条。
- 物理输入：基102一份、基101企业表四个稳定行分片、基101治理设施一份。
- 源类闭合：工业锅炉4,046条、火电热力615条、范围外1,261条。
- 规则：T/CSES 144-2024固定燃烧规则包v1.0.1。
- 专业引擎：3.0.0。
- 评价：D/N/E v2.1，`EICPI_core = 100(0.35D + 0.35N + 0.30E)`，不设置独立质量门槛。v2.1 是实验完成后针对异常类别失衡的协议修订，不修改封存运行。
- 方法边界：所有 Agent 运行都进行独立边界审计；边界异常如实报告，不另行改变 D/N/E 或中断无门槛综合评价。
- Hermes Profile：`fixed-combustion-inventory`，正式运行前为空会话库，正式会话可在Desktop中查看。

## 方法

1. Full：Agent组织＋污染物核算项＋可执行规则门控＋准入控制＋确定性计算＋完整过程记录。
2. 简单确定性脚本：固定表头、固定映射、固定查表与直接计算。
3. 通用工具调用型Agent：只有通用文件、终端、代码和表格能力及T/CSES PDF。
4. 既有专家主导型：保留的14个人工结果包以只读方式规范化，不补造未记录过程。

## 实验

- A：全量端到端清单编制，机器方法各三次。
- B1：表头、别名、列顺序及组合扰动。
- B2：每个源类10个正向异常、3个负对照、2个原有问题对象。
- C：25%、50%、75%、100%目标中立物理子集，机器方法各三次。
- D：Full及四个组件消融版本，全量运行。

## 主要入口

- 输入构建：`experiment_control/prepare_raw_inputs.py`
- 运行打包：`experiment_control/prepare_run.py`
- 矩阵执行：`experiment_control/execute_experiment_matrix.py`
- 专业Plugin：`plugin/fixed-combustion-inventory/`
- 简单脚本：`baseline/simple_deterministic/`
- 人工规范化：`human_baseline/normalize_human_v2.py`
- 独立参照：`reference/frozen/v2.0.0/`
- D/N/E评价：`evaluation/aggregate_experiment_results_v2.py`（活动指标定义见 `evaluation/metric_spec_v2_1.json`）
- 一键复现：`reproduction/run_all_machine_experiments.sh`

旧实验的完整Git历史、LFS对象、正式运行包、Hermes会话数据库和历史归档已保存到私有GitHub Release `legacy-pre-raw-input-20260821`，不再保留于本地活动仓库。
