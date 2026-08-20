---
name: create-device-pollutant-slots
description: Experiment-D replacement for the pollutant calculation-item stage. Create one device-by-pollutant slot set and omit fuel/process item dependencies while keeping the downstream interface stable.
---

# 设备污染物结果槽生成（消融）

## Procedure

1. Confirm the device-source revision.
2. Call `generate_device_pollutant_slots` once for the current batch.
3. Do not call `generate_pollutant_calculation_items` in this run.
4. Load the normal rule-execution Skill after the replacement stage commits.

## Verification

Verify one slot per device and pollutant, stable identifiers, and the explicit `CALCULATION_ITEM_STRUCTURE_REMOVED` disposition downstream.
