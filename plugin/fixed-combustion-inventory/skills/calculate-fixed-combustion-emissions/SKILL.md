---
name: calculate-fixed-combustion-emissions
description: Batch-calculate fixed-combustion generation and emissions from admitted items using assigned formulas and parameters, including coal material balance, pollutant-specific control, PM size fractions, and ammonia slip. Use as the fifth inventory stage.
---

# 固定燃烧排放量计算

## Procedure

1. Confirm the current admission revision and calculate only `allow_calculation` items.
2. Call `calculate_fixed_combustion_emissions` once for the current scope.
3. Use the formula and parameter identifiers already assigned upstream; write Excel formulas for activity level, factor application, generation, control efficiency, and final emission fields.
4. Preserve calculation-tool failures separately from business information insufficiency.
5. Load `fixed-combustion-inventory:review-and-export-inventory` after the calculation stage commits.

## Constraints

- Do not re-select rules, parameters, or defaults.
- Do not use Base102 reported generation or emission values in calculations.
- Apply the frozen PM10 fine/coarse pathway, BC/OC efficiencies, and SCR/SNCR ammonia-slip formula exactly as assigned.
- Do not replace missing calculable inputs with zero.

## Verification

Verify unit dimensions, non-negative results, formula-cell presence, deterministic recalculation, and one current calculation revision per admitted item.
