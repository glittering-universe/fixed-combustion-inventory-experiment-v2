"""JSON-safe Hermes tool handlers."""

import json
from pathlib import Path
import subprocess

from .engine import rule_package, stage_runner


def _json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _run(stage_name, args):
    try:
        return _json(stage_runner.run(stage_name, args))
    except Exception as exc:  # Hermes handlers must never raise.
        return _json({"success": False, "stage": stage_name, "error": str(exc)})


def extract_tcses_rule_scope(args, **kwargs):
    del kwargs
    try:
        python = Path("/Users/wushuo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3")
        script = Path(__file__).parent / "engine" / "pdf_extract.py"
        completed = subprocess.run(
            [str(python), str(script)],
            input=json.dumps(args, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        return _json(json.loads(completed.stdout))
    except Exception as exc:
        return _json({"success": False, "stage": "pdf_scope_extraction", "error": str(exc)})


def adapt_environmental_workbooks(args, **kwargs):
    del kwargs
    return _run("adapt_environmental_workbooks", args)


def build_device_emission_sources(args, **kwargs):
    del kwargs
    return _run("build_device_emission_sources", args)


def generate_pollutant_calculation_items(args, **kwargs):
    del kwargs
    return _run("generate_pollutant_calculation_items", args)


def execute_tcses_calculation_rules(args, **kwargs):
    del kwargs
    return _run("execute_tcses_calculation_rules", args)


def control_calculation_admission(args, **kwargs):
    del kwargs
    return _run("control_calculation_admission", args)


def calculate_fixed_combustion_emissions(args, **kwargs):
    del kwargs
    return _run("calculate_fixed_combustion_emissions", args)


def validate_and_export_fixed_combustion_inventory(args, **kwargs):
    del kwargs
    return _run("validate_and_export_fixed_combustion_inventory", args)


def archive_complete_calculation_process(args, **kwargs):
    del kwargs
    return _run("archive_complete_calculation_process", args)


def generate_device_pollutant_slots(args, **kwargs):
    del kwargs
    return _run("generate_device_pollutant_slots", args)


def record_agent_rule_choices(args, **kwargs):
    del kwargs
    return _run("record_agent_rule_choices", args)


def summarize_agent_rule_options(args, **kwargs):
    del kwargs
    return _run("summarize_agent_rule_options", args)


def record_agent_admission_decisions(args, **kwargs):
    del kwargs
    return _run("record_agent_admission_decisions", args)


def summarize_agent_admission_options(args, **kwargs):
    del kwargs
    return _run("summarize_agent_admission_options", args)


def archive_minimal_calculation_process(args, **kwargs):
    del kwargs
    return _run("archive_minimal_calculation_process", args)


def validate_fixed_combustion_rule_package(args, **kwargs):
    del kwargs
    try:
        result = rule_package.validate_candidate(
            Path(args["candidate_path"]),
            Path(args["source_pdf_path"]),
            Path(args["report_path"]),
        )
        return _json(result)
    except Exception as exc:
        return _json({"success": False, "stage": "rule_package_validation", "error": str(exc)})


def compare_fixed_combustion_rule_coverage(args, **kwargs):
    del kwargs
    try:
        result = rule_package.compare_coverage(
            Path(args["candidate_path"]),
            Path(args["required_inventory_path"]),
            Path(args["report_path"]),
        )
        return _json(result)
    except Exception as exc:
        return _json({"success": False, "stage": "rule_coverage", "error": str(exc)})
