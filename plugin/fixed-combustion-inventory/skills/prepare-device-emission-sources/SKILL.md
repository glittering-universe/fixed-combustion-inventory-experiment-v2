---
name: prepare-device-emission-sources
description: Batch-adapt environmental-statistics workbooks and construct device-level fixed-combustion emission sources for one run package. Use as the first stage of every full, baseline, perturbation, scale, and ablation inventory run.
---

# 环境统计数据整理与设备源构建

## Procedure

1. Receive only `run_id`, `run_package_path`, `scenario`, `expected_upstream_revision`, and `method_bundle_hash`. Experiment C may carry a target-neutral candidate subset in the manifest; never accept target member IDs.
2. Call `adapt_environmental_workbooks` once for the complete raw Base-102 candidate batch (or the manifest's neutral Experiment-C subset).
3. If the returned stage result is `can_continue` or `partial_continue`, call `build_device_emission_sources` once for the same candidate batch.
4. Return the stage summary and next-stage permission. Keep record-level data in the run database.
5. Load `fixed-combustion-inventory:create-pollutant-calculation-items` only after the device-source stage has committed successfully.

## Constraints

- Do not create pollutant calculation items, choose rules, infer factors, or calculate emissions.
- Do not silently change source values. Record normalization and association outcomes as new stage records.
- Treat unresolved local relationships as localized exceptions; stop the whole stage only for unreadable workbooks, invalid run manifests, or database failures.

## Verification

Verify the three raw-structure input hashes, candidate count, source-decision closure across industrial/power/outside-scope outcomes, revision identifiers, and stage-table ownership before proceeding.
