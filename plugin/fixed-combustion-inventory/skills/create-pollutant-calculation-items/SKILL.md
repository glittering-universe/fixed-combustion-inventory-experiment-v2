---
name: create-pollutant-calculation-items
description: Batch-create pollutant emission calculation items that preserve fuel, pollutant, control-process, particulate-size, and ammonia-slip dependencies. Use as the second stage after device-level sources have been committed.
---

# 污染物排放核算项生成

## Procedure

1. Confirm the device-source revision declared by the run manifest.
2. Call `generate_pollutant_calculation_items` once for the current scope.
3. Preserve separate fuel items, PM10/PM2.5/BC/OC dependencies, and SCR/SNCR ammonia-slip process items.
4. Return counts by item type and localized structural exception.
5. Load `fixed-combustion-inventory:apply-tcses-calculation-rules` only after the item stage has committed.

## Constraints

- Do not select formulas, factors, control efficiencies, or admission decisions.
- Do not merge ammonia slip into fuel-combustion factors.
- Do not let downstream tools reconstruct missing item relationships.

## Verification

Verify stable item identifiers, source/fuel/process relationships, pollutant coverage, current revision markers, and absence of duplicate active items.
