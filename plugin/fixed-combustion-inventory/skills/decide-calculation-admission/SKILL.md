---
name: decide-calculation-admission
description: Batch-decide whether each pollutant calculation item may be calculated, is not applicable, is withheld for insufficient information, or is blocked by a foundational anomaly. Use as the fourth inventory stage after rule execution.
---

# 污染物核算准入

## Procedure

1. Confirm the current item and rule-result revisions.
2. Call `control_calculation_admission` once for the current scope.
3. Permit empirical zero control efficiency only for missing or unmapped control information, with the prescribed flag.
4. Withhold items missing activity, fuel identity, sulfur, ash, capacity, formula, or emission-factor parameters required by their selected method.
5. Return counts for the four external states and their localized root causes.
6. Load `fixed-combustion-inventory:calculate-fixed-combustion-emissions` after the admission stage commits.

## Constraints

- Never treat missing control information as proof that no control measure exists.
- Never treat a missing pollutant-specific generation parameter as zero or not applicable.
- Do not allow non-admitted items into calculation.

## Verification

Verify that every active item has exactly one current action and reason, that localized blocking does not affect unrelated items, and that the empirical-control flags are retained.
