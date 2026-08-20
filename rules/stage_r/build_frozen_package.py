#!/usr/bin/env python3
"""Build the corrected and reviewed fixed-combustion rule package.

The Stage-R candidate is immutable evidence of the Agent extraction.  This
script creates a separate frozen package, applies the reviewed corrections,
adds the experiment's explicit operational rules, and locks every file by
hash.  It deliberately does not modify the candidate directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from openpyxl import load_workbook


ROOT = Path("/Users/wushuo/Desktop/环境学院论文")
EXPERIMENT = ROOT / "固定燃烧源清单经验缺失处置实验"
CANDIDATE = EXPERIMENT / "rules/stage_r/candidate"
FROZEN = EXPERIMENT / "rules/frozen/v1.0.1"
STANDARD = ROOT / "1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf"
PROTOCOL = ROOT / "论文研究笔记/固定燃烧源清单实验实施协议.md"
INPUTS = [
    (
        ROOT / "02 计算过程/固定燃烧源-工业锅炉_调整.xlsx",
        "用2022环统计算的结果",
        "INDUSTRIAL_ORIGINAL",
    ),
    (
        ROOT / "02 计算过程/固定燃烧源-火电、热力生产与供应_调整.xlsx",
        "火力、热力生产与供应（不包括生物质能发电）",
        "POWER_ORIGINAL",
    ),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and p.name != "rule_package_lock.json"):
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def dump_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def rule(rule_id: str, rule_type: str, source_type: str, section: str, pages: list[int],
         scope: dict[str, Any], conditions: dict[str, Any], action: dict[str, Any],
         on_missing: str = "withhold") -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_type": rule_type,
        "source": {
            "source_type": source_type,
            "document": "T/CSES 144—2024" if source_type.startswith("T_CSES") else "固定燃烧源清单实验实施协议v4",
            "section": section,
            "physical_pdf_pages": pages or [1],
        },
        "scope": scope,
        "conditions": conditions,
        "action": action,
        "on_missing": on_missing,
    }


def standard_rules() -> dict[str, list[dict[str, Any]]]:
    scope_rules = [
        rule("r_scope_power_heat", "scope", "T_CSES", "7.1", [9],
             {"source_sector": "电力热力源"},
             {"industry_code": ["4411", "4412", "4417", "4430"]},
             {"type": "select_scope", "selected_scope": "power_heat_fixed_combustion"}, "not_applicable"),
        rule("r_scope_industrial_boiler", "scope", "T_CSES", "8.3", [14],
             {"source_sector": "工业源化石燃料固定燃烧"},
             {"industry": ["采矿业", "制造业"], "equipment": "工业锅炉"},
             {"type": "select_scope", "selected_scope": "industrial_fossil_fixed_combustion"}, "not_applicable"),
        rule("r_scope_level4_unit", "scope", "T_CSES", "5.1、6.2", [6, 8],
             {"calculation_structure": "第四级排放源"},
             {"dimensions": ["行业", "燃料", "燃烧技术", "末端控制技术"]},
             {"type": "select_scope", "selected_scope": "level4_basic_calculation_unit"}),
        rule("r_scope_pollutants", "scope", "T_CSES", "1", [4],
             {"inventory_pollutants": ["SO2", "NOX", "CO", "VOCS", "PM10", "PM25", "BC", "OC", "NH3"]},
             {"pollutant_in_scope": True},
             {"type": "select_scope", "selected_scope": "pollutant_in_standard_scope"}, "not_applicable"),
    ]

    method_rules = [
        rule("r_method_coefficient_only", "method", "DATA_SCOPE_DECISION", "实施协议v4-1", [],
             {"experiment": "2022固定燃烧源清单重算"},
             {"verified_monitoring_dataset": False, "reported_base102_role": "comparison_only"},
             {"type": "select_method", "method": "production_emission_coefficient_and_coal_mass_balance"}),
        rule("r_method_industrial_boiler", "method", "T_CSES", "8.3.1", [14],
             {"source_sector": "工业源化石燃料固定燃烧"},
             {"activity": "燃料消耗量", "default_factor_table": "E.7"},
             {"type": "select_method", "method": "coefficient_or_coal_mass_balance"}),
        rule("r_method_power_heat", "method", "T_CSES", "7.2", [9, 10],
             {"source_sector": "电力热力源"},
             {"activity": "燃料消耗量", "default_factor_table": "D.1"},
             {"type": "select_method", "method": "coefficient_or_coal_mass_balance"}),
        rule("r_method_ef_acquisition", "method", "T_CSES", "6.3.1、7.2.1.2、8.3.1", [8, 9, 14],
             {"parameter": "产生系数"},
             {"experiment_available_sources": ["燃煤物料衡算", "D.1", "E.7"]},
             {"type": "select_method", "priority": ["coal_mass_balance_when_applicable", "appendix_factor"]}),
        rule("r_method_coal_mass_balance", "method", "T_CSES", "7.2.1.2、7.2.2、8.3.1", [9, 10, 14],
             {"fuel": "煤炭", "pollutants": ["SO2", "PM10", "PM25", "BC", "OC"]},
             {"required": ["sulfur_or_ash", "coal_parameter", "combustion_technology"]},
             {"type": "select_method", "method": "coal_mass_balance"}),
        rule("r_method_eta_actual", "method", "T_CSES", "7.2.1.2", [9],
             {"parameter": "污染物实际去除效率"},
             {"standard_formula": "eta_actual = eta_average * operation_rate"},
             {"type": "select_method", "method": "eta_average_times_operation_rate"}),
    ]

    formula_rules = [
        rule("r_formula_coefficient_method", "formula", "T_CSES", "公式(2)", [9],
             {"pollutants": ["SO2", "NOX", "CO", "VOCS", "PM10", "PM25", "BC", "OC"]},
             {"units": ["g/kg", "g/m3"], "output": "t"},
             {"type": "assign_formula", "formula": "G_t=A*EF/1000000; E_t=G_t*(1-eta)"}),
        rule("r_formula_so2_balance", "formula", "T_CSES", "公式(3)、C.1", [10, 40],
             {"fuel": "煤炭", "pollutant": "SO2"},
             {"source_sulfur_unit": "%", "required": ["S_percent", "sr"]},
             {"type": "assign_formula", "standard_formula": "EF=2*S*(1-sr)",
              "excel_formula_for_percent_input": "EF_g_per_kg=20*S_percent*(1-sr)"}),
        rule("r_formula_pm_balance", "formula", "T_CSES", "公式(4)、C.1、C.2", [10, 40, 41],
             {"fuel": "煤炭", "pollutants": ["PM10", "PM25"]},
             {"source_ash_unit": "%", "required": ["Aar_percent", "ar", "fPM"]},
             {"type": "assign_formula", "standard_formula": "EF=Aar*(1-ar)*fPM",
              "excel_formula_for_percent_input": "EF_g_per_kg=10*Aar_percent*(1-ar)*fPM"}),
        rule("r_formula_bc", "formula", "T_CSES", "公式(5)、C.3", [10, 42],
             {"fuel": "煤炭", "pollutant": "BC"}, {"required": ["EF_PM25", "fBC"]},
             {"type": "assign_formula", "formula": "EF_BC=EF_PM25*fBC"}),
        rule("r_formula_oc", "formula", "T_CSES", "公式(6)、C.3", [10, 42],
             {"fuel": "煤炭", "pollutant": "OC"}, {"required": ["EF_PM25", "fOC"]},
             {"type": "assign_formula", "formula": "EF_OC=EF_PM25*fOC"}),
        rule("r_formula_nox_capacity_lookup", "formula", "T_CSES", "D.1注b", [45],
             {"department": "电力生产", "fuel": ["煤炭", "煤矸石"], "pollutant": "NOX"},
             {"capacity_mw": {"<=100": 8.96, ">100_and_<300": 8.19, ">=300": 7.21}},
             {"type": "assign_formula", "formula": "EF_NOX=capacity_lookup(capacity_mw)"}),
        rule("r_formula_pm10_after_control", "formula", "T_CSES_DERIVED", "公式(2)、A.1注a", [9, 35],
             {"pollutant": "PM10"},
             {"constraint": "G_PM25<=G_PM10", "fractions": ["fine", "coarse"]},
             {"type": "assign_formula",
              "formula": "E_PM10=G_PM2.5*(1-eta_fine)+(G_PM10-G_PM2.5)*(1-eta_coarse)",
              "coverage_tokens": ["G_PM2.5", "G_PM10-G_PM2.5", "eta_fine", "eta_coarse"]}),
    ]

    control_rules = [
        rule("r_control_efficiency_table", "control", "T_CSES", "A.1", [33, 34, 35],
             {"parameter": "控制措施平均去除效率"},
             {"lookup_key": ["control_technology", "pollutant"],
              "pollutants": ["SO2", "NOX", "VOCS", "PM25", "PM25_10", "BC", "OC"]},
             {"type": "assign_control", "parameter_table": "parameters/control_efficiencies.csv"}),
        rule("r_control_none", "control", "T_CSES", "3.4、A.1", [5, 35],
             {"control_status": "无控制技术"}, {"control_technology": "无控制技术"},
             {"type": "assign_control", "eta": 0, "label": "无适用治理措施按0"}, "not_applicable"),
        rule("r_control_pm25_10_definition", "control", "T_CSES", "A.1注a", [35],
             {"particle_fraction": "PM25_10"}, {"definition": "2.5um<diameter<=10um"},
             {"type": "assign_control", "formula": "G_PM25_10=G_PM10-G_PM25"}),
        rule("r_control_nh3_slip", "control", "T_CSES", "E.1烟气脱硝行", [50],
             {"process": ["SCR", "SNCR"], "pollutant": "NH3"},
             {"factor": {"SCR": 0.16, "SNCR": 0.17}, "unit": "g/kg煤"},
             {"type": "assign_control", "parameter_table": "parameters/ammonia_slip_factors.csv"}),
    ]

    qc_rules = yaml.safe_load((CANDIDATE / "rules/quality_control.yaml").read_text(encoding="utf-8"))["rules"][:3]
    return {
        "scope.yaml": scope_rules,
        "methods.yaml": method_rules,
        "formulas.yaml": formula_rules,
        "controls.yaml": control_rules,
        "quality_control.yaml": qc_rules,
    }


def operational_rules() -> list[dict[str, Any]]:
    labels = [
        "工艺缺省效率", "标准组合工艺缺省效率", "低氮燃烧缺省效率", "协同去除缺省效率",
        "多工艺取最大缺省效率", "治理信息缺失按0", "治理工艺无法映射按0",
        "无适用治理措施按0", "SCR/SNCR氨逃逸核算",
    ]
    return [
        rule("r_ops_no_monitoring_path", "method", "DATA_SCOPE_DECISION", "实施协议v4-1", [],
             {"experiment": "2022重算"}, {"verified_monitoring_data": False},
             {"type": "select_method", "selected": "排放因子法和燃煤物料衡算法",
              "base102": "基102产生量、排放量只作结果对比，不参与方法选择、效率反推或最终计算"}),
        rule("r_ops_e7_gas_unit", "formula", "EXTERNAL_UNIT_INTERPRETATION", "D.1注c、广东指南表3注c、生态环境部公告2021年第24号", [],
             {"fuels": ["焦炉煤气", "高炉煤气", "其它煤气", "天然气", "转炉煤气", "其它气体燃料"]},
             {"activity_unit": "m3"}, {"type": "assign_formula", "factor_unit": "g/m3"}),
        rule("r_ops_excel_formula_chain", "formula", "IMPLEMENTATION_REQUIREMENT", "实施协议v4-2", [],
             {"workbook": "Excel"}, {"formula_fields": ["活动水平", "因子匹配", "产生量"]},
             {"type": "assign_formula", "implementation": "Excel公式"}),
        rule("r_ops_operation_rate_one", "control", "EMPIRICAL_ASSUMPTION", "实施协议v4-10", [],
             {"operation_time_available": False}, {"设施投运率": "=1", "interpretation": "经验"},
             {"type": "assign_control", "operation_rate": 1,
              "note": "经验缺省效率，不代表设施真实去除效率"}),
        rule("r_ops_control_missing_zero", "control", "EMPIRICAL_MISSING_DATA_RULE", "实施协议v4-4", [],
             {"control_field": "blank"}, {"applicable_efficiency": 0},
             {"type": "assign_control", "label": "治理信息缺失按0"}),
        rule("r_ops_control_unmapped_zero", "control", "EMPIRICAL_MISSING_DATA_RULE", "实施协议v4-4", [],
             {"control_field": "nonblank_unmapped"}, {"applicable_efficiency": 0},
             {"type": "assign_control", "label": "治理工艺无法映射按0"}),
        rule("r_ops_standard_combination", "control", "T_CSES_OPERATIONALIZATION", "A.1、实施协议v4-6", [33, 34, 35],
             {"explicit_combinations": ["低氮燃烧技术+选择性非催化还原法", "低氮燃烧技术+选择性催化还原法"]},
             {"priority": "combination_row"},
             {"type": "assign_control", "label": "标准组合工艺缺省效率"}),
        rule("r_ops_multi_process_max", "control", "EMPIRICAL_COMBINATION_RULE", "实施协议v4-6", [],
             {"multiple_applicable_processes": True, "standard_combination_available": False},
             {"aggregation": "max_not_sum"},
             {"type": "assign_control", "label": "多工艺取最大缺省效率"}),
        rule("r_ops_cobenefit_all_fields", "control", "T_CSES_OPERATIONALIZATION", "A.1、实施协议v4-6", [33, 34, 35],
             {"for_each_pollutant_check_fields": ["脱硫", "脱硝", "除尘"], "effect": "协同去除"},
             {"candidate_efficiencies": "all mapped A.1 cells"},
             {"type": "assign_control", "label": "协同去除缺省效率"}),
        rule("r_ops_co_vocs_zero", "control", "T_CSES_OPERATIONALIZATION", "A.1、实施协议v4-8", [33, 34, 35],
             {"pollutants": ["CO", "VOCs"], "explicit_applicable_control": False},
             {"efficiency": 0}, {"type": "assign_control", "label": "无适用治理措施按0"}),
        rule("r_ops_nh3_no_scr_zero", "control", "EXPERIMENT_DISPOSITION_RULE", "实施协议v4-9", [],
             {"explicit": "明确没有SCR/SNCR", "pollutant": "NH3"}, {"result": 0},
             {"type": "assign_control", "label": "无适用治理措施按0"}),
        rule("r_ops_nh3_denox_missing_zero", "control", "EMPIRICAL_MISSING_DATA_RULE", "实施协议v4-9", [],
             {"denox_information": "blank", "pollutant": "NH3"}, {"result": 0},
             {"type": "assign_control", "label": "脱硝信息缺失按0"}),
        rule("r_ops_nh3_coal_missing", "control", "CALCULATION_SAFETY_RULE", "实施协议v4-9", [],
             {"SCR/SNCR": True, "applicable_coal_activity": False}, {"result": "留空", "destination": "异常清单"},
             {"type": "assign_control", "label": "信息不足", "note": "SCR/SNCR存在但煤耗缺失时NH3留空并进入异常清单"}),
        rule("r_ops_foundational_missing", "quality_control", "CALCULATION_SAFETY_RULE", "实施协议v4-11", [],
             {"required_parameters": ["燃料量", "燃料类型", "硫分", "灰分", "容量", "产生因子"]},
             {"when_missing": "留空", "destination": "最终异常清单"},
             {"type": "check_quality", "admission": "withhold"}),
        rule("r_ops_no_reported_inverse", "quality_control", "EXPERIMENT_PROHIBITION", "实施协议v4-3", [],
             {"prohibited": "1-环统排放量/环统产生量"}, {"enforcement": "禁止环统反推治理效率"},
             {"type": "check_quality", "required_absence": True}),
        rule("r_ops_no_arbitrary_vocs", "quality_control", "EXPERIMENT_PROHIBITION", "实施协议v4-3、v4-8", [],
             {"pollutant": "VOCs", "prohibited": ["除以5", "除以2"]}, {"enforcement": "禁止"},
             {"type": "check_quality", "required_absence": True}),
        rule("r_ops_reported_comparison_only", "quality_control", "DATA_SCOPE_DECISION", "实施协议v4-1", [],
             {"source": "基102", "role": "只作结果对比"}, {"calculation_participation": "不参与"},
             {"type": "check_quality", "separate_columns": True}),
        rule("r_ops_source_aggregation_gate", "quality_control", "CALCULATION_SAFETY_RULE", "实施协议v4-11", [],
             {"any_required_fuel_item_missing": True}, {"source_pollutant_total": "不得输出部分汇总"},
             {"type": "check_quality", "admission": "withhold"}),
        rule("r_ops_disposition_labels", "quality_control", "OUTPUT_REQUIREMENT", "实施协议v4-12", [],
             {"per_source_pollutant": True}, {"labels": labels},
             {"type": "check_quality", "required_labels": labels}),
        rule("r_ops_exception_list", "quality_control", "OUTPUT_REQUIREMENT", "实施协议v4-11", [],
             {"unreliable_or_notice_items": True}, {"destination": "最终异常清单"},
             {"type": "check_quality", "output": "exception_ledger"}),
        rule("r_ops_industrial_coal_department_canonicalization", "parameter_mapping", "ANOMALY_AUDIT_CORRECTION", "异常审计2026-08", [],
             {"sector": "工业源", "parameter_table": "附录C"},
             {"canonical_department": "采矿业和制造业"},
             {"type": "normalize_parameter_key", "field": "department"}),
        rule("r_ops_no_technology_factor_fallback", "parameter_mapping", "ANOMALY_AUDIT_CORRECTION", "异常审计2026-08", [],
             {"exact_technology_match": False, "fallback_technology": "不分技术"},
             {"candidate_count": 1},
             {"type": "controlled_parameter_fallback", "ambiguous_candidates": "withhold"}),
        rule("r_ops_anomaly_audit_taxonomy", "quality_control", "ANOMALY_AUDIT_CORRECTION", "异常审计2026-08", [],
             {"audited_source_exceptions": True},
             {"reason_codes": [
                 "NH3_FACTOR_SCOPE_MISMATCH", "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE",
                 "OTHER_FUEL_TCE_UNRESOLVED", "COMBUSTION_TECHNOLOGY_AMBIGUOUS",
                 "T_CSES_COAL_BC_OC_PARAMETER_GAP", "FACTOR_APPLICABILITY_UNRESOLVED",
                 "T_CSES_COAL_PARAMETER_GAP",
             ]},
             {"type": "assign_exception_taxonomy", "source_level_reporting": True}),
    ]


def actual_values() -> dict[str, Counter[str]]:
    fields = ["燃料一类型", "燃料二类型", "工业锅炉燃烧方式", "电站锅炉燃烧方式",
              "脱硫处理工艺名称", "脱硝处理工艺名称", "除尘处理工艺名称"]
    counters = {field: Counter() for field in fields}
    for path, sheet_name, _ in INPUTS:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value).strip() if value is not None else "" for value in next(rows)]
        index = {name: pos for pos, name in enumerate(headers) if name}
        for row in rows:
            year = row[index["统计年份"]] if "统计年份" in index else None
            company = row[index["填报单位详细名称"]] if "填报单位详细名称" in index else None
            if year in (None, "") or company in (None, ""):
                continue
            for field in fields:
                if field in index and row[index[field]] not in (None, ""):
                    counters[field][str(row[index[field]]).strip()] += 1
        workbook.close()
    return counters


def fuel_mapping(raw: str) -> tuple[str, str, str]:
    direct = {
        "天然气": "天然气", "柴油": "柴油", "一般烟煤": "煤炭", "无烟煤": "煤炭",
        "原煤": "煤炭", "褐煤": "煤炭", "高炉煤气": "高炉煤气", "燃料油": "燃料油",
        "焦炉煤气": "焦炉煤气", "煤制品": "煤炭", "石油焦": "石油焦",
        "炼厂干气": "炼厂干气", "煤矸石（用于燃料）": "煤矸石", "其他石油制品": "其它石油制品",
        "原油": "原油", "转炉煤气": "转炉煤气", "发生炉煤气": "其它煤气",
        "润滑油": "其它石油制品", "焦炭": "焦炭", "其他焦化产品": "其它焦化产品",
        "其他洗煤": "煤炭", "液化天然气": "液化天然气", "液化石油气": "液化石油气",
    }
    if raw in direct:
        return direct[raw], "mapped", "同义词或标准原词"
    if raw in {"其他燃料", "其他燃料（燃气锅炉，又对比低位发热量，判断可能是重油热裂解煤气）"}:
        return "", "unmapped", "燃料事实不足，不依据描述推断"
    if raw in {"工业废料（用于燃料）", "生物燃料"}:
        return "", "outside_fossil_scope", "不属于本次化石燃料固定燃烧因子范围"
    return "", "unmapped", "规则包无唯一映射"


def combustion_mapping(raw: str) -> tuple[str, str, str]:
    if raw == "煤粉炉":
        return "煤粉炉", "mapped", "标准原词"
    if raw == "循环流化床锅炉":
        return "流化床炉", "mapped", "标准同义名称"
    if raw in {"链条炉", "抛煤机炉"}:
        return "自动炉排层燃炉", "mapped", "机械加煤层燃炉"
    if raw in {"室燃炉", "其他", "其他层燃炉", "层燃炉"}:
        return "", "fuel_dependent_or_ambiguous", "气/液体燃料按燃料确定；煤炭记录需单独核定"
    return "", "unmapped", "规则包无唯一映射"


def unique_extend(result: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in result:
            result.append(value)


def control_mapping(field: str, raw: str) -> tuple[list[str], str, str]:
    values: list[str] = []
    note_parts: list[str] = []
    if field == "脱硫处理工艺名称":
        if "炉内喷钙" in raw or "炉内脱硫" in raw:
            unique_extend(values, ["炉内喷钙法"])
        if any(token in raw for token in ["石灰石/石膏", "石灰石-石膏", "石灰石－石膏", "湿法石灰石-石膏"]):
            unique_extend(values, ["石灰石/石膏法"])
        if "石灰/石膏" in raw:
            unique_extend(values, ["石灰石/石膏法"]); note_parts.append("石灰/石膏按湿法石膏路线同义归并")
        if "双碱" in raw: unique_extend(values, ["双碱法"])
        if "烟气循环流化床" in raw: unique_extend(values, ["烟气循环流化床法"])
        if "旋转喷雾干燥" in raw: unique_extend(values, ["旋转喷雾干燥法"])
        if "氧化镁" in raw: unique_extend(values, ["氧化镁法"])
        if "海水" in raw: unique_extend(values, ["海水法"])
        if "氨法" in raw: unique_extend(values, ["氨法"])
        if "湿法除尘" in raw: unique_extend(values, ["湿式除尘法"])
        if any(token in raw for token in ["钠碱", "电石渣", "型煤固硫", "S12|其他"]):
            unique_extend(values, ["其他脱硫技术"]); note_parts.append("未列名脱硫路线归入A.1其他脱硫技术")
        if "湿法脱硫除尘一体化" in raw:
            unique_extend(values, ["其他脱硫技术", "湿式除尘法"]); note_parts.append("一体化同时保留脱硫和湿式除尘作用")
    elif field == "脱硝处理工艺名称":
        has_scr = "SCR" in raw or ("选择性催化还原" in raw and "非催化" not in raw)
        has_sncr = "SNCR" in raw or "选择性非催化还原" in raw
        has_low = "低氮燃烧" in raw
        if has_low and has_scr and not has_sncr:
            unique_extend(values, ["低氮燃烧技术+选择性催化还原法"])
        elif has_low and has_sncr and not has_scr:
            unique_extend(values, ["低氮燃烧技术+选择性非催化还原法"])
        else:
            if has_low: unique_extend(values, ["低氮燃烧技术"])
            if has_scr: unique_extend(values, ["选择性催化还原法"])
            if has_sncr: unique_extend(values, ["选择性非催化还原法"])
        if "烟气循环燃烧" in raw or raw == "N08|其他":
            unique_extend(values, ["其他脱硝技术"])
        if "N08其他（低氮燃烧" in raw:
            unique_extend(values, ["低氮燃烧技术"])
    else:
        if "高效静电" in raw or "湿式电除尘" in raw or "高效电除尘" in raw:
            unique_extend(values, ["高效静电除尘法"])
        if "电袋" in raw:
            unique_extend(values, ["电袋复合除尘法"])
        if "袋式" in raw or "布袋" in raw:
            unique_extend(values, ["袋式除尘法"])
        if ("静电除尘" in raw or "P05|板式" in raw or "P06|管式" in raw) and "高效静电" not in raw:
            unique_extend(values, ["普通静电除尘法"])
        if any(token in raw for token in ["喷淋塔", "冲击水浴", "文丘里", "离心水膜", "湿式除雾", "除雾器", "湿式除尘器"]):
            unique_extend(values, ["湿式除尘法"])
        if "多管旋风" in raw:
            unique_extend(values, ["多管旋风除尘法"])
        elif "旋风" in raw:
            unique_extend(values, ["机械式除尘法"])
        if "机械" in raw:
            unique_extend(values, ["机械式除尘法"])
        if "P15|其他" in raw:
            unique_extend(values, ["其他除尘技术"])
        if "湿法脱硫协同" in raw:
            unique_extend(values, ["其他脱硫技术"]); note_parts.append("湿法脱硫协同归入A.1其他脱硫技术并参与跨字段协同判断")
    status = "mapped" if values else "unmapped"
    note = "；".join(note_parts) if note_parts else ("按原词或编码语义映射" if values else "规则包无唯一映射")
    return values, status, note


def build_mappings() -> None:
    counters = actual_values()
    fuel_counter = counters["燃料一类型"] + counters["燃料二类型"]
    fuel_rows = []
    for raw, count in sorted(fuel_counter.items()):
        standard, status, note = fuel_mapping(raw)
        fuel_rows.append({"raw_value": raw, "standard_fuel": standard, "mapping_status": status,
                          "observed_count": count, "source_type": "reviewed_operational_mapping", "note": note})
    write_csv(FROZEN / "mappings/source_fuel_aliases.csv",
              ["raw_value", "standard_fuel", "mapping_status", "observed_count", "source_type", "note"], fuel_rows)

    combustion_counter = counters["工业锅炉燃烧方式"] + counters["电站锅炉燃烧方式"]
    combustion_rows = []
    for raw, count in sorted(combustion_counter.items()):
        standard, status, note = combustion_mapping(raw)
        combustion_rows.append({"raw_value": raw, "standard_combustion_technology": standard,
                                "mapping_status": status, "observed_count": count,
                                "source_type": "reviewed_operational_mapping", "note": note})
    write_csv(FROZEN / "mappings/source_combustion_aliases.csv",
              ["raw_value", "standard_combustion_technology", "mapping_status", "observed_count", "source_type", "note"],
              combustion_rows)

    control_rows = []
    for field in ["脱硫处理工艺名称", "脱硝处理工艺名称", "除尘处理工艺名称"]:
        for raw, count in sorted(counters[field].items()):
            standards, status, note = control_mapping(field, raw)
            if not standards:
                standards = [""]
            for order, standard in enumerate(standards, start=1):
                control_rows.append({"raw_field": field, "raw_value": raw,
                                     "standard_control_technology": standard, "component_order": order,
                                     "mapping_status": status, "observed_count": count,
                                     "source_type": "reviewed_operational_mapping", "note": note})
    write_csv(FROZEN / "mappings/source_control_aliases.csv",
              ["raw_field", "raw_value", "standard_control_technology", "component_order",
               "mapping_status", "observed_count", "source_type", "note"], control_rows)


def patch_industrial_gas_units() -> None:
    path = FROZEN / "parameters/industrial_boiler_emission_factors.csv"
    rows = read_csv(path)
    volume_gases = {"焦炉煤气", "高炉煤气", "其它煤气", "天然气", "转炉煤气", "其它气体燃料"}
    for row in rows:
        if row["fuel"] in volume_gases and row["mode"] == "constant":
            row["unit"] = "g/m3"
            row["source_section"] = row["source_section"] + "；气体单位按外部一致性解释"
    write_csv(path, list(rows[0]), rows)


def patch_industrial_coal_department() -> None:
    """Align Appendix-C industrial rows with the engine's T/CSES 8.3 department."""
    path = FROZEN / "parameters/coal_parameters.csv"
    rows = read_csv(path)
    for row in rows:
        if row["sector"] == "工业源":
            row["department"] = "采矿业和制造业"
    write_csv(path, list(rows[0]), rows)


def build_tests(all_rule_ids: list[str]) -> None:
    selected = [
        "r_formula_coefficient_method", "r_formula_so2_balance", "r_formula_pm_balance",
        "r_formula_pm10_after_control", "r_formula_nox_capacity_lookup", "r_control_nh3_slip",
        "r_ops_control_missing_zero", "r_ops_control_unmapped_zero", "r_ops_standard_combination",
        "r_ops_multi_process_max", "r_ops_cobenefit_all_fields", "r_ops_nh3_coal_missing",
        "r_ops_foundational_missing", "r_ops_source_aggregation_gate", "r_ops_no_reported_inverse",
        "r_ops_no_arbitrary_vocs", "r_ops_disposition_labels",
        "r_ops_industrial_coal_department_canonicalization",
        "r_ops_no_technology_factor_fallback",
        "r_ops_anomaly_audit_taxonomy",
    ]
    tests = []
    for index, rule_id in enumerate(selected, start=1):
        tests.append({"test_id": f"FRZ-{index:03d}", "rule_id": rule_id, "test_type": "reviewed_contract",
                      "expected": "pass", "note": "冻结前语义与参数契约测试"})
    assert set(selected) <= set(all_rule_ids)
    path = FROZEN / "tests/rule_tests.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in tests), encoding="utf-8")


def main() -> None:
    if FROZEN.exists():
        shutil.rmtree(FROZEN)
    (FROZEN / "parameters").mkdir(parents=True)
    (FROZEN / "mappings").mkdir(parents=True)

    for path in (CANDIDATE / "parameters").glob("*.csv"):
        shutil.copy2(path, FROZEN / "parameters" / path.name)
    for path in (CANDIDATE / "mappings").glob("standard_*.csv"):
        shutil.copy2(path, FROZEN / "mappings" / path.name)

    patch_industrial_gas_units()
    patch_industrial_coal_department()
    build_mappings()

    grouped = standard_rules()
    grouped["operational.yaml"] = operational_rules()
    all_rules: list[dict[str, Any]] = []
    for name, rules in grouped.items():
        dump_yaml(FROZEN / "rules" / name, {"rules": rules})
        all_rules.extend(rules)
    dump_yaml(FROZEN / "rules.yaml", {
        "package_id": "tcses-144-2024-fixed-combustion-reviewed",
        "version": "1.0.1",
        "rule_count": len(all_rules),
        "rule_files": [{"path": f"rules/{name}", "rule_ids": [r["rule_id"] for r in rules]} for name, rules in grouped.items()],
    })
    dump_json(FROZEN / "rule_index.json", [
        {
            "rule_id": item["rule_id"],
            "rule_type": item["rule_type"],
            "source_type": item["source"].get("source_type", "T_CSES"),
            "source_section": item["source"]["section"],
            "scope": item["scope"],
            "action": item["action"],
            "on_missing": item["on_missing"],
        }
        for item in all_rules
    ])
    build_tests([item["rule_id"] for item in all_rules])

    dump_yaml(FROZEN / "references.yaml", {
        "standard": [{"title": "T/CSES 144—2024 城市大气污染源排放清单编制技术指南",
                      "path": str(STANDARD), "sha256": sha256(STANDARD),
                      "role": "范围、方法、公式与缺省参数"}],
        "supporting_sources": [
            {"title": "生态环境部公告2021年第24号及工业锅炉产排污系数手册",
             "url": "https://www.mee.gov.cn/xxgk2018/xxgk/xxgk01/202106/t20210618_839512.html",
             "role": "气体燃料活动水平单位一致性解释"},
            {"title": "广东省大气污染物排放源清单编制指南（2024年11月稿）",
             "path": str(ROOT / "广东省大气污染物排放源清单编制指南-202411.docx"),
             "role": "表3注c气体燃料因子单位一致性解释，不替换T/CSES因子值"},
            {"title": "固定燃烧源清单实验实施协议v4", "path": str(PROTOCOL),
             "role": "当前数据范围、经验缺失处置、禁止逻辑与输出要求"},
        ],
        "source_hierarchy": ["T_CSES", "T_CSES_DERIVED", "EXTERNAL_UNIT_INTERPRETATION",
                             "DATA_SCOPE_DECISION", "EMPIRICAL_ASSUMPTION", "CALCULATION_SAFETY_RULE",
                             "IMPLEMENTATION_REQUIREMENT"],
    })

    review_text = f"""# 固定燃烧源规则包专业复核快照\n\n- 状态：**已复核通过并冻结**\n- 版本：`1.0.1`\n- 候选包：`{CANDIDATE}`（保留不改）\n- 指南：T/CSES 144—2024\n- 标准文件 SHA-256：`{sha256(STANDARD)}`\n\n## 复核后关键决定\n\n1. 当前数据不建立监测数据核算路径；基102产生量和排放量只作结果对比。\n2. 补入 PM10 细、粗粒径分别治理的推导式。\n3. 低氮燃烧、SNCR、SCR及标准组合统一采用22%、30%、42%、52%、64%。\n4. 三类治理字段对每个污染物共同参与A.1适用性判断，保留协同去除。\n5. 无标准组合时采用最大适用缺省效率，不将多项效率相加。\n6. 治理信息缺失或无法映射可经验按0；核算必要参数缺失必须留空。\n7. NH3只按SCR/SNCR氨逃逸独立核算。\n8. 设施投运率经验假设为1，不表述为真实设施效率。\n9. 禁止环统反推效率，禁止VOCs无依据除以5或除以2。\n10. E.7六类体积计量气体燃料的因子单位按g/m3解释，并显式记录为外部一致性解释。\n11. 工业源附录C参数部门名称统一为“采矿业和制造业”。\n12. 仅在精确技术匹配不存在且候选唯一时，允许回退至“不分技术”因子。\n13. 502条源级异常审计形成六类明确处置原因；两类程序误阻断已修复。\n\n专业复核仅作为候选规则进入冻结包的关口，不与Agent抽取结果做方法优劣比较。\n"""
    (FROZEN / "professional_review_snapshot.md").write_text(review_text, encoding="utf-8")

    declared = []
    for path in sorted(p for p in FROZEN.rglob("*") if p.is_file() and p.name not in {"manifest.yaml", "rule_package_lock.json"}):
        declared.append({"path": path.relative_to(FROZEN).as_posix(), "sha256": sha256(path)})
    manifest = {
        "package_id": "tcses-144-2024-fixed-combustion-reviewed",
        "version": "1.0.1",
        "status": "frozen",
        "professional_review_status": "passed",
        "standard": {"name": "T/CSES 144—2024", "path": str(STANDARD), "sha256": sha256(STANDARD)},
        "scope": {"records": {"industrial_boiler": 4025, "power_heat": 367},
                  "pollutants": ["SO2", "NOX", "CO", "VOCS", "PM10", "PM25", "BC", "OC", "NH3"],
                  "method_path": ["coefficient", "coal_mass_balance"], "monitoring_path": False},
        "files": declared,
    }
    dump_yaml(FROZEN / "manifest.yaml", manifest)
    package_hash = tree_hash(FROZEN)
    dump_json(FROZEN / "rule_package_lock.json", {
        "package_id": manifest["package_id"], "version": manifest["version"], "status": "frozen",
        "professional_review_status": "passed", "package_hash": package_hash,
        "candidate_path": str(CANDIDATE), "candidate_is_immutable": True,
    })
    print(json.dumps({"success": True, "frozen_path": str(FROZEN), "rule_count": len(all_rules),
                      "package_hash": package_hash, "files": len(declared)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
