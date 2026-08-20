---
name: review-and-export-inventory
description: Batch-check structural and numerical hard constraints, aggregate item results to device-level fixed-combustion inventories, and export workbooks plus final exception lists. Use as the sixth inventory stage.
---

# 清单质量复核与结果生成

## Procedure

1. Confirm current revisions from all five upstream stages.
2. Call `validate_and_export_fixed_combustion_inventory` once for the current scope.
3. Check dimensions, non-negativity, efficiency bounds, PM2.5 not exceeding PM10, aggregation conservation, formula reproducibility, and output-schema integrity.
4. Export the two inventory workbooks, the concise final exception list, the quality summary, and machine-readable result tables.
5. Do not correct upstream values in this stage.
6. Load `fixed-combustion-inventory:archive-calculation-process` after export succeeds.

## Constraints

- Do not reconstruct missing calculation items, rule matches, admission decisions, or calculation history.
- Keep Base102 reported values in comparison columns only.
- Present the small set of unresolved matters as the final user review list.

## Verification

Verify output file hashes, record-count closure, current revisions, formula-error scan, external-link scan, and exception-to-item references.
