# 固定燃烧源清单 Experiment D：Agent 接管单一阶段

你正在同一个 `fixed-combustion-inventory` Profile 中执行一次消融实验。只允许接管 `run_manifest.json` 中指定被移除的阶段；其余阶段必须调用固定燃烧清单 Plugin 中原有的批量工具，且不得用终端、脚本或自然语言结果替代它们。

共同要求：

- 所有阶段一次处理本次运行范围，不逐条向用户提问。
- 不使用基102填报的产生量或排放量选择方法、反推效率或形成最终结果。
- 不读取 `rules/frozen`、其他运行包、标准答案、实验标签或评价结果。
- Agent 接管阶段只能使用对应的只读选项摘要工具和决策记录工具；不得读取数据库、Plugin 源码或原组件实现。
- 决策完成后只调用对应的 `record_agent_*` 工具；禁止调用被移除的 `execute_tcses_calculation_rules` 或 `control_calculation_admission`。
- 若某项无法判断，保留为空并添加明确标志，不得把缺失的活动水平、燃料、硫分、灰分、容量或产生因子当作0。

## `w_o_calculation_item_structure`

1. 加载并执行 `fixed-combustion-inventory:prepare-device-emission-sources`。
2. 加载 `fixed-combustion-inventory:create-device-pollutant-slots`，调用
   `generate_device_pollutant_slots`；不得调用 `generate_pollutant_calculation_items`。
3. 其后恢复原有的规则实施、核算准入、排放计算、复核导出和完整过程归档
   Skills。不得由后续阶段重新构造被删除的燃料/过程核算项关系。

## `w_o_executable_rule_gate`

1. 依次加载并执行：
   `fixed-combustion-inventory:prepare-device-emission-sources`、
   `fixed-combustion-inventory:create-pollutant-calculation-items`。
2. 加载 `fixed-combustion-inventory:select-calculation-rules-with-agent`。
3. 调用 `summarize_agent_rule_options` 获取当前批次的原始组合，不读取冻结规则值。根据 T/CSES 144—2024 的通常核算逻辑和你自己的判断，形成一个紧凑的 `agent_policy`，并传给 `record_agent_rule_choices`。策略至少可包含：`factor_rules`、`control_efficiency_by_pollutant`、`pm_efficiencies` 和 `nh3`。每条 `factor_rules` 可用 `*` 作通配符，并使用以下字段：

```json
{
  "target": "POWER|INDUSTRIAL|*",
  "department": "...|*",
  "pollutant": "SO2|NOX|CO|VOCS|PM10|PM25|BC|OC",
  "raw_fuel": "...|*",
  "raw_unit": "吨|万立方米|*",
  "combustion": "...|*",
  "normalized_fuel": "...",
  "normalized_technology": "...",
  "parameter_id": "...",
  "mode": "...",
  "factor_value": 0.0,
  "factor_unit": "g/kg燃料或g/m3",
  "factor_formula": "..."
}
```

4. 调用 `record_agent_rule_choices`，然后恢复原有的
   `fixed-combustion-inventory:decide-calculation-admission`、
   `fixed-combustion-inventory:calculate-fixed-combustion-emissions`、
   `fixed-combustion-inventory:review-and-export-inventory`、
   `fixed-combustion-inventory:archive-calculation-process`。

## `agent_managed_admission`

1. 依次加载和执行前三个原有阶段，直至 `execute_tcses_calculation_rules` 完成。
2. 加载 `fixed-combustion-inventory:decide-admission-with-agent`。
3. 调用 `summarize_agent_admission_options` 获取标志及样例，但不得读取原准入实现。独立确定 `withhold_flags`、`not_applicable_flags` 与 `default_action`，作为 `agent_policy` 传给 `record_agent_admission_decisions`：

```json
{"withhold_flags":["..."],"not_applicable_flags":["..."],"default_action":"allow_calculation|withhold"}
```

4. 调用 `record_agent_admission_decisions`，然后恢复原有的计算、复核导出和完整过程归档阶段。

## `w_o_complete_trace`

1. 按 Full 的原有 Skills 执行到复核导出完成，所有数值结果保持不变。
2. 加载 `fixed-combustion-inventory:archive-minimal-process`，调用
   `archive_minimal_calculation_process`；不得调用
   `archive_complete_calculation_process`，不得事后补写逐核算项完整过程记录。

结束时仅报告阶段状态、决策覆盖率、输出路径、异常数量和运行包是否封存，不改写工具返回结果。
