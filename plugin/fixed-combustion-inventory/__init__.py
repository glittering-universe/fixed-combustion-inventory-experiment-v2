"""Registration for the fixed-combustion inventory Hermes plugin."""

from pathlib import Path

from . import schemas, tools


_TOOL_BINDINGS = (
    (schemas.EXTRACT_T_CSES_SCOPE, tools.extract_tcses_rule_scope),
    (schemas.ADAPT_WORKBOOKS, tools.adapt_environmental_workbooks),
    (schemas.BUILD_DEVICE_SOURCES, tools.build_device_emission_sources),
    (schemas.GENERATE_ITEMS, tools.generate_pollutant_calculation_items),
    (schemas.EXECUTE_RULES, tools.execute_tcses_calculation_rules),
    (schemas.CONTROL_ADMISSION, tools.control_calculation_admission),
    (schemas.CALCULATE, tools.calculate_fixed_combustion_emissions),
    (schemas.VALIDATE_EXPORT, tools.validate_and_export_fixed_combustion_inventory),
    (schemas.ARCHIVE_TRACE, tools.archive_complete_calculation_process),
    (schemas.VALIDATE_RULE_PACKAGE, tools.validate_fixed_combustion_rule_package),
    (schemas.COMPARE_RULE_COVERAGE, tools.compare_fixed_combustion_rule_coverage),
    (schemas.GENERATE_DEVICE_SLOTS, tools.generate_device_pollutant_slots),
    (schemas.SUMMARIZE_AGENT_RULE_OPTIONS, tools.summarize_agent_rule_options),
    (schemas.RECORD_AGENT_RULES, tools.record_agent_rule_choices),
    (schemas.SUMMARIZE_AGENT_ADMISSION_OPTIONS, tools.summarize_agent_admission_options),
    (schemas.RECORD_AGENT_ADMISSION, tools.record_agent_admission_decisions),
    (schemas.ARCHIVE_MINIMAL_TRACE, tools.archive_minimal_calculation_process),
)


def _pre_tool_call(tool_name, args, **kwargs):
    """Block experiment tools that omit run isolation fields."""
    del kwargs
    if tool_name not in {schema["name"] for schema, _ in _TOOL_BINDINGS}:
        return None
    if tool_name.startswith(("extract_tcses_", "validate_fixed_", "compare_fixed_")):
        return None
    if not args.get("run_id") or not args.get("run_package_path"):
        return {"action": "block", "message": "Experiment tool call lacks run isolation fields."}
    return None


def _post_tool_call(tool_name, args, result, **kwargs):
    """Forward-compatible observer; durable details are written by the tools."""
    del tool_name, args, result, kwargs


def register(ctx):
    for schema, handler in _TOOL_BINDINGS:
        ctx.register_tool(
            name=schema["name"],
            toolset="fixed_combustion_inventory",
            schema=schema,
            handler=handler,
            description=schema["description"],
        )

    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)

    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
