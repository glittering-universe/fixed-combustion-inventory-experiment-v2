# 通用工具调用型 Agent 任务

请依据当前运行包 `run_manifest.json` 指向的三张保持原始基表结构的答案隔离
工作簿和 `inputs/T_CSES_144-2024.pdf`，从全部基102设备候选中自主完成固定燃烧源工业锅炉或
火电热力排放清单编制。你可以使用通用文件、终端、代码与表格工具，但
不得使用 fixed-combustion-inventory 专用工具、专用 Skills、冻结规则包、
输入适配配置、其他方法输出、参照包或遗留结果公式。不得浏览当前运行包
以外的工程目录；当前运行包以外发现的任何代码、规则、结果都不属于本次
可用输入。

输入工作簿保留基102、基101企业及治理设施表的原始行粒度；污染物历史产生量和
排放量已统一留空。只能根据未留空的源字段和 PDF 自主建立处理逻辑，不得把空白结果列
解释为零，也不得从其他位置寻找旧答案。必须对运行包内全部候选记录作出
工业锅炉、火电热力或范围外判断，再仅汇总 `run_manifest.json` 指定目标类型。
`inputs/source_identity_index.json` 只提供目标中立的候选 `source_id` 与基102行对应关系，
可用于稳定标识；它不包含源类答案，禁止根据ID格式推断去向。

输出必须写入当前运行包的 `outputs` 目录，至少生成以下三个 UTF-8 文件：

1. `source_decisions.csv`：每个候选源一行，必须包含
   `source_id,requested_target,decided_target,disposition,reason_code`。
2. `generic_inventory_items.csv`：每个源×污染物一行，必须包含
   `source_id,target,pollutant,status,method,activity_record_json,parameter_record_json,control_record_json,generation_t,emission_t,standard_reference,reason_code`。

   三个 `*_record_json` 字段必须是合法 JSON 数组，不得写成单个数值或
   JSON 对象。每一个燃料或独立计算组件在三个数组中各占一个元素，
   数组长度、元素顺序及 `component_id` 必须一一对应。多燃料不得先汇总
   活动水平或排放因子后再记录。元素的最低字段如下：

   - `activity_record_json`：`component_id,value,unit`；
   - `parameter_record_json`：`component_id,parameter_id,mode,value,unit`；
   - `control_record_json`：`component_id,method,efficiency,fine_efficiency,coarse_efficiency`。

   对 SO₂、NOx、CO、VOC、PM₂.₅、BC、OC 和 NH₃，`efficiency` 记录该组件
   实际使用的去除效率，无适用治理时明确写 `0`。对 PM₁₀，
   `fine_efficiency` 和 `coarse_efficiency` 分别记录 PM₂.₅ 组分与
   PM₂.₅–₁₀ 组分的去除效率，两者均必须给出；不得只写一个
   `control_efficiency`。例如两种燃料时，三个数组必须均有2个元素，
   分别以相同的 `component_id` 对齐。
3. `generic_source_disposition.csv`：每个候选源记录一行，必须包含
   `source_id,target,disposition,reason_code`。
4. `generic_exception_list.csv`：每个需要用户处理的根因组一行，必须包含
   `exception_id,source_id,pollutant,root_cause,action_required`。
5. `generic_run_summary.json`：记录输入文件、处理源数、核算项数、各状态数、
   输出路径、未完成事项和你实际采用的规则/脚本文件路径。

`status` 只允许使用：`calculated`、`not_involved`、`information_insufficient`、
`source_data_invalid`。数值为空必须给出状态和 `reason_code`；信息不足时不得
编造燃料量、因子、治理效率或排放量。完成后检查四个 CSV 的列名、唯一性、
数值非负性和范围覆盖，再报告实际输出路径和未完成事项。
