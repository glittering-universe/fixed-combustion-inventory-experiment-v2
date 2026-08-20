---
name: archive-minimal-process
description: Experiment-D replacement for complete calculation-process recording. Seal the run with only the minimal stage log while keeping all upstream results unchanged.
---

# 最小过程记录归档（消融）

## Procedure

1. Confirm the exported workbook and exception-list hashes.
2. Call `archive_minimal_calculation_process` once.
3. Do not call `archive_complete_calculation_process`.

## Verification

Verify the minimum stage log, sealed manifest, and absence of item-level complete process records.
