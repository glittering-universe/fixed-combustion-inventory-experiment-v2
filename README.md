# 固定燃烧源清单原始基表实验 v2 / 评价 v2.2

本仓库是重新设计后的唯一活动实验版本。所有机器方法从同一组答案隔离的原始结构环境统计基表开始，对基102中的5,922条设备候选自主完成源类判定、排放核算、异常处置和结果输出。

## 整理后的结果入口

本地统一入口为[正式实验结果](正式实验结果/README.md)，按A—D保存最终输出，并提供当前评价、原人工工作簿、读取复核、规则参照、共享输入和复现代码。大文件通过[私有GitHub Release](https://github.com/glittering-universe/fixed-combustion-inventory-experiment-v2/releases/tag/results-v2.2.0-human-reading-v2.1.0)发布；Git仓库保留代码、当前轻量评价与文件校验索引。

完整运行目录、会话、计算轨迹及数据库压缩保存于`正式实验结果/08_完整封存/`。本地清理后，若需再次重评，先执行`python3 reproduction/curate_results.py restore`还原运行目录，再使用当前评价命令。还原只复制原封存文件，不启动新实验；已存在且内容不同的文件不会被覆盖。

旧规范化、旧评价、预览和未纳入正式统计的数据只保存在历史发布资产中。原人工输入保留在`human_baseline/original_packages/`，原始环境统计数据、论文文稿和Hermes Desktop实时记录不在此次清理范围内。

## 活动实验边界

- 原始候选：基102设备明细5,922条。
- 关联数据：基101企业主表18,734条、基101治理设施43,902条。
- 物理输入：基102一份、基101企业表四个稳定行分片、基101治理设施一份。
- 源类闭合：工业锅炉4,046条、火电热力615条、范围外1,261条。
- 规则：T/CSES 144-2024固定燃烧规则包v1.0.1。
- 专业引擎：3.0.0。
- 评价：D/N/E v2.2，`EICPI_core = 100(0.35D + 0.35N + 0.30E)`，不设置独立质量门槛。v2.1引入常规/异常分层D，v2.2固定N的必需数值及PM配对分母，均通过重评既有封存运行实施。
- 方法边界：所有 Agent 运行都进行独立边界审计；边界异常如实报告，不另行改变 D/N/E 或中断无门槛综合评价。
- Hermes Profile：`fixed-combustion-inventory`，正式运行前为空会话库，正式会话可在Desktop中查看。

## 方法

1. Full：Agent组织＋污染物核算项＋可执行规则门控＋准入控制＋确定性计算＋完整过程记录。
2. 简单确定性脚本：固定表头、固定映射、固定查表与直接计算。
3. 通用工具调用型Agent：只有通用文件、终端、代码和表格能力及T/CSES PDF。
4. 既有专家主导型：保留的14个人工结果包以只读方式规范化，不补造未记录过程。当前按原表表头读取的版本为`normalized_v2_1`；实验A暂按历史业务流程对照保留，等待原始任务条件证明。新B1/B2人工表现需另有对应当前输入的真实交付。

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
- D/N/E评价：`evaluation/aggregate_experiment_results_v2.py`（活动指标定义见 `evaluation/metric_spec_v2_2.json`）

当前汇总采用`*_v2_2_human_v2_1.*`，读取修正、逐单元复核及人工得分变化见[人工结果读取与评价修正报告](human_baseline/reading_audit_v2_1/人工结果读取与评价修正报告.md)。原人工工作簿保留原样，旧导出和完整原封存可由发布资产恢复。
- 一键复现：`reproduction/run_all_machine_experiments.sh`

旧实验的完整Git历史、LFS对象、正式运行包、Hermes会话数据库和历史归档已保存到私有GitHub Release `legacy-pre-raw-input-20260821`，不再保留于本地活动仓库。
