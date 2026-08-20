"""Build the neutral JSON payload consumed by the artifact-tool workbook renderer."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


POLLUTANTS = ("SO2", "NOX", "CO", "VOCS", "PM10", "PM25", "BC", "OC", "NH3")


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def decode_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [clean(value)]
    return result if isinstance(result, list) else [result]


def join_list(value: Any) -> str:
    return " | ".join(clean(item) for item in decode_list(value) if clean(item))


def factor_key(target: str, department: str, fuel: str, technology: str, pollutant: str) -> str:
    return "|".join((target, department, fuel, technology, pollutant))


def coal_key(target: str, department: str, technology: str) -> str:
    return "|".join((target, department, technology))


def nh3_key(row: sqlite3.Row) -> str:
    ids = set(decode_list(row["control_parameter_ids"]))
    if {"E1-SCR-NH3", "E1-SNCR-NH3"}.issubset(ids):
        return "NH3|SCR+SNCR"
    if "E1-SCR-NH3" in ids:
        return "NH3|SCR"
    if "E1-SNCR-NH3" in ids:
        return "NH3|SNCR"
    return "NH3|ZERO"


def source_rows(target: str, connection: sqlite3.Connection) -> tuple[list[str], list[list[Any]], dict[str, dict[str, Any]]]:
    headers = [
        "源ID", "目标源类", "企业", "地市", "区县", "行业代码", "行业名称", "经度", "纬度", "排放口",
        "是否低氮燃烧", "原始脱硫工艺", "原始脱硝工艺", "原始除尘工艺", "原始燃烧方式", "装机容量(MW)",
        "来源文件", "基102_SO2产生量(t)", "基102_SO2排放量(t)", "基102_NOx产生量(t)", "基102_NOx排放量(t)",
        "基102_PM产生量(t)", "基102_PM排放量(t)", "基102_VOCs产生量(t)", "基102_VOCs排放量(t)", "原Excel行",
    ]
    output: list[list[Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    query = """
        SELECT d.*,rr.source_path,rr.source_sheet,rr.source_row
        FROM device_sources d JOIN raw_records rr ON rr.raw_id=d.raw_id
        WHERE d.target=? ORDER BY d.source_id
    """
    for row in connection.execute(query, (target,)):
        payload = json.loads(row["payload_json"])
        cache[row["source_id"]] = payload
        reported = payload.get("reported") or {}
        output.append([
            row["source_id"], row["target"], payload.get("company"), payload.get("city"), payload.get("district"),
            payload.get("industry_code"), payload.get("industry_name"), payload.get("longitude"), payload.get("latitude"),
            payload.get("outlet_id"), payload.get("low_nox"), payload.get("desulf_raw"), payload.get("denox_raw"),
            payload.get("dust_raw"), payload.get("combustion"), payload.get("capacity_mw"), row["source_path"],
            numeric(reported.get("SO2_generation_t")), numeric(reported.get("SO2_emission_t")),
            numeric(reported.get("NOX_generation_t")), numeric(reported.get("NOX_emission_t")),
            numeric(reported.get("PM_generation_t")), numeric(reported.get("PM_emission_t")),
            (numeric(reported.get("VOCS_generation_kg")) or 0.0) / 1000.0 if numeric(reported.get("VOCS_generation_kg")) is not None else None,
            (numeric(reported.get("VOCS_emission_kg")) or 0.0) / 1000.0 if numeric(reported.get("VOCS_emission_kg")) is not None else None,
            row["source_row"],
        ])
    return headers, output, cache


def fuel_rows(
    target: str, connection: sqlite3.Connection, sources: dict[str, dict[str, Any]],
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "燃料行ID", "源ID", "目标源类", "核算部门", "原始燃料", "标准燃料快照", "原始燃烧方式", "标准技术快照",
        "原始消耗量", "原始单位", "含硫量", "含硫量单位", "灰分(%)", "标准活动水平(公式)", "标准单位(公式)",
        "活动水平快照", "公式-快照差", "映射状态", "来源文件", "来源工作表", "原Excel行",
    ]
    query = """
        SELECT f.*,d.target,d.department,rr.source_path,rr.source_sheet,rr.source_row,
               r.normalized_fuel,r.normalized_technology,r.activity_value
        FROM fuels f JOIN device_sources d ON d.source_id=f.source_id
        JOIN raw_records rr ON rr.raw_id=d.raw_id
        LEFT JOIN calculation_items i ON i.fuel_id=f.fuel_id AND i.pollutant='SO2'
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        WHERE d.target=? ORDER BY f.source_id,f.fuel_id
    """
    output = []
    for row in connection.execute(query, (target,)):
        mapped = "通过" if clean(row["normalized_fuel"]) and clean(row["normalized_technology"]) else "待核"
        output.append([
            row["fuel_id"], row["source_id"], row["target"], row["department"], row["raw_fuel"], row["normalized_fuel"],
            sources[row["source_id"]].get("combustion"), row["normalized_technology"], row["amount"], row["raw_unit"],
            row["sulfur"], row["sulfur_unit"], row["ash"], None, None, row["activity_value"], None, mapped,
            row["source_path"], row["source_sheet"], row["source_row"],
        ])
    return headers, output


def item_rows(target: str, connection: sqlite3.Connection, package: Path) -> tuple[list[str], list[list[Any]], list[sqlite3.Row]]:
    headers = [
        "核算项ID", "源ID", "核算项类型", "目标源类", "核算部门", "企业", "地市", "行业代码", "设备类型", "原始燃烧方式",
        "装机容量(MW)", "是否低氮", "燃料行ID", "燃料槽位", "原始燃料", "标准燃料快照", "标准技术快照", "原始消耗量",
        "原始单位", "含硫量", "含硫量单位", "灰分(%)", "污染物", "依赖核算项", "因子检索键", "因子模式(公式)",
        "参数ID(公式)", "参数值(公式)", "参数单位(公式)", "燃煤参数键", "硫入底灰比(公式)", "灰入底灰比(公式)",
        "PM2.5比例(公式)", "PM10比例(公式)", "BC比例(公式)", "OC比例(公式)", "有效因子(公式)", "因子快照", "公式-因子差",
        "活动水平(公式)", "活动水平单位(公式)", "活动水平快照", "公式-活动差", "有效治理技术", "治理参数ID", "治理处置标签",
        "设施投运率经验假设", "一般去除效率(%)", "PM2.5去除效率(%)", "PM2.5-10去除效率(%)", "候选工艺", "规则标志",
        "准入动作", "准入原因", "产生量(公式,t)", "排放量(公式,t)", "产生量快照(t)", "排放量快照(t)", "公式-产生量差",
        "公式-排放量差", "标准位置", "标准页码", "来源文件", "来源工作表", "原Excel行",
    ]
    query = """
        SELECT i.item_id,i.source_id,i.fuel_id,i.item_type,i.pollutant,i.dependency_id,
               d.target,d.department,d.payload_json,rr.source_path,rr.source_sheet,rr.source_row,
               f.slot,f.raw_fuel,f.amount,f.raw_unit,f.sulfur,f.sulfur_unit,f.ash,
               r.*,a.action,a.reason_code,c.generation_t,c.emission_t
        FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
        JOIN raw_records rr ON rr.raw_id=d.raw_id LEFT JOIN fuels f ON f.fuel_id=i.fuel_id
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        LEFT JOIN admission_results a ON a.item_id=i.item_id
        LEFT JOIN calculations c ON c.item_id=i.item_id
        WHERE d.target=? ORDER BY i.source_id,i.item_id
    """
    joined = list(connection.execute(query, (target,)))
    factor_file = "power_heat_emission_factors.csv" if target == "POWER" else "industrial_boiler_emission_factors.csv"
    factor_parameters = {}
    for parameter in read_csv(package / "parameters" / factor_file):
        key = factor_key(target, parameter["department"], parameter["fuel"], parameter["combustion_technology"], parameter["pollutant"])
        factor_parameters[key] = parameter
    factor_parameters.update({
        "NH3|SCR": {"parameter_id": "E1-SCR-NH3", "mode": "constant_sum", "value": "0.16", "unit": "g/kg煤"},
        "NH3|SNCR": {"parameter_id": "E1-SNCR-NH3", "mode": "constant_sum", "value": "0.17", "unit": "g/kg煤"},
        "NH3|SCR+SNCR": {"parameter_id": "E1-SCR-NH3+E1-SNCR-NH3", "mode": "constant_sum", "value": "0.33", "unit": "g/kg煤"},
        "NH3|ZERO": {"parameter_id": "EMP-NH3-ZERO", "mode": "constant_sum", "value": "0", "unit": "g/kg煤"},
    })
    coal_parameters = {}
    for parameter in read_csv(package / "parameters" / "coal_parameters.csv"):
        row_target = "POWER" if parameter["sector"] == "电力热力源" else "INDUSTRIAL"
        if row_target == target:
            coal_parameters[coal_key(target, parameter["department"], parameter["combustion_technology"])] = parameter
    output: list[list[Any]] = []
    for row in joined:
        payload = json.loads(row["payload_json"])
        is_nh3 = row["item_type"] == "ammonia_slip"
        f_key = nh3_key(row) if is_nh3 else factor_key(
            target, row["department"], clean(row["normalized_fuel"]), clean(row["normalized_technology"]), row["pollutant"],
        )
        c_key = ""
        if clean(row["normalized_fuel"]) in {"煤炭", "煤矸石"} and clean(row["normalized_technology"]):
            c_key = coal_key(target, row["department"], row["normalized_technology"])
        factor_parameter = factor_parameters.get(f_key, {})
        coal_parameter = coal_parameters.get(c_key, {})
        output.append([
            row["item_id"], row["source_id"], row["item_type"], target, row["department"], payload.get("company"), payload.get("city"),
            payload.get("industry_code"), payload.get("equipment"), payload.get("combustion"), payload.get("capacity_mw"), payload.get("low_nox"),
            row["fuel_id"], row["slot"], row["raw_fuel"], row["normalized_fuel"], row["normalized_technology"], row["amount"], row["raw_unit"],
            row["sulfur"], row["sulfur_unit"], row["ash"], row["pollutant"], row["dependency_id"], f_key,
            row["mode"] or factor_parameter.get("mode"), row["parameter_id"] or factor_parameter.get("parameter_id"),
            numeric(factor_parameter.get("value")), row["factor_unit"] or factor_parameter.get("unit"), c_key,
            numeric(coal_parameter.get("sulfur_to_bottom_ash")), numeric(coal_parameter.get("ash_to_bottom_ash")),
            numeric(coal_parameter.get("pm25_fraction")), numeric(coal_parameter.get("pm10_fraction")),
            numeric(coal_parameter.get("bc_fraction_of_pm25")), numeric(coal_parameter.get("oc_fraction_of_pm25")),
            None, row["factor_value"], None, None, None,
            row["activity_value"], None, row["control_technology"], join_list(row["control_parameter_ids"]), row["control_label"],
            row["operation_rate"], row["control_efficiency"], row["control_fine_efficiency"], row["control_coarse_efficiency"],
            row["control_candidates_json"], join_list(row["flags_json"]), row["action"], row["reason_code"], None, None,
            row["generation_t"], row["emission_t"], None, None, row["source_section"], row["source_page"], row["source_path"],
            row["source_sheet"], row["source_row"],
        ])
    return headers, output, joined


def parameter_payload(target: str, package: Path) -> dict[str, Any]:
    factor_file = "power_heat_emission_factors.csv" if target == "POWER" else "industrial_boiler_emission_factors.csv"
    factor_rows = read_csv(package / "parameters" / factor_file)
    factors = [[
        factor_key(target, row["department"], row["fuel"], row["combustion_technology"], row["pollutant"]),
        row["parameter_id"], row["mode"], numeric(row["value"]), row["unit"], row["sector"], row["source_section"], row["physical_pdf_page"],
    ] for row in factor_rows]
    factors.extend([
        ["NH3|SCR", "E1-SCR-NH3", "constant_sum", 0.16, "g/kg煤", "固定燃烧源脱硝", "附录E 表E.1", "50"],
        ["NH3|SNCR", "E1-SNCR-NH3", "constant_sum", 0.17, "g/kg煤", "固定燃烧源脱硝", "附录E 表E.1", "50"],
        ["NH3|SCR+SNCR", "E1-SCR-NH3+E1-SNCR-NH3", "constant_sum", 0.33, "g/kg煤", "固定燃烧源脱硝", "附录E 表E.1", "50"],
        ["NH3|ZERO", "EMP-NH3-ZERO", "constant_sum", 0.0, "g/kg煤", "经验缺失处置", "实施协议v4", ""],
    ])
    coal = []
    for row in read_csv(package / "parameters" / "coal_parameters.csv"):
        row_target = "POWER" if row["sector"] == "电力热力源" else "INDUSTRIAL"
        if row_target != target:
            continue
        coal.append([
            coal_key(target, row["department"], row["combustion_technology"]), row["parameter_id"],
            numeric(row["sulfur_to_bottom_ash"]), numeric(row["ash_to_bottom_ash"]), numeric(row["pm25_fraction"]),
            numeric(row["pm10_fraction"]), numeric(row["bc_fraction_of_pm25"]), numeric(row["oc_fraction_of_pm25"]),
            row["source_section"], row["physical_pdf_page"],
        ])
    control_eff = read_csv(package / "parameters" / "control_efficiencies.csv")
    return {
        "factor_headers": ["检索键", "参数ID", "模式", "参数值", "参数单位", "适用源类", "标准位置", "页码"],
        "factors": factors,
        "coal_headers": ["检索键", "参数ID", "硫入底灰比", "灰入底灰比", "PM2.5比例", "PM10比例", "BC占PM2.5", "OC占PM2.5", "标准位置", "页码"],
        "coal_parameters": coal,
        "control_efficiency_headers": list(control_eff[0].keys()) if control_eff else [],
        "control_efficiencies": [[row[key] for key in control_eff[0].keys()] for row in control_eff] if control_eff else [],
    }


def mapping_payload(package: Path) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for stem, prefix in (("source_fuel_aliases", "fuel"), ("source_combustion_aliases", "combustion"), ("source_control_aliases", "control")):
        rows = read_csv(package / "mappings" / f"{stem}.csv")
        output[f"{prefix}_mapping_headers"] = list(rows[0].keys()) if rows else []
        output[f"{prefix}_mappings"] = [[row[key] for key in rows[0].keys()] for row in rows] if rows else []
    return output


def rules_payload(package: Path) -> tuple[list[str], list[list[Any]]]:
    headers = ["规则ID", "类型", "来源类型", "标准位置", "适用范围", "执行动作", "缺失处置"]
    rows = []
    for rule in read_json(package / "rule_index.json"):
        rows.append([
            rule.get("rule_id"), rule.get("rule_type"), rule.get("source_type"), rule.get("source_section"),
            json.dumps(rule.get("scope"), ensure_ascii=False, sort_keys=True),
            json.dumps(rule.get("action"), ensure_ascii=False, sort_keys=True), rule.get("on_missing"),
        ])
    return headers, rows


def calculation_rows(target: str, connection: sqlite3.Connection, package: Path) -> tuple[list[str], list[list[Any]]]:
    combustion_pollutants = POLLUTANTS[:-1]
    base_headers = [
        "燃料行ID", "源ID", "目标源类", "核算部门", "企业", "地市", "行业代码", "设备类型", "原始燃烧方式", "装机容量(MW)",
        "燃料槽位", "原始燃料", "标准燃料", "标准燃烧技术", "原始消耗量", "原始单位", "含硫量", "含硫量单位", "灰分(%)",
        "燃煤参数键", "硫入底灰比", "灰入底灰比", "PM2.5比例", "PM10比例", "BC占PM2.5", "OC占PM2.5",
        "标准活动水平(公式)", "标准单位(公式)",
    ]
    static_suffixes = (
        "因子键", "因子模式", "原始参数值", "参数单位", "因子快照", "一般去除效率(%)", "PM2.5去除效率(%)",
        "PM2.5-10去除效率(%)", "准入动作", "产生量快照(t)", "排放量快照(t)",
    )
    headers = base_headers + [f"{pollutant}_{suffix}" for pollutant in combustion_pollutants for suffix in static_suffixes]
    headers += [f"{pollutant}_有效因子(公式)" for pollutant in combustion_pollutants]
    headers += [f"{pollutant}_产生量(公式,t)" for pollutant in combustion_pollutants]
    headers += [f"{pollutant}_排放量(公式,t)" for pollutant in combustion_pollutants]

    factor_file = "power_heat_emission_factors.csv" if target == "POWER" else "industrial_boiler_emission_factors.csv"
    factor_parameters = {}
    for parameter in read_csv(package / "parameters" / factor_file):
        key = factor_key(target, parameter["department"], parameter["fuel"], parameter["combustion_technology"], parameter["pollutant"])
        factor_parameters[key] = parameter
    coal_parameters = {}
    for parameter in read_csv(package / "parameters" / "coal_parameters.csv"):
        row_target = "POWER" if parameter["sector"] == "电力热力源" else "INDUSTRIAL"
        if row_target == target:
            coal_parameters[coal_key(target, parameter["department"], parameter["combustion_technology"])] = parameter

    query = """
        SELECT i.item_id,i.source_id,i.fuel_id,i.pollutant,d.target,d.department,d.payload_json,
               f.slot,f.raw_fuel,f.amount,f.raw_unit,f.sulfur,f.sulfur_unit,f.ash,
               r.normalized_fuel,r.normalized_technology,r.factor_value,r.control_efficiency,
               r.control_fine_efficiency,r.control_coarse_efficiency,a.action,c.generation_t,c.emission_t
        FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
        JOIN fuels f ON f.fuel_id=i.fuel_id JOIN rule_results r ON r.item_id=i.item_id
        JOIN admission_results a ON a.item_id=i.item_id JOIN calculations c ON c.item_id=i.item_id
        WHERE d.target=? AND i.item_type='combustion' ORDER BY i.source_id,i.fuel_id,i.pollutant
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in connection.execute(query, (target,)):
        entry = grouped.setdefault(row["fuel_id"], {"first": row, "pollutants": {}})
        entry["pollutants"][row["pollutant"]] = row
    output: list[list[Any]] = []
    for entry in grouped.values():
        first = entry["first"]
        payload = json.loads(first["payload_json"])
        c_key = ""
        if clean(first["normalized_fuel"]) in {"煤炭", "煤矸石"} and clean(first["normalized_technology"]):
            c_key = coal_key(target, first["department"], first["normalized_technology"])
        coal = coal_parameters.get(c_key, {})
        values: list[Any] = [
            first["fuel_id"], first["source_id"], target, first["department"], payload.get("company"), payload.get("city"),
            payload.get("industry_code"), payload.get("equipment"), payload.get("combustion"), payload.get("capacity_mw"),
            first["slot"], first["raw_fuel"], first["normalized_fuel"], first["normalized_technology"], first["amount"], first["raw_unit"],
            first["sulfur"], first["sulfur_unit"], first["ash"], c_key,
            numeric(coal.get("sulfur_to_bottom_ash")), numeric(coal.get("ash_to_bottom_ash")), numeric(coal.get("pm25_fraction")),
            numeric(coal.get("pm10_fraction")), numeric(coal.get("bc_fraction_of_pm25")), numeric(coal.get("oc_fraction_of_pm25")),
            None, None,
        ]
        for pollutant in combustion_pollutants:
            item = entry["pollutants"].get(pollutant)
            f_key = factor_key(target, first["department"], clean(first["normalized_fuel"]), clean(first["normalized_technology"]), pollutant)
            parameter = factor_parameters.get(f_key, {})
            values.extend([
                f_key, parameter.get("mode"), numeric(parameter.get("value")), parameter.get("unit"), item["factor_value"] if item else None,
                item["control_efficiency"] if item else None, item["control_fine_efficiency"] if item else None,
                item["control_coarse_efficiency"] if item else None, item["action"] if item else "withhold",
                item["generation_t"] if item else None, item["emission_t"] if item else None,
            ])
        values.extend([None] * (len(combustion_pollutants) * 3))
        output.append(values)
    return headers, output


def nh3_rows(target: str, connection: sqlite3.Connection) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "源ID", "企业", "地市", "原始脱硝工艺", "因子键", "参数ID", "因子快照(g/kg煤)", "准入动作", "准入原因",
        "治理处置标签", "煤活动水平快照(kg)", "产生量快照(t)", "排放量快照(t)",
        "煤活动水平(公式,kg)", "有效因子(公式,g/kg煤)", "产生量(公式,t)", "排放量(公式,t)",
    ]
    query = """
        SELECT i.source_id,d.payload_json,r.control_parameter_ids,r.factor_value,r.activity_value,r.control_label,
               a.action,a.reason_code,c.generation_t,c.emission_t
        FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
        JOIN rule_results r ON r.item_id=i.item_id JOIN admission_results a ON a.item_id=i.item_id
        JOIN calculations c ON c.item_id=i.item_id
        WHERE d.target=? AND i.pollutant='NH3' ORDER BY i.source_id
    """
    output = []
    for row in connection.execute(query, (target,)):
        payload = json.loads(row["payload_json"])
        output.append([
            row["source_id"], payload.get("company"), payload.get("city"), payload.get("denox_raw"), nh3_key(row),
            join_list(row["control_parameter_ids"]), row["factor_value"], row["action"], row["reason_code"], row["control_label"],
            row["activity_value"], row["generation_t"], row["emission_t"], None, None, None, None,
        ])
    return headers, output


def result_rows(
    target: str, connection: sqlite3.Connection, sources: dict[str, dict[str, Any]], calculations: list[list[Any]],
) -> tuple[list[str], list[list[Any]]]:
    headers = ["源ID", "企业", "地市", "区县", "行业代码", "设备类型", "排放口", "经度", "纬度"]
    for pollutant in POLLUTANTS:
        headers += [f"{pollutant}_产生量汇总(t)", f"{pollutant}_排放量汇总(t)", f"{pollutant}_治理处置", f"{pollutant}_结果状态"]
    headers += ["基102_SO2产生量(t)", "基102_SO2排放量(t)", "基102_NOx产生量(t)", "基102_NOx排放量(t)",
                "基102_PM产生量(t)", "基102_PM排放量(t)", "基102_VOCs产生量(t)", "基102_VOCs排放量(t)"]
    headers += ["燃料计算起始行", "燃料计算结束行"]
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    actions: dict[tuple[str, str], set[str]] = defaultdict(set)
    totals: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"generation": 0.0, "emission": 0.0, "withheld": False})
    bounds: dict[str, list[int]] = {}
    for row in connection.execute(
        "SELECT i.source_id,i.pollutant,r.control_label FROM calculation_items i "
        "JOIN device_sources d ON d.source_id=i.source_id JOIN rule_results r ON r.item_id=i.item_id WHERE d.target=?",
        (target,),
    ):
        if clean(row["control_label"]):
            labels[(row["source_id"], row["pollutant"])].add(clean(row["control_label"]))
    for row in connection.execute(
        "SELECT i.source_id,i.pollutant,a.action,c.generation_t,c.emission_t FROM calculation_items i "
        "JOIN device_sources d ON d.source_id=i.source_id JOIN admission_results a ON a.item_id=i.item_id "
        "JOIN calculations c ON c.item_id=i.item_id WHERE d.target=?",
        (target,),
    ):
        key = (row["source_id"], row["pollutant"])
        action = clean(row["action"])
        actions[key].add(action)
        if action == "withhold":
            totals[key]["withheld"] = True
        if row["generation_t"] is not None:
            totals[key]["generation"] += float(row["generation_t"])
        if row["emission_t"] is not None:
            totals[key]["emission"] += float(row["emission_t"])
    for excel_row, calculation in enumerate(calculations, start=2):
        source_id = calculation[1]
        if source_id not in bounds:
            bounds[source_id] = [excel_row, excel_row]
        else:
            bounds[source_id][1] = excel_row
    output = []
    for source_id, payload in sources.items():
        reported = payload.get("reported") or {}
        calculation_groups = []
        for pollutant in POLLUTANTS:
            static_label = "；".join(sorted(labels.get((source_id, pollutant), set())))
            static_status = "信息不足" if "withhold" in actions.get((source_id, pollutant), set()) else "已计算"
            total = totals[(source_id, pollutant)]
            generation = None if total["withheld"] else total["generation"]
            emission = None if total["withheld"] else total["emission"]
            calculation_groups.extend([generation, emission, static_label, static_status])
        output.append([
            source_id, payload.get("company"), payload.get("city"), payload.get("district"), payload.get("industry_code"),
            payload.get("equipment"), payload.get("outlet_id"), payload.get("longitude"), payload.get("latitude"),
            *calculation_groups,
            numeric(reported.get("SO2_generation_t")), numeric(reported.get("SO2_emission_t")),
            numeric(reported.get("NOX_generation_t")), numeric(reported.get("NOX_emission_t")),
            numeric(reported.get("PM_generation_t")), numeric(reported.get("PM_emission_t")),
            (numeric(reported.get("VOCS_generation_kg")) or 0.0) / 1000.0 if numeric(reported.get("VOCS_generation_kg")) is not None else None,
            (numeric(reported.get("VOCS_emission_kg")) or 0.0) / 1000.0 if numeric(reported.get("VOCS_emission_kg")) is not None else None,
            *(bounds.get(source_id) or [None, None]),
        ])
    return headers, output


def exception_rows(target: str, connection: sqlite3.Connection, sources: dict[str, dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    headers = ["源ID", "企业", "地市", "目标源类", "准入原因", "受影响核算项数", "受影响污染物", "规则标志", "处置", "说明"]
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    query = """
        SELECT i.source_id,i.pollutant,a.action,a.reason_code,r.flags_json
        FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
        JOIN admission_results a ON a.item_id=i.item_id LEFT JOIN rule_results r ON r.item_id=i.item_id
        WHERE d.target=? AND (
            a.action='withhold'
            OR (a.action='not_applicable' AND a.reason_code='FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE')
            OR r.flags_json LIKE '%COORDINATE_REVIEW_REQUIRED%'
        )
        ORDER BY i.source_id,a.action,a.reason_code,i.pollutant
    """
    for row in connection.execute(query, (target,)):
        flags = set(decode_list(row["flags_json"]))
        reasons: list[str] = []
        if row["action"] == "withhold":
            reasons.append(clean(row["reason_code"]))
            # Fuel identity and activity-unit failures are two independent
            # user decisions in the frozen reference and must remain visible
            # as separate rows instead of being hidden by a single primary
            # admission code.
            if "FUEL_UNMAPPED" in flags and "ACTIVITY_OR_UNIT_MISSING" in flags:
                reasons.extend(["FUEL_UNMAPPED", "ACTIVITY_OR_UNIT_MISSING"])
        elif row["action"] == "not_applicable" and row["reason_code"] == "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE":
            reasons.append("FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE")
        if "COORDINATE_REVIEW_REQUIRED" in flags:
            reasons.append("COORDINATE_REVIEW_REQUIRED")
        for reason in sorted(set(reason for reason in reasons if reason)):
            entry = grouped.setdefault(
                (row["source_id"], reason),
                {"count": 0, "pollutants": set(), "flags": set(), "action": row["action"]},
            )
            entry["count"] += 1
            entry["pollutants"].add(row["pollutant"])
            entry["flags"].add(reason)
    output = []
    for (source_id, reason), entry in sorted(grouped.items()):
        payload = sources[source_id]
        action = entry["action"]
        if reason == "COORDINATE_REVIEW_REQUIRED":
            disposition = "保留核算结果并列入空间信息复核"
        else:
            disposition = "不进入本次化石燃料核算" if action == "not_applicable" else "结果留空并进入异常清单"
        explanation = {
            "NH3_FACTOR_SCOPE_MISMATCH": "SCR/SNCR氨逃逸因子以煤耗为活动水平；非煤燃料源不按该因子计算",
            "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE": "生物燃料或工业废料不属于本次化石燃料固定燃烧核算范围",
            "OTHER_FUEL_TCE_UNRESOLVED": "吨标准煤汇总量不能唯一还原燃料种类及实物活动水平",
            "COMBUSTION_TECHNOLOGY_AMBIGUOUS": "原始燃烧方式不能唯一映射至附录C燃烧技术",
            "T_CSES_COAL_BC_OC_PARAMETER_GAP": "T/CSES附录C未给出电力供应煤粉炉BC/OC所需参数",
            "T_CSES_COAL_PARAMETER_GAP": "焦炭因子要求物料衡算，但附录C没有“不分技术”的对应燃烧参数",
            "FACTOR_APPLICABILITY_UNRESOLVED": "石油焦或工业煤矸石与现有因子适用条件不兼容",
            "COORDINATE_REVIEW_REQUIRED": "排放口坐标超出广东范围，保留计算结果并列入空间信息复核",
            "FUEL_UNMAPPED": "原始燃料名称不能唯一映射至标准燃料",
            "ACTIVITY_OR_UNIT_MISSING": "燃料活动水平或计量单位不足以完成标准单位换算",
        }.get(reason, "基础核算信息或规则适用条件未闭合")
        output.append([
            source_id, payload.get("company"), payload.get("city"), target, reason, entry["count"],
            "、".join(sorted(entry["pollutants"])), " | ".join(sorted(clean(flag) for flag in entry["flags"] if clean(flag))),
            disposition, explanation,
        ])
    return headers, output


def ledger_rows(target: str, connection: sqlite3.Connection) -> tuple[list[str], list[list[Any]]]:
    headers = ["原始记录ID", "原始表标签", "来源文件", "来源工作表", "原Excel行", "企业", "行业代码", "设备类型", "最终去向", "处理动作", "判定依据"]
    own_tag = "IND" if target == "INDUSTRIAL" else "PWR"
    output = []
    query = """
        SELECT rr.*,d.target,d.payload_json AS device_payload,e.reason
        FROM raw_records rr LEFT JOIN device_sources d ON d.raw_id=rr.raw_id
        LEFT JOIN excluded_records e ON e.raw_id=rr.raw_id ORDER BY rr.raw_id
    """
    for row in connection.execute(query):
        payload = json.loads(row["payload_json"])
        destination = clean(row["target"]) or "EXCLUDE"
        include = destination == target or row["source_tag"] == own_tag
        if not include:
            continue
        if destination == target:
            action = "保留" if row["source_tag"] == own_tag else "从另一原表转入"
        elif destination == "EXCLUDE":
            action = "排除"
        else:
            action = "转出至另一清单"
        output.append([
            row["raw_id"], row["source_tag"], row["source_path"], row["source_sheet"], row["source_row"], payload.get("company"),
            payload.get("industry_code"), payload.get("equipment"), destination, action,
            row["reason"] or ("T/CSES 7.1行业范围" if destination == "POWER" else "T/CSES 8.3行业与锅炉范围"),
        ])
    return headers, output


def review_rows(target: str, connection: sqlite3.Connection) -> tuple[list[str], list[list[Any]]]:
    headers = ["步骤", "复核对象", "判定要求", "总数", "异常/待核数", "状态", "对应规则", "修正入口"]
    source_count = connection.execute("SELECT COUNT(*) FROM device_sources WHERE target=?", (target,)).fetchone()[0]
    fuel_count = connection.execute("SELECT COUNT(*) FROM fuels f JOIN device_sources d ON d.source_id=f.source_id WHERE d.target=?", (target,)).fetchone()[0]
    item_count = connection.execute("SELECT COUNT(*) FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id WHERE d.target=?", (target,)).fetchone()[0]
    withheld = connection.execute("SELECT COUNT(*) FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id JOIN admission_results a ON a.item_id=i.item_id WHERE d.target=? AND a.action='withhold'", (target,)).fetchone()[0]
    unmapped_fuels = connection.execute("SELECT COUNT(DISTINCT f.fuel_id) FROM fuels f JOIN device_sources d ON d.source_id=f.source_id LEFT JOIN calculation_items i ON i.fuel_id=f.fuel_id AND i.pollutant='SO2' LEFT JOIN rule_results r ON r.item_id=i.item_id WHERE d.target=? AND (r.normalized_fuel IS NULL OR r.normalized_fuel='')", (target,)).fetchone()[0]
    control_counts = Counter(row[0] or "" for row in connection.execute("SELECT r.control_label FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id JOIN rule_results r ON r.item_id=i.item_id WHERE d.target=?", (target,)))
    nh3_withheld = connection.execute("SELECT COUNT(*) FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id JOIN admission_results a ON a.item_id=i.item_id WHERE d.target=? AND i.pollutant='NH3' AND a.action='withhold'", (target,)).fetchone()[0]
    calculated = connection.execute("SELECT COUNT(*) FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id JOIN calculations c ON c.item_id=i.item_id WHERE d.target=? AND c.calculation_status='calculated'", (target,)).fetchone()[0]
    entries = [
        ("01", "源范围与重分流", "源记录均可回溯且落入唯一去向", source_count, 0, "通过", "r_scope_*", "06_转移排除"),
        ("02", "燃料与技术映射", "燃料和燃烧技术可唯一映射", fuel_count, unmapped_fuels, "通过" if unmapped_fuels == 0 else "待核", "r_map_*", "02_燃料明细"),
        ("03", "污染物排放核算项", "每个燃料按8种燃烧污染物展开；每个源另设NH3", item_count, 0, "通过", "r_structure_*", "03_燃料污染物计算/04_氨逃逸"),
        ("04", "因子与物料衡算", "因子唯一且所需硫分、灰分、容量完整", item_count, withheld - nh3_withheld, "通过" if withheld - nh3_withheld == 0 else "待核", "r_factor_*", "03_燃料污染物计算"),
        ("05", "经验治理处置", "缺失按0、无法映射按0、多工艺取最大均显式标注", item_count, control_counts.get("治理工艺无法映射按0", 0), "通过", "r_control_*", "03_燃料污染物计算"),
        ("06", "NH3氨逃逸", "仅SCR/SNCR并有煤活动水平时核算", source_count, nh3_withheld, "通过" if nh3_withheld == 0 else "待核", "r_nh3_*", "04_氨逃逸"),
        ("07", "计算与PM粒径关系", "产生量及治理后排放完成，PM2.5不大于PM10", item_count, withheld, "通过" if withheld == 0 else "待核", "r_formula_*", "03_燃料污染物计算"),
        ("08", "源级汇总", "存在任一基础信息阻断时对应源×污染物结果留空", source_count * len(POLLUTANTS), withheld, "通过", "r_admission_*", "05_源结果"),
        ("09", "已完成计算项", "计算状态为calculated", item_count, item_count - calculated, "说明", "r_formula_*", "03_燃料污染物计算/04_氨逃逸"),
        ("10", "Excel公式与版式", "导出后进行错误扫描并逐表渲染", 0, 0, "导出后复核", "QA", "预览目录/qa.json"),
    ]
    return headers, [list(row) for row in entries]


def build_payload(target: str, connection: sqlite3.Connection, package: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    source_headers, sources, source_cache = source_rows(target, connection)
    fuel_headers, fuels = fuel_rows(target, connection, source_cache)
    calculation_headers, calculations = calculation_rows(target, connection, package)
    nh3_headers, nh3 = nh3_rows(target, connection)
    result_headers, results = result_rows(target, connection, source_cache, calculations)
    exception_headers, exceptions = exception_rows(target, connection, source_cache)
    ledger_headers, ledger = ledger_rows(target, connection)
    review_headers, review = review_rows(target, connection)
    rule_headers, rules = rules_payload(package)
    lock = read_json(package / "rule_package_lock.json")
    payload = {
        "title": "T/CSES 144—2024 固定燃烧源经验缺失处置标准计算答案",
        "target": target,
        "target_label": "工业锅炉" if target == "INDUSTRIAL" else "火电、热力生产与供应",
        "rule_package_id": lock["package_id"],
        "rule_package_version": lock["version"],
        "rule_package_hash": lock["package_hash"],
        "pollutants": list(POLLUTANTS),
        "source_headers": source_headers, "sources": sources,
        "fuel_headers": fuel_headers, "fuels": fuels,
        "calculation_headers": calculation_headers, "calculations": calculations,
        "nh3_headers": nh3_headers, "nh3": nh3,
        "calculation_item_count": connection.execute(
            "SELECT COUNT(*) FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id WHERE d.target=?",
            (target,),
        ).fetchone()[0],
        "result_headers": result_headers, "results": results,
        "exception_headers": exception_headers, "exceptions": exceptions,
        "ledger_headers": ledger_headers, "ledger": ledger,
        "review_headers": review_headers, "review": review,
        "rule_headers": rule_headers, "rules": rules,
        "scenario": manifest.get("scenario"), "run_id": manifest.get("run_id"),
    }
    payload.update(parameter_payload(target, package))
    payload.update(mapping_payload(package))
    return payload
