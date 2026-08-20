---
name: select-calculation-rules-with-agent
description: Experiment-D replacement for executable T/CSES rule selection. Let the Agent produce one batch decision file and record it through the standard stage interface without calling the frozen rule executor.
---

# Agent 核算规则选择（消融）

## Procedure

1. Call `summarize_agent_rule_options` once; it exposes current raw combinations but no frozen rule values.
2. Author one compact policy following the ablation prompt schema.
3. Call `record_agent_rule_choices` once with that policy.
4. Do not call `execute_tcses_calculation_rules` or read the frozen rule package.

## Verification

Report item coverage, structurally complete decisions, unmatched items, and the recorded stage revision without repairing Agent choices.
