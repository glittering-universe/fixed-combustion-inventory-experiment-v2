---
name: decide-admission-with-agent
description: Experiment-D replacement for strict deterministic calculation admission. Let the Agent assign item actions in one batch and record them without calling the normal admission controller.
---

# Agent 核算准入判断（消融）

## Procedure

1. Call `summarize_agent_admission_options` once; it exposes flag counts and examples but not the removed admission output.
2. Author one compact batch policy containing withhold flags, not-applicable flags, and a default action.
3. Call `record_agent_admission_decisions` once with that policy.
4. Do not call `control_calculation_admission` in this run.

## Verification

Report decision coverage, invalid or absent actions, action counts, and the replacement-stage revision.
