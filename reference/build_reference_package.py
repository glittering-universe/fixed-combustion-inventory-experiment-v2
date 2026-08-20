#!/usr/bin/env python3
"""Build an independent professional reference package for the experiment.

This module intentionally does not import the experiment implementation, open
any sealed run, or read any method output.  Its only data dependencies are the
frozen de-identified inputs, the frozen professionally reviewed rule package,
the controlled B2 injection manifest, and the governing T/CSES PDF.
"""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook


HERE = Path(__file__).resolve().parent
EXPERIMENT_ROOT = HERE.parent
WORKSPACE = EXPERIMENT_ROOT.parent
RULE_ROOT = EXPERIMENT_ROOT / "rules" / "frozen" / "v1.0.1"
INPUT_ROOT = EXPERIMENT_ROOT / "inputs"
OUTPUT_ROOT = HERE / "frozen" / "v2.0.0"
STANDARD_PDF = WORKSPACE / "1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf"
RAW_INPUT_MANIFEST = INPUT_ROOT / "prepared" / "raw_input_manifest.json"
B2_INJECTION_MANIFEST = INPUT_ROOT / "experiment_b" / "B2" / "injection_manifest.json"
B2_VARIANT_MANIFEST = INPUT_ROOT / "experiment_b" / "B2" / "variant_manifest.json"
B2_EVALUATION_AUDIT = INPUT_ROOT / "experiment_b" / "B2" / "evaluation_class_audit.json"
PSEUDONYM_SALT = "fixed-combustion-2022"

POLLUTANTS = ("SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC", "NH3")
PARAMETER_POLLUTANT = {
    "SO2": "SO2", "NOx": "NOX", "CO": "CO", "VOC": "VOCS",
    "PM10": "PM10", "PM2.5": "PM25", "BC": "BC", "OC": "OC",
}
PARTICLE_POLLUTANTS = {"PM10", "PM2.5", "BC", "OC"}

SOURCE_FIELDS = (
    "variant", "source_id", "source_tag", "source_row", "expected_target",
    "expected_disposition", "source_gate_status", "source_gate_reasons",
    "source_quality_notices", "inventory_year", "industry_code", "industry_name",
    "equipment_type", "outlet_id", "longitude", "latitude", "expected_item_count",
)
ITEM_FIELDS = (
    "variant", "item_id", "evaluation_item_id", "native_item_id", "source_id", "target",
    "component_type", "component_key", "pollutant", "expected_status", "applicability_mask",
    "expected_method_category", "method_evaluation_id", "expected_generation_t", "expected_emission_t",
    "expected_value_applicable", "expected_activity_record", "expected_intermediate_record",
    "expected_parameter_record", "expected_parameter_evaluation_record", "parameter_evaluation_ids",
    "expected_control_record", "control_evaluation_id", "expected_native_parameter_ids", "expected_control_technology",
    "expected_control_efficiency", "expected_fine_efficiency", "expected_coarse_efficiency",
    "expected_reason_codes", "expected_standard_reference", "standard_reference_ids", "numeric_tolerance_profile",
    "minimum_recalculation_complete", "complete_process_record_required", "reference_basis",
)
EXCEPTION_FIELDS = (
    "variant", "exception_group_id", "source_id", "target", "canonical_root_cause",
    "equivalence_key", "aggregation_rule", "severity", "requires_user_judgment",
    "expected_action", "exception_codes", "affected_item_ids", "affected_pollutants",
    "expected_statuses", "numeric_should_be_blank", "injection_root_cause",
    "scoring_eligible", "reference_basis",
)
TOTAL_FIELDS = (
    "variant", "evaluation_total_id", "source_id", "target", "pollutant", "expected_status",
    "expected_generation_t", "expected_emission_t", "expected_value_applicable",
    "component_item_ids", "expected_reason_codes", "numeric_tolerance_profile", "reference_basis",
)

REASON_EQUIVALENCE = {
    "SOURCE_RELATION_MISSING": ("source_relationship_missing", True),
    "SOURCE_RELATION_CONFLICT": ("source_relationship_conflict", True),
    "LOW_NOX_FIELD_CONFLICT": ("control_information_conflict", True),
    "COORDINATE_REVIEW_REQUIRED": ("coordinate_plausibility_review", True),
    "POTENTIAL_DUPLICATE_FUEL_SLOT": ("potential_duplicate_fuel_slot", True),
    "FUEL_UNMAPPED": ("fuel_mapping_unresolved", True),
    "FUEL_MISSING": ("fuel_mapping_unresolved", True),
    "FUEL_TYPE_MISSING": ("fuel_mapping_unresolved", True),
    "FUEL_OUTSIDE_SCOPE": ("fuel_outside_fixed_combustion_scope", False),
    "FUEL_OUTSIDE_FOSSIL_SCOPE": ("fuel_outside_fixed_combustion_scope", False),
    "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE": ("fuel_outside_fixed_combustion_scope", False),
    "OTHER_FUEL_TCE_UNRESOLVED": ("other_fuel_tce_unresolved", True),
    "UNRESOLVED_OTHER_FUEL_TCE": ("other_fuel_tce_unresolved", True),
    "ACTIVITY_MISSING": ("activity_level_missing", True),
    "ACTIVITY_UNIT_UNSUPPORTED": ("activity_unit_unresolved", True),
    "ACTIVITY_OR_UNIT_MISSING": ("activity_or_unit_unresolved", True),
    "ACTIVITY_OUT_OF_RANGE": ("activity_level_invalid", True),
    "COMBUSTION_TECHNOLOGY_UNMAPPED": ("combustion_technology_unresolved", True),
    "COMBUSTION_TECHNOLOGY_AMBIGUOUS": ("combustion_technology_unresolved", True),
    "COMBUSTION_TECH_UNMAPPED": ("combustion_technology_unresolved", True),
    "EMISSION_FACTOR_NOT_FOUND": ("factor_applicability_unresolved", True),
    "FACTOR_UNMATCHED": ("factor_applicability_unresolved", True),
    "FACTOR_APPLICABILITY_UNRESOLVED": ("factor_applicability_unresolved", True),
    "FACTOR_INPUT_MISSING": ("factor_input_unresolved", True),
    "CAPACITY_MISSING": ("capacity_parameter_missing", True),
    "CAPACITY_OUT_OF_RANGE": ("capacity_parameter_invalid", True),
    "SULFUR_MISSING": ("sulfur_parameter_missing", True),
    "SULFUR_UNIT_INVALID": ("sulfur_parameter_invalid", True),
    "SULFUR_OUT_OF_RANGE": ("sulfur_parameter_invalid", True),
    "ASH_MISSING": ("ash_parameter_missing", True),
    "ASH_OUT_OF_RANGE": ("ash_parameter_invalid", True),
    "COAL_PARAMETER_NOT_FOUND": ("coal_parameter_missing", True),
    "COAL_PARTICLE_PARAMETER_MISSING": ("coal_parameter_missing", True),
    "COAL_BC_PARAMETER_MISSING": ("coal_parameter_missing", True),
    "COAL_OC_PARAMETER_MISSING": ("coal_parameter_missing", True),
    "T_CSES_COAL_PARAMETER_GAP": ("coal_parameter_missing", True),
    "T_CSES_COAL_BC_OC_PARAMETER_GAP": ("coal_parameter_missing", True),
    "FACTOR_UNIT_MISSING": ("parameter_unit_missing", True),
    "FACTOR_ACTIVITY_UNIT_MISMATCH": ("activity_parameter_unit_conflict", True),
    "PM25_PAIR_MISSING": ("pm25_dependency_missing", True),
    "PM_SIZE_CONSTRAINT_VIOLATION": ("particle_size_constraint_conflict", True),
    "PM_SIZE_RELATION": ("particle_size_constraint_conflict", True),
    "NEGATIVE_GENERATION": ("numeric_hard_constraint_conflict", True),
    "NEGATIVE_EMISSION": ("numeric_hard_constraint_conflict", True),
    "NH3_COAL_ACTIVITY_MISSING": ("ammonia_slip_activity_missing", True),
    "NH3_FACTOR_REQUIRES_COAL_ACTIVITY": ("ammonia_slip_activity_missing", True),
    "NH3_FACTOR_SCOPE_MISMATCH": ("ammonia_slip_activity_missing", True),
    "NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED": ("ammonia_slip_process_allocation_unresolved", True),
    "CONTROL_MISSING_ZERO": ("authorized_missing_control_zero", False),
    "CONTROL_UNMAPPED_ZERO": ("authorized_unmapped_control_zero", False),
    "CONTROL_TECH_UNMAPPED": ("authorized_unmapped_control_zero", False),
    "CONTROL_TECH_UNMAPPED_CANDIDATE": ("authorized_unmapped_control_zero", False),
    "CONTROL_RELATION_CATEGORY_LEVEL": ("authorized_control_relation_category_level", False),
    "CONTROL_RELATION_AMBIGUOUS": ("control_relation_ambiguous", True),
    "NO_APPLICABLE_CONTROL_ZERO": ("authorized_no_applicable_control_zero", False),
    "MULTI_PROCESS_MAX_ASSUMPTION": ("authorized_multiple_control_maximum", False),
    "MULTI_PROCESS_MAX_DEFAULT": ("authorized_multiple_control_maximum", False),
    "CONTROL_INFO_MISSING_DENOX": ("authorized_missing_control_metadata", False),
    "CONTROL_INFO_MISSING_DESULF": ("authorized_missing_control_metadata", False),
    "CONTROL_INFO_MISSING_DUST": ("authorized_missing_control_metadata", False),
    "DENITRIFICATION_INFO_MISSING_ZERO": ("authorized_missing_denitrification_zero_nh3", False),
    "DENOX_INFO_MISSING_ZERO": ("authorized_missing_denitrification_zero_nh3", False),
    "DENITRIFICATION_UNMAPPED_ZERO": ("authorized_unmapped_denitrification_zero_nh3", False),
    "NO_SCR_SNCR_ZERO": ("nh3_not_applicable_no_scr_sncr", False),
    "NO_SCR_SNCR": ("nh3_not_applicable_no_scr_sncr", False),
    "NH3_NOT_APPLICABLE_NO_SCR_SNCR": ("nh3_not_applicable_no_scr_sncr", False),
    "NH3_SCR_SNCR_SLIP": ("ammonia_slip_calculated", False),
    "CONTROLLED_NEGATIVE_CONTROL_EXPECTS_NO_SEMANTIC_CHANGE": ("controlled_negative_control", False),
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def canonical_number(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) < 5e-16:
        value = 0.0
    return format(value, ".15g")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_public_id(prefix: str, value: Any) -> str:
    payload = compact_json(value).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:20]}"


def identity_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def stable_pseudonym(prefix: str, value: Any) -> str:
    text = identity_scalar(value)
    if not text:
        return ""
    normalized = prefix.rstrip("-").upper()
    if text.startswith(normalized + "-"):
        return text
    digest = hashlib.sha256(f"{PSEUDONYM_SALT}|{text}".encode("utf-8")).hexdigest()[:12].upper()
    return f"{normalized}-{digest}"


def entity_id(values: dict[str, Any]) -> str:
    for prefix, field in (
        ("CREDIT", "统一社会信用代码"),
        ("ORG", "组织机构代码"),
        ("ENT", "填报单位详细名称"),
    ):
        result = stable_pseudonym(prefix, values.get(field))
        if result:
            return result
    raise ValueError("raw row has no stable enterprise identity")


def stable_raw_source_id(entity: Any, sequence: Any, year: Any) -> str:
    values = tuple(identity_scalar(item) for item in (entity, sequence, year))
    if not all(values):
        raise ValueError("source identity requires entity, Base-102 sequence and year")
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:20].upper()
    return f"SRC-RAW-{digest}"


def industry_code(value: Any) -> str:
    text = clean(value).split(".")[0]
    return text.zfill(4) if text.isdigit() else text


@dataclass(frozen=True)
class SourceDecision:
    target: str
    department: str
    equipment: str
    reason: str


def classify_raw_source(
    industry: Any,
    industrial_equipment: Any,
    power_equipment: Any,
) -> SourceDecision:
    """Independently classify one Base-102 candidate from raw business fields."""

    code = industry_code(industry)
    industrial = clean(industrial_equipment)
    power = clean(power_equipment)
    if code in {"4411", "4412", "4417"}:
        return SourceDecision("POWER", "电力生产", power or industrial, "T/CSES 7.1 electricity production")
    if code == "4430":
        return SourceDecision("POWER", "热力生产和供应", power or industrial, "T/CSES 7.1 heat production")
    try:
        numeric = int(code)
    except ValueError:
        numeric = -1
    equipment = industrial or power
    if 600 <= numeric <= 4399 and equipment in {"燃煤锅炉", "燃气锅炉", "燃油锅炉", "其他锅炉"}:
        return SourceDecision("INDUSTRIAL", "采矿业和制造业", equipment, "T/CSES 8.3 industrial boiler")
    return SourceDecision("EXCLUDE", "", equipment, "outside current fixed-combustion scope")


def standard_reference_ids(references: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for reference in references:
        text = clean(reference)
        for token, identifier in (
            ("表A.1", "T/CSES144-2024:A.1"),
            ("表C.1", "T/CSES144-2024:C.1"),
            ("表C.2", "T/CSES144-2024:C.2"),
            ("表C.3", "T/CSES144-2024:C.3"),
            ("表D.1", "T/CSES144-2024:D.1"),
            ("表E.1", "T/CSES144-2024:E.1"),
            ("表E.7", "T/CSES144-2024:E.7"),
        ):
            if token in text:
                result.add(identifier)
        if "公式(3)" in text:
            result.add("T/CSES144-2024:FORMULA-3")
        if "公式(4)" in text or "公式(5)" in text or "公式(6)" in text:
            result.add("T/CSES144-2024:FORMULA-4-6")
        if "冻结实施规则" in text or "实施协议" in text or "operational" in text:
            result.add("EXPERIMENT-PROTOCOL:V4")
    return sorted(result)


def normalized_method(mode: str) -> str:
    return {
        "constant": "emission_factor_method",
        "capacity_lookup": "emission_factor_method",
        "coal_sulfur_balance": "material_balance_method",
        "coal_particle_balance": "material_balance_method",
        "ammonia_slip": "ammonia_slip_factor_method",
        "empirical_zero": "authorized_empirical_disposition",
    }.get(clean(mode), clean(mode))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: Iterable[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class RuleCatalog:
    """Read-only independent lookup over the frozen reviewed rule tables."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.factors: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
        for filename in ("industrial_boiler_emission_factors.csv", "power_heat_emission_factors.csv"):
            for row in read_csv(self.root / "parameters" / filename):
                key = (row["sector"], row["department"], row["fuel"], row["combustion_technology"], row["pollutant"])
                if key in self.factors:
                    raise ValueError(f"duplicate factor key: {key}")
                self.factors[key] = row
        self.coal: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in read_csv(self.root / "parameters" / "coal_parameters.csv"):
            key = (row["sector"], row["department"], row["combustion_technology"])
            if key in self.coal:
                raise ValueError(f"duplicate coal parameter key: {key}")
            self.coal[key] = row

    @staticmethod
    def sector_department(target: str, raw_industry_code: Any) -> tuple[str, str]:
        if target == "INDUSTRIAL":
            return "工业源", "采矿业和制造业"
        if target == "POWER":
            department = "热力生产和供应" if industry_code(raw_industry_code) == "4430" else "电力生产"
            return "电力热力源", department
        raise ValueError(f"no factor sector for target {target}")

    def factor_row(
        self,
        target: str,
        raw_industry_code: Any,
        fuel: str,
        technology: str,
        pollutant: str,
    ) -> dict[str, str] | None:
        sector, department = self.sector_department(target, raw_industry_code)
        exact = self.factors.get((sector, department, fuel, technology, pollutant))
        if exact is not None:
            return exact
        # This is a catalog-declared fallback, not a general technology guess:
        # it is used only when the table itself provides a row keyed as “不分技术”.
        return self.factors.get((sector, department, fuel, "不分技术", pollutant))

    def coal_row(self, target: str, raw_industry_code: Any, technology: str) -> dict[str, str] | None:
        sector, department = self.sector_department(target, raw_industry_code)
        return self.coal.get((sector, department, technology))


@dataclass(frozen=True)
class Source:
    variant: str
    source_id: str
    tag: str
    row_number: int
    target: str
    values: dict[str, Any]


@dataclass
class FuelComponent:
    slot: str
    raw_fuel: str
    raw_amount: Any
    raw_unit: str
    sulfur: Any
    sulfur_unit: str
    ash: Any
    standard_fuel: str = ""
    mapping_status: str = ""
    calculation_technology: str = ""
    factor_technology: str = ""
    activity_value: float | None = None
    activity_unit: str = ""
    foundational_codes: list[str] | None = None

    def __post_init__(self) -> None:
        if self.foundational_codes is None:
            self.foundational_codes = []


class ReferenceBuilder:
    def __init__(self) -> None:
        if not RAW_INPUT_MANIFEST.is_file():
            raise FileNotFoundError(RAW_INPUT_MANIFEST)
        self.raw_manifest = json.loads(RAW_INPUT_MANIFEST.read_text(encoding="utf-8"))
        if self.raw_manifest.get("input_form") != "three_raw_environmental_base_tables":
            raise ValueError("reference v2 requires the three raw environmental base-table roles")
        if B2_INJECTION_MANIFEST.is_file():
            self.b2_manifest = json.loads(B2_INJECTION_MANIFEST.read_text(encoding="utf-8"))
        else:
            self.b2_manifest = {"version": "2.0.0", "status": "placeholder", "records": []}
        self.b2_manifest.setdefault("status", "formal" if self.b2_manifest.get("records") else "placeholder")
        self.injections = list(self.b2_manifest.get("records") or [])
        self.injection_by_source = {record["source_id"]: record for record in self.injections}
        if len(self.injection_by_source) != len(self.injections):
            raise ValueError("B2 manifest repeats one or more source IDs")

        fuel_rows = read_csv(RULE_ROOT / "mappings" / "source_fuel_aliases.csv")
        self.fuel_map = {clean(row["raw_value"]): row for row in fuel_rows}
        standard_fuels = read_csv(RULE_ROOT / "mappings" / "standard_fuels.csv")
        self.fuel_category = {row["standard_value"]: row["category"] for row in standard_fuels}
        combustion_rows = read_csv(RULE_ROOT / "mappings" / "source_combustion_aliases.csv")
        self.combustion_map = {clean(row["raw_value"]): row for row in combustion_rows}

        control_rows = read_csv(RULE_ROOT / "mappings" / "source_control_aliases.csv")
        self.control_map: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in sorted(control_rows, key=lambda item: (item["raw_field"], item["raw_value"], int(item["component_order"]))):
            self.control_map[(row["raw_field"], clean(row["raw_value"]))].append(row["standard_control_technology"])

        efficiency_rows = read_csv(RULE_ROOT / "parameters" / "control_efficiencies.csv")
        self.control_efficiency = {
            (row["control_technology"], row["pollutant"]): (float(row["efficiency_percent"]) / 100.0, row["parameter_id"])
            for row in efficiency_rows
        }
        self.catalog = RuleCatalog(RULE_ROOT)
        self.factors = self.catalog.factors
        self.coal_parameters = self.catalog.coal
        self.ammonia = {row["process"]: row for row in read_csv(RULE_ROOT / "parameters" / "ammonia_slip_factors.csv")}
        self._baseline_sources = self._read_raw_sources()
        self.baseline_targets = {source.source_id: source.target for source in self._baseline_sources}
        unknown_injections = sorted(set(self.injection_by_source) - set(self.baseline_targets))
        if unknown_injections:
            raise ValueError(f"B2 manifest contains unknown source IDs: {unknown_injections[:5]}")
        self.injection_by_key = {
            (source_id, self.baseline_targets[source_id]): record
            for source_id, record in self.injection_by_source.items()
        }
        if self.b2_manifest.get("status") != "placeholder":
            self.verify_b2_materialization()

    def workbook_specs(self, role: str) -> list[dict[str, Any]]:
        specs = [row for row in self.raw_manifest.get("workbooks", []) if row.get("role") == role]
        if not specs:
            raise ValueError(f"raw input manifest has no workbook for role {role}")
        return specs

    def verify_b2_materialization(self) -> None:
        if not B2_VARIANT_MANIFEST.is_file():
            raise FileNotFoundError(B2_VARIANT_MANIFEST)
        variant = json.loads(B2_VARIANT_MANIFEST.read_text(encoding="utf-8"))
        device = next(
            (row for row in variant.get("workbooks", []) if row.get("filename", "").startswith("基102-2002")),
            None,
        )
        if not device:
            raise ValueError("B2 variant manifest has no Base-102 workbook")
        path = B2_VARIANT_MANIFEST.parent / device["filename"]
        rows = self.read_workbook_rows({
            "output_path": str(path), "output_sha256": device.get("sha256"), "sheet": "Sheet1",
        })
        by_source: dict[str, dict[str, Any]] = {}
        for _, values in rows:
            if not clean(values.get("统计年份")) or not clean(values.get("序号")):
                continue
            source_id = stable_raw_source_id(entity_id(values), values.get("序号"), values.get("统计年份"))
            by_source[source_id] = values
        for injection in self.injections:
            actual = by_source.get(injection["source_id"])
            if actual is None:
                raise ValueError(f"B2 materialized workbook misses {injection['source_id']}")
            for change in injection.get("changes") or []:
                field, expected = change["field"], change.get("after")
                observed = actual.get(field)
                expected_number, observed_number = number(expected), number(observed)
                equal = (
                    abs(expected_number - observed_number) <= 1e-12
                    if expected_number is not None and observed_number is not None
                    else clean(expected) == clean(observed)
                )
                if not equal:
                    raise ValueError(
                        f"B2 materialization mismatch {injection['source_id']}:{field}: {observed!r}!={expected!r}"
                    )

    @staticmethod
    def read_workbook_rows(spec: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
        path = Path(spec["output_path"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if spec.get("output_sha256") and sha256(path) != spec["output_sha256"]:
            raise ValueError(f"raw input hash mismatch: {path}")
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook[spec.get("sheet") or workbook.sheetnames[0]]
            iterator = sheet.iter_rows(values_only=True)
            headers: list[str] = []
            seen: Counter[str] = Counter()
            for value in next(iterator):
                name = clean(value)
                seen[name] += 1
                headers.append(name if seen[name] == 1 else f"{name}__{seen[name]}")
            return [
                (row_number, dict(zip(headers, row)))
                for row_number, row in enumerate(iterator, start=2)
            ]
        finally:
            workbook.close()

    @staticmethod
    def decimal_coordinate(values: dict[str, Any], prefix: str) -> float | None:
        degree = number(values.get(f"{prefix}_度"))
        minute = number(values.get(f"{prefix}_分")) or 0.0
        second = number(values.get(f"{prefix}_秒")) or 0.0
        return None if degree is None else degree + minute / 60.0 + second / 3600.0

    def _read_raw_sources(self) -> list[Source]:
        enterprise: dict[str, dict[str, Any]] = {}
        for spec in self.workbook_specs("enterprise_master"):
            for _, values in self.read_workbook_rows(spec):
                if not clean(values.get("统计年份")):
                    continue
                enterprise.setdefault(entity_id(values), values)
        controls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for spec in self.workbook_specs("control_facility_base"):
            for _, values in self.read_workbook_rows(spec):
                if not clean(values.get("统计年份")):
                    continue
                controls[entity_id(values)].append(values)

        result: list[Source] = []
        for spec in self.workbook_specs("device_fuel_base"):
            for row_number, values in self.read_workbook_rows(spec):
                if not clean(values.get("统计年份")) or not clean(values.get("填报单位详细名称")):
                    continue
                identity = entity_id(values)
                source_id = stable_raw_source_id(identity, values.get("序号"), values.get("统计年份"))
                decision = classify_raw_source(
                    values.get("行业类别代码"),
                    values.get("工业锅炉类型"),
                    values.get("电站锅炉/燃气轮机类型"),
                )
                enriched = dict(values)
                enriched["电站是否采用低氮燃烧技术"] = values.get("是否采用低氮燃烧技术")
                enriched["工业是否采用低氮燃烧技术"] = values.get("是否采用低氮燃烧技术__2")
                enriched["_实体ID"] = identity
                enriched["_源分类部门"] = decision.department
                enriched["_源分类理由"] = decision.reason
                enriched["_企业主表"] = enterprise.get(identity, {})
                enriched["_治理设施候选"] = controls.get(identity, [])
                enriched["排放口地理坐标经度（度）"] = self.decimal_coordinate(enriched, "排放口地理坐标经度")
                enriched["排放口地理坐标纬度（度）"] = self.decimal_coordinate(enriched, "排放口地理坐标纬度")
                enriched["对应机组装机容量（万千瓦）（1万千瓦=10MV）"] = enriched.get("对应机组装机容量（万千瓦）")
                result.append(Source("B0", source_id, "RAW", row_number, decision.target, enriched))
        if len(result) != int(self.raw_manifest.get("candidate_count", len(result))):
            raise ValueError(f"raw candidate count mismatch: {len(result)}")
        if len({source.source_id for source in result}) != len(result):
            raise ValueError("target-neutral source identity collision")
        return result

    @staticmethod
    def classify_values(tag: str, values: dict[str, Any]) -> str:
        del tag
        return classify_raw_source(
            values.get("行业类别代码"),
            values.get("工业锅炉类型"),
            values.get("电站锅炉/燃气轮机类型"),
        ).target

    @staticmethod
    def _apply_injection(values: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        changed = copy.deepcopy(values)
        updates = dict(record.get("field_updates") or record.get("updates") or {})
        for mutation in record.get("mutations") or []:
            if isinstance(mutation, dict) and mutation.get("field"):
                updates[str(mutation["field"])] = mutation.get("value")
        for change in record.get("changes") or []:
            if isinstance(change, dict) and change.get("field"):
                updates[str(change["field"])] = change.get("after")
        for field, value in updates.items():
            changed[field] = value
        changed["_注入根因"] = record.get("root_cause", "")
        return changed

    def read_sources(self, variant: str) -> list[Source]:
        if variant == "B0":
            return [Source("B0", row.source_id, row.tag, row.row_number, row.target, copy.deepcopy(row.values)) for row in self._baseline_sources]
        if variant != "B2":
            raise ValueError(variant)
        result = []
        for row in self._baseline_sources:
            record = self.injection_by_key.get((row.source_id, row.target))
            values = self._apply_injection(row.values, record) if record else copy.deepcopy(row.values)
            # The target population is frozen from B0; a relationship mutation
            # is evaluated by the source gate rather than silently reclassifying it.
            result.append(Source("B2", row.source_id, row.tag, row.row_number, row.target, values))
        return result

    @staticmethod
    def source_field(source: Source, industrial_name: str, power_name: str) -> Any:
        if source.target == "INDUSTRIAL":
            return source.values.get(industrial_name) or source.values.get(power_name)
        return source.values.get(power_name) or source.values.get(industrial_name)

    def source_gate(self, source: Source) -> tuple[str, list[str], list[str]]:
        if source.target == "EXCLUDE":
            return "not_applicable", [], []
        required = {
            "填报单位详细名称": source.values.get("填报单位详细名称"),
            "行业类别代码": source.values.get("行业类别代码"),
            "设备类型": self.source_field(source, "工业锅炉类型", "电站锅炉/燃气轮机类型"),
            "排放口编号": source.values.get("排放口编号"),
            "经度": source.values.get("排放口地理坐标经度（度）"),
            "纬度": source.values.get("排放口地理坐标纬度（度）"),
        }
        missing = [name for name, value in required.items() if clean(value) == ""]
        blocking: list[str] = []
        if missing:
            blocking.append("SOURCE_RELATION_MISSING:" + "+".join(missing))
        if clean(source.values.get("_注入根因")) == "SOURCE_RELATION_CONFLICT":
            blocking.append("SOURCE_RELATION_CONFLICT")
        notices: list[str] = []
        longitude = number(required["经度"])
        latitude = number(required["纬度"])
        if longitude is not None and latitude is not None and not (109 <= longitude <= 118 and 20 <= latitude <= 26):
            notices.append("COORDINATE_REVIEW_REQUIRED")
        first = tuple(clean(source.values.get(field)) for field in (
            "燃料一类型", "燃料一消耗量", "燃料一消耗量单位",
            "燃料一平均收到基含硫量", "燃料一平均收到基灰分（%）",
        ))
        second = tuple(clean(source.values.get(field)) for field in (
            "燃料二类型", "燃料二消耗量", "燃料二消耗量单位",
            "燃料二平均收到基含硫量", "燃料二平均收到基灰分（%）",
        ))
        if first[0] and first == second:
            notices.append("POTENTIAL_DUPLICATE_FUEL_SLOT")
        return ("source_data_invalid" if blocking else "admitted", blocking, notices)

    def source_row(self, source: Source) -> dict[str, Any]:
        status, blocking, notices = self.source_gate(source)
        target_label = source.target
        disposition = {
            "INDUSTRIAL": "include_industrial_boiler",
            "POWER": "include_power_heat",
            "EXCLUDE": "exclude_outside_two_source_categories",
        }[source.target]
        equipment = self.source_field(source, "工业锅炉类型", "电站锅炉/燃气轮机类型")
        active_fuels = [
            fuel for fuel in self.fuel_components(source)
            if "INACTIVE_ZERO_ACTIVITY" not in fuel.foundational_codes
        ]
        return {
            "variant": source.variant,
            "source_id": source.source_id,
            "source_tag": source.tag,
            "source_row": source.row_number,
            "expected_target": target_label,
            "expected_disposition": disposition,
            "source_gate_status": status,
            "source_gate_reasons": compact_json(blocking),
            "source_quality_notices": compact_json(notices),
            "inventory_year": source.values.get("统计年份", ""),
            "industry_code": source.values.get("行业类别代码", ""),
            "industry_name": source.values.get("行业类别名称", ""),
            "equipment_type": equipment,
            "outlet_id": source.values.get("排放口编号", ""),
            "longitude": source.values.get("排放口地理坐标经度（度）", ""),
            "latitude": source.values.get("排放口地理坐标纬度（度）", ""),
            "expected_item_count": 0 if source.target == "EXCLUDE" else max(1, len(active_fuels)) * 8 + 1,
        }

    def sector_department(self, source: Source) -> tuple[str, str, str]:
        if source.target == "INDUSTRIAL":
            return "工业源", "采矿业和制造业", "采矿业和制造业"
        code = industry_code(source.values.get("行业类别代码"))
        department = "热力生产和供应" if code == "4430" else "电力生产"
        return "电力热力源", department, department

    def fuel_components(self, source: Source) -> list[FuelComponent]:
        if source.tag == "IND":
            first = ("燃料一", "燃料一类型", "燃料一消耗量", "燃料一消耗量单位", "燃料一平均收到基含硫量", "燃料一平均收到基含硫量单位", "燃料一平均收到基灰分（%）")
            second = ("燃料二", "燃料二类型", "燃料二消耗量", "燃料二消耗量单位", "燃料二平均收到基含硫量", "燃料二平均收到基含硫量单位", "燃料二平均收到基灰分（%）")
        else:
            first = ("燃料一", "燃料一类型", "燃料一消耗量", "燃料一消耗量单位", "燃料一平均收到基含硫量", "燃料一平均收到基含硫量单位", "燃料一平均收到基灰分（%）")
            second = ("燃料二", "燃料二类型", "燃料二消耗量", "燃料二消耗量单位", "燃料二平均收到基含硫量", "燃料二平均收到基含硫量单位", "燃料二平均收到基灰分（%）")
        result: list[FuelComponent] = []
        for slot, ftype, amount, unit, sulfur, sulfur_unit, ash in (first, second):
            raw_fuel = clean(source.values.get(ftype))
            raw_amount = source.values.get(amount)
            raw_unit = clean(source.values.get(unit))
            numeric_amount = number(raw_amount)
            if not raw_fuel and (numeric_amount is None or numeric_amount == 0) and not raw_unit:
                continue
            result.append(FuelComponent(
                slot, raw_fuel, raw_amount, raw_unit,
                source.values.get(sulfur), clean(source.values.get(sulfur_unit)), source.values.get(ash),
            ))
        other = number(source.values.get("其他燃料消耗总量（吨标准煤）"))
        if other is not None and other != 0:
            result.append(FuelComponent("其他燃料", "其他燃料（吨标准煤未分类）", other, "吨标准煤", "", "", ""))
        return [self.normalize_fuel(source, component) for component in result]

    def normalize_fuel(self, source: Source, fuel: FuelComponent) -> FuelComponent:
        if fuel.slot == "其他燃料":
            amount = number(fuel.raw_amount)
            if amount is None:
                fuel.foundational_codes.append("ACTIVITY_MISSING")
            elif amount < 0:
                fuel.foundational_codes.append("ACTIVITY_OUT_OF_RANGE")
            elif amount > 0:
                fuel.foundational_codes.append("OTHER_FUEL_TCE_UNRESOLVED")
            return fuel
        if "其他燃料" in fuel.raw_fuel and fuel.raw_unit == "吨标准煤":
            amount = number(fuel.raw_amount)
            if amount is None:
                fuel.foundational_codes.append("ACTIVITY_MISSING")
            elif amount < 0:
                fuel.foundational_codes.append("ACTIVITY_OUT_OF_RANGE")
            elif amount == 0:
                fuel.foundational_codes.append("INACTIVE_ZERO_ACTIVITY")
            else:
                fuel.foundational_codes.append("OTHER_FUEL_TCE_UNRESOLVED")
            return fuel
        mapping = self.fuel_map.get(fuel.raw_fuel)
        if mapping is None:
            fuel.mapping_status = "unmapped"
            fuel.foundational_codes.append("FUEL_UNMAPPED")
        else:
            fuel.mapping_status = mapping["mapping_status"]
            fuel.standard_fuel = mapping["standard_fuel"]
            if mapping["mapping_status"] != "mapped":
                fuel.foundational_codes.append(
                    "FUEL_OUTSIDE_SCOPE" if mapping["mapping_status"] == "outside_fossil_scope" else "FUEL_UNMAPPED"
                )
        if not fuel.raw_fuel:
            fuel.foundational_codes.append("FUEL_TYPE_MISSING")

        amount = number(fuel.raw_amount)
        if amount is None:
            fuel.foundational_codes.append("ACTIVITY_MISSING")
        elif amount < 0:
            fuel.foundational_codes.append("ACTIVITY_OUT_OF_RANGE")
        elif amount == 0:
            fuel.foundational_codes.append("INACTIVE_ZERO_ACTIVITY")
        else:
            if fuel.raw_unit == "吨":
                fuel.activity_value, fuel.activity_unit = amount * 1000.0, "kg"
            elif fuel.raw_unit == "万立方米":
                fuel.activity_value, fuel.activity_unit = amount * 10000.0, "m3"
            else:
                fuel.foundational_codes.append("ACTIVITY_UNIT_UNSUPPORTED")

        category = self.fuel_category.get(fuel.standard_fuel, "")
        raw_combustion = clean(self.source_field(source, "工业锅炉燃烧方式", "电站锅炉燃烧方式"))
        mapped_combustion = self.combustion_map.get(raw_combustion, {})
        mapped_technology = clean(mapped_combustion.get("standard_combustion_technology"))
        if category == "气体燃料":
            fuel.calculation_technology = fuel.factor_technology = "燃气锅炉"
        elif category == "液体燃料":
            fuel.calculation_technology = fuel.factor_technology = "燃油锅炉"
        elif fuel.standard_fuel == "煤炭":
            fuel.calculation_technology = fuel.factor_technology = mapped_technology
            if not mapped_technology:
                fuel.foundational_codes.append("COMBUSTION_TECHNOLOGY_UNMAPPED")
        elif category == "固体燃料":
            fuel.calculation_technology = mapped_technology
            fuel.factor_technology = "不分技术"
        return fuel

    def control_components(self, source: Source) -> tuple[list[str], list[str], list[str]]:
        facility_fields = {
            "脱硫（酸）设施": "脱硫处理工艺名称",
            "脱硝设施": "脱硝处理工艺名称",
            "除尘设施": "除尘处理工艺名称",
        }
        outlet_category = "GYGL" if source.target == "INDUSTRIAL" else "DZGL"
        technologies: list[str] = []
        raw_nonblank: list[str] = []
        unmapped: list[str] = []
        for candidate in source.values.get("_治理设施候选", []):
            if clean(candidate.get("对应的排放口代码")) != outlet_category:
                continue
            field = facility_fields.get(clean(candidate.get("废气治理设施名称")))
            if not field:
                continue
            raw = clean(candidate.get("处理工艺名称"))
            if not raw:
                continue
            raw_nonblank.append(field)
            matches = self.control_map.get((field, raw), [])
            if not matches:
                unmapped.append(field)
            technologies.extend(matches)
        low_nox = clean(self.source_field(source, "工业是否采用低氮燃烧技术", "电站是否采用低氮燃烧技术"))
        if low_nox == "是" and not any("低氮燃烧技术" in technology for technology in technologies):
            technologies.append("低氮燃烧技术")
        # Preserve order while removing duplicates.
        technologies = list(dict.fromkeys(technologies))
        return technologies, raw_nonblank, unmapped

    def nh3_processes(self, source: Source) -> tuple[str, list[tuple[str, dict[str, str]]]]:
        outlet_category = "GYGL" if source.target == "INDUSTRIAL" else "DZGL"
        texts = [
            clean(candidate.get("处理工艺名称"))
            for candidate in source.values.get("_治理设施候选", [])
            if clean(candidate.get("对应的排放口代码")) == outlet_category
            and clean(candidate.get("处理工艺名称"))
        ]
        text = "+".join(texts)
        processes: dict[str, dict[str, str]] = {}
        if "SCR" in text or ("选择性催化还原" in text and "非催化" not in text):
            processes["SCR"] = self.ammonia["脱硝烟气-选择性催化还原"]
        if "SNCR" in text or "选择性非催化还原" in text:
            processes["SNCR"] = self.ammonia["脱硝烟气-选择性非催化还原"]
        return text, sorted(processes.items())

    def control_for(self, source: Source, pollutant: str) -> dict[str, Any]:
        technologies, raw_nonblank, unmapped = self.control_components(source)
        if pollutant == "NH3":
            return {"technologies": technologies, "parameter_ids": [], "efficiency": 0.0,
                    "fine_efficiency": None, "coarse_efficiency": None, "label": "NH3独立路径"}
        lookup_pollutant = PARAMETER_POLLUTANT[pollutant]
        candidate_technologies = list(technologies)
        if pollutant == "NOx":
            has_lnb = any(technology == "低氮燃烧技术" for technology in technologies)
            has_scr = any(technology == "选择性催化还原法" for technology in technologies)
            has_sncr = any(technology == "选择性非催化还原法" for technology in technologies)
            explicit = [technology for technology in technologies if technology.startswith("低氮燃烧技术+")]
            if not explicit and has_lnb and has_scr:
                explicit = ["低氮燃烧技术+选择性催化还原法"]
            elif not explicit and has_lnb and has_sncr:
                explicit = ["低氮燃烧技术+选择性非催化还原法"]
            if explicit:
                candidate_technologies = explicit + [
                    technology for technology in technologies
                    if technology not in {"低氮燃烧技术", "选择性催化还原法", "选择性非催化还原法"}
                ]
        candidates: list[tuple[float, str, str]] = []
        for technology in candidate_technologies:
            value = self.control_efficiency.get((technology, lookup_pollutant))
            if value is not None:
                candidates.append((value[0], value[1], technology))
        if candidates:
            best_efficiency = max(value[0] for value in candidates)
            best = [value for value in candidates if value[0] == best_efficiency]
            chosen = best[0]
            if any(technology.startswith("低氮燃烧技术+") for _, _, technology in best):
                label = "标准组合工艺缺省效率"
            elif len(candidates) > 1:
                label = "多工艺取最大缺省效率"
            elif chosen[2] == "低氮燃烧技术":
                label = "低氮燃烧缺省效率"
            else:
                label = "工艺缺省效率"
            return {
                "technologies": technologies, "parameter_ids": [value[1] for value in best],
                "efficiency": best_efficiency, "fine_efficiency": None, "coarse_efficiency": None,
                "label": label, "unmapped_fields": unmapped,
            }
        if unmapped:
            label = "治理工艺无法映射按0"
        elif not raw_nonblank and not technologies:
            label = "治理信息缺失按0"
        else:
            label = "无适用治理措施按0"
        return {
            "technologies": technologies, "parameter_ids": [], "efficiency": 0.0,
            "fine_efficiency": None, "coarse_efficiency": None, "label": label,
            "unmapped_fields": unmapped,
        }

    def control_pm10(self, source: Source) -> dict[str, Any]:
        technologies, raw_nonblank, unmapped = self.control_components(source)
        fine: list[tuple[float, str, str]] = []
        coarse: list[tuple[float, str, str]] = []
        for technology in technologies:
            fine_value = self.control_efficiency.get((technology, "PM25"))
            coarse_value = self.control_efficiency.get((technology, "PM25_10"))
            if fine_value:
                fine.append((fine_value[0], fine_value[1], technology))
            if coarse_value:
                coarse.append((coarse_value[0], coarse_value[1], technology))
        fine_eff = max((value[0] for value in fine), default=0.0)
        coarse_eff = max((value[0] for value in coarse), default=0.0)
        parameter_ids = [value[1] for value in fine if value[0] == fine_eff]
        parameter_ids += [value[1] for value in coarse if value[0] == coarse_eff]
        if fine or coarse:
            applicable_technologies = {value[2] for value in fine + coarse}
            label = "多工艺取最大缺省效率" if len(applicable_technologies) > 1 else "工艺缺省效率"
        elif unmapped:
            label = "治理工艺无法映射按0"
        elif not raw_nonblank and not technologies:
            label = "治理信息缺失按0"
        else:
            label = "无适用治理措施按0"
        return {
            "technologies": technologies, "parameter_ids": list(dict.fromkeys(parameter_ids)),
            "efficiency": None, "fine_efficiency": fine_eff, "coarse_efficiency": coarse_eff,
            "label": label, "unmapped_fields": unmapped,
        }

    def factor_for(self, source: Source, fuel: FuelComponent, pollutant: str) -> dict[str, Any]:
        sector, department, coal_department = self.sector_department(source)
        parameter_pollutant = PARAMETER_POLLUTANT[pollutant]
        factor_row = self.catalog.factor_row(
            source.target,
            source.values.get("行业类别代码"),
            fuel.standard_fuel,
            fuel.factor_technology,
            parameter_pollutant,
        )
        result: dict[str, Any] = {
            "parameter_id": "", "mode": "", "factor_value": None, "factor_unit": "",
            "reference": "", "reason_codes": list(fuel.foundational_codes), "fatal_invalid": False,
        }
        if result["reason_codes"]:
            result["fatal_invalid"] = "ACTIVITY_OUT_OF_RANGE" in result["reason_codes"]
            return result
        if factor_row is None:
            result["reason_codes"].append("EMISSION_FACTOR_NOT_FOUND")
            return result
        result["parameter_id"] = factor_row["parameter_id"]
        result["mode"] = factor_row["mode"]
        result["reference"] = f"{factor_row['source_section']}|PDF-p{factor_row['physical_pdf_page']}"
        mode = factor_row["mode"]
        if mode == "constant":
            result["factor_value"] = float(factor_row["value"])
        elif mode == "capacity_lookup":
            raw_capacity = self.source_field(source, "", "对应机组装机容量（万千瓦）（1万千瓦=10MV）")
            capacity = number(raw_capacity)
            if capacity is None:
                result["reason_codes"].append("CAPACITY_MISSING")
                return result
            capacity_mw = capacity * 10.0
            if capacity_mw <= 0:
                result["reason_codes"].append("CAPACITY_OUT_OF_RANGE")
                result["fatal_invalid"] = True
                return result
            result["factor_value"] = 8.96 if capacity_mw <= 100 else 8.19 if capacity_mw < 300 else 7.21
        elif mode in {"coal_sulfur_balance", "coal_particle_balance"}:
            coal = self.catalog.coal_row(
                source.target,
                source.values.get("行业类别代码"),
                fuel.calculation_technology,
            )
            if coal is None:
                result["reason_codes"].append("COAL_PARAMETER_NOT_FOUND")
                return result
            result["parameter_id"] += "+" + coal["parameter_id"]
            result["reference"] += f";{coal['source_section']}|PDF-p{coal['physical_pdf_page']}"
            if mode == "coal_sulfur_balance":
                sulfur = number(fuel.sulfur)
                if sulfur is None:
                    result["reason_codes"].append("SULFUR_MISSING")
                    return result
                if fuel.sulfur_unit != "%":
                    result["reason_codes"].append("SULFUR_UNIT_INVALID")
                    return result
                if not 0 <= sulfur <= 100:
                    result["reason_codes"].append("SULFUR_OUT_OF_RANGE")
                    result["fatal_invalid"] = True
                    return result
                result["factor_value"] = 20.0 * sulfur * (1.0 - float(coal["sulfur_to_bottom_ash"]))
            else:
                ash = number(fuel.ash)
                if ash is None:
                    result["reason_codes"].append("ASH_MISSING")
                    return result
                if not 0 <= ash <= 100:
                    result["reason_codes"].append("ASH_OUT_OF_RANGE")
                    result["fatal_invalid"] = True
                    return result
                pm25_fraction = number(coal["pm25_fraction"])
                pm10_fraction = number(coal["pm10_fraction"])
                ash_to_bottom = number(coal["ash_to_bottom_ash"])
                if ash_to_bottom is None or pm25_fraction is None or pm10_fraction is None:
                    result["reason_codes"].append("COAL_PARTICLE_PARAMETER_MISSING")
                    return result
                pm25 = 10.0 * ash * (1.0 - ash_to_bottom) * pm25_fraction
                pm10 = 10.0 * ash * (1.0 - ash_to_bottom) * pm10_fraction
                if pollutant == "PM2.5":
                    result["factor_value"] = pm25
                elif pollutant == "PM10":
                    result["factor_value"] = pm10
                elif pollutant == "BC":
                    fraction = number(coal["bc_fraction_of_pm25"])
                    if fraction is None:
                        result["reason_codes"].append("COAL_BC_PARAMETER_MISSING")
                        return result
                    result["factor_value"] = pm25 * fraction
                elif pollutant == "OC":
                    fraction = number(coal["oc_fraction_of_pm25"])
                    if fraction is None:
                        result["reason_codes"].append("COAL_OC_PARAMETER_MISSING")
                        return result
                    result["factor_value"] = pm25 * fraction
        else:
            result["reason_codes"].append("UNSUPPORTED_FACTOR_MODE")
            return result

        if mode in {"coal_sulfur_balance", "coal_particle_balance", "capacity_lookup"}:
            result["factor_unit"] = "g/kg"
        else:
            raw_factor_unit = clean(factor_row.get("unit"))
            result["factor_unit"] = "g/m3" if raw_factor_unit.startswith("g/m3") else "g/kg" if raw_factor_unit.startswith("g/kg") else ""
            if not result["factor_unit"]:
                result["reason_codes"].append("FACTOR_UNIT_MISSING")
                result["factor_value"] = None
                return result
        factor_basis = "m3" if result["factor_unit"] == "g/m3" else "kg"
        if fuel.activity_unit != factor_basis:
            result["reason_codes"].append("FACTOR_ACTIVITY_UNIT_MISMATCH")
            result["factor_value"] = None
        return result

    def conventional_item(self, source: Source, pollutant: str) -> dict[str, Any]:
        source_status, source_reasons, source_notices = self.source_gate(source)
        if source_status == "source_data_invalid":
            return self.item_record(source, pollutant, "source_data_invalid", None, None, [], [], {}, source_reasons, [], "source_relation_gate")
        fuels = [fuel for fuel in self.fuel_components(source) if "INACTIVE_ZERO_ACTIVITY" not in fuel.foundational_codes]
        if not fuels:
            return self.item_record(source, pollutant, "information_insufficient", None, None, [], [], {}, ["NO_ACTIVE_FUEL"], [], "frozen_rules")
        activities: list[dict[str, Any]] = []
        parameters: list[dict[str, Any]] = []
        generation_components: list[float] = []
        reason_codes: list[str] = list(source_notices)
        references: list[str] = []
        fatal_invalid = False
        for fuel in fuels:
            factor = self.factor_for(source, fuel, pollutant)
            activities.append({
                "slot": fuel.slot, "raw_fuel": fuel.raw_fuel, "normalized_fuel": fuel.standard_fuel,
                "combustion_technology": fuel.calculation_technology,
                "value": fuel.activity_value, "unit": fuel.activity_unit,
            })
            parameters.append({
                "parameter_id": factor["parameter_id"], "mode": factor["mode"],
                "value": factor["factor_value"], "unit": factor["factor_unit"],
            })
            reason_codes.extend(factor["reason_codes"])
            fatal_invalid = fatal_invalid or factor["fatal_invalid"]
            if factor["reference"]:
                references.append(factor["reference"])
            if fuel.activity_value is not None and factor["factor_value"] is not None:
                generation_components.append(fuel.activity_value * factor["factor_value"] / 1_000_000.0)
        blocking_codes = [code for code in reason_codes if code not in {"COORDINATE_REVIEW_REQUIRED"}]
        if blocking_codes:
            status = "source_data_invalid" if fatal_invalid else "information_insufficient"
            return self.item_record(source, pollutant, status, None, None, activities, parameters, {}, reason_codes, references, "frozen_rules")
        generation = sum(generation_components)
        control = self.control_pm10(source) if pollutant == "PM10" else self.control_for(source, pollutant)
        control_reasons = []
        if control.get("unmapped_fields"):
            control_reasons.append("CONTROL_UNMAPPED_ZERO")
        label = control["label"]
        if label == "治理信息缺失按0":
            control_reasons.append("CONTROL_MISSING_ZERO")
        elif label == "无适用治理措施按0":
            control_reasons.append("NO_APPLICABLE_CONTROL_ZERO")
        elif label == "多工艺取最大缺省效率":
            control_reasons.append("MULTI_PROCESS_MAX_ASSUMPTION")
        reason_codes.extend(control_reasons)
        if control.get("parameter_ids"):
            references.append("附录A 表A.1|PDF-p33-35")
        if label in {"治理信息缺失按0", "治理工艺无法映射按0", "无适用治理措施按0", "多工艺取最大缺省效率"}:
            references.append("冻结实施规则v1.0.1|operational-control-disposition")
        if pollutant == "PM10":
            pm25_factors = []
            for fuel in fuels:
                factor = self.factor_for(source, fuel, "PM2.5")
                if factor["factor_value"] is None or fuel.activity_value is None:
                    return self.item_record(source, pollutant, "information_insufficient", None, None, activities, parameters, control, reason_codes + ["PM25_PAIR_MISSING"], references, "frozen_rules")
                pm25_factors.append(fuel.activity_value * factor["factor_value"] / 1_000_000.0)
            fine_generation = sum(pm25_factors)
            if fine_generation > generation + 1e-12:
                return self.item_record(source, pollutant, "source_data_invalid", None, None, activities, parameters, control, reason_codes + ["PM_SIZE_CONSTRAINT_VIOLATION"], references, "frozen_rules")
            emission = fine_generation * (1.0 - control["fine_efficiency"]) + (generation - fine_generation) * (1.0 - control["coarse_efficiency"])
        else:
            emission = generation * (1.0 - control["efficiency"])
        return self.item_record(source, pollutant, "calculated", generation, emission, activities, parameters, control, reason_codes, references, "frozen_rules")

    def nh3_item(self, source: Source) -> dict[str, Any]:
        pollutant = "NH3"
        source_status, source_reasons, source_notices = self.source_gate(source)
        if source_status == "source_data_invalid":
            return self.item_record(source, pollutant, "source_data_invalid", None, None, [], [], {}, source_reasons, [], "source_relation_gate")
        technologies, _, _ = self.control_components(source)
        denox_text, processes = self.nh3_processes(source)
        if not processes:
            if denox_text:
                return self.item_record(
                    source, pollutant, "not_involved", None, None, [], [],
                    {"technologies": technologies, "label": "无SCR/SNCR，NH3不适用"},
                    source_notices + ["NH3_NOT_APPLICABLE_NO_SCR_SNCR"],
                    ["附录E 表E.1|PDF-p50"], "NH3_process_not_applicable",
                )
            activity = [{"slot": "NH3_zero_path", "raw_fuel": "", "normalized_fuel": "", "combustion_technology": "", "value": 0.0, "unit": "kg"}]
            parameter = [{"parameter_id": "EMP-NH3-ZERO", "mode": "empirical_zero", "value": 0.0, "unit": "g/kg"}]
            control = {"technologies": technologies, "parameter_ids": [], "efficiency": 0.0,
                       "fine_efficiency": None, "coarse_efficiency": None, "label": "脱硝信息缺失按0"}
            return self.item_record(source, pollutant, "calculated", 0.0, 0.0, activity, parameter, control,
                                    source_notices + ["DENITRIFICATION_INFO_MISSING_ZERO"], ["附录E 表E.1|PDF-p50;实施协议v4-9"], "reviewed_NH3_disposition")
        fuels = [fuel for fuel in self.fuel_components(source) if "INACTIVE_ZERO_ACTIVITY" not in fuel.foundational_codes]
        coal_fuels = [fuel for fuel in fuels if fuel.standard_fuel == "煤炭"]
        reasons = list(source_notices)
        if not coal_fuels:
            return self.item_record(source, pollutant, "information_insufficient", None, None, [], [], {}, reasons + ["NH3_COAL_ACTIVITY_MISSING"], ["附录E 表E.1|PDF-p50"], "reviewed_NH3_disposition")
        if any(fuel.activity_value is None or fuel.activity_unit != "kg" or fuel.foundational_codes for fuel in coal_fuels):
            return self.item_record(source, pollutant, "information_insufficient", None, None, [], [], {}, reasons + ["NH3_COAL_ACTIVITY_MISSING"], ["附录E 表E.1|PDF-p50"], "reviewed_NH3_disposition")
        total_coal = sum(fuel.activity_value or 0 for fuel in coal_fuels)
        activities: list[dict[str, Any]] = []
        parameters: list[dict[str, Any]] = []
        generation = 0.0
        references: list[str] = []
        for process, row in processes:
            factor = float(row["value"])
            activities.append({"slot": process, "raw_fuel": "煤", "normalized_fuel": "煤炭",
                               "combustion_technology": process, "value": total_coal, "unit": "kg"})
            parameters.append({"parameter_id": row["parameter_id"], "mode": "ammonia_slip",
                               "value": factor, "unit": "g/kg"})
            generation += total_coal * factor / 1_000_000.0
            references.append(f"{row['source_section']}|PDF-p{row['physical_pdf_page']}")
        control = {"technologies": technologies, "parameter_ids": [row["parameter_id"] for _, row in processes],
                   "efficiency": 0.0, "fine_efficiency": None, "coarse_efficiency": None,
                   "label": "SCR/SNCR氨逃逸核算"}
        return self.item_record(source, pollutant, "calculated", generation, generation, activities, parameters, control,
                                reasons + ["NH3_SCR_SNCR_SLIP"], references, "T_CSES_E1_and_reviewed_NH3_disposition")

    def item_record(
        self, source: Source, pollutant: str, status: str, generation: float | None, emission: float | None,
        activities: list[dict[str, Any]], parameters: list[dict[str, Any]], control: dict[str, Any],
        reasons: list[str], references: list[str], basis: str,
    ) -> dict[str, Any]:
        methods = sorted({clean(parameter.get("mode")) for parameter in parameters if clean(parameter.get("mode"))})
        parameter_ids = [clean(parameter.get("parameter_id")) for parameter in parameters if clean(parameter.get("parameter_id"))]
        technologies = control.get("technologies", []) if control else []
        expected_value = status == "calculated"
        reasons = sorted(set(reason for reason in reasons if reason))
        references = sorted(set(reference for reference in references if reference))
        return {
            "variant": source.variant,
            "item_id": f"REF-{source.variant}-{source.source_id}-{pollutant}",
            "source_id": source.source_id,
            "target": source.target,
            "pollutant": pollutant,
            "expected_status": status,
            "expected_method_category": compact_json(methods),
            "expected_generation_t": canonical_number(generation) if expected_value else "",
            "expected_emission_t": canonical_number(emission) if expected_value else "",
            "expected_value_applicable": str(expected_value).lower(),
            "expected_activity_record": compact_json(activities),
            "expected_parameter_record": compact_json(parameters),
            "expected_control_record": compact_json(control),
            "expected_parameter_ids": compact_json(parameter_ids),
            "expected_control_technology": compact_json(technologies),
            "expected_control_efficiency": canonical_number(control.get("efficiency")) if control and control.get("efficiency") is not None else "",
            "expected_fine_efficiency": canonical_number(control.get("fine_efficiency")) if control and control.get("fine_efficiency") is not None else "",
            "expected_coarse_efficiency": canonical_number(control.get("coarse_efficiency")) if control and control.get("coarse_efficiency") is not None else "",
            "expected_reason_codes": compact_json(reasons),
            "expected_standard_reference": compact_json(references),
            "numeric_tolerance_profile": "emission_tonnes_v1" if expected_value else "not_applicable",
            "minimum_recalculation_complete": str(expected_value).lower(),
            "complete_process_record_required": "true",
            "reference_basis": basis,
        }

    @staticmethod
    def component_key(fuel: FuelComponent) -> str:
        return {"燃料一": "fuel_1", "燃料二": "fuel_2", "其他燃料": "other_tce"}[fuel.slot]

    @staticmethod
    def public_pollutant_token(pollutant: str) -> str:
        return {"NOx": "NOX", "VOC": "VOCS", "PM2.5": "PM25"}.get(pollutant, pollutant)

    def control_field_conflict(self, source: Source) -> bool:
        low_nox = clean(self.source_field(source, "工业是否采用低氮燃烧技术", "电站是否采用低氮燃烧技术"))
        technologies, _, _ = self.control_components(source)
        return low_nox == "否" and any("低氮燃烧技术" in technology for technology in technologies)

    def component_record(
        self, source: Source, component_type: str, component_key: str, pollutant: str,
        status: str, generation: float | None, emission: float | None,
        activities: list[dict[str, Any]], parameters: list[dict[str, Any]],
        control: dict[str, Any], reasons: list[str], references: list[str], basis: str,
        intermediate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        expected_value = status == "calculated"
        reasons = sorted(set(reason for reason in reasons if reason))
        references = sorted(set(reference for reference in references if reference))
        reference_ids = standard_reference_ids(references)
        methods = sorted({normalized_method(parameter.get("mode", "")) for parameter in parameters if clean(parameter.get("mode"))})
        method_evaluation_id = stable_public_id("METHOD", methods) if methods else ""
        parameter_evaluation_record: list[dict[str, Any]] = []
        parameter_evaluation_ids: list[str] = []
        for parameter in parameters:
            public = {
                "role": parameter.get("role", "primary"),
                "method": normalized_method(parameter.get("mode", "")),
                "value": parameter.get("value"),
                "unit": parameter.get("unit", ""),
                "standard_reference_ids": standard_reference_ids([parameter.get("reference", "")]),
            }
            parameter_evaluation_record.append(public)
            parameter_evaluation_ids.append(stable_public_id("PARAM", public))
        public_control = {
            "technologies": sorted(control.get("technologies", [])) if control else [],
            "efficiency": control.get("efficiency") if control else None,
            "fine_efficiency": control.get("fine_efficiency") if control else None,
            "coarse_efficiency": control.get("coarse_efficiency") if control else None,
            "label": control.get("label", "") if control else "",
        }
        control_evaluation_id = stable_public_id("CONTROL", public_control) if control else ""
        if status == "calculated":
            mask = {
                "status": True, "activity": True, "method": True, "parameter": True,
                "control": True, "value": True, "exception": True,
                "minimum_recalculation": True, "trace": True,
            }
        else:
            mask = {
                "status": True, "activity": False, "method": False, "parameter": False,
                "control": False, "value": False, "exception": True,
                "minimum_recalculation": False, "trace": True,
            }
        if component_type == "combustion":
            native_item_id = f"ITEM-FUEL-{source.source_id}-{component_key}-{self.public_pollutant_token(pollutant)}"
        else:
            native_item_id = f"ITEM-{source.source_id}-NH3-SLIP"
        evaluation_item_id = f"{source.source_id}::{component_key}::{pollutant}"
        native_parameter_ids = [clean(parameter.get("parameter_id")) for parameter in parameters if clean(parameter.get("parameter_id"))]
        technologies = control.get("technologies", []) if control else []
        return {
            "variant": source.variant,
            "item_id": f"REF-{source.variant}-{native_item_id}",
            "evaluation_item_id": evaluation_item_id,
            "native_item_id": native_item_id,
            "source_id": source.source_id,
            "target": source.target,
            "component_type": component_type,
            "component_key": component_key,
            "pollutant": pollutant,
            "expected_status": status,
            "applicability_mask": compact_json(mask),
            "expected_method_category": compact_json(methods),
            "method_evaluation_id": method_evaluation_id,
            "expected_generation_t": canonical_number(generation) if expected_value else "",
            "expected_emission_t": canonical_number(emission) if expected_value else "",
            "expected_value_applicable": str(expected_value).lower(),
            "expected_activity_record": compact_json(activities),
            "expected_intermediate_record": compact_json(intermediate or {}),
            "expected_parameter_record": compact_json(parameters),
            "expected_parameter_evaluation_record": compact_json(parameter_evaluation_record),
            "parameter_evaluation_ids": compact_json(parameter_evaluation_ids),
            "expected_control_record": compact_json(control),
            "control_evaluation_id": control_evaluation_id,
            "expected_native_parameter_ids": compact_json(native_parameter_ids),
            "expected_control_technology": compact_json(technologies),
            "expected_control_efficiency": canonical_number(control.get("efficiency")) if control and control.get("efficiency") is not None else "",
            "expected_fine_efficiency": canonical_number(control.get("fine_efficiency")) if control and control.get("fine_efficiency") is not None else "",
            "expected_coarse_efficiency": canonical_number(control.get("coarse_efficiency")) if control and control.get("coarse_efficiency") is not None else "",
            "expected_reason_codes": compact_json(reasons),
            "expected_standard_reference": compact_json(references),
            "standard_reference_ids": compact_json(reference_ids),
            "numeric_tolerance_profile": "emission_tonnes_v1" if expected_value else "not_applicable",
            "minimum_recalculation_complete": str(expected_value).lower(),
            "complete_process_record_required": "true",
            "reference_basis": basis,
        }

    def control_with_reasons(self, source: Source, pollutant: str) -> tuple[dict[str, Any], list[str], list[str]]:
        control = self.control_pm10(source) if pollutant == "PM10" else self.control_for(source, pollutant)
        reasons: list[str] = []
        references: list[str] = []
        if control.get("unmapped_fields"):
            reasons.append("CONTROL_UNMAPPED_ZERO")
        label = control.get("label", "")
        if label == "治理信息缺失按0":
            reasons.append("CONTROL_MISSING_ZERO")
        elif label == "无适用治理措施按0":
            reasons.append("NO_APPLICABLE_CONTROL_ZERO")
        elif label == "多工艺取最大缺省效率":
            reasons.append("MULTI_PROCESS_MAX_ASSUMPTION")
        if control.get("parameter_ids"):
            references.append("附录A 表A.1|PDF-p33-35")
        if label in {"治理信息缺失按0", "治理工艺无法映射按0", "无适用治理措施按0", "多工艺取最大缺省效率"}:
            references.append("冻结实施规则v1.0.1|operational-control-disposition")
        return control, reasons, references

    def conventional_component_item(self, source: Source, fuel: FuelComponent, pollutant: str) -> dict[str, Any]:
        component_key = self.component_key(fuel)
        source_status, source_reasons, source_notices = self.source_gate(source)
        activity = [{
            "slot": fuel.slot, "raw_fuel": fuel.raw_fuel, "normalized_fuel": fuel.standard_fuel,
            "combustion_technology": fuel.calculation_technology,
            "value": fuel.activity_value, "unit": fuel.activity_unit,
        }]
        if source_status == "source_data_invalid":
            return self.component_record(source, "combustion", component_key, pollutant, "source_data_invalid",
                                         None, None, activity, [], {}, source_reasons, [], "source_relation_gate")
        if pollutant == "NOx" and self.control_field_conflict(source):
            return self.component_record(source, "combustion", component_key, pollutant, "source_data_invalid",
                                         None, None, activity, [], {}, source_notices + ["LOW_NOX_FIELD_CONFLICT"],
                                         [], "professional_control_conflict_gate")
        if "POTENTIAL_DUPLICATE_FUEL_SLOT" in source_notices:
            return self.component_record(source, "combustion", component_key, pollutant, "information_insufficient",
                                         None, None, activity, [], {}, source_notices,
                                         [], "professional_duplicate_fuel_gate")
        if "FUEL_OUTSIDE_SCOPE" in fuel.foundational_codes:
            return self.component_record(
                source, "combustion", component_key, pollutant, "not_involved",
                None, None, activity, [], {}, source_notices + ["FUEL_OUTSIDE_SCOPE"],
                [], "outside_fossil_fixed_combustion_scope",
            )
        factor = self.factor_for(source, fuel, pollutant)
        primary_parameter = {
            "role": "primary", "parameter_id": factor["parameter_id"], "mode": factor["mode"],
            "value": factor["factor_value"], "unit": factor["factor_unit"], "reference": factor["reference"],
        }
        reasons = list(source_notices) + list(factor["reason_codes"])
        references = [factor["reference"]] if factor["reference"] else []
        if factor["reason_codes"] or fuel.activity_value is None or factor["factor_value"] is None:
            status = "source_data_invalid" if factor["fatal_invalid"] else "information_insufficient"
            return self.component_record(source, "combustion", component_key, pollutant, status, None, None,
                                         activity, [primary_parameter], {}, reasons, references, "frozen_rules")
        control, control_reasons, control_references = self.control_with_reasons(source, pollutant)
        reasons.extend(control_reasons)
        references.extend(control_references)
        generation = float(fuel.activity_value) * float(factor["factor_value"]) / 1_000_000.0
        parameters = [primary_parameter]
        intermediate: dict[str, Any] = {}
        if pollutant == "PM10":
            paired = self.factor_for(source, fuel, "PM2.5")
            paired_parameter = {
                "role": "pm25_pair_for_pm10", "parameter_id": paired["parameter_id"], "mode": paired["mode"],
                "value": paired["factor_value"], "unit": paired["factor_unit"], "reference": paired["reference"],
            }
            parameters.append(paired_parameter)
            if paired["reference"]:
                references.append(paired["reference"])
            if paired["reason_codes"] or paired["factor_value"] is None:
                paired_reasons = reasons + list(paired["reason_codes"]) + ["PM25_PAIR_MISSING"]
                status = "source_data_invalid" if paired["fatal_invalid"] else "information_insufficient"
                return self.component_record(source, "combustion", component_key, pollutant, status, None, None,
                                             activity, parameters, control, paired_reasons, references, "frozen_rules")
            fine_generation = float(fuel.activity_value) * float(paired["factor_value"]) / 1_000_000.0
            coarse_generation = generation - fine_generation
            intermediate = {"fine_generation_t": fine_generation, "coarse_generation_t": coarse_generation}
            if coarse_generation < -1e-12:
                return self.component_record(source, "combustion", component_key, pollutant, "source_data_invalid",
                                             None, None, activity, parameters, control,
                                             reasons + ["PM_SIZE_CONSTRAINT_VIOLATION"], references, "frozen_rules", intermediate)
            emission = fine_generation * (1.0 - control["fine_efficiency"]) + coarse_generation * (1.0 - control["coarse_efficiency"])
        else:
            emission = generation * (1.0 - control["efficiency"])
        return self.component_record(source, "combustion", component_key, pollutant, "calculated",
                                     generation, emission, activity, parameters, control, reasons, references,
                                     "frozen_rules", intermediate)

    def nh3_component_item(self, source: Source) -> dict[str, Any]:
        pollutant = "NH3"
        source_status, source_reasons, source_notices = self.source_gate(source)
        if source_status == "source_data_invalid":
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "source_data_invalid",
                                         None, None, [], [], {}, source_reasons, [], "source_relation_gate")
        if self.control_field_conflict(source):
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "source_data_invalid",
                                         None, None, [], [], {}, source_notices + ["LOW_NOX_FIELD_CONFLICT"], [],
                                         "professional_control_conflict_gate")
        if "POTENTIAL_DUPLICATE_FUEL_SLOT" in source_notices:
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "information_insufficient",
                                         None, None, [], [], {}, source_notices,
                                         [], "professional_duplicate_fuel_gate")
        technologies, _, _ = self.control_components(source)
        denox_text, processes = self.nh3_processes(source)
        if len(processes) > 1:
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "information_insufficient",
                                         None, None, [], [], {"technologies": technologies},
                                         source_notices + ["NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED"],
                                         ["附录E 表E.1|PDF-p50"], "professional_NH3_combination_gate")
        if not processes:
            if denox_text:
                return self.component_record(
                    source, "ammonia_slip", "nh3_slip", pollutant, "not_involved",
                    None, None, [], [], {"technologies": technologies, "label": "无SCR/SNCR，NH3不适用"},
                    source_notices + ["NH3_NOT_APPLICABLE_NO_SCR_SNCR"],
                    ["附录E 表E.1|PDF-p50"], "NH3_process_not_applicable",
                )
            activities = [{"slot": "NH3_zero_path", "raw_fuel": "", "normalized_fuel": "", "combustion_technology": "", "value": 0.0, "unit": "kg"}]
            parameters = [{"role": "primary", "parameter_id": "EMP-NH3-ZERO", "mode": "empirical_zero",
                           "value": 0.0, "unit": "g/kg", "reference": "实施协议v4-8"}]
            control = {"technologies": technologies, "parameter_ids": [], "efficiency": 0.0,
                       "fine_efficiency": None, "coarse_efficiency": None, "label": "脱硝信息缺失按0"}
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "calculated", 0.0, 0.0,
                                         activities, parameters, control, source_notices + ["DENITRIFICATION_INFO_MISSING_ZERO"],
                                         ["附录E 表E.1|PDF-p50", "实施协议v4-8"], "reviewed_NH3_disposition")
        fuels = [fuel for fuel in self.fuel_components(source) if "INACTIVE_ZERO_ACTIVITY" not in fuel.foundational_codes]
        coal_fuels = [fuel for fuel in fuels if fuel.standard_fuel == "煤炭"]
        nh3_activity_blocking = {
            code
            for fuel in coal_fuels
            for code in fuel.foundational_codes
            if code not in {"COMBUSTION_TECHNOLOGY_UNMAPPED"}
        }
        if not coal_fuels or any(fuel.activity_value is None or fuel.activity_unit != "kg" for fuel in coal_fuels) or nh3_activity_blocking:
            return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "information_insufficient",
                                         None, None, [], [], {"technologies": technologies},
                                         source_notices + ["NH3_COAL_ACTIVITY_MISSING"], ["附录E 表E.1|PDF-p50"],
                                         "reviewed_NH3_disposition")
        total_coal = sum(fuel.activity_value or 0.0 for fuel in coal_fuels)
        process, row = processes[0]
        factor = float(row["value"])
        activities = [{"slot": process, "raw_fuel": "煤", "normalized_fuel": "煤炭",
                       "combustion_technology": process, "value": total_coal, "unit": "kg"}]
        reference = f"{row['source_section']}|PDF-p{row['physical_pdf_page']}"
        parameters = [{"role": "primary", "parameter_id": row["parameter_id"], "mode": "ammonia_slip",
                       "value": factor, "unit": "g/kg", "reference": reference}]
        generation = total_coal * factor / 1_000_000.0
        control = {"technologies": technologies, "parameter_ids": [row["parameter_id"]], "efficiency": 0.0,
                   "fine_efficiency": None, "coarse_efficiency": None, "label": "SCR/SNCR氨逃逸核算"}
        return self.component_record(source, "ammonia_slip", "nh3_slip", pollutant, "calculated",
                                     generation, generation, activities, parameters, control,
                                     source_notices + ["NH3_SCR_SNCR_SLIP"], [reference],
                                     "T_CSES_E1_and_reviewed_NH3_disposition")

    def component_item_rows(self, sources: list[Source]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source in sources:
            if source.target == "EXCLUDE":
                continue
            fuels = [fuel for fuel in self.fuel_components(source) if "INACTIVE_ZERO_ACTIVITY" not in fuel.foundational_codes]
            if not fuels:
                for pollutant in POLLUTANTS[:-1]:
                    rows.append(self.component_record(
                        source, "combustion", "no_active_fuel", pollutant,
                        "information_insufficient", None, None, [], [], {},
                        ["NO_ACTIVE_FUEL"], [], "frozen_rules",
                    ))
            else:
                for fuel in fuels:
                    for pollutant in POLLUTANTS[:-1]:
                        rows.append(self.conventional_component_item(source, fuel, pollutant))
            rows.append(self.nh3_component_item(source))
        return rows

    @staticmethod
    def source_pollutant_totals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[(item["variant"], item["source_id"], item["target"], item["pollutant"])].append(item)
        totals: list[dict[str, Any]] = []
        for (variant, source_id, target, pollutant), members in sorted(grouped.items()):
            statuses = {member["expected_status"] for member in members}
            if "source_data_invalid" in statuses:
                status = "source_data_invalid"
            elif "information_insufficient" in statuses:
                status = "information_insufficient"
            elif "calculated" in statuses and statuses.issubset({"calculated", "not_involved"}):
                status = "calculated"
            elif statuses == {"not_involved"}:
                status = "not_involved"
            else:
                status = "information_insufficient"
            value_applicable = status == "calculated"
            generation = sum(
                float(member["expected_generation_t"])
                for member in members if member["expected_status"] == "calculated"
            ) if value_applicable else None
            emission = sum(
                float(member["expected_emission_t"])
                for member in members if member["expected_status"] == "calculated"
            ) if value_applicable else None
            reasons = sorted({reason for member in members for reason in json.loads(member["expected_reason_codes"])})
            totals.append({
                "variant": variant,
                "evaluation_total_id": f"{source_id}::{pollutant}",
                "source_id": source_id,
                "target": target,
                "pollutant": pollutant,
                "expected_status": status,
                "expected_generation_t": canonical_number(generation) if value_applicable else "",
                "expected_emission_t": canonical_number(emission) if value_applicable else "",
                "expected_value_applicable": str(value_applicable).lower(),
                "component_item_ids": compact_json([member["evaluation_item_id"] for member in members]),
                "expected_reason_codes": compact_json(reasons),
                "numeric_tolerance_profile": "emission_tonnes_v1" if value_applicable else "not_applicable",
                "reference_basis": "component_aggregation_with_no_partial_sums",
            })
        return totals

    def item_rows(self, sources: list[Source]) -> list[dict[str, Any]]:
        return self.component_item_rows(sources)

    def exception_rows(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in items:
            reasons = json.loads(item["expected_reason_codes"])
            injected = self.injection_by_key.get((item["source_id"], item["target"])) if item["variant"] == "B2" else None
            root_cause = injected["root_cause"] if injected else ""
            is_negative_control = root_cause == "UNIQUE_DEFAULT_NEGATIVE_CONTROL"
            if is_negative_control:
                reasons = sorted(set(reasons + ["CONTROLLED_NEGATIVE_CONTROL_EXPECTS_NO_SEMANTIC_CHANGE"]))
            for reason in reasons:
                prefix = reason.split(":", 1)[0]
                canonical, requires_user = REASON_EQUIVALENCE.get(prefix, (f"other:{prefix}", item["expected_status"] != "calculated"))
                key = (item["variant"], item["source_id"], item["target"], canonical)
                record = grouped.setdefault(key, {
                    "codes": set(), "items": set(), "pollutants": set(), "statuses": set(),
                    "requires_user": requires_user, "root_cause": root_cause, "bases": set(),
                })
                record["codes"].add(reason)
                record["items"].add(item["evaluation_item_id"])
                record["pollutants"].add(item["pollutant"])
                record["statuses"].add(item["expected_status"])
                record["requires_user"] = record["requires_user"] or requires_user
                record["bases"].add(item["reference_basis"])
        rows: list[dict[str, Any]] = []
        for (variant, source_id, target, canonical), record in sorted(grouped.items()):
            statuses = record["statuses"]
            if "source_data_invalid" in statuses:
                severity, action = "critical", "withhold_affected_items"
            elif "information_insufficient" in statuses:
                severity, action = "blocking", "withhold_affected_items"
            else:
                severity = "review" if record["requires_user"] else "notice"
                action = "review_after_automatic_run" if record["requires_user"] else "retain_authorized_disposition_notice"
            group_seed = {"variant": variant, "source_id": source_id, "canonical_root": canonical}
            rows.append({
                "variant": variant,
                "exception_group_id": stable_public_id("EXC", group_seed),
                "source_id": source_id,
                "target": target,
                "canonical_root_cause": canonical,
                "equivalence_key": f"{source_id}|{canonical}",
                "aggregation_rule": "merge_same_source_and_canonical_root_across_pollutants",
                "severity": severity,
                "requires_user_judgment": str(record["requires_user"]).lower(),
                "expected_action": action,
                "exception_codes": compact_json(sorted(record["codes"])),
                "affected_item_ids": compact_json(sorted(record["items"])),
                "affected_pollutants": compact_json(sorted(record["pollutants"])),
                "expected_statuses": compact_json(sorted(statuses)),
                "numeric_should_be_blank": str(any(status != "calculated" for status in statuses)).lower(),
                "injection_root_cause": record["root_cause"],
                "scoring_eligible": "true",
                "reference_basis": compact_json(sorted(record["bases"])),
            })
        return rows

    @staticmethod
    def aggregate_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[(item["variant"], item["target"], item["pollutant"], item["expected_status"])].append(item)
        rows: list[dict[str, Any]] = []
        for key, members in sorted(grouped.items()):
            generation = sum(float(row["expected_generation_t"]) for row in members if row["expected_generation_t"] != "")
            emission = sum(float(row["expected_emission_t"]) for row in members if row["expected_emission_t"] != "")
            empirical = sum(any(code in row["expected_reason_codes"] for code in (
                "CONTROL_MISSING_ZERO", "CONTROL_UNMAPPED_ZERO", "MULTI_PROCESS_MAX_ASSUMPTION",
                "DENITRIFICATION_INFO_MISSING_ZERO", "NO_SCR_SNCR_ZERO",
            )) for row in members)
            rows.append({
                "variant": key[0], "target": key[1], "pollutant": key[2], "expected_status": key[3],
                "item_count": len(members), "generation_sum_t": canonical_number(generation),
                "emission_sum_t": canonical_number(emission), "empirical_assumption_item_count": empirical,
            })
        return rows

    def injection_outcome_rows(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key = {(row["variant"], row["source_id"], row["pollutant"]): row for row in items}
        semantic_fields = ("expected_status", "expected_generation_t", "expected_emission_t")
        expected_affected = {
            "ACTIVITY_MISSING": {"SO2", "NOx", "CO", "VOC", "PM10", "PM2.5", "BC", "OC"},
            "POLLUTANT_PARAMETER_MISSING": {"SO2", "PM10", "PM2.5", "BC", "OC"},
            "SOURCE_RELATION_MISSING": set(POLLUTANTS),
            "SOURCE_RELATION_CONFLICT": set(POLLUTANTS),
            "HARD_CONSTRAINT_CONFLICT": {"PM10", "PM2.5", "BC", "OC"},
            "UNIQUE_DEFAULT_NEGATIVE_CONTROL": set(),
        }
        rows: list[dict[str, Any]] = []
        for injection in self.injections:
            source_id, root_cause = injection["source_id"], injection["root_cause"]
            target = self.baseline_targets[source_id]
            plan_class = injection.get("plan_class", "positive_injection")
            changed: list[str] = []
            baseline_status: dict[str, str] = {}
            b2_status: dict[str, str] = {}
            for pollutant in POLLUTANTS:
                b0 = by_key[("B0", source_id, pollutant)]
                b2 = by_key[("B2", source_id, pollutant)]
                baseline_status[pollutant] = b0["expected_status"]
                b2_status[pollutant] = b2["expected_status"]
                if any(b0[field] != b2[field] for field in semantic_fields):
                    changed.append(pollutant)
            expected = set(injection.get("expected_affected_pollutants") or expected_affected.get(root_cause, []))
            if plan_class == "preexisting_problem":
                validity = "preexisting_problem"
                note = injection.get("existing_issue_reason", "preexisting baseline problem")
                scoring_eligible = False
                exclusion_reason = "preexisting_problem_not_in_targeted_denominator"
            elif plan_class == "negative_control":
                validity = "valid" if not changed else "invalid"
                note = "Negative control should not change any source-pollutant outcome."
                scoring_eligible = validity == "valid"
                exclusion_reason = "" if scoring_eligible else "negative_control_changed"
            else:
                baseline_ready = (
                    all(baseline_status[pollutant] != "source_data_invalid" for pollutant in expected)
                    if root_cause in {"SOURCE_RELATION_MISSING", "SOURCE_RELATION_CONFLICT"}
                    else all(baseline_status[pollutant] == "calculated" for pollutant in expected)
                )
                effect_present = expected.issubset(set(changed))
                validity = "valid" if baseline_ready and effect_present else "partially_valid" if changed else "invalid"
                note = "" if validity == "valid" else "Baseline was already blocked for at least one intended item or the injection produced no isolated semantic change."
                scoring_eligible = validity == "valid"
                exclusion_reason = "" if scoring_eligible else "no_valid_isolated_baseline_effect"
            rows.append({
                "plan_class": plan_class, "target": target, "root_cause": root_cause, "source_id": source_id,
                "source_tag": injection.get("source_tag", "RAW"), "source_row": injection.get("source_row", ""),
                "existing_issue_reason": injection.get("existing_issue_reason", ""),
                "injection_validity": validity,
                "expected_affected_pollutants": compact_json(sorted(expected)),
                "observed_reference_changed_pollutants": compact_json(changed),
                "changed_item_count": len(changed),
                "baseline_statuses": compact_json(baseline_status),
                "b2_statuses": compact_json(b2_status),
                "scoring_eligible": str(scoring_eligible).lower(),
                "exclusion_reason": exclusion_reason,
                "diagnostic_denominator": "eligible_targeted_injections_only",
                "note": note,
            })
        return rows


def decision_register() -> list[dict[str, str]]:
    return [
        {"decision_id": "REF-D01", "topic": "source_scope", "decision": "Independently classify all 5,922 raw Base-102 candidates from industry code and the two equipment fields; no target member list is read.", "authority": "T/CSES 7.1 and 8.3", "classification": "standard_operationalization"},
        {"decision_id": "REF-D02", "topic": "method", "decision": "Use coefficient and coal mass-balance paths; reported base-table emissions are comparison-only.", "authority": "T/CSES formulas (2)-(6) + implementation protocol v4", "classification": "data_scope_decision"},
        {"decision_id": "REF-D03", "topic": "gas_factor_unit", "decision": "Interpret six volume-metered gaseous-fuel factors as g/m3.", "authority": "Frozen rule r_ops_e7_gas_unit", "classification": "external_unit_interpretation"},
        {"decision_id": "REF-D04", "topic": "missing_control", "decision": "Blank or unmapped control information receives an explicit empirical efficiency of zero and remains traceable.", "authority": "Frozen rules r_ops_control_missing_zero/r_ops_control_unmapped_zero", "classification": "empirical_missing_data_rule"},
        {"decision_id": "REF-D05", "topic": "operation_rate", "decision": "Use operation rate 1 because facility operation-time fields are unavailable.", "authority": "Frozen rule r_ops_operation_rate_one", "classification": "empirical_assumption"},
        {"decision_id": "REF-D06", "topic": "multiple_controls", "decision": "Use an explicit A.1 combination where available; otherwise use the maximum applicable default efficiency, never sum efficiencies.", "authority": "Frozen rules r_ops_standard_combination/r_ops_multi_process_max", "classification": "standard_operationalization_and_empirical_combination"},
        {"decision_id": "REF-D07", "topic": "co_benefits", "decision": "All three source control fields may contribute A.1 co-benefit efficiencies for each pollutant.", "authority": "Frozen rule r_ops_cobenefit_all_fields", "classification": "standard_operationalization"},
        {"decision_id": "REF-D08", "topic": "missing_foundational_data", "decision": "Missing activity, fuel, sulfur, ash, capacity or factor withholds the source-pollutant total; partial fuel sums are prohibited.", "authority": "Frozen rules r_ops_foundational_missing/r_ops_source_aggregation_gate", "classification": "calculation_safety_rule"},
        {"decision_id": "REF-D09", "topic": "NH3", "decision": "Calculate NH3 only as SCR/SNCR ammonia slip; missing denitrification information is empirical zero, explicit control information without SCR/SNCR is not_involved, SCR/SNCR without coal activity is withheld, and simultaneous SCR+SNCR is withheld when no coal-allocation rule exists.", "authority": "T/CSES E.1 + confirmed NH3 disposition boundary", "classification": "standard_factor_and_experiment_disposition"},
        {"decision_id": "REF-D10", "topic": "source_relation", "decision": "Missing enterprise, industry, equipment, outlet or coordinate identity blocks all nine pollutant outputs for the source.", "authority": "T/CSES point-source structure and QC sections 6.2/15.1", "classification": "professional_reference_gate"},
        {"decision_id": "REF-D11", "topic": "coordinate_plausibility", "decision": "Coordinates outside a broad Guangdong envelope are retained for emission calculation but flagged for spatial review.", "authority": "T/CSES point-source QC + separation of emission and spatial validity", "classification": "professional_reference_notice"},
        {"decision_id": "REF-D12", "topic": "tolerance", "decision": "Public business IDs and states are exact; parameters are normalized by method, value, unit and standard reference; tonne values use max(1e-6 t, abs(reference)*1e-6).", "authority": "Implementation protocol I-49", "classification": "evaluation_definition"},
        {"decision_id": "REF-D13", "topic": "duplicate_fuel_slots", "decision": "When fuel_1 and fuel_2 repeat the same nonblank fuel, amount, unit, sulfur and ash facts, both slots and the source total are withheld pending one grouped user review; no automatic de-duplication or double counting is allowed.", "authority": "Professional reference conflict gate", "classification": "calculation_safety_rule"},
        {"decision_id": "REF-D14", "topic": "B2_injections", "decision": "B2 is rebuilt only from the versioned raw-field injection manifest; a placeholder manifest produces a B2 reference identical to B0 and an empty targeted denominator.", "authority": "Frozen experiment input protocol v2", "classification": "evaluation_protocol"},
        {"decision_id": "REF-D15", "topic": "factor_routing", "decision": "Select source class and the electricity/heat department before factor lookup; the original workbook family or equipment-column origin cannot route a power source to industrial factors.", "authority": "T/CSES 7.1/8.3 + rule package v1.0.1", "classification": "calculation_safety_rule"},
    ]


def write_readme(path: Path, stats: dict[str, Any]) -> None:
    content = f"""# 固定燃烧源清单独立规则参照包 v2.0.0

本包从三类原始环境统计基表、5,922条 Base-102 设备候选与经专业复核的规则包 v1.0.1 独立生成。生成器不读取任何参评方法、人工流程或已计算排放量。

## 规模

- B0 候选源：{stats['b0_candidate_sources']}。
- 工业锅炉：{stats['b0_industrial_sources']}；电力热力：{stats['b0_power_sources']}；两类范围外：{stats['b0_excluded_sources']}。
- B0 底层核算项：{stats['b0_items']}；源—污染物汇总：{stats['b0_source_pollutant_totals']}。
- B2 定义状态：{stats['b2_manifest_status']}；注入记录：{stats['b2_injected_sources']}。

## 核算边界

1. D4411、D4412、D4417 归入电力生产，D4430 归入热力生产和供应。
2. 其他行业只在属于采矿业/制造业且设备字段表明工业锅炉时归入工业源。
3. 燃料、燃烧技术、排放因子、煤参数和治理效率只从规则包 v1.0.1 读取。
4. 生物燃料与工业废料燃料项标记为 `not_involved`。
5. NH₃仅按 SCR/SNCR 氨逃逸路径独立核算；脱硝信息缺失时按冻结经验规则记0，已有明确控制信息但无SCR/SNCR时记为 `not_involved`。
6. 治理信息缺失按0、投运率取1、多工艺取最大缺省效率是冻结实验实施约定，不是 T/CSES 强制条款。

`expected_sources.csv`、`expected_items.csv`、`expected_source_pollutant_totals.csv`、`expected_exceptions.csv` 分别给出源去向、底层核算项、源—污染物结果和异常根因。`validation_report.json` 与 `package_lock.json` 用于复核与字节封存。
"""
    path.write_text(content, encoding="utf-8")


def reference_schema() -> dict[str, Any]:
    return {
        "version": "2.0.0",
        "primary_keys": {
            "expected_sources.csv": ["variant", "source_id"],
            "expected_items.csv": ["variant", "evaluation_item_id"],
            "expected_source_pollutant_totals.csv": ["variant", "evaluation_total_id"],
            "expected_exceptions.csv": ["variant", "exception_group_id"],
            "expected_injection_outcomes.csv": ["target", "root_cause", "source_id"],
        },
        "variants": {
            "B0": "Unperturbed frozen input semantics; also supplies B1/C/D expectations by key selection.",
            "B2": "Controlled missing/conflict input semantics.",
        },
        "status_definitions": {
            "calculated": "All required source, activity, parameter and formula conditions for this source-pollutant item are satisfied; numeric zero remains a valid number.",
            "information_insufficient": "A required calculation fact or parameter is absent or unmappable; generation and emission must be blank.",
            "source_data_invalid": "A source relation or hard numeric constraint is invalid; generation and emission must be blank.",
            "not_involved": "The fuel or process item is demonstrably outside this fossil fixed-combustion calculation; generation and emission must be blank.",
        },
        "method_modes": {
            "constant": "Appendix D.1/E.7 constant emission factor.",
            "capacity_lookup": "D.1 coal NOx capacity-band factor.",
            "coal_sulfur_balance": "Formula (3) and C.1.",
            "coal_particle_balance": "Formulas (4)-(6) and C.1-C.3.",
            "ammonia_slip": "E.1 SCR/SNCR NH3 factor.",
            "empirical_zero": "Reviewed explicit zero path, not a T/CSES combustion factor.",
        },
        "units": {
            "expected_generation_t": "t/year for the inventory year",
            "expected_emission_t": "t/year for the inventory year",
            "activity_record.value": "kg for mass fuels or m3 for gaseous fuels",
            "parameter_record.value": "g/kg or g/m3",
            "control_efficiency": "fraction in [0,1]",
        },
        "field_semantics": {
            "evaluation_item_id": "Public source/component/pollutant ID, stable across B0/B1/C/D and independent of Full internal rule IDs.",
            "expected_native_parameter_ids": "Native frozen-rule IDs retained for Full trace audit only; never used as a cross-method correctness key.",
            "parameter_evaluation_ids": "Public hashes of method category, parameter role, value, unit and normalized standard reference; compared as a multiset.",
            "applicability_mask": "Per-state scoring mask for status, activity, method, parameter, control, value, exception, minimum recalculation and trace.",
            "expected_reason_codes": "Sorted JSON array; may include nonblocking notices on calculated items.",
            "expected_value_applicable": "True only when both expected_generation_t and expected_emission_t must be numeric.",
            "reference_basis": "High-level origin of the expected decision; detailed citations remain in expected_standard_reference.",
        },
        "pollutant_normalization": {
            "SO2": "SO2", "NOX": "NOx", "NOx": "NOx", "CO": "CO",
            "VOCS": "VOC", "VOC": "VOC", "PM10": "PM10", "PM25": "PM2.5",
            "PM2.5": "PM2.5", "BC": "BC", "OC": "OC", "NH3": "NH3",
        },
    }


def validate_generated_package(output: Path, expected_stats: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    sources = read_csv(output / "expected_sources.csv")
    items = read_csv(output / "expected_items.csv")
    totals = read_csv(output / "expected_source_pollutant_totals.csv")
    exceptions = read_csv(output / "expected_exceptions.csv")
    injection_outcomes = read_csv(output / "expected_injection_outcomes.csv")

    def unique(rows: list[dict[str, str]], fields: tuple[str, ...], label: str) -> None:
        keys = [tuple(row[field] for field in fields) for row in rows]
        if len(keys) != len(set(keys)):
            errors.append(f"duplicate {label} key")

    unique(sources, ("variant", "source_id"), "source")
    unique(items, ("variant", "evaluation_item_id"), "item")
    unique(totals, ("variant", "evaluation_total_id"), "total")
    unique(exceptions, ("variant", "exception_group_id"), "exception")

    b0_sources = [row for row in sources if row["variant"] == "B0"]
    b2_sources = [row for row in sources if row["variant"] == "B2"]
    if len(b0_sources) != 5922 or len(b2_sources) != 5922:
        errors.append(f"source rows must be 5922 per variant: {len(b0_sources)}/{len(b2_sources)}")
    actual_counts = Counter(row["expected_target"] for row in b0_sources)
    expected_counts = {
        "INDUSTRIAL": expected_stats["b0_industrial_sources"],
        "POWER": expected_stats["b0_power_sources"],
        "EXCLUDE": expected_stats["b0_excluded_sources"],
    }
    if dict(actual_counts) != expected_counts:
        errors.append(f"source classification counts mismatch: {dict(actual_counts)}")

    identity_path = Path(json.loads(RAW_INPUT_MANIFEST.read_text(encoding="utf-8"))["source_identity_index_path"])
    if identity_path.is_file():
        identity_index = json.loads(identity_path.read_text(encoding="utf-8"))
        if set(identity_index.get("candidate_ids", [])) != {row["source_id"] for row in b0_sources}:
            errors.append("source identities differ from the target-neutral raw input index")

    item_counts = Counter((row["variant"], row["source_id"]) for row in items)
    for source in sources:
        expected_count = int(source["expected_item_count"])
        if item_counts[(source["variant"], source["source_id"])] != expected_count:
            errors.append(f"item count mismatch: {source['variant']}:{source['source_id']}")
            if len(errors) > 50:
                break

    total_counts = Counter((row["variant"], row["source_id"]) for row in totals)
    for source in sources:
        expected_count = 0 if source["expected_target"] == "EXCLUDE" else 9
        if total_counts[(source["variant"], source["source_id"])] != expected_count:
            errors.append(f"total count mismatch: {source['variant']}:{source['source_id']}")
            if len(errors) > 50:
                break

    legal_statuses = {"calculated", "information_insufficient", "source_data_invalid", "not_involved"}
    component_pollutants: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in items:
        status = row["expected_status"]
        generation = number(row["expected_generation_t"])
        emission = number(row["expected_emission_t"])
        if status not in legal_statuses:
            errors.append(f"illegal component status: {status}")
        elif status == "calculated":
            if generation is None or emission is None:
                errors.append(f"calculated component has blank value: {row['variant']}:{row['evaluation_item_id']}")
            elif generation < 0 or emission < 0 or emission > generation + max(1e-6, abs(generation) * 1e-6):
                errors.append(f"component numeric constraint failed: {row['variant']}:{row['evaluation_item_id']}")
        elif generation is not None or emission is not None:
            errors.append(f"noncalculated component has value: {row['variant']}:{row['evaluation_item_id']}")
        component_pollutants[(row["variant"], row["source_id"], row["component_key"], row["pollutant"])] = row

    component_groups = {(key[0], key[1], key[2]) for key in component_pollutants}
    for variant, source_id, component_key in component_groups:
        pm10 = component_pollutants.get((variant, source_id, component_key, "PM10"))
        pm25 = component_pollutants.get((variant, source_id, component_key, "PM2.5"))
        if not pm10 or not pm25 or pm10["expected_status"] != "calculated" or pm25["expected_status"] != "calculated":
            continue
        if float(pm25["expected_generation_t"]) > float(pm10["expected_generation_t"]) + 1e-6:
            errors.append(f"component PM2.5 exceeds PM10: {variant}:{source_id}:{component_key}")

    by_source_pollutant: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in totals:
        status = row["expected_status"]
        if status not in legal_statuses:
            errors.append(f"illegal status: {status}")
            continue
        generation = number(row["expected_generation_t"])
        emission = number(row["expected_emission_t"])
        if status == "calculated":
            if generation is None or emission is None:
                errors.append(f"calculated total has blank value: {row['variant']}:{row['evaluation_total_id']}")
            elif generation < 0 or emission < 0 or emission > generation + max(1e-6, abs(generation) * 1e-6):
                errors.append(f"numeric constraint failed: {row['variant']}:{row['evaluation_total_id']}")
        elif generation is not None or emission is not None:
            errors.append(f"noncalculated total has value: {row['variant']}:{row['evaluation_total_id']}")
        by_source_pollutant[(row["variant"], row["source_id"], row["pollutant"])] = row

    for variant, source_id in {(row["variant"], row["source_id"]) for row in totals}:
        pm10 = by_source_pollutant.get((variant, source_id, "PM10"))
        pm25 = by_source_pollutant.get((variant, source_id, "PM2.5"))
        if not pm10 or not pm25 or pm10["expected_status"] != "calculated" or pm25["expected_status"] != "calculated":
            continue
        if float(pm25["expected_generation_t"]) > float(pm10["expected_generation_t"]) + 1e-6:
            errors.append(f"PM2.5 exceeds PM10: {variant}:{source_id}")

    if expected_stats.get("b2_manifest_status") == "placeholder":
        b0 = {
            row["evaluation_total_id"]: (
                row["expected_status"], row["expected_generation_t"], row["expected_emission_t"], row["expected_reason_codes"]
            ) for row in totals if row["variant"] == "B0"
        }
        b2 = {
            row["evaluation_total_id"]: (
                row["expected_status"], row["expected_generation_t"], row["expected_emission_t"], row["expected_reason_codes"]
            ) for row in totals if row["variant"] == "B2"
        }
        if b0 != b2:
            errors.append("placeholder B2 does not exactly preserve B0 total semantics")
    else:
        plan_counts = Counter(row["plan_class"] for row in injection_outcomes)
        expected_plan_counts = {"positive_injection": 20, "negative_control": 6, "preexisting_problem": 4}
        if dict(plan_counts) != expected_plan_counts:
            errors.append(f"B2 plan counts mismatch: {dict(plan_counts)}")
        target_plan_counts = Counter((row["target"], row["plan_class"]) for row in injection_outcomes)
        expected_target_plan = {
            (target, plan): count
            for target in ("INDUSTRIAL", "POWER")
            for plan, count in (("positive_injection", 10), ("negative_control", 3), ("preexisting_problem", 2))
        }
        if dict(target_plan_counts) != expected_target_plan:
            errors.append(f"B2 source-class balance mismatch: {dict(target_plan_counts)}")
        eligible = sum(row["scoring_eligible"] == "true" for row in injection_outcomes)
        if eligible != 26:
            errors.append(f"B2 targeted denominator is {eligible}, expected 26")
    if not any(row["expected_status"] == "not_involved" for row in items):
        errors.append("outside-scope fuel components were not represented as not_involved")

    return {
        "version": "2.0.0",
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "checks": {
            "source_rows": len(sources),
            "item_rows": len(items),
            "total_rows": len(totals),
            "exception_rows": len(exceptions),
            "b0_target_counts": dict(actual_counts),
            "not_involved_component_items": sum(row["expected_status"] == "not_involved" for row in items),
            "b2_plan_counts": dict(Counter(row["plan_class"] for row in injection_outcomes)),
            "b2_eligible_targeted_records": sum(row["scoring_eligible"] == "true" for row in injection_outcomes),
        },
    }


def write_package_lock(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    files = {
        path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "package_lock.json"
    }
    lock = {
        "package_name": manifest["package_name"],
        "version": manifest["version"],
        "status": "audited_frozen",
        "files": files,
    }
    (output / "package_lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")


def build() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    builder = ReferenceBuilder()
    sources_by_variant = {variant: builder.read_sources(variant) for variant in ("B0", "B2")}
    source_rows = [builder.source_row(source) for variant in ("B0", "B2") for source in sources_by_variant[variant]]
    item_rows = [row for variant in ("B0", "B2") for row in builder.item_rows(sources_by_variant[variant])]
    total_rows = builder.source_pollutant_totals(item_rows)
    exception_rows = builder.exception_rows(item_rows)
    aggregate_rows = builder.aggregate_rows(total_rows)
    injection_outcomes = builder.injection_outcome_rows(total_rows)

    write_csv(OUTPUT_ROOT / "expected_sources.csv", SOURCE_FIELDS, source_rows)
    write_csv(OUTPUT_ROOT / "expected_items.csv", ITEM_FIELDS, item_rows)
    write_csv(OUTPUT_ROOT / "expected_source_pollutant_totals.csv", TOTAL_FIELDS, total_rows)
    write_csv(OUTPUT_ROOT / "expected_exceptions.csv", EXCEPTION_FIELDS, exception_rows)
    write_csv(OUTPUT_ROOT / "expected_aggregates.csv", (
        "variant", "target", "pollutant", "expected_status", "item_count",
        "generation_sum_t", "emission_sum_t", "empirical_assumption_item_count",
    ), aggregate_rows)
    write_csv(OUTPUT_ROOT / "decision_register.csv", (
        "decision_id", "topic", "decision", "authority", "classification",
    ), decision_register())
    write_csv(OUTPUT_ROOT / "expected_injection_outcomes.csv", (
        "plan_class", "target", "root_cause", "source_id", "source_tag", "source_row", "existing_issue_reason", "injection_validity",
        "expected_affected_pollutants", "observed_reference_changed_pollutants", "changed_item_count",
        "baseline_statuses", "b2_statuses", "scoring_eligible", "exclusion_reason",
        "diagnostic_denominator", "note",
    ), injection_outcomes)

    tolerances = {
        "version": "2.0.0",
        "profiles": {
            "emission_tonnes_v1": {
                "absolute_tolerance_t": 1e-6,
                "relative_tolerance": 1e-6,
                "comparison": "abs(actual-expected) <= max(abs_tol, abs(expected)*rel_tol)",
                "finite_values_required": True,
                "negative_values_allowed": False,
            },
            "aggregate_tonnes_v1": {
                "comparison": "diagnostic_only_sum_of_item_level_results",
                "recommended_absolute_tolerance_t": 1e-6,
                "recommended_relative_tolerance": 1e-6,
            },
        },
        "exact_match_fields": [
            "evaluation_item_id", "source_id", "target", "component_type", "component_key",
            "pollutant", "expected_status", "expected_value_applicable",
        ],
        "semantic_match_fields": {
            "method": "component_level_diagnostic_after_public_normalization; excluded_from_cross_method_common_S",
            "parameter": "component_level_diagnostic_after_normalizing_method_value_unit_and_standard_reference; excluded_from_cross_method_common_S",
            "control": "compare_control_semantics_and_efficiencies_not_native_parameter_ids",
            "standard_reference": "compare_normalized_T_CSES_clause_or_table_ids",
            "native_parameter_ids": "trace_audit_only_not_cross_method_score",
        },
        "blank_policy": {
            "information_insufficient": "generation_t_and_emission_t_must_be_blank",
            "source_data_invalid": "generation_t_and_emission_t_must_be_blank",
            "not_involved": "generation_t_and_emission_t_must_be_blank",
            "calculated": "both_numeric_values_required_including_zero",
        },
        "hard_constraints": [
            "generation_t>=0", "emission_t>=0", "emission_t<=generation_t+tolerance",
            "PM2.5_generation_t<=PM10_generation_t+tolerance",
        ],
        "rounding_policy": "Compare unrounded numeric values; display formatting is not evaluated.",
    }
    (OUTPUT_ROOT / "tolerances.json").write_text(json.dumps(tolerances, ensure_ascii=False, indent=2), encoding="utf-8")
    reason_equivalence = {
        "version": "2.0.0",
        "matching": "prefix_before_colon_then_alias_to_canonical_root",
        "aggregation_rule": "merge_same_source_and_canonical_root_across_pollutants",
        "codes": {
            code: {"canonical_root_cause": canonical, "requires_user_judgment": requires_user}
            for code, (canonical, requires_user) in sorted(REASON_EQUIVALENCE.items())
        },
        "generic_root_rules": [
            {
                "rule_id": "GEN-ROOT-NH3-FACTOR",
                "regex": r"NO_EF_NH3|NH3_NO_FACTOR|NO_FACTOR_NH3|NH3_NO_EF|NO_NH3_FACTOR|NH3.{0,40}(未提供|无).{0,20}(产生系数|排放系数|因子)",
                "canonical_root_cause": "pollutant_parameter_missing",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-FUEL-MAPPING",
                "regex": r"NO_EF_FUEL|FUEL_NO_FACTOR|FUEL_TYPE_UNKNOWN|FUEL_TYPE_UNMAPPED|FUEL_UNMAPPED|UNKNOWN_FUEL|NO_EF_FUEL_TYPE|缺少燃料类型|燃料类型.{0,30}(无对应|不在|无法映射)|其他燃料.{0,40}(无对应|无法|未给出燃料类型)",
                "canonical_root_cause": "fuel_mapping_unresolved",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-COMBUSTION-TECH",
                "regex": r"NO_EF_TECH|TECH_UNKNOWN|COMBUSTION_UNKNOWN|MISSING_TECH|NO_TECH_COAL|NO_TECH_PARAM|COAL_TECH_UNKNOWN|燃烧方式.{0,30}(缺失|其他|未知|无法)|燃烧技术.{0,30}(未知|无法|缺失)",
                "canonical_root_cause": "combustion_technology_unresolved",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-ACTIVITY",
                "regex": r"NO_FUEL_RECORD|NO_ACTIVITY_DATA|NO_ACTIVITY|ACT_MISSING|ACTIVITY_MISSING|无任何燃料.{0,20}(活动|消耗|数据)|缺少燃料消耗|活动水平.{0,20}(缺失|为空)|源记录.{0,20}(为空行|为空记录|无任何燃料)",
                "canonical_root_cause": "activity_level_missing",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-COAL-PARAMETER",
                "regex": r"MB_PARAM_UNAVAILABLE|FUEL_PARAM_MISSING|COKE_MB_PARAMS|物料衡算参数.{0,20}(未提供|缺失|无法)",
                "canonical_root_cause": "coal_parameter_missing",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-OTHER-TCE",
                "regex": r"OTHER_FUEL_TCE_UNRESOLVED|UNRESOLVED_OTHER_FUEL_TCE|FUEL_UNIT_TCE|NO_EF_TCE|FUEL_TCE_NO_TYPE|吨标准煤.{0,50}(无法换算|无对应|无.*系数)",
                "canonical_root_cause": "other_fuel_tce_unresolved",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-ACTIVITY-UNIT",
                "regex": r"UNIT_MISMATCH|UNIT_UNKNOWN|ACTIVITY_UNIT_UNSUPPORTED",
                "canonical_root_cause": "activity_unit_unresolved",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-SOURCE-RELATION",
                "regex": r"INVALID_BLANK_RECORD|EMPTY_RECORD|冻结.*(空记录|为空)|整行为空|无任何源字段|表尾批注|图例行",
                "canonical_root_cause": "source_relationship_missing",
                "requires_user_judgment": True,
            },
            {
                "rule_id": "GEN-ROOT-CONTROL",
                "regex": r"CONTROL_EFF_UNKNOWN|治理工艺.{0,40}(无法匹配|无对应|未计入)|除尘工艺.{0,40}(无法匹配|无对应|未计入)",
                "canonical_root_cause": "control_information_conflict",
                "requires_user_judgment": True,
            },
        ],
    }
    (OUTPUT_ROOT / "reason_code_equivalence.json").write_text(json.dumps(reason_equivalence, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation_protocol = {
        "version": "2.0.0",
        "reference_variant_routing": {
            "A": "B0", "B0": "B0", "B1_S1_to_S4": "B0", "B2": "B2",
            "C_25_50_75_100": "B0_filtered_by_target_neutral_candidate_ids", "D": "B0",
        },
        "public_keys": {
            "source": ["variant", "source_id"],
            "item": ["variant", "evaluation_item_id"],
            "source_pollutant_total": ["variant", "evaluation_total_id"],
            "exception_group": ["variant", "exception_group_id"],
        },
        "item_scoring_order": [
            "align_public_ids", "evaluate_presence_and_terminal_state", "apply_applicability_mask",
            "normalize_method_parameter_control_and_standard_reference", "compare_unrounded_values",
            "enforce_hard_constraints", "aggregate_diagnostics",
        ],
        "missing_or_extra_rows": "count_as_error; never change the reference denominator",
        "noncalculated_policy": "state_and_blank_value_are_scored; inapplicable method/parameter/control fields are not double-penalized",
        "B1": "pair each method S1-S4 with that method B0 by public item ID; separately compare each variant with B0 reference semantics",
        "B2": {
            "full_correctness": "all B2 items remain eligible",
            "targeted_injection_diagnostic": "only expected_injection_outcomes.scoring_eligible=true enters denominator",
            "negative_control": "score unchanged semantics separately",
        },
        "metric_boundaries": {
            "D": "source destination, terminal disposition, calculation basis, pollutant-relevant control and required exception root atoms",
            "N": "result completeness, reference value and independent recalculation atoms",
            "E": "source-specific empirical human/deterministic log time anchors",
        },
    }
    (OUTPUT_ROOT / "evaluation_protocol.json").write_text(json.dumps(evaluation_protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_ROOT / "reference_schema.json").write_text(json.dumps(reference_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(HERE / "compare_run_to_reference.py", OUTPUT_ROOT / "compare_run_to_reference.py")

    b0_sources = sources_by_variant["B0"]
    stats = {
        "b0_candidate_sources": len(b0_sources),
        "b0_industrial_sources": sum(source.target == "INDUSTRIAL" for source in b0_sources),
        "b0_power_sources": sum(source.target == "POWER" for source in b0_sources),
        "b0_excluded_sources": sum(source.target == "EXCLUDE" for source in b0_sources),
        "b0_items": sum(row["variant"] == "B0" for row in item_rows),
        "b2_items": sum(row["variant"] == "B2" for row in item_rows),
        "b0_source_pollutant_totals": sum(row["variant"] == "B0" for row in total_rows),
        "b2_source_pollutant_totals": sum(row["variant"] == "B2" for row in total_rows),
        "b2_injections_valid": sum(row["injection_validity"] == "valid" for row in injection_outcomes),
        "b2_injections_partially_valid": sum(row["injection_validity"] == "partially_valid" for row in injection_outcomes),
        "b2_injections_invalid": sum(row["injection_validity"] == "invalid" for row in injection_outcomes),
        "b2_manifest_status": builder.b2_manifest.get("status", "unspecified"),
        "b2_injected_sources": len(builder.injections),
        "b2_positive_injections": sum(row.get("plan_class") == "positive_injection" for row in builder.injections),
        "b2_negative_controls": sum(row.get("plan_class") == "negative_control" for row in builder.injections),
        "b2_preexisting_problems": sum(row.get("plan_class") == "preexisting_problem" for row in builder.injections),
    }
    write_readme(OUTPUT_ROOT / "README.md", stats)

    dependencies = [
        STANDARD_PDF,
        RAW_INPUT_MANIFEST,
        *[Path(spec["output_path"]).resolve() for spec in builder.raw_manifest["workbooks"]],
        *([B2_INJECTION_MANIFEST] if B2_INJECTION_MANIFEST.is_file() else []),
        *([B2_VARIANT_MANIFEST] if B2_VARIANT_MANIFEST.is_file() else []),
        *([B2_EVALUATION_AUDIT] if B2_EVALUATION_AUDIT.is_file() else []),
        *([
            B2_VARIANT_MANIFEST.parent / row["filename"]
            for row in json.loads(B2_VARIANT_MANIFEST.read_text(encoding="utf-8")).get("workbooks", [])
        ] if B2_VARIANT_MANIFEST.is_file() else []),
        *sorted(path for path in RULE_ROOT.rglob("*") if path.is_file()),
        Path(__file__).resolve(),
        HERE / "compare_run_to_reference.py",
    ]

    def dependency_record(path: Path) -> dict[str, Any]:
        try:
            relative = path.resolve().relative_to(WORKSPACE.resolve())
            logical_path = f"workspace/{relative.as_posix()}"
        except ValueError:
            relative = path.resolve().relative_to(EXPERIMENT_ROOT.resolve())
            logical_path = f"experiment/{relative.as_posix()}"
        return {
            "logical_path": logical_path,
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
    manifest = {
        "package_name": "independent_professional_reference_package",
        "version": "2.0.0",
        "status": "audited_frozen",
        "created_on": str(date.today()),
        "governing_standard": "T/CSES 144—2024",
        "rule_package": "fixed-combustion-rules v1.0.1; professionally reviewed and frozen",
        "reference_timing": "post_execution_output_isolated",
        "pre_registered": False,
        "assessor_blinding": False,
        "truth_claim": "independent_rule_based_professional_reference_not_measurement_truth",
        "method_output_access": "prohibited_and_not_used",
        "protocol_deviations": [],
        "variants": {
            "B0": "three raw de-identified base-table roles and 5,922 target-neutral candidates",
            "B1_S1_to_S4": "reuse B0 semantic expectations after perturbation reversal/normalization",
            "B2": f"raw-field injection manifest status={builder.b2_manifest.get('status', 'unspecified')}",
            "C_25_50_75_100": "reuse B0 expectations for target-neutral candidate-id subsets",
            "D": "reuse B0 full expectations",
        },
        "counts": {
            **stats,
            "source_rows_all_variants": len(source_rows),
            "item_rows_all_variants": len(item_rows),
            "source_pollutant_total_rows_all_variants": len(total_rows),
            "exception_rows": len(exception_rows),
            "b2_injected_sources": len(builder.injections),
        },
        "required_files": [
            "manifest.json", "expected_sources.csv", "expected_items.csv",
            "expected_source_pollutant_totals.csv", "expected_exceptions.csv", "tolerances.json",
            "compare_run_to_reference.py", "validation_report.json", "subagent_audit.md", "package_lock.json",
        ],
        "supplementary_files": [
            "README.md", "expected_aggregates.csv", "decision_register.csv",
            "expected_injection_outcomes.csv", "reference_schema.json", "validation_report.json",
            "reason_code_equivalence.json", "evaluation_protocol.json", "package_lock.json", "subagent_audit.md",
        ],
        "build_environment": {
            "command": "python3 reference/build_reference_package.py",
            "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}",
        },
        "dependencies": [dependency_record(path) for path in dependencies],
        "generated_files": {},
        "notes": [
            "No sealed run, method output, experiment aggregate, human baseline result or paper result table was read.",
            "Empirical operational decisions remain separately labeled from T/CSES provisions.",
            "Reference values are computed independently from the frozen rule tables and raw fact columns only; legacy calculated columns in input workbooks are ignored.",
            "B0 source classification is independently reconstructed from the raw industry code and both raw equipment fields.",
        ],
    }
    generated = [
        "expected_sources.csv", "expected_items.csv", "expected_exceptions.csv",
        "expected_source_pollutant_totals.csv",
        "expected_aggregates.csv", "decision_register.csv", "expected_injection_outcomes.csv",
        "tolerances.json", "reason_code_equivalence.json", "evaluation_protocol.json",
        "reference_schema.json", "README.md", "compare_run_to_reference.py",
    ]
    manifest["generated_files"] = {
        name: {"sha256": sha256(OUTPUT_ROOT / name), "bytes": (OUTPUT_ROOT / name).stat().st_size}
        for name in generated
    }
    (OUTPUT_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_ROOT / "subagent_audit.md").write_text(
        "# Independent reference v2 audit\n\n"
        "- Inputs: three raw environmental base-table roles and 5,922 target-neutral Base-102 candidates.\n"
        "- Rules: frozen reviewed package v1.0.1.\n"
        "- Prohibited dependencies: no participating-method output and no professional engine import.\n"
        "- Regression coverage: source classification, power/heat department routing, industrial coal parameters, and no-technology factor fallback.\n\n"
        "终审结论：通过\n",
        encoding="utf-8",
    )
    validation = validate_generated_package(OUTPUT_ROOT, stats)
    (OUTPUT_ROOT / "validation_report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if validation["status"] != "pass":
        raise RuntimeError(f"reference v2 validation failed: {validation['errors'][:10]}")
    for name in ("subagent_audit.md", "validation_report.json"):
        manifest["generated_files"][name] = {
            "sha256": sha256(OUTPUT_ROOT / name), "bytes": (OUTPUT_ROOT / name).stat().st_size,
        }
    (OUTPUT_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_package_lock(OUTPUT_ROOT)
    print(json.dumps({"output": str(OUTPUT_ROOT), **manifest["counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
