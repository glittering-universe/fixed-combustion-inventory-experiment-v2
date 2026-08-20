---
name: formalize-fixed-combustion-rules
description: Organize the fixed-combustion sections of T/CSES 144—2024 into a candidate executable rule package, validate its structure and tests, and export a review snapshot. Use only for the auxiliary Stage R before the inventory experiments; never use it to modify a frozen rule package during an inventory run.
---

# 固定燃烧规则整理

## Procedure

1. Confirm that the input is the original T/CSES PDF, the blank rule-package specification, and the frozen section scope.
2. Call `extract_tcses_rule_scope` once and read only its page-separated output. Do not read existing rule workbooks, previous calculation answers, experiment outputs, or reference labels.
3. Create the candidate YAML, CSV, and JSONL files in `rules/stage_r/candidate/`.
4. Call `validate_fixed_combustion_rule_package` once for the entire candidate package.
5. Correct only structural, reference, formula, and test failures reported by the validator, then call it once more.
6. Call `compare_fixed_combustion_rule_coverage` to compare the candidate rule identifiers with the independently prepared required-rule inventory.
7. Export the professional-review snapshot. Mark the package `awaiting_professional_review`; never mark it frozen or approved yourself.

## Constraints

- Keep the rule package independent of Hermes, prompts, Skills, Python code, and Excel display files.
- Do not invent missing standard values or silently resolve ambiguous applicability.
- Do not compare Agent performance with a human rule-extraction method.
- Report candidate rule count, parameter count, test count, validation results, coverage ratio, and review status only.

## Verification

Accept Stage R output only when the candidate manifest, all cited source locations, schemas, formulas, tests, and coverage report are present. Professional approval remains an external final gate.
