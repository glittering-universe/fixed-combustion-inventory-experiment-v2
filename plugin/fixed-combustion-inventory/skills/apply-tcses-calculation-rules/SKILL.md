---
name: apply-tcses-calculation-rules
description: Execute the frozen T/CSES fixed-combustion rule package for all calculation items in a run scope, recording method, formula, parameter, applicability, and standard-location matches. Use as the third inventory stage.
---

# T/CSES 核算规则实施

## Procedure

1. Verify the frozen rule-package version and hash from the run manifest.
2. Call `execute_tcses_calculation_rules` once for the current scope.
3. Accept only rule and parameter identifiers returned by the deterministic tool.
4. Preserve unresolved fuel, technology, formula, or parameter matches as item-level results.
5. Load `fixed-combustion-inventory:decide-calculation-admission` after the rule stage commits.

## Constraints

- Do not reinterpret the PDF, prompts, Excel formulas, or code as an alternative rule source.
- Do not invent parameters or repair unmatched items in natural language.
- Do not apply the empirical control-missing policy to missing activity, fuel, sulfur, ash, or generation factors.

## Verification

Verify the frozen package hash, one current rule result per applicable item, parameter units, citations, conflict outcomes, and stage-interface version.
