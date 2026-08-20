---
name: archive-calculation-process
description: Materialize and archive the complete calculation-process record for every terminal pollutant item, then seal the run package without changing inventory results. Use as the seventh and final inventory stage.
---

# 完整核算过程记录归档

## Procedure

1. Confirm the sixth-stage output hashes and current immutable revisions.
2. Call `archive_complete_calculation_process` once for the current scope.
3. Record input workbook/sheet/cell locations, normalization actions, rule and parameter identifiers, standard locations, formulas, intermediate values, units, admission decisions, quality findings, output locations, and revision links.
4. Seal the run manifest and return the final stage summary and exception count.

## Constraints

- Do not modify the sixth-stage inventory workbooks or exception list.
- Do not reconstruct unavailable upstream facts.
- Preserve the minimum stage log in every method version; the no-full-trace ablation replaces only this item-level materialization.

## Verification

Verify process-record coverage under the applicability mask, agreement with frozen stage tables, package hashes, and sealed status.
