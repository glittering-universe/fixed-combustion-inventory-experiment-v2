"""Hermes-visible tool schemas for the fixed-combustion experiment."""


def _stage_schema(name: str, description: str, extra_properties=None, extra_required=None):
    properties = {
        "run_id": {"type": "string", "description": "Unique experiment run identifier."},
        "run_package_path": {
            "type": "string",
            "description": "Absolute path to the isolated run package.",
        },
        "scenario": {
            "type": "string",
            "description": "Frozen method and experiment scenario identifier.",
        },
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional target-neutral raw candidate subset used only by Experiment C; omit for A, B and D.",
        },
        "expected_upstream_revision": {
            "type": "string",
            "description": "Expected current upstream revision, if one exists.",
        },
        "method_bundle_hash": {
            "type": "string",
            "description": "Frozen method-package SHA-256 recorded in the run manifest.",
        },
    }
    if extra_properties:
        properties.update(extra_properties)
    required = ["run_id", "run_package_path", "scenario", "method_bundle_hash"]
    if extra_required:
        required.extend(extra_required)
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


ADAPT_WORKBOOKS = _stage_schema(
    "adapt_environmental_workbooks",
    "Batch-read the frozen environmental-statistics input workbooks, normalize registered table structures and field aliases, and write the unified input stage of one isolated run package. Use only as stage 1a; it never constructs pollutant items or calculates emissions.",
)

BUILD_DEVICE_SOURCES = _stage_schema(
    "build_device_emission_sources",
    "Batch-build device-level fixed-combustion emission sources and their enterprise, fuel, outlet, and control relationships from the current unified-input revision. Use only as stage 1b.",
)

GENERATE_ITEMS = _stage_schema(
    "generate_pollutant_calculation_items",
    "Batch-create fuel-level and control-process pollutant emission calculation items, including PM-size dependencies and separate SCR/SNCR ammonia-slip items. Use only as stage 2.",
)

EXECUTE_RULES = _stage_schema(
    "execute_tcses_calculation_rules",
    "Batch-execute the frozen T/CSES rule package for all current calculation items and write selected methods, formula IDs, parameter IDs, units, applicability results, and standard locations. Use only as stage 3.",
)

CONTROL_ADMISSION = _stage_schema(
    "control_calculation_admission",
    "Batch-assign calculation admission actions and localized reasons to all current pollutant items. Apply the frozen empirical-control-missing policy without converting missing activity, fuel, sulfur, ash, capacity, formula, or generation factors to zero. Use only as stage 4.",
)

CALCULATE = _stage_schema(
    "calculate_fixed_combustion_emissions",
    "Batch-calculate admitted fixed-combustion generation and final emissions using only upstream formula and parameter assignments. Includes unit conversion, coal material balance, pollutant-specific control, PM fine/coarse treatment, BC/OC, and ammonia slip. Use only as stage 5.",
)

VALIDATE_EXPORT = _stage_schema(
    "validate_and_export_fixed_combustion_inventory",
    "Batch-run structural and numerical hard checks, aggregate current item results, and export the industrial-boiler and power/heat inventory workbooks plus a concise final exception list. Use only as stage 6 and never repair upstream data.",
)

ARCHIVE_TRACE = _stage_schema(
    "archive_complete_calculation_process",
    "Materialize complete item-level calculation-process records from immutable current stage revisions and seal the run package without changing inventory results. Use only as stage 7.",
)

EXTRACT_T_CSES_SCOPE = {
    "name": "extract_tcses_rule_scope",
    "description": "Extract the frozen fixed-combustion page scope from the original T/CSES PDF into page-separated UTF-8 text for Stage R. This tool performs document extraction only and does not interpret or create rules.",
    "parameters": {
        "type": "object",
        "properties": {
            "source_pdf_path": {"type": "string", "description": "Absolute original T/CSES PDF path."},
            "output_path": {"type": "string", "description": "Absolute output Markdown text path."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Physical one-based PDF pages. Omit to use the frozen Stage-R scope.",
            },
        },
        "required": ["source_pdf_path", "output_path"],
        "additionalProperties": False,
    },
}

VALIDATE_RULE_PACKAGE = {
    "name": "validate_fixed_combustion_rule_package",
    "description": "Validate one Stage-R candidate fixed-combustion rule package for manifest, schema, citation, formula, parameter, mapping, and JSONL test integrity. This does not approve or freeze the package.",
    "parameters": {
        "type": "object",
        "properties": {
            "candidate_path": {"type": "string", "description": "Absolute candidate rule-package directory."},
            "source_pdf_path": {"type": "string", "description": "Absolute original T/CSES PDF path."},
            "report_path": {"type": "string", "description": "Absolute output JSON report path."},
        },
        "required": ["candidate_path", "source_pdf_path", "report_path"],
        "additionalProperties": False,
    },
}

COMPARE_RULE_COVERAGE = {
    "name": "compare_fixed_combustion_rule_coverage",
    "description": "Deterministically compare Stage-R candidate rule identifiers with the independently prepared required-rule inventory and report matched, missing, extra, and coverage ratio. This is descriptive and does not rank the main methods.",
    "parameters": {
        "type": "object",
        "properties": {
            "candidate_path": {"type": "string"},
            "required_inventory_path": {"type": "string"},
            "report_path": {"type": "string"},
        },
        "required": ["candidate_path", "required_inventory_path", "report_path"],
        "additionalProperties": False,
    },
}

# Frozen same-interface replacements used only by Experiment D.
GENERATE_DEVICE_SLOTS = _stage_schema(
    "generate_device_pollutant_slots",
    "Experiment-D replacement for stage 2. Create interface-compatible device-by-pollutant result slots without fuel/process calculation-item dependencies. Do not use in the Full method.",
)

RECORD_AGENT_RULES = _stage_schema(
    "record_agent_rule_choices",
    "Experiment-D replacement for stage 3. Record the Agent's batch rule choices in the standard stage interface without executing applicability gates or correcting the choices.",
    {
        "decision_file": {"type": "string", "description": "Optional absolute JSONL file containing Agent batch rule choices."},
        "agent_policy": {"type": "object", "description": "Optional Agent-authored compact matching policy applied mechanically to the current batch.", "additionalProperties": True},
    },
)

RECORD_AGENT_ADMISSION = _stage_schema(
    "record_agent_admission_decisions",
    "Experiment-D replacement for stage 4. Record Agent-managed admission actions and reasons in the standard stage interface without applying deterministic admission rules.",
    {
        "decision_file": {"type": "string", "description": "Optional absolute JSONL file containing Agent admission actions and reasons."},
        "agent_policy": {
            "type": "object",
            "description": "Optional Agent-authored batch policy with withhold_flags, not_applicable_flags, and default_action.",
            "additionalProperties": True,
        },
    },
)

SUMMARIZE_AGENT_RULE_OPTIONS = _stage_schema(
    "summarize_agent_rule_options",
    "Experiment-D read-only summary of current raw rule-choice combinations. It exposes no frozen parameters, executable rules, prior decisions, or reference answers.",
)

SUMMARIZE_AGENT_ADMISSION_OPTIONS = _stage_schema(
    "summarize_agent_admission_options",
    "Experiment-D read-only summary of current rule-result flags and item states. It exposes no deterministic admission decision or prior admission output.",
)

ARCHIVE_MINIMAL_TRACE = _stage_schema(
    "archive_minimal_calculation_process",
    "Experiment-D replacement for stage 7. Preserve only the required minimal stage log, omit item-level calculation-process materialization, and seal the run package.",
)
