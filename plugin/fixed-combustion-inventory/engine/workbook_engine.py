"""Deterministic stage engine for the fixed-combustion inventory experiment.

The engine owns all durable run state. Hermes can orchestrate stages, but it
cannot alter formulas, rule matches, admission policy, or arithmetic here.
Each invocation accepts one JSON request on stdin and returns one JSON object.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import xlsxwriter
from openpyxl import load_workbook
from workbook_payload import build_payload
from source_domain import classify_source, stable_source_id


ENGINE_VERSION = "3.0.0"
DB_NAME = "run.sqlite3"
MANIFEST_NAME = "run_manifest.json"
POLLUTANTS = ("SO2", "NOX", "CO", "VOCS", "PM10", "PM25", "BC", "OC", "NH3")
COMBUSTION_POLLUTANTS = POLLUTANTS[:-1]
VALID_UNITS = {"吨", "万立方米", "吨标准煤"}
HEADER_TRANSLATION = str.maketrans({"（": "(", "）": ")", "【": "[", "】": "]", "：": ":"})


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "nan"} else text


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = clean(value).replace(",", "")
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def hash_tree(path: Path, excluded: set[str] | None = None) -> str:
    excluded = excluded or set()
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = file_path.relative_to(path).as_posix()
        if relative in excluded or any(part.startswith(".") for part in file_path.relative_to(path).parts):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(file_path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def connect(run_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(run_path / DB_NAME)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS stage_commits (
          stage TEXT PRIMARY KEY, revision TEXT NOT NULL, status TEXT NOT NULL,
          upstream_revision TEXT, summary_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_records (
          raw_id TEXT PRIMARY KEY, source_tag TEXT NOT NULL, source_path TEXT NOT NULL,
          source_sheet TEXT NOT NULL, source_row INTEGER NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS device_sources (
          source_id TEXT PRIMARY KEY, raw_id TEXT NOT NULL, target TEXT NOT NULL,
          department TEXT NOT NULL, payload_json TEXT NOT NULL,
          FOREIGN KEY(raw_id) REFERENCES raw_records(raw_id)
        );
        CREATE TABLE IF NOT EXISTS excluded_records (
          raw_id TEXT PRIMARY KEY, reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_decisions (
          source_id TEXT PRIMARY KEY, raw_id TEXT NOT NULL,
          decided_target TEXT NOT NULL, requested_target TEXT NOT NULL,
          disposition TEXT NOT NULL, reason TEXT NOT NULL,
          FOREIGN KEY(raw_id) REFERENCES raw_records(raw_id)
        );
        CREATE TABLE IF NOT EXISTS fuels (
          fuel_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, slot TEXT NOT NULL,
          raw_fuel TEXT, amount REAL, raw_unit TEXT, sulfur REAL,
          sulfur_unit TEXT, ash REAL, FOREIGN KEY(source_id) REFERENCES device_sources(source_id)
        );
        CREATE TABLE IF NOT EXISTS calculation_items (
          item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, fuel_id TEXT,
          item_type TEXT NOT NULL, pollutant TEXT NOT NULL, dependency_id TEXT,
          FOREIGN KEY(source_id) REFERENCES device_sources(source_id)
        );
        CREATE TABLE IF NOT EXISTS rule_results (
          item_id TEXT PRIMARY KEY, normalized_fuel TEXT, normalized_technology TEXT,
          activity_value REAL, activity_unit TEXT, activity_formula TEXT,
          parameter_id TEXT, mode TEXT, factor_value REAL, factor_unit TEXT,
          factor_formula TEXT, source_section TEXT, source_page TEXT,
          control_technology TEXT, control_parameter_ids TEXT,
          control_efficiency REAL, control_fine_efficiency REAL,
          control_coarse_efficiency REAL, control_label TEXT,
          control_candidates_json TEXT, operation_rate REAL,
          flags_json TEXT NOT NULL,
          FOREIGN KEY(item_id) REFERENCES calculation_items(item_id)
        );
        CREATE TABLE IF NOT EXISTS admission_results (
          item_id TEXT PRIMARY KEY, action TEXT NOT NULL, reason_code TEXT NOT NULL,
          flags_json TEXT NOT NULL, FOREIGN KEY(item_id) REFERENCES calculation_items(item_id)
        );
        CREATE TABLE IF NOT EXISTS calculations (
          item_id TEXT PRIMARY KEY, generation_t REAL, emission_t REAL,
          generation_formula TEXT, emission_formula TEXT, calculation_status TEXT NOT NULL,
          FOREIGN KEY(item_id) REFERENCES calculation_items(item_id)
        );
        CREATE TABLE IF NOT EXISTS quality_findings (
          finding_id TEXT PRIMARY KEY, source_id TEXT, item_id TEXT,
          severity TEXT NOT NULL, code TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_rule_choices (
          item_id TEXT PRIMARY KEY, decision_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_admission_decisions (
          item_id TEXT PRIMARY KEY, decision_json TEXT NOT NULL
        );
        """
    )
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(rule_results)")}
    for name, declaration in (
        ("control_label", "TEXT"),
        ("control_candidates_json", "TEXT"),
        ("operation_rate", "REAL"),
    ):
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE rule_results ADD COLUMN {name} {declaration}")
    connection.commit()
    return connection


def load_context(request: dict[str, Any]) -> tuple[Path, dict[str, Any], sqlite3.Connection]:
    run_path = Path(request["run_package_path"]).resolve()
    manifest_path = run_path / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"run manifest missing: {manifest_path}")
    manifest = read_json(manifest_path)
    for key in ("run_id", "scenario", "method_bundle_hash"):
        if clean(request.get(key)) != clean(manifest.get(key)):
            raise ValueError(f"request/manifest mismatch: {key}")
    return run_path, manifest, connect(run_path)


def effective_candidate_scope(request: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Resolve an optional target-neutral Experiment-C candidate subset."""
    requested = request.get("candidate_ids")
    if requested:
        return [clean(value) for value in requested if clean(value)]
    declared = manifest.get("candidate_ids") or []
    return [clean(value) for value in declared if clean(value)]


def stage_revision(stage: str, request: dict[str, Any], manifest: dict[str, Any], upstream: str | None) -> str:
    return canonical_hash(
        {
            "engine_version": ENGINE_VERSION,
            "stage": stage,
            "run_id": request["run_id"],
            "scenario": request["scenario"],
            "method_bundle_hash": request["method_bundle_hash"],
            "candidate_ids": sorted(effective_candidate_scope(request, manifest)),
            "manifest_hash": canonical_hash(manifest),
            "upstream": upstream,
        }
    )


def require_stage(connection: sqlite3.Connection, stage: str) -> str:
    row = connection.execute("SELECT revision,status FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if not row or row["status"] != "complete":
        raise ValueError(f"required upstream stage incomplete: {stage}")
    return str(row["revision"])


def commit_stage(
    connection: sqlite3.Connection,
    stage: str,
    revision: str,
    upstream: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    existing = connection.execute("SELECT revision,summary_json FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if existing:
        if existing["revision"] != revision:
            raise ValueError(f"stage already committed with another revision: {stage}")
        return json.loads(existing["summary_json"])
    result = {
        "success": True,
        "stage": stage,
        "status": "complete",
        "revision": revision,
        "upstream_revision": upstream,
        **summary,
    }
    connection.execute(
        "INSERT INTO stage_commits(stage,revision,status,upstream_revision,summary_json) VALUES(?,?,?,?,?)",
        (stage, revision, "complete", upstream, json.dumps(result, ensure_ascii=False, sort_keys=True)),
    )
    connection.commit()
    return result


def input_specs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    specs = manifest.get("inputs")
    if not isinstance(specs, list) or not specs:
        raise ValueError("run manifest inputs must be a non-empty list")
    return specs


def normalize_header_text(value: Any) -> str:
    return re.sub(r"\s+", "", clean(value).translate(HEADER_TRANSLATION))


def header_map(row: Iterable[Any], tag: str, aliases: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    tag_aliases = {
        normalize_header_text(raw): normalize_header_text(target)
        for raw, target in (aliases.get(tag) or {}).items()
    }
    for index, value in enumerate(row):
        name = normalize_header_text(value)
        name = tag_aliases.get(name, name)
        if name and name not in result:
            result[name] = index
    return result


def header_positions(row: Iterable[Any], tag: str, aliases: dict[str, Any]) -> dict[str, list[int]]:
    """Keep every matching column so duplicate Base-102 headers stay distinct."""

    result: dict[str, list[int]] = defaultdict(list)
    tag_aliases = {
        normalize_header_text(raw): normalize_header_text(target)
        for raw, target in (aliases.get(tag) or {}).items()
    }
    for index, raw_value in enumerate(row):
        name = normalize_header_text(raw_value)
        name = tag_aliases.get(name, name)
        if name:
            result[name].append(index)
    return dict(result)


def value(row: tuple[Any, ...], headers: dict[str, int], *names: str) -> Any:
    for name in names:
        index = headers.get(normalize_header_text(name))
        if index is not None and index < len(row):
            return row[index]
    return None


def value_at(
    row: tuple[Any, ...], headers: dict[str, list[int]], name: str, occurrence: int = 0
) -> Any:
    positions = headers.get(normalize_header_text(name), [])
    if occurrence < 0 or occurrence >= len(positions):
        return None
    index = positions[occurrence]
    return row[index] if index < len(row) else None


def pseudonymous_entity_id(payload: dict[str, Any]) -> str:
    return next(
        (
            clean(payload.get(key))
            for key in ("credit_code", "org_code", "company")
            if clean(payload.get(key))
        ),
        "",
    )


def decimal_coordinate(deg: Any, minute: Any, second: Any, direct: Any) -> float | None:
    direct_value = number(direct)
    if direct_value is not None:
        return direct_value
    d, m, s = number(deg), number(minute), number(second)
    if d is None:
        return None
    return d + (m or 0.0) / 60.0 + (s or 0.0) / 3600.0


def normalize_source_payload(tag: str, row: tuple[Any, ...], headers: dict[str, int]) -> dict[str, Any]:
    equipment = value(row, headers, "工业锅炉类型", "电站锅炉/燃气轮机类型")
    combustion = value(row, headers, "工业锅炉燃烧方式", "电站锅炉燃烧方式")
    capacity_10k_kw = number(value(row, headers, "对应机组装机容量（万千瓦）（1万千瓦=10MV）", "对应机组装机容量（万千瓦）"))
    payload = {
        "year": value(row, headers, "统计年份"),
        "org_code": value(row, headers, "组织机构代码"),
        "credit_code": value(row, headers, "统一社会信用代码"),
        "company": value(row, headers, "填报单位详细名称"),
        "admin_code": value(row, headers, "行政区代码"),
        "city": value(row, headers, "地市", "城市"),
        "district": value(row, headers, "行政区名称"),
        "industry_code": clean(value(row, headers, "行业类别代码")).split(".")[0].zfill(4),
        "industry_name": value(row, headers, "行业类别名称"),
        "equipment": equipment,
        "combustion": combustion,
        "capacity_mw": capacity_10k_kw * 10.0 if capacity_10k_kw is not None else None,
        "rated_output": value(row, headers, "工业锅炉额定出力（蒸吨/小时）", "电站锅炉/燃气轮机额定出力（蒸吨/小时）"),
        "low_nox": value(row, headers, "是否采用低氮燃烧技术"),
        "outlet_id": value(row, headers, "排放口编号"),
        "longitude": decimal_coordinate(
            value(row, headers, "排放口地理坐标经度_度"),
            value(row, headers, "排放口地理坐标经度_分"),
            value(row, headers, "排放口地理坐标经度_秒"),
            value(row, headers, "排放口地理坐标经度（度）"),
        ),
        "latitude": decimal_coordinate(
            value(row, headers, "排放口地理坐标纬度_度"),
            value(row, headers, "排放口地理坐标纬度_分"),
            value(row, headers, "排放口地理坐标纬度_秒"),
            value(row, headers, "排放口地理坐标纬度（度）"),
        ),
        "outlet_height": value(row, headers, "排放口高度（米）"),
        "desulf_raw": value(row, headers, "脱硫处理工艺名称"),
        "denox_raw": value(row, headers, "脱硝处理工艺名称"),
        "dust_raw": value(row, headers, "除尘处理工艺名称"),
        "reported": {
            "SO2_generation_t": value(row, headers, "二氧化硫产生量（吨）"),
            "SO2_emission_t": value(row, headers, "二氧化硫排放量（吨）"),
            "NOX_generation_t": value(row, headers, "氮氧化物产生量（吨）"),
            "NOX_emission_t": value(row, headers, "氮氧化物排放量（吨）"),
            "PM_generation_t": value(row, headers, "颗粒物产生量（吨）"),
            "PM_emission_t": value(row, headers, "颗粒物排放量（吨）"),
            "VOCS_generation_kg": value(row, headers, "挥发性有机物产生量（千克）"),
            "VOCS_emission_kg": value(row, headers, "挥发性有机物排放量（千克）"),
        },
        "fuels": [
            {
                "slot": "fuel_1",
                "fuel": value(row, headers, "燃料一类型"),
                "amount": value(row, headers, "燃料一消耗量"),
                "unit": value(row, headers, "燃料一消耗量单位"),
                "sulfur": value(row, headers, "燃料一平均收到基含硫量"),
                "sulfur_unit": value(row, headers, "燃料一平均收到基含硫量单位"),
                "ash": value(row, headers, "燃料一平均收到基灰分（%）"),
            },
            {
                "slot": "fuel_2",
                "fuel": value(row, headers, "燃料二类型"),
                "amount": value(row, headers, "燃料二消耗量"),
                "unit": value(row, headers, "燃料二消耗量单位"),
                "sulfur": value(row, headers, "燃料二平均收到基含硫量"),
                "sulfur_unit": value(row, headers, "燃料二平均收到基含硫量单位"),
                "ash": value(row, headers, "燃料二平均收到基灰分（%）"),
            },
            {
                "slot": "other_tce",
                "fuel": "其他燃料（吨标准煤）",
                "amount": value(row, headers, "其他燃料消耗总量（吨标准煤）"),
                "unit": "吨标准煤",
                "sulfur": None,
                "sulfur_unit": None,
                "ash": None,
            },
        ],
        "source_tag": tag,
    }
    return payload


def normalize_enterprise_payload(row: tuple[Any, ...], headers: dict[str, list[int]]) -> dict[str, Any]:
    return {
        "year": value_at(row, headers, "统计年份"),
        "org_code": value_at(row, headers, "组织机构代码"),
        "credit_code": value_at(row, headers, "统一社会信用代码"),
        "company": value_at(row, headers, "填报单位详细名称"),
        "normal_hours": value_at(row, headers, "年正常生产时间（小时）"),
        "city": value_at(row, headers, "详细地址地区（市、州、盟）"),
        "center_longitude": decimal_coordinate(
            value_at(row, headers, "中心经度（度）"),
            value_at(row, headers, "中心经度（分）"),
            value_at(row, headers, "中心经度（秒）"),
            None,
        ),
        "center_latitude": decimal_coordinate(
            value_at(row, headers, "中心纬度（度）"),
            value_at(row, headers, "中心纬度（分）"),
            value_at(row, headers, "中心纬度（秒）"),
            None,
        ),
    }


def normalize_control_payload(row: tuple[Any, ...], headers: dict[str, list[int]]) -> dict[str, Any]:
    return {
        "year": value_at(row, headers, "统计年份"),
        "org_code": value_at(row, headers, "组织机构代码"),
        "credit_code": value_at(row, headers, "统一社会信用代码"),
        "company": value_at(row, headers, "填报单位详细名称"),
        "facility_id": value_at(row, headers, "编号"),
        "facility_name": value_at(row, headers, "废气治理设施名称"),
        "outlet_category": value_at(row, headers, "对应的排放口代码"),
        "outlet_name": value_at(row, headers, "对应的排放口名称"),
        "process": value_at(row, headers, "处理工艺名称"),
        "reported_efficiency": value_at(row, headers, "去除效率（%）"),
    }


def normalize_device_payload(
    row: tuple[Any, ...],
    headers: dict[str, list[int]],
    enterprise: dict[str, Any] | None,
    controls: list[dict[str, Any]],
) -> dict[str, Any]:
    capacity_10k_kw = number(value_at(row, headers, "对应机组装机容量（万千瓦）"))
    payload = {
        "year": value_at(row, headers, "统计年份"),
        "org_code": value_at(row, headers, "组织机构代码"),
        "credit_code": value_at(row, headers, "统一社会信用代码"),
        "company": value_at(row, headers, "填报单位详细名称"),
        "admin_code": value_at(row, headers, "行政区代码"),
        "district": value_at(row, headers, "行政区名称"),
        "industry_code": clean(value_at(row, headers, "行业类别代码")).split(".")[0].zfill(4),
        "industry_name": value_at(row, headers, "行业类别名称"),
        "source_sequence": value_at(row, headers, "序号"),
        "power_equipment_id": value_at(row, headers, "电站锅炉/燃气轮机编号"),
        "power_equipment": value_at(row, headers, "电站锅炉/燃气轮机类型"),
        "power_combustion": value_at(row, headers, "电站锅炉燃烧方式"),
        "power_rated_output": value_at(row, headers, "电站锅炉/燃气轮机额定出力（蒸吨/小时）"),
        "power_run_hours": value_at(row, headers, "电站锅炉/燃气轮机运行时间（小时）"),
        "power_low_nox": value_at(row, headers, "是否采用低氮燃烧技术", 0),
        "industrial_equipment_id": value_at(row, headers, "工业锅炉编号"),
        "industrial_equipment": value_at(row, headers, "工业锅炉类型"),
        "industrial_combustion": value_at(row, headers, "工业锅炉燃烧方式"),
        "industrial_rated_output": value_at(row, headers, "工业锅炉额定出力（蒸吨/小时）"),
        "industrial_run_hours": value_at(row, headers, "工业锅炉运行时间（小时）"),
        "industrial_low_nox": value_at(row, headers, "是否采用低氮燃烧技术", 1),
        "capacity_mw": capacity_10k_kw * 10.0 if capacity_10k_kw is not None else None,
        "outlet_id": value_at(row, headers, "排放口编号"),
        "longitude": decimal_coordinate(
            value_at(row, headers, "排放口地理坐标经度_度"),
            value_at(row, headers, "排放口地理坐标经度_分"),
            value_at(row, headers, "排放口地理坐标经度_秒"),
            None,
        ),
        "latitude": decimal_coordinate(
            value_at(row, headers, "排放口地理坐标纬度_度"),
            value_at(row, headers, "排放口地理坐标纬度_分"),
            value_at(row, headers, "排放口地理坐标纬度_秒"),
            None,
        ),
        "outlet_height": value_at(row, headers, "排放口高度（米）"),
        "reported": {},
        "fuels": [
            {
                "slot": "fuel_1",
                "fuel": value_at(row, headers, "燃料一类型"),
                "amount": value_at(row, headers, "燃料一消耗量"),
                "unit": value_at(row, headers, "燃料一消耗量单位"),
                "sulfur": value_at(row, headers, "燃料一平均收到基含硫量"),
                "sulfur_unit": value_at(row, headers, "燃料一平均收到基含硫量单位"),
                "ash": value_at(row, headers, "燃料一平均收到基灰分（%）"),
            },
            {
                "slot": "fuel_2",
                "fuel": value_at(row, headers, "燃料二类型"),
                "amount": value_at(row, headers, "燃料二消耗量"),
                "unit": value_at(row, headers, "燃料二消耗量单位"),
                "sulfur": value_at(row, headers, "燃料二平均收到基含硫量"),
                "sulfur_unit": value_at(row, headers, "燃料二平均收到基含硫量单位"),
                "ash": value_at(row, headers, "燃料二平均收到基灰分（%）"),
            },
            {
                "slot": "other_tce",
                "fuel": "其他燃料（吨标准煤）",
                "amount": value_at(row, headers, "其他燃料消耗总量（吨标准煤）"),
                "unit": "吨标准煤",
                "sulfur": None,
                "sulfur_unit": None,
                "ash": None,
            },
        ],
        "control_candidates_raw": controls,
        "source_tag": "B102_DEVICE",
    }
    if enterprise:
        payload["normal_hours"] = enterprise.get("normal_hours")
        payload["city"] = enterprise.get("city")
        payload["center_longitude"] = enterprise.get("center_longitude")
        payload["center_latitude"] = enterprise.get("center_latitude")
    return payload


def classify(payload: dict[str, Any]) -> tuple[str, str, str]:
    decision = classify_source(
        industry_code=payload.get("industry_code"),
        industrial_equipment=payload.get("industrial_equipment", payload.get("equipment")),
        power_equipment=payload.get("power_equipment"),
    )
    payload["equipment"] = decision.equipment
    return decision.target, decision.department, decision.reason


def source_relationship_flags(payload: dict[str, Any]) -> list[str]:
    """Return source-level relationship defects that invalidate point-source output.

    The emission source is still materialized so that the exception remains
    attributable to the original row, but every pollutant item derived from it
    is withheld by the admission stage.
    """
    flags: list[str] = []
    required_values = (
        payload.get("company"),
        payload.get("industry_code"),
        payload.get("equipment"),
        payload.get("outlet_id"),
        payload.get("longitude"),
        payload.get("latitude"),
    )
    if any(not clean(value) for value in required_values):
        flags.append("SOURCE_RELATION_MISSING")
    longitude = number(payload.get("longitude"))
    latitude = number(payload.get("latitude"))
    if (
        longitude is not None
        and latitude is not None
        and not (109.0 <= longitude <= 118.0 and 20.0 <= latitude <= 26.0)
    ):
        flags.append("COORDINATE_REVIEW_REQUIRED")
    fuels = payload.get("fuels") or []
    if len(fuels) >= 2:
        first = tuple(
            clean(fuels[0].get(key))
            for key in ("fuel", "amount", "unit", "sulfur", "ash")
        )
        second = tuple(
            clean(fuels[1].get(key))
            for key in ("fuel", "amount", "unit", "sulfur", "ash")
        )
        if first[0] and first == second:
            flags.append("POTENTIAL_DUPLICATE_FUEL_SLOT")
    return flags


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def rule_path(manifest: dict[str, Any]) -> Path:
    path = Path(str(manifest.get("rule_package_path", ""))).resolve()
    if not path.is_dir():
        raise ValueError(f"frozen rule package unavailable: {path}")
    lock_path = path / "rule_package_lock.json"
    if not lock_path.is_file():
        raise ValueError("rule package is not frozen: lock file missing")
    lock = read_json(lock_path)
    if lock.get("status") != "frozen" or lock.get("professional_review_status") != "passed":
        raise ValueError("rule package has not passed professional review")
    actual_hash = hash_tree(path, {"rule_package_lock.json"})
    if clean(lock.get("package_hash")) != actual_hash:
        raise ValueError("frozen rule package hash mismatch")
    if clean(manifest.get("rule_package_hash")) != actual_hash:
        raise ValueError("run manifest does not pin the frozen rule package hash")
    return path


GAS_FUELS = {"天然气", "液化天然气", "液化石油气", "炼厂干气", "焦炉煤气", "高炉煤气", "转炉煤气", "其它煤气", "其它气体燃料"}
LIQUID_FUELS = {"柴油", "燃料油", "原油", "汽油", "煤油", "其它液体燃料", "其它石油制品", "石油沥青"}
COAL_FUELS = {"煤炭", "煤矸石"}
COAL_MASS_BALANCE_FUELS = COAL_FUELS | {"焦炭"}


def mapping_index(package: Path, filename: str, key: str) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in load_csv(package / "mappings" / filename):
        result[clean(row.get(key))].append(row)
    return dict(result)


def normalize_fuel(raw: str, aliases: dict[str, list[dict[str, str]]]) -> tuple[str, str]:
    raw = clean(raw)
    rows = aliases.get(raw, [])
    if len(rows) != 1:
        return "", "unmapped"
    return clean(rows[0].get("standard_fuel")), clean(rows[0].get("mapping_status"))


def normalize_technology(
    fuel: str,
    combustion: str,
    aliases: dict[str, list[dict[str, str]]],
    fuel_categories: dict[str, str] | None = None,
) -> tuple[str, str]:
    category = (fuel_categories or {}).get(fuel, "")
    if category == "气体燃料" or fuel in GAS_FUELS:
        return "燃气锅炉", "fuel_determined"
    if category == "液体燃料" or fuel in LIQUID_FUELS:
        return "燃油锅炉", "fuel_determined"
    if fuel not in COAL_FUELS:
        return "不分技术", "fuel_determined"
    raw = clean(combustion)
    rows = aliases.get(raw, [])
    if len(rows) != 1 or clean(rows[0].get("mapping_status")) != "mapped":
        return "", clean(rows[0].get("mapping_status")) if rows else "unmapped"
    return clean(rows[0].get("standard_combustion_technology")), "mapped"


CONTROL_FIELD_CONFIG = (
    ("desulf_raw", "脱硫处理工艺名称", "DESULF"),
    ("denox_raw", "脱硝处理工艺名称", "DENOX"),
    ("dust_raw", "除尘处理工艺名称", "DUST"),
)
NOMINAL_CONTROL_FIELDS = {
    "SO2": {"脱硫处理工艺名称"},
    "NOX": {"脱硝处理工艺名称", "低氮燃烧字段"},
    "PM25": {"除尘处理工艺名称"},
    "PM25_10": {"除尘处理工艺名称"},
    "BC": {"除尘处理工艺名称"},
    "OC": {"除尘处理工艺名称"},
    "VOCS": set(),
    "CO": set(),
}


def source_control_components(
    payload: dict[str, Any], aliases: dict[tuple[str, str], list[dict[str, str]]]
) -> tuple[list[dict[str, str]], list[str], set[str]]:
    components: list[dict[str, str]] = []
    flags: list[str] = []
    missing_fields: set[str] = set()
    raw_candidates = payload.get("control_candidates_raw") or []
    if raw_candidates:
        fields_with_mapped_component: set[str] = set()
        for candidate in raw_candidates:
            raw_value = clean(candidate.get("process"))
            if not raw_value:
                continue
            mapped_for_candidate = False
            for _, raw_field, _ in CONTROL_FIELD_CONFIG:
                rows = aliases.get((raw_field, raw_value), [])
                if not rows or any(clean(row.get("mapping_status")) != "mapped" for row in rows):
                    continue
                for row in rows:
                    technology = clean(row.get("standard_control_technology"))
                    if technology:
                        components.append(
                            {
                                "technology": technology,
                                "raw_field": raw_field,
                                "raw_value": raw_value,
                                "facility_id": clean(candidate.get("facility_id")),
                                "reported_efficiency": clean(candidate.get("reported_efficiency")),
                            }
                        )
                        fields_with_mapped_component.add(raw_field)
                        mapped_for_candidate = True
            if not mapped_for_candidate:
                flags.append("CONTROL_TECH_UNMAPPED_CANDIDATE")
        for _, raw_field, flag_suffix in CONTROL_FIELD_CONFIG:
            if raw_field not in fields_with_mapped_component:
                missing_fields.add(raw_field)
                flags.append(f"CONTROL_INFO_MISSING_{flag_suffix}")
    else:
        for payload_key, raw_field, flag_suffix in CONTROL_FIELD_CONFIG:
            raw_value = clean(payload.get(payload_key))
            if not raw_value:
                missing_fields.add(raw_field)
                flags.append(f"CONTROL_INFO_MISSING_{flag_suffix}")
                continue
            rows = aliases.get((raw_field, raw_value), [])
            if not rows or any(clean(row.get("mapping_status")) != "mapped" for row in rows):
                flags.append(f"CONTROL_TECH_UNMAPPED_{flag_suffix}")
                continue
            for row in rows:
                technology = clean(row.get("standard_control_technology"))
                if technology:
                    components.append({"technology": technology, "raw_field": raw_field, "raw_value": raw_value})

    low_nox = clean(payload.get("low_nox")) == "是"
    if low_nox and not any("低氮燃烧技术" in row["technology"] for row in components):
        components.append({"technology": "低氮燃烧技术", "raw_field": "低氮燃烧字段", "raw_value": "是"})

    deduplicated: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in components:
        key = (item["technology"], item["raw_field"])
        if key not in seen:
            deduplicated.append(item)
            seen.add(key)
    raw_values_by_field: dict[str, set[str]] = defaultdict(set)
    for item in deduplicated:
        raw_values_by_field[item["raw_field"]].add(item["raw_value"])
    if any(len(raw_values) > 1 for raw_values in raw_values_by_field.values()):
        flags.append("CONTROL_RELATION_AMBIGUOUS")
    return deduplicated, sorted(set(flags)), missing_fields


def denitrification_processes(payload: dict[str, Any]) -> tuple[list[str], str]:
    """Resolve the independently reported SCR/SNCR ammonia-slip process.

    The raw control-facility text is the evidence used by the frozen reference
    package.  A non-empty denitrification description without SCR/SNCR means
    the ammonia-slip path is not involved; an entirely missing description is
    the reviewed empirical-zero path.
    """

    raw_values = [clean(payload.get("denox_raw"))]
    raw_values.extend(clean(item.get("process")) for item in (payload.get("control_candidates_raw") or []))
    text = "+".join(value for value in raw_values if value)
    processes: list[str] = []
    if "SCR" in text.upper() or ("选择性催化还原" in text and "非催化" not in text):
        processes.append("脱硝烟气-选择性催化还原")
    if "SNCR" in text.upper() or "选择性非催化还原" in text:
        processes.append("脱硝烟气-选择性非催化还原")
    if processes:
        return sorted(set(processes)), ""
    return (
        [],
        "NH3_NOT_APPLICABLE_NO_SCR_SNCR" if text else "DENITRIFICATION_INFO_MISSING_ZERO",
    )


def low_nox_control_conflict(
    payload: dict[str, Any], aliases: dict[tuple[str, str], list[dict[str, str]]]
) -> bool:
    components, _, _ = source_control_components(payload, aliases)
    return clean(payload.get("low_nox")) == "否" and any(
        "低氮燃烧技术" in component["technology"] for component in components
    )


def select_control(
    payload: dict[str, Any], pollutant: str, efficiencies: list[dict[str, str]],
    aliases: dict[tuple[str, str], list[dict[str, str]]],
) -> tuple[str, list[str], float, str, list[dict[str, Any]], list[str]]:
    components, flags, missing_fields = source_control_components(payload, aliases)
    if pollutant == "NOX":
        technologies = {row["technology"] for row in components}
        has_low_nox = "低氮燃烧技术" in technologies
        has_scr = "选择性催化还原法" in technologies
        has_sncr = "选择性非催化还原法" in technologies
        explicit = {
            technology for technology in technologies
            if technology.startswith("低氮燃烧技术+")
        }
        if explicit:
            components = [
                row for row in components
                if row["technology"] in explicit
                or row["technology"] not in {
                    "低氮燃烧技术",
                    "选择性催化还原法",
                    "选择性非催化还原法",
                }
            ]
        elif has_low_nox and has_scr:
            components = [
                row for row in components
                if row["technology"] not in {"低氮燃烧技术", "选择性催化还原法"}
            ]
            components.append({
                "technology": "低氮燃烧技术+选择性催化还原法",
                "raw_field": "低氮燃烧字段",
                "raw_value": "是+SCR",
            })
        elif has_low_nox and has_sncr:
            components = [
                row for row in components
                if row["technology"] not in {"低氮燃烧技术", "选择性非催化还原法"}
            ]
            components.append({
                "technology": "低氮燃烧技术+选择性非催化还原法",
                "raw_field": "低氮燃烧字段",
                "raw_value": "是+SNCR",
            })
    by_key = {(row["control_technology"], row["pollutant"]): row for row in efficiencies}
    table_pollutant = "PM25_10" if pollutant == "PM10_COARSE" else pollutant
    applicable: list[dict[str, Any]] = []
    for component in components:
        parameter = by_key.get((component["technology"], table_pollutant))
        efficiency = number(parameter.get("efficiency_percent")) if parameter else None
        if parameter and efficiency is not None and efficiency > 0:
            applicable.append({**component, **parameter, "efficiency": efficiency})

    if not applicable:
        unmapped = any(flag.startswith("CONTROL_TECH_UNMAPPED") for flag in flags)
        relevant_fields = NOMINAL_CONTROL_FIELDS.get(table_pollutant, set())
        relevant_missing = bool(relevant_fields.intersection(missing_fields))
        if unmapped:
            label = "治理工艺无法映射按0"
            flags.append("CONTROL_UNMAPPED_ZERO")
        elif relevant_missing:
            label = "治理信息缺失按0"
            flags.append("CONTROL_MISSING_ZERO")
        else:
            label = "无适用治理措施按0"
            flags.append("NO_APPLICABLE_CONTROL_ZERO")
        return "", [], 0.0, label, [], sorted(set(flags))

    chosen = max(applicable, key=lambda row: float(row["efficiency"]))
    if len(applicable) > 1:
        label = "多工艺取最大缺省效率"
        flags.append("MULTI_PROCESS_MAX_DEFAULT")
    elif chosen["technology"].startswith("低氮燃烧技术+"):
        label = "标准组合工艺缺省效率"
    elif chosen["technology"] == "低氮燃烧技术":
        label = "低氮燃烧缺省效率"
    elif chosen["raw_field"] not in NOMINAL_CONTROL_FIELDS.get(table_pollutant, set()):
        label = "协同去除缺省效率"
    else:
        label = "工艺缺省效率"
    return (
        str(chosen["technology"]),
        [str(row["parameter_id"]) for row in applicable],
        float(chosen["efficiency"]),
        label,
        applicable,
        sorted(set(flags)),
    )


def activity(amount: float | None, unit: str) -> tuple[float | None, str, str]:
    if amount is None or amount <= 0:
        return None, "", ""
    if unit == "吨":
        return amount * 1000.0, "kg", "raw_amount*1000"
    if unit == "万立方米":
        return amount * 10000.0, "m3", "raw_amount*10000"
    return None, "", ""


def factor_index(package: Path, target: str) -> list[dict[str, str]]:
    name = "power_heat_emission_factors.csv" if target == "POWER" else "industrial_boiler_emission_factors.csv"
    return load_csv(package / "parameters" / name)


def coal_index(package: Path) -> list[dict[str, str]]:
    return load_csv(package / "parameters" / "coal_parameters.csv")


def normalize_pollutant_for_table(pollutant: str) -> str:
    return pollutant


def match_factor(
    rows: list[dict[str, str]], department: str, fuel: str, technology: str, pollutant: str
) -> dict[str, str] | None:
    candidates = [
        row for row in rows
        if row["department"] == department
        and row["fuel"] == fuel
        and row["combustion_technology"] == technology
        and row["pollutant"] == normalize_pollutant_for_table(pollutant)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if candidates or technology == "不分技术":
        return None
    fallback = [
        row for row in rows
        if row["department"] == department
        and row["fuel"] == fuel
        and row["combustion_technology"] == "不分技术"
        and row["pollutant"] == normalize_pollutant_for_table(pollutant)
    ]
    return fallback[0] if len(fallback) == 1 else None


def match_coal(rows: list[dict[str, str]], target: str, department: str, technology: str) -> dict[str, str] | None:
    sector = "电力热力源" if target == "POWER" else "工业源"
    candidates = [
        row for row in rows
        if row["sector"] == sector and row["department"] == department and row["combustion_technology"] == technology
    ]
    return candidates[0] if len(candidates) == 1 else None


def admission_disposition(item_type: str, pollutant: str, flags: list[str]) -> tuple[str, str]:
    """Translate rule flags into the audited admission action and reason.

    The order is deliberate: it gives each audited source condition one clear
    primary reason instead of exposing an incidental downstream failure.
    """
    flag_set = set(flags)
    priority = [
        ("SOURCE_RELATION_MISSING", "withhold", "SOURCE_RELATION_MISSING"),
        ("LOW_NOX_FIELD_CONFLICT", "withhold", "LOW_NOX_FIELD_CONFLICT"),
        ("POTENTIAL_DUPLICATE_FUEL_SLOT", "withhold", "POTENTIAL_DUPLICATE_FUEL_SLOT"),
        ("NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED", "withhold", "NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED"),
        ("FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE", "not_applicable", "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE"),
        ("NH3_FACTOR_REQUIRES_COAL_ACTIVITY", "withhold", "NH3_FACTOR_SCOPE_MISMATCH"),
        ("UNRESOLVED_OTHER_FUEL_TCE", "withhold", "OTHER_FUEL_TCE_UNRESOLVED"),
        ("FACTOR_APPLICABILITY_UNRESOLVED", "withhold", "FACTOR_APPLICABILITY_UNRESOLVED"),
        ("COMBUSTION_TECHNOLOGY_AMBIGUOUS", "withhold", "COMBUSTION_TECHNOLOGY_AMBIGUOUS"),
        ("COMBUSTION_TECH_UNMAPPED", "withhold", "COMBUSTION_TECHNOLOGY_AMBIGUOUS"),
        ("T_CSES_COAL_BC_OC_PARAMETER_GAP", "withhold", "T_CSES_COAL_BC_OC_PARAMETER_GAP"),
        ("T_CSES_COAL_PARAMETER_GAP", "withhold", "T_CSES_COAL_PARAMETER_GAP"),
    ]
    for flag, action, reason in priority:
        if flag in flag_set:
            return action, reason
    if pollutant == "NH3" and "NH3_NOT_APPLICABLE_NO_SCR_SNCR" in flag_set:
        return "not_applicable", "NH3_NOT_APPLICABLE_NO_SCR_SNCR"
    foundational = [
        flag for flag in (
            "SOURCE_RELATION_MISSING",
            "FUEL_MISSING", "FUEL_UNMAPPED", "FUEL_OUTSIDE_FOSSIL_SCOPE",
            "COMBUSTION_TECH_UNMAPPED", "ACTIVITY_OR_UNIT_MISSING", "ACTIVITY_OUT_OF_RANGE",
            "FACTOR_UNMATCHED", "FACTOR_INPUT_MISSING", "FACTOR_ACTIVITY_UNIT_MISMATCH",
            "SULFUR_OUT_OF_RANGE", "ASH_OUT_OF_RANGE", "CAPACITY_OUT_OF_RANGE",
            "NH3_COAL_ACTIVITY_MISSING",
        )
        if flag in flag_set
    ]
    if foundational:
        return "withhold", foundational[0]
    if item_type == "device_slot":
        return "allow_calculation", "ABLATION_AGENT_MAPPING"
    return "allow_calculation", "RULE_CHAIN_COMPLETE"


def calculate_factor(
    mode: str, raw_value: str, pollutant: str, payload: dict[str, Any], fuel_row: sqlite3.Row,
    coal: dict[str, str] | None,
) -> tuple[float | None, str]:
    if mode == "constant":
        return number(raw_value), "constant parameter"
    if mode == "capacity_lookup":
        capacity = number(payload.get("capacity_mw"))
        if capacity is None:
            return None, "capacity missing"
        return (8.96 if capacity <= 100 else (8.19 if capacity < 300 else 7.21)), "capacity band"
    if mode == "coal_sulfur_balance":
        sulfur = number(fuel_row["sulfur"])
        sulfur_unit = clean(fuel_row["sulfur_unit"])
        sr = number(coal.get("sulfur_to_bottom_ash")) if coal else None
        if sulfur is None or sulfur_unit != "%" or sr is None:
            return None, "sulfur balance inputs missing"
        return 20.0 * sulfur * (1.0 - sr), "20*S_percent*(1-sr)"
    if mode == "coal_particle_balance":
        ash = number(fuel_row["ash"])
        ar = number(coal.get("ash_to_bottom_ash")) if coal else None
        if ash is None or ar is None:
            return None, "particle balance inputs missing"
        fraction_key = {
            "PM25": "pm25_fraction", "PM10": "pm10_fraction",
            "BC": "bc_fraction_of_pm25", "OC": "oc_fraction_of_pm25",
        }[pollutant]
        fraction = number(coal.get(fraction_key)) if coal else None
        pm25_fraction = number(coal.get("pm25_fraction")) if coal else None
        if fraction is None:
            return None, "particle fraction missing"
        if pollutant in {"BC", "OC"}:
            if pm25_fraction is None:
                return None, "PM25 fraction missing"
            return 10.0 * ash * (1.0 - ar) * pm25_fraction * fraction, f"10*Aar*(1-ar)*fPM25*f{pollutant}"
        return 10.0 * ash * (1.0 - ar) * fraction, f"10*Aar*(1-ar)*f{pollutant}"
    return None, "unsupported factor mode"


def stage_adapt(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    stage = "adapt_environmental_workbooks"
    revision = stage_revision(stage, request, manifest, None)
    existing = connection.execute("SELECT summary_json FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if existing:
        return json.loads(existing["summary_json"])
    adapter_path = Path(manifest.get("input_adapter_path", "")).resolve()
    if not adapter_path.is_file():
        raise ValueError(f"input adapter missing: {adapter_path}")
    if clean(manifest.get("input_adapter_hash")) != sha256_file(adapter_path):
        raise ValueError("input adapter hash mismatch")
    adapter = read_json(adapter_path)
    aliases = adapter.get("header_aliases") or {}
    hashes: dict[str, str] = {str(adapter_path): sha256_file(adapter_path)}
    counts: Counter[str] = Counter()
    specs = input_specs(manifest)

    def checked_path(spec: dict[str, Any]) -> Path:
        path = Path(spec["path"]).resolve()
        if not path.is_file():
            raise ValueError(f"input workbook missing: {path}")
        expected = clean(spec.get("sha256"))
        actual = sha256_file(path)
        if expected and expected != actual:
            raise ValueError(f"input hash mismatch: {path}")
        hashes[str(path)] = actual
        return path

    def role(spec: dict[str, Any]) -> str:
        return clean(spec.get("role") or spec.get("tag"))

    def is_role(value: str, *accepted: str) -> bool:
        return value in set(accepted)

    enterprise_by_key: dict[str, dict[str, Any]] = {}
    controls_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for spec in specs:
        current_role = role(spec)
        if not is_role(current_role, "B101_ENTERPRISE", "enterprise_master", "B101_CONTROL", "control_facility_base"):
            continue
        path = checked_path(spec)
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet_name = spec.get("sheet") or workbook.sheetnames[0]
        iterator = workbook[sheet_name].iter_rows(values_only=True)
        header_tag = clean(spec.get("tag")) or current_role
        headers = header_positions(next(iterator), header_tag, aliases)
        for row in iterator:
            payload = (
                normalize_enterprise_payload(row, headers)
                if is_role(current_role, "B101_ENTERPRISE", "enterprise_master")
                else normalize_control_payload(row, headers)
            )
            key = pseudonymous_entity_id(payload)
            if not key or not clean(payload.get("year")):
                continue
            if is_role(current_role, "B101_ENTERPRISE", "enterprise_master"):
                enterprise_by_key.setdefault(key, payload)
            elif clean(payload.get("outlet_category")):
                controls_by_key[key].append(payload)
            counts[current_role] += 1
        workbook.close()

    for spec in specs:
        current_role = role(spec)
        if not is_role(current_role, "B102_DEVICE", "device_fuel_base"):
            continue
        path = checked_path(spec)
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet_name = spec.get("sheet") or workbook.sheetnames[0]
        iterator = workbook[sheet_name].iter_rows(values_only=True)
        header_tag = clean(spec.get("tag")) or current_role
        headers = header_positions(next(iterator), header_tag, aliases)
        for excel_row, row in enumerate(iterator, start=2):
            identity_stub = {
                "year": value_at(row, headers, "统计年份"),
                "org_code": value_at(row, headers, "组织机构代码"),
                "credit_code": value_at(row, headers, "统一社会信用代码"),
                "company": value_at(row, headers, "填报单位详细名称"),
            }
            entity_key = pseudonymous_entity_id(identity_stub)
            sequence = value_at(row, headers, "序号")
            if not entity_key or not clean(identity_stub.get("year")) or not clean(sequence):
                continue
            source_id = stable_source_id(entity_key, sequence, identity_stub["year"])
            payload = normalize_device_payload(
                row,
                headers,
                enterprise_by_key.get(entity_key),
                controls_by_key.get(entity_key, []),
            )
            payload["_source_id"] = source_id
            raw_id = source_id.replace("SRC-", "RAW-", 1)
            connection.execute(
                "INSERT INTO raw_records VALUES(?,?,?,?,?,?)",
                (
                    raw_id,
                    clean(spec.get("tag")) or current_role,
                    str(path),
                    sheet_name,
                    excel_row,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            counts["B102_DEVICE"] += 1
        workbook.close()
    if counts.get("B102_DEVICE", 0) == 0:
        raise ValueError("no Base-102 device candidates were loaded")
    connection.commit()
    return commit_stage(
        connection,
        stage,
        revision,
        None,
        {
            "record_counts": dict(counts),
            "enterprise_join_keys": len(enterprise_by_key),
            "control_join_keys": len(controls_by_key),
            "input_hashes": hashes,
            "can_continue": True,
        },
    )


def stage_sources(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    del run_path
    upstream = require_stage(connection, "adapt_environmental_workbooks")
    stage = "build_device_emission_sources"
    revision = stage_revision(stage, request, manifest, upstream)
    existing = connection.execute("SELECT summary_json FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if existing:
        return json.loads(existing["summary_json"])
    candidate_scope = set(effective_candidate_scope(request, manifest))
    requested_target = clean(manifest.get("target"))
    if requested_target not in {"INDUSTRIAL", "POWER"}:
        raise ValueError("run manifest target must be INDUSTRIAL or POWER")
    counts: Counter[str] = Counter()
    for row in connection.execute("SELECT * FROM raw_records ORDER BY raw_id"):
        payload = json.loads(row["payload_json"])
        source_id = clean(payload.get("_source_id"))
        if not source_id:
            raise ValueError(f"raw candidate has no target-neutral source id: {row['raw_id']}")
        if candidate_scope and source_id not in candidate_scope:
            continue
        target, department, reason = classify(payload)
        disposition = "include" if target == requested_target else ("outside_current_scope" if target == "EXCLUDE" else "other_target")
        connection.execute(
            "INSERT INTO source_decisions VALUES(?,?,?,?,?,?)",
            (source_id, row["raw_id"], target, requested_target, disposition, reason),
        )
        if target == "EXCLUDE":
            connection.execute("INSERT INTO excluded_records VALUES(?,?)", (row["raw_id"], reason))
            counts["EXCLUDE"] += 1
            continue
        counts[target] += 1
        if target != requested_target:
            continue
        if target == "POWER":
            payload["equipment"] = clean(payload.get("power_equipment")) or clean(payload.get("industrial_equipment"))
            payload["combustion"] = clean(payload.get("power_combustion")) or clean(payload.get("industrial_combustion"))
            payload["rated_output"] = payload.get("power_rated_output") or payload.get("industrial_rated_output")
            payload["run_hours"] = payload.get("power_run_hours") or payload.get("industrial_run_hours")
            payload["low_nox"] = payload.get("power_low_nox") if clean(payload.get("power_low_nox")) else payload.get("industrial_low_nox")
            relevant_outlet_category = "DZGL"
        else:
            payload["equipment"] = clean(payload.get("industrial_equipment")) or clean(payload.get("power_equipment"))
            payload["combustion"] = clean(payload.get("industrial_combustion")) or clean(payload.get("power_combustion"))
            payload["rated_output"] = payload.get("industrial_rated_output") or payload.get("power_rated_output")
            payload["run_hours"] = payload.get("industrial_run_hours") or payload.get("power_run_hours")
            payload["low_nox"] = payload.get("industrial_low_nox") if clean(payload.get("industrial_low_nox")) else payload.get("power_low_nox")
            relevant_outlet_category = "GYGL"
        payload["control_candidates_raw"] = [
            candidate
            for candidate in (payload.get("control_candidates_raw") or [])
            if clean(candidate.get("outlet_category")) == relevant_outlet_category
        ]
        source_flags = source_relationship_flags(payload)
        if payload["control_candidates_raw"]:
            source_flags.append("CONTROL_RELATION_CATEGORY_LEVEL")
        payload["_source_flags"] = sorted(set(source_flags))
        connection.execute(
            "INSERT INTO device_sources VALUES(?,?,?,?,?)",
            (source_id, row["raw_id"], target, department, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    connection.commit()
    return commit_stage(
        connection,
        stage,
        revision,
        upstream,
        {
            "candidate_count": sum(counts.values()),
            "source_counts": dict(counts),
            "requested_target": requested_target,
            "included_count": counts.get(requested_target, 0),
            "can_continue": True,
        },
    )


def stage_items(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection, device_slots: bool = False) -> dict[str, Any]:
    del run_path
    upstream = require_stage(connection, "build_device_emission_sources")
    stage = "generate_device_pollutant_slots" if device_slots else "generate_pollutant_calculation_items"
    revision = stage_revision(stage, request, manifest, upstream)
    existing = connection.execute("SELECT summary_json FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if existing:
        return json.loads(existing["summary_json"])
    counts: Counter[str] = Counter()
    for source in connection.execute("SELECT * FROM device_sources ORDER BY source_id"):
        payload = json.loads(source["payload_json"])
        source_fuel_ids: list[str] = []
        for fuel in payload["fuels"]:
            amount = number(fuel.get("amount"))
            raw_fuel = clean(fuel.get("fuel"))
            if amount == 0:
                continue
            if fuel["slot"] == "other_tce" and (amount is None or amount <= 0):
                continue
            if not raw_fuel and (amount is None or amount == 0):
                continue
            if amount is None and not raw_fuel:
                continue
            fuel_id = f"FUEL-{source['source_id']}-{fuel['slot']}"
            source_fuel_ids.append(fuel_id)
            connection.execute(
                "INSERT INTO fuels VALUES(?,?,?,?,?,?,?,?,?)",
                (fuel_id, source["source_id"], fuel["slot"], raw_fuel, amount, clean(fuel.get("unit")), number(fuel.get("sulfur")), clean(fuel.get("sulfur_unit")), number(fuel.get("ash"))),
            )
            if device_slots:
                continue
            pm25_item = f"ITEM-{fuel_id}-PM25"
            for pollutant in COMBUSTION_POLLUTANTS:
                item_id = f"ITEM-{fuel_id}-{pollutant}"
                dependency = pm25_item if pollutant in {"PM10", "BC", "OC"} else None
                connection.execute("INSERT INTO calculation_items VALUES(?,?,?,?,?,?)", (item_id, source["source_id"], fuel_id, "combustion", pollutant, dependency))
                counts[pollutant] += 1
        if not source_fuel_ids and not device_slots:
            fuel_id = f"FUEL-{source['source_id']}-missing"
            source_fuel_ids.append(fuel_id)
            connection.execute(
                "INSERT INTO fuels VALUES(?,?,?,?,?,?,?,?,?)",
                (fuel_id, source["source_id"], "missing", "", None, "", None, "", None),
            )
            pm25_item = f"ITEM-{fuel_id}-PM25"
            for pollutant in COMBUSTION_POLLUTANTS:
                item_id = f"ITEM-{fuel_id}-{pollutant}"
                dependency = pm25_item if pollutant in {"PM10", "BC", "OC"} else None
                connection.execute(
                    "INSERT INTO calculation_items VALUES(?,?,?,?,?,?)",
                    (item_id, source["source_id"], fuel_id, "combustion", pollutant, dependency),
                )
                counts[pollutant] += 1
        if device_slots:
            for pollutant in POLLUTANTS:
                item_id = f"ITEM-{source['source_id']}-{pollutant}"
                connection.execute("INSERT INTO calculation_items VALUES(?,?,?,?,?,?)", (item_id, source["source_id"], None, "device_slot", pollutant, None))
                counts[pollutant] += 1
            continue
        nh3_item = f"ITEM-{source['source_id']}-NH3-SLIP"
        connection.execute("INSERT INTO calculation_items VALUES(?,?,?,?,?,?)", (nh3_item, source["source_id"], None, "ammonia_slip", "NH3", None))
        counts["NH3"] += 1
    connection.commit()
    return commit_stage(connection, stage, revision, upstream, {"item_counts": dict(counts), "item_count": sum(counts.values()), "can_continue": True})


def stage_rules(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    del run_path
    item_stage = "generate_device_pollutant_slots" if request["scenario"] == "w_o_calculation_item_structure" else "generate_pollutant_calculation_items"
    upstream = require_stage(connection, item_stage)
    stage = "execute_tcses_calculation_rules"
    revision = stage_revision(stage, request, manifest, upstream)
    existing = connection.execute("SELECT summary_json FROM stage_commits WHERE stage=?", (stage,)).fetchone()
    if existing:
        return json.loads(existing["summary_json"])
    package = rule_path(manifest)
    coal_rows = coal_index(package)
    efficiencies = load_csv(package / "parameters" / "control_efficiencies.csv")
    slip_rows = load_csv(package / "parameters" / "ammonia_slip_factors.csv")
    fuel_aliases = mapping_index(package, "source_fuel_aliases.csv", "raw_value")
    fuel_categories = {
        clean(row.get("standard_value")): clean(row.get("category"))
        for row in load_csv(package / "mappings" / "standard_fuels.csv")
    }
    combustion_aliases = mapping_index(package, "source_combustion_aliases.csv", "raw_value")
    control_aliases: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for mapping in load_csv(package / "mappings" / "source_control_aliases.csv"):
        control_aliases[(clean(mapping.get("raw_field")), clean(mapping.get("raw_value")))].append(mapping)
    factors_by_target = {target: factor_index(package, target) for target in ("POWER", "INDUSTRIAL")}
    source_cache = {row["source_id"]: row for row in connection.execute("SELECT * FROM device_sources")}
    fuel_cache = {row["fuel_id"]: row for row in connection.execute("SELECT * FROM fuels")}
    flags_count: Counter[str] = Counter()
    matched = 0
    for item in connection.execute("SELECT * FROM calculation_items ORDER BY item_id"):
        source = source_cache[item["source_id"]]
        payload = json.loads(source["payload_json"])
        flags: list[str] = list(payload.get("_source_flags") or [])
        if item["pollutant"] in {"NOX", "NH3"} and low_nox_control_conflict(payload, control_aliases):
            flags.append("LOW_NOX_FIELD_CONFLICT")
        if item["item_type"] == "ammonia_slip":
            processes, zero_reason = denitrification_processes(payload)
            coal_fuels = []
            for fuel_row in fuel_cache.values():
                normalized, status = normalize_fuel(fuel_row["raw_fuel"], fuel_aliases)
                if fuel_row["source_id"] == source["source_id"] and normalized in COAL_FUELS and status == "mapped":
                    coal_fuels.append(fuel_row)
            factor_rows = [row for row in slip_rows if row["process"] in processes]
            parameter_ids = [row["parameter_id"] for row in factor_rows]
            factor_value = sum(number(row.get("value")) or 0.0 for row in factor_rows) if factor_rows else 0.0
            factor_unit = "g/kg煤"
            activity_value: float | None
            activity_unit = "kg_coal"
            activity_formula = "SUM(适用煤炭活动水平kg)"
            control_label = "SCR/SNCR氨逃逸核算"
            if len(processes) > 1:
                flags.append("NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED")
                factor_value = None
                activity_value = None
                factor_formula = "SCR/SNCR组合活动水平分配待核"
                control_label = "SCR/SNCR组合待核"
            elif zero_reason == "DENITRIFICATION_INFO_MISSING_ZERO":
                flags.append(zero_reason)
                factor_value = 0.0
                activity_value = 0.0
                factor_formula = "脱硝信息缺失按0"
                control_label = "治理信息缺失按0"
            elif zero_reason == "NH3_NOT_APPLICABLE_NO_SCR_SNCR":
                flags.append(zero_reason)
                factor_value = 0.0
                activity_value = 0.0
                factor_formula = "明确没有SCR/SNCR，NH3为0"
                control_label = "无SCR/SNCR，NH3不适用"
            elif not coal_fuels:
                activity_value = None
                factor_formula = "+".join(parameter_ids)
                flags.append("NH3_FACTOR_REQUIRES_COAL_ACTIVITY")
            else:
                activities = [activity(number(row["amount"]), clean(row["raw_unit"]))[0] for row in coal_fuels]
                activity_value = sum(v for v in activities if v is not None) if any(v is not None for v in activities) else None
                factor_formula = "+".join(parameter_ids)
                if activity_value is None:
                    flags.append("NH3_FACTOR_REQUIRES_COAL_ACTIVITY")
            connection.execute(
                "INSERT INTO rule_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["item_id"], "煤炭" if processes else "", "+".join(processes),
                    activity_value, activity_unit, activity_formula,
                    "+".join(parameter_ids) if parameter_ids else None,
                    "constant_sum" if parameter_ids else "empirical_zero",
                    factor_value, factor_unit, factor_formula,
                    "附录E 表E.1" if parameter_ids else "经验缺失处置规则", "50" if parameter_ids else "",
                    "+".join(processes), json.dumps(parameter_ids, ensure_ascii=False), 0.0, None, None,
                    control_label, json.dumps(processes, ensure_ascii=False), 1.0,
                    json.dumps(sorted(set(flags)), ensure_ascii=False),
                ),
            )
            for flag in flags:
                flags_count[flag] += 1
            continue
        if item["item_type"] == "device_slot":
            flags.append("CALCULATION_ITEM_STRUCTURE_REMOVED")
            source_fuels = [row for row in fuel_cache.values() if row["source_id"] == source["source_id"]]
            parameter_ids: list[str] = []
            normalized_fuels: list[str] = []
            technologies: list[str] = []
            aggregate_generation: float | None = 0.0
            control_technology = ""
            control_ids: list[str] = []
            control_eff = 0.0
            fine_eff: float | None = None
            coarse_eff: float | None = None
            control_label = "无适用治理措施按0"
            control_candidates: list[dict[str, Any]] = []
            if item["pollutant"] == "NH3":
                processes, zero_reason = denitrification_processes(payload)
                coal_activity = 0.0
                coal_activity_found = False
                for fuel_row in source_fuels:
                    normalized, status = normalize_fuel(fuel_row["raw_fuel"], fuel_aliases)
                    if status == "mapped" and normalized in COAL_FUELS:
                        value, _, _ = activity(number(fuel_row["amount"]), clean(fuel_row["raw_unit"]))
                        if value is not None:
                            coal_activity += value
                            coal_activity_found = True
                selected_slip = [row for row in slip_rows if row["process"] in processes]
                parameter_ids = [row["parameter_id"] for row in selected_slip]
                if len(processes) > 1:
                    flags.append("NH3_MULTI_PROCESS_ALLOCATION_UNRESOLVED")
                    aggregate_generation = None
                    control_label = "SCR/SNCR组合待核"
                elif zero_reason == "DENITRIFICATION_INFO_MISSING_ZERO":
                    flags.append(zero_reason)
                    aggregate_generation = 0.0
                    control_label = "治理信息缺失按0"
                elif zero_reason == "NH3_NOT_APPLICABLE_NO_SCR_SNCR":
                    flags.append(zero_reason)
                    aggregate_generation = 0.0
                    control_label = "无SCR/SNCR，NH3不适用"
                elif not coal_activity_found:
                    flags.append("NH3_FACTOR_REQUIRES_COAL_ACTIVITY")
                    aggregate_generation = None
                    control_label = "SCR/SNCR氨逃逸核算"
                else:
                    slip_factor = sum(number(row.get("value")) or 0.0 for row in selected_slip)
                    aggregate_generation = coal_activity * slip_factor / 1_000_000.0
                    control_label = "SCR/SNCR氨逃逸核算"
                normalized_fuels = ["煤炭"] if processes else []
                technologies = processes
            else:
                valid_contributions: list[float] = []
                if not source_fuels:
                    flags.append("ACTIVITY_OR_UNIT_MISSING")
                for fuel_row in source_fuels:
                    normalized, fuel_status = normalize_fuel(fuel_row["raw_fuel"], fuel_aliases)
                    technology, technology_status = normalize_technology(
                        normalized, payload.get("combustion"), combustion_aliases, fuel_categories
                    )
                    act_value, act_unit, _ = activity(number(fuel_row["amount"]), clean(fuel_row["raw_unit"]))
                    factor_row = match_factor(factors_by_target[source["target"]], source["department"], normalized, technology, item["pollutant"])
                    coal = match_coal(coal_rows, source["target"], source["department"], technology) if normalized in COAL_MASS_BALANCE_FUELS else None
                    factor_value = None
                    outside_scope = fuel_status == "outside_fossil_scope"
                    unresolved_tce = not normalized and clean(fuel_row["raw_unit"]) == "吨标准煤"
                    ambiguous_technology = (
                        normalized in COAL_FUELS and technology_status == "fuel_dependent_or_ambiguous"
                    )
                    if outside_scope:
                        flags.append("FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE")
                    elif unresolved_tce:
                        flags.append("UNRESOLVED_OTHER_FUEL_TCE")
                    elif not normalized:
                        flags.append("FUEL_UNMAPPED")
                    if ambiguous_technology:
                        flags.append("COMBUSTION_TECHNOLOGY_AMBIGUOUS")
                    elif not technology and normalized:
                        flags.append("COMBUSTION_TECH_UNMAPPED")
                    if act_value is None and not unresolved_tce:
                        flags.append("ACTIVITY_OR_UNIT_MISSING")
                    if factor_row:
                        factor_value, _ = calculate_factor(factor_row["mode"], factor_row["value"], item["pollutant"], payload, fuel_row, coal)
                        parameter_ids.append(factor_row["parameter_id"])
                        sulfur = number(fuel_row["sulfur"])
                        ash = number(fuel_row["ash"])
                        capacity = number(payload.get("capacity_mw"))
                        if factor_row["mode"] == "coal_sulfur_balance" and sulfur is not None and not (0.0 <= sulfur <= 100.0):
                            factor_value = None
                            flags.append("SULFUR_OUT_OF_RANGE")
                        if factor_row["mode"] == "coal_particle_balance" and ash is not None and not (0.0 <= ash <= 100.0):
                            factor_value = None
                            flags.append("ASH_OUT_OF_RANGE")
                        if factor_row["mode"] == "capacity_lookup" and capacity is not None and capacity <= 0.0:
                            factor_value = None
                            flags.append("CAPACITY_OUT_OF_RANGE")
                    elif not outside_scope and not unresolved_tce:
                        if normalized == "石油焦" or (source["target"] == "INDUSTRIAL" and normalized == "煤矸石"):
                            flags.append("FACTOR_APPLICABILITY_UNRESOLVED")
                        elif not ambiguous_technology:
                            flags.append("FACTOR_UNMATCHED")
                    if factor_row and factor_value is None:
                        if (source["target"] == "POWER" and source["department"] == "电力供应"
                                and normalized in COAL_FUELS and technology == "煤粉炉"
                                and item["pollutant"] in {"BC", "OC"}):
                            flags.append("T_CSES_COAL_BC_OC_PARAMETER_GAP")
                        elif normalized == "焦炭" and coal is None:
                            flags.append("T_CSES_COAL_PARAMETER_GAP")
                        else:
                            flags.append("FACTOR_INPUT_MISSING")
                    expected_unit = clean(factor_row.get("unit")) if factor_row else ""
                    if factor_value is not None and ((expected_unit == "g/m3" and act_unit != "m3") or (expected_unit == "g/kg燃料" and act_unit != "kg")):
                        flags.append("FACTOR_ACTIVITY_UNIT_MISMATCH")
                        factor_value = None
                    if act_value is not None and factor_value is not None:
                        valid_contributions.append(act_value * factor_value / 1_000_000.0)
                    normalized_fuels.append(normalized)
                    technologies.append(technology)
                blocking = {
                    "FUEL_UNMAPPED", "FUEL_OUTSIDE_FOSSIL_SCOPE", "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE",
                    "UNRESOLVED_OTHER_FUEL_TCE", "COMBUSTION_TECH_UNMAPPED", "COMBUSTION_TECHNOLOGY_AMBIGUOUS",
                    "ACTIVITY_OR_UNIT_MISSING", "FACTOR_UNMATCHED", "FACTOR_INPUT_MISSING",
                    "T_CSES_COAL_BC_OC_PARAMETER_GAP", "FACTOR_APPLICABILITY_UNRESOLVED",
                    "T_CSES_COAL_PARAMETER_GAP",
                    "FACTOR_ACTIVITY_UNIT_MISMATCH", "SULFUR_OUT_OF_RANGE", "ASH_OUT_OF_RANGE",
                    "CAPACITY_OUT_OF_RANGE", "SOURCE_RELATION_MISSING",
                }
                aggregate_generation = None if blocking.intersection(flags) else sum(valid_contributions)
                if item["pollutant"] == "PM10":
                    control_technology, control_ids, fine_eff, fine_label, fine_candidates, control_flags = select_control(payload, "PM25", efficiencies, control_aliases)
                    coarse_technology, coarse_ids, coarse_eff, coarse_label, coarse_candidates, coarse_flags = select_control(payload, "PM10_COARSE", efficiencies, control_aliases)
                    control_ids = sorted(set(control_ids + coarse_ids))
                    control_eff = coarse_eff
                    control_label = "多工艺取最大缺省效率" if "多工艺取最大缺省效率" in {fine_label, coarse_label} else fine_label
                    control_candidates = fine_candidates + coarse_candidates
                    control_technology = control_technology or coarse_technology
                    flags.extend(control_flags + coarse_flags)
                else:
                    control_technology, control_ids, control_eff, control_label, control_candidates, control_flags = select_control(
                        payload, item["pollutant"], efficiencies, control_aliases
                    )
                    flags.extend(control_flags)
            connection.execute(
                "INSERT INTO rule_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["item_id"],
                    "|".join(sorted(set(value for value in normalized_fuels if value))),
                    "|".join(sorted(set(value for value in technologies if value))),
                    1_000_000.0 if aggregate_generation is not None else None,
                    "aggregate_scaler",
                    "设备级直接汇总，不保留燃料污染物核算项",
                    "+".join(sorted(set(parameter_ids))) or None,
                    "device_direct_aggregate",
                    aggregate_generation,
                    "t/source",
                    "SUM(设备燃料贡献)",
                    "T/CSES direct Agent organization ablation",
                    "",
                    control_technology,
                    json.dumps(control_ids, ensure_ascii=False),
                    control_eff,
                    fine_eff,
                    coarse_eff,
                    control_label,
                    json.dumps(control_candidates, ensure_ascii=False, sort_keys=True),
                    1.0,
                    json.dumps(sorted(set(flags)), ensure_ascii=False),
                ),
            )
            for flag in set(flags):
                flags_count[flag] += 1
            continue
        fuel = fuel_cache[item["fuel_id"]]
        normalized_fuel, fuel_status = normalize_fuel(fuel["raw_fuel"], fuel_aliases)
        technology, technology_status = normalize_technology(
            normalized_fuel, payload.get("combustion"), combustion_aliases, fuel_categories
        )
        act_value, act_unit, act_formula = activity(number(fuel["amount"]), clean(fuel["raw_unit"]))
        factor_row = match_factor(factors_by_target[source["target"]], source["department"], normalized_fuel, technology, item["pollutant"])
        coal = match_coal(coal_rows, source["target"], source["department"], technology) if normalized_fuel in COAL_MASS_BALANCE_FUELS else None
        factor_value, factor_formula = (None, "factor not matched")
        if factor_row:
            factor_value, factor_formula = calculate_factor(factor_row["mode"], factor_row["value"], item["pollutant"], payload, fuel, coal)
            matched += 1
            mode = clean(factor_row.get("mode"))
            sulfur = number(fuel["sulfur"])
            ash = number(fuel["ash"])
            capacity = number(payload.get("capacity_mw"))
            if mode == "coal_sulfur_balance" and sulfur is not None and not (0.0 <= sulfur <= 100.0):
                factor_value, factor_formula = None, "sulfur percentage outside [0,100]"
                flags.append("SULFUR_OUT_OF_RANGE")
            if mode == "coal_particle_balance" and ash is not None and not (0.0 <= ash <= 100.0):
                factor_value, factor_formula = None, "ash percentage outside [0,100]"
                flags.append("ASH_OUT_OF_RANGE")
            if mode == "capacity_lookup" and capacity is not None and capacity <= 0.0:
                factor_value, factor_formula = None, "capacity must be greater than zero"
                flags.append("CAPACITY_OUT_OF_RANGE")
        effective_factor_unit = (factor_row.get("unit") or "g/kg燃料") if factor_row else None
        outside_scope = fuel_status == "outside_fossil_scope"
        unresolved_tce = not normalized_fuel and clean(fuel["raw_unit"]) == "吨标准煤"
        ambiguous_technology = (
            normalized_fuel in COAL_FUELS and technology_status == "fuel_dependent_or_ambiguous"
        )
        if outside_scope:
            flags.append("FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE")
        elif unresolved_tce:
            flags.append("UNRESOLVED_OTHER_FUEL_TCE")
        elif not normalized_fuel:
            flags.append("FUEL_UNMAPPED")
        elif ambiguous_technology:
            flags.append("COMBUSTION_TECHNOLOGY_AMBIGUOUS")
        elif not technology:
            flags.append("COMBUSTION_TECH_UNMAPPED")
        if act_value is None and not unresolved_tce:
            flags.append("ACTIVITY_OR_UNIT_MISSING")
            if number(fuel["amount"]) is not None and number(fuel["amount"]) <= 0:
                flags.append("ACTIVITY_OUT_OF_RANGE")
        if not factor_row and not outside_scope and not unresolved_tce:
            if normalized_fuel == "石油焦" or (source["target"] == "INDUSTRIAL" and normalized_fuel == "煤矸石"):
                flags.append("FACTOR_APPLICABILITY_UNRESOLVED")
            elif not ambiguous_technology:
                flags.append("FACTOR_UNMATCHED")
        elif factor_value is None:
            if (source["target"] == "POWER" and source["department"] == "电力供应"
                    and normalized_fuel in COAL_FUELS and technology == "煤粉炉"
                    and item["pollutant"] in {"BC", "OC"}):
                flags.append("T_CSES_COAL_BC_OC_PARAMETER_GAP")
            elif normalized_fuel == "焦炭" and coal is None:
                flags.append("T_CSES_COAL_PARAMETER_GAP")
            else:
                flags.append("FACTOR_INPUT_MISSING")
        elif effective_factor_unit == "g/m3" and act_unit != "m3":
            flags.append("FACTOR_ACTIVITY_UNIT_MISMATCH")
        elif effective_factor_unit == "g/kg燃料" and act_unit != "kg":
            flags.append("FACTOR_ACTIVITY_UNIT_MISMATCH")
        if item["pollutant"] == "PM10":
            control_technology, control_ids, fine_eff, fine_label, fine_candidates, control_flags = select_control(payload, "PM25", efficiencies, control_aliases)
            coarse_technology, coarse_ids, coarse_eff, coarse_label, coarse_candidates, coarse_flags = select_control(payload, "PM10_COARSE", efficiencies, control_aliases)
            control_ids = sorted(set(control_ids + coarse_ids))
            control_eff = coarse_eff
            label_priority = ["多工艺取最大缺省效率", "标准组合工艺缺省效率", "协同去除缺省效率", "工艺缺省效率",
                              "治理工艺无法映射按0", "治理信息缺失按0", "无适用治理措施按0"]
            control_label = next((label for label in label_priority if label in {fine_label, coarse_label}), fine_label)
            control_candidates = fine_candidates + coarse_candidates
            if not control_technology:
                control_technology = coarse_technology
            flags.extend(control_flags + coarse_flags)
        else:
            control_technology, control_ids, control_eff, control_label, control_candidates, control_flags = select_control(
                payload, item["pollutant"], efficiencies, control_aliases
            )
            fine_eff = None
            coarse_eff = None
            flags.extend(control_flags)
        connection.execute(
            "INSERT INTO rule_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["item_id"], normalized_fuel, technology, act_value, act_unit, act_formula,
                factor_row.get("parameter_id") if factor_row else None,
                factor_row.get("mode") if factor_row else None,
                factor_value, effective_factor_unit, factor_formula,
                factor_row.get("source_section") if factor_row else None,
                factor_row.get("physical_pdf_page") if factor_row else None,
                control_technology, json.dumps(control_ids, ensure_ascii=False), control_eff,
                fine_eff, coarse_eff, control_label,
                json.dumps(control_candidates, ensure_ascii=False, sort_keys=True), 1.0,
                json.dumps(sorted(set(flags)), ensure_ascii=False),
            ),
        )
        for flag in set(flags):
            flags_count[flag] += 1
    connection.commit()
    return commit_stage(connection, stage, revision, upstream, {"matched_item_count": matched, "flags": dict(flags_count), "can_continue": True})


def read_decisions(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                result[str(payload["item_id"])] = payload
    return result


def json_list(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value] if value else []
        value = parsed
    if value is None:
        value = []
    if not isinstance(value, list):
        value = [value]
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stage_agent_rule_options(
    request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection,
) -> dict[str, Any]:
    del request, run_path, manifest
    require_stage(connection, "generate_pollutant_calculation_items")
    combinations: Counter[tuple[Any, ...]] = Counter()
    query = """
        SELECT i.item_type,i.pollutant,d.target,d.department,d.payload_json,
               f.raw_fuel,f.raw_unit,f.sulfur,f.sulfur_unit,f.ash
        FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
        LEFT JOIN fuels f ON f.fuel_id=i.fuel_id ORDER BY i.item_id
    """
    for row in connection.execute(query):
        payload = json.loads(row["payload_json"])
        key = (
            row["item_type"], row["target"], row["department"], row["pollutant"],
            clean(row["raw_fuel"]), clean(row["raw_unit"]), clean(payload.get("combustion")),
            "missing" if number(payload.get("capacity_mw")) is None else "present",
            "missing" if number(row["sulfur"]) is None else "present",
            "missing" if number(row["ash"]) is None else "present",
            clean(payload.get("denox_raw")), clean(payload.get("low_nox")),
        )
        combinations[key] += 1
    ordered = sorted(combinations.items(), key=lambda item: (-item[1], tuple(str(v) for v in item[0])))
    fields = [
        "item_type", "target", "department", "pollutant", "raw_fuel", "raw_unit", "combustion",
        "capacity", "sulfur", "ash", "denox_raw", "low_nox", "count",
    ]
    rows = [dict(zip(fields, [*key, count])) for key, count in ordered[:400]]
    return {
        "success": True,
        "stage": "summarize_agent_rule_options",
        "item_count": sum(combinations.values()),
        "combination_count": len(combinations),
        "returned_combination_count": len(rows),
        "truncated_combination_count": max(0, len(combinations) - len(rows)),
        "combinations": rows,
        "frozen_rule_values_exposed": False,
    }


def stage_agent_admission_options(
    request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection,
) -> dict[str, Any]:
    del request, run_path, manifest
    require_stage(connection, "execute_tcses_calculation_rules")
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    item_count = 0
    for row in connection.execute("SELECT item_id,flags_json FROM rule_results ORDER BY item_id"):
        item_count += 1
        flags = json.loads(row["flags_json"] or "[]")
        if not flags:
            counts["NO_FLAG"] += 1
        for flag in flags:
            counts[str(flag)] += 1
            if len(examples[str(flag)]) < 3:
                examples[str(flag)].append(row["item_id"])
    return {
        "success": True,
        "stage": "summarize_agent_admission_options",
        "item_count": item_count,
        "flag_counts": dict(counts),
        "examples": dict(examples),
        "deterministic_admission_exposed": False,
    }


def policy_matches(rule: dict[str, Any], context: dict[str, str]) -> bool:
    for key in ("item_type", "target", "department", "pollutant", "raw_fuel", "raw_unit", "combustion"):
        expected = clean(rule.get(key))
        if expected and expected != "*" and expected != clean(context.get(key)):
            return False
    return True


def agent_rule_decisions_from_policy(connection: sqlite3.Connection, policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    factor_rules = [row for row in (policy.get("factor_rules") or []) if isinstance(row, dict)]
    control_efficiencies = policy.get("control_efficiency_by_pollutant") or {}
    nh3_policy = policy.get("nh3") or {}
    decisions: dict[str, dict[str, Any]] = {}
    source_cache = {row["source_id"]: row for row in connection.execute("SELECT * FROM device_sources")}
    fuel_cache = {row["fuel_id"]: row for row in connection.execute("SELECT * FROM fuels")}
    for item in connection.execute("SELECT * FROM calculation_items ORDER BY item_id"):
        source = source_cache[item["source_id"]]
        payload = json.loads(source["payload_json"])
        flags = list(payload.get("_source_flags") or [])
        if item["item_type"] == "ammonia_slip":
            processes, zero_reason = denitrification_processes(payload)
            has_scr = "脱硝烟气-选择性催化还原" in processes
            has_sncr = "脱硝烟气-选择性非催化还原" in processes
            coal_rows = [
                row for row in fuel_cache.values()
                if row["source_id"] == source["source_id"] and "煤" in clean(row["raw_fuel"])
            ]
            coal_activity = sum(
                value for value in (activity(number(row["amount"]), clean(row["raw_unit"]))[0] for row in coal_rows)
                if value is not None
            )
            if zero_reason == "DENITRIFICATION_INFO_MISSING_ZERO":
                factor_value, activity_value = 0.0, 0.0
                flags.append(zero_reason)
                label = "Agent判断：脱硝信息缺失按0"
            elif zero_reason == "NH3_NOT_APPLICABLE_NO_SCR_SNCR":
                factor_value, activity_value = 0.0, 0.0
                flags.append(zero_reason)
                label = "Agent判断：无SCR/SNCR"
            elif not coal_rows or coal_activity <= 0:
                factor_value, activity_value = None, None
                flags.append("NH3_FACTOR_REQUIRES_COAL_ACTIVITY")
                label = "Agent判断：氨逃逸煤炭活动水平缺失"
            else:
                factor_value = (number(nh3_policy.get("scr_factor")) or 0.0) * int(has_scr) + (number(nh3_policy.get("sncr_factor")) or 0.0) * int(has_sncr)
                activity_value = coal_activity
                label = "Agent判断：SCR/SNCR氨逃逸"
            decisions[item["item_id"]] = {
                "item_id": item["item_id"], "normalized_fuel": "煤炭" if has_scr or has_sncr else "",
                "normalized_technology": "+".join(name for name, present in (("SCR", has_scr), ("SNCR", has_sncr)) if present),
                "activity_value": activity_value, "activity_unit": "kg_coal", "activity_formula": "Agent policy coal activity",
                "parameter_id": clean(nh3_policy.get("parameter_id")) or "AGENT-NH3",
                "mode": "agent_selected", "factor_value": factor_value, "factor_unit": "g/kg煤",
                "factor_formula": "Agent policy NH3 factor", "source_section": "Agent prompt judgment", "source_page": "",
                "control_technology": "", "control_parameter_ids": [], "control_efficiency": 0.0,
                "control_fine_efficiency": None, "control_coarse_efficiency": None, "control_label": label,
                "control_candidates": [], "operation_rate": 1.0, "flags": flags,
            }
            continue
        fuel = fuel_cache[item["fuel_id"]]
        context = {
            "item_type": item["item_type"], "target": source["target"], "department": source["department"],
            "pollutant": item["pollutant"], "raw_fuel": clean(fuel["raw_fuel"]), "raw_unit": clean(fuel["raw_unit"]),
            "combustion": clean(payload.get("combustion")),
        }
        selected = next((rule for rule in factor_rules if policy_matches(rule, context)), None)
        act_value, act_unit, act_formula = activity(number(fuel["amount"]), clean(fuel["raw_unit"]))
        factor_value = number(selected.get("factor_value")) if selected else None
        normalized_fuel = clean(selected.get("normalized_fuel")) if selected else clean(fuel["raw_fuel"])
        normalized_technology = clean(selected.get("normalized_technology")) if selected else clean(payload.get("combustion"))
        if act_value is None:
            flags.append("ACTIVITY_OR_UNIT_MISSING")
        if not selected:
            flags.append("FACTOR_UNMATCHED")
        control_value = number(control_efficiencies.get(item["pollutant"]))
        control_value = control_value if control_value is not None else 0.0
        decisions[item["item_id"]] = {
            "item_id": item["item_id"], "normalized_fuel": normalized_fuel,
            "normalized_technology": normalized_technology, "activity_value": act_value,
            "activity_unit": act_unit, "activity_formula": act_formula or "Agent policy activity",
            "parameter_id": clean(selected.get("parameter_id")) if selected else "",
            "mode": clean(selected.get("mode")) if selected else "agent_selected",
            "factor_value": factor_value, "factor_unit": clean(selected.get("factor_unit")) if selected else "",
            "factor_formula": clean(selected.get("factor_formula")) if selected else "Agent factor unmatched",
            "source_section": "Agent prompt judgment", "source_page": "",
            "control_technology": "Agent flat pollutant control assumption",
            "control_parameter_ids": [], "control_efficiency": control_value,
            "control_fine_efficiency": number((policy.get("pm_efficiencies") or {}).get("fine")) if item["pollutant"] == "PM10" else None,
            "control_coarse_efficiency": number((policy.get("pm_efficiencies") or {}).get("coarse")) if item["pollutant"] == "PM10" else None,
            "control_label": "Agent判断", "control_candidates": [], "operation_rate": 1.0, "flags": flags,
        }
    return decisions


def stage_agent_rules(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    del run_path
    upstream = require_stage(connection, "generate_pollutant_calculation_items")
    stage = "record_agent_rule_choices"
    revision = stage_revision(stage, request, manifest, upstream)
    if request.get("decision_file"):
        decisions = read_decisions(Path(request["decision_file"]).resolve())
    elif isinstance(request.get("agent_policy"), dict):
        decisions = agent_rule_decisions_from_policy(connection, request["agent_policy"])
    else:
        raise ValueError("record_agent_rule_choices requires decision_file or agent_policy")
    item_ids = {row["item_id"] for row in connection.execute("SELECT item_id FROM calculation_items")}
    recorded = 0
    complete = 0
    for item_id in sorted(item_ids):
        payload = decisions.get(item_id)
        if payload is None:
            payload = {
                "item_id": item_id,
                "flags": ["AGENT_RULE_DECISION_MISSING"],
                "control_label": "Agent规则选择缺失",
                "operation_rate": 1.0,
            }
        else:
            recorded += 1
        connection.execute("INSERT INTO agent_rule_choices VALUES(?,?)", (item_id, json.dumps(payload, ensure_ascii=False, sort_keys=True)))
        required_numeric = ("activity_value", "factor_value", "control_efficiency")
        if all(key in payload for key in required_numeric):
            complete += 1
        flags = payload.get("flags") or []
        if not isinstance(flags, list):
            flags = [str(flags)]
        if payload.get("activity_value") is None:
            flags.append("ACTIVITY_OR_UNIT_MISSING")
        if payload.get("factor_value") is None:
            flags.append("FACTOR_INPUT_MISSING")
        connection.execute(
            "INSERT INTO rule_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                clean(payload.get("normalized_fuel")),
                clean(payload.get("normalized_technology")),
                number(payload.get("activity_value")),
                clean(payload.get("activity_unit")),
                clean(payload.get("activity_formula")) or "Agent-selected activity",
                clean(payload.get("parameter_id")) or None,
                clean(payload.get("mode")) or "agent_selected",
                number(payload.get("factor_value")),
                clean(payload.get("factor_unit")),
                clean(payload.get("factor_formula")) or "Agent-selected factor",
                clean(payload.get("source_section")) or "Agent prompt judgment",
                clean(payload.get("source_page")),
                clean(payload.get("control_technology")),
                json_list(payload.get("control_parameter_ids")),
                number(payload.get("control_efficiency")) or 0.0,
                number(payload.get("control_fine_efficiency")),
                number(payload.get("control_coarse_efficiency")),
                clean(payload.get("control_label")) or "Agent判断",
                json_list(payload.get("control_candidates")),
                number(payload.get("operation_rate")) if number(payload.get("operation_rate")) is not None else 1.0,
                json.dumps(sorted(set(str(flag) for flag in flags if clean(flag))), ensure_ascii=False),
            ),
        )
    connection.commit()
    return commit_stage(connection, stage, revision, upstream, {
        "recorded_count": recorded,
        "complete_decision_count": complete,
        "item_count": len(item_ids),
        "coverage": recorded / len(item_ids) if item_ids else None,
        "can_continue": True,
    })


def stage_admission(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection, agent_managed: bool = False) -> dict[str, Any]:
    del run_path
    upstream_stage = "record_agent_admission_decisions" if agent_managed else ("record_agent_rule_choices" if request["scenario"] == "w_o_executable_rule_gate" else "execute_tcses_calculation_rules")
    if agent_managed:
        upstream_stage = "execute_tcses_calculation_rules"
    upstream = require_stage(connection, upstream_stage)
    stage = "record_agent_admission_decisions" if agent_managed else "control_calculation_admission"
    revision = stage_revision(stage, request, manifest, upstream)
    if agent_managed:
        if request.get("decision_file"):
            decisions = read_decisions(Path(request["decision_file"]).resolve())
        elif isinstance(request.get("agent_policy"), dict):
            policy = request["agent_policy"]
            withhold_flags = set(str(value) for value in policy.get("withhold_flags", []))
            not_applicable_flags = set(str(value) for value in policy.get("not_applicable_flags", []))
            default_action = clean(policy.get("default_action")) or "allow_calculation"
            decisions = {}
            for row in connection.execute("SELECT item_id,flags_json FROM rule_results ORDER BY item_id"):
                flags = [str(value) for value in json.loads(row["flags_json"] or "[]")]
                if withhold_flags.intersection(flags):
                    action = "withhold"
                    reason = sorted(withhold_flags.intersection(flags))[0]
                elif not_applicable_flags.intersection(flags):
                    action = "not_applicable"
                    reason = sorted(not_applicable_flags.intersection(flags))[0]
                else:
                    action = default_action
                    reason = "AGENT_POLICY_DEFAULT"
                decisions[row["item_id"]] = {"item_id": row["item_id"], "action": action, "reason_code": reason, "flags": flags}
        else:
            raise ValueError("record_agent_admission_decisions requires decision_file or agent_policy")
        for item in connection.execute("SELECT item_id FROM calculation_items"):
            payload = decisions.get(item["item_id"], {"item_id": item["item_id"], "action": "withhold", "reason_code": "AGENT_DECISION_MISSING", "flags": []})
            if payload.get("action") not in {"allow_calculation", "not_applicable", "withhold"}:
                payload = {
                    **payload,
                    "action": "withhold",
                    "reason_code": "AGENT_DECISION_INVALID",
                    "flags": [*(payload.get("flags") or []), "AGENT_DECISION_INVALID"],
                }
            connection.execute("INSERT INTO agent_admission_decisions VALUES(?,?)", (item["item_id"], json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            connection.execute("INSERT INTO admission_results VALUES(?,?,?,?)", (item["item_id"], payload["action"], payload.get("reason_code", "AGENT_DECISION"), json.dumps(payload.get("flags", []), ensure_ascii=False)))
    else:
        for row in connection.execute("SELECT i.item_id,i.item_type,i.pollutant,r.* FROM calculation_items i LEFT JOIN rule_results r ON r.item_id=i.item_id"):
            flags = json.loads(row["flags_json"] or "[]")
            action, reason = admission_disposition(row["item_type"], row["pollutant"], flags)
            connection.execute("INSERT INTO admission_results VALUES(?,?,?,?)", (row["item_id"], action, reason, json.dumps(flags, ensure_ascii=False)))
    connection.commit()
    counts = dict(Counter(row["action"] for row in connection.execute("SELECT action FROM admission_results")))
    return commit_stage(connection, stage, revision, upstream, {"admission_counts": counts, "can_continue": True})


def stage_calculate(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    del run_path
    admission_stage = "record_agent_admission_decisions" if request["scenario"] == "agent_managed_admission" else "control_calculation_admission"
    upstream = require_stage(connection, admission_stage)
    stage = "calculate_fixed_combustion_emissions"
    revision = stage_revision(stage, request, manifest, upstream)
    items = {row["item_id"]: row for row in connection.execute("SELECT * FROM calculation_items")}
    joined_rows = list(connection.execute(
        "SELECT a.*,r.* FROM admission_results a LEFT JOIN rule_results r ON r.item_id=a.item_id ORDER BY a.item_id"
    ))
    generation_cache: dict[str, float | None] = {}
    status_cache: dict[str, str] = {}
    for row in joined_rows:
        if row["action"] == "not_applicable":
            generation_cache[row["item_id"]] = 0.0
            status_cache[row["item_id"]] = "not_applicable_zero"
        elif row["action"] != "allow_calculation" or row["activity_value"] is None or row["factor_value"] is None:
            generation_cache[row["item_id"]] = None
            status_cache[row["item_id"]] = "withheld"
        else:
            generation_cache[row["item_id"]] = float(row["activity_value"]) * float(row["factor_value"]) / 1_000_000.0
            status_cache[row["item_id"]] = "generation_ready"
    for row in joined_rows:
        item = items[row["item_id"]]
        generation = generation_cache[row["item_id"]]
        if status_cache[row["item_id"]] == "not_applicable_zero":
            emission = 0.0
            status = "not_applicable_zero"
        elif status_cache[row["item_id"]] == "withheld":
            emission = None
            status = "withheld"
        else:
            if item["pollutant"] == "PM10" and item["dependency_id"]:
                fine_generation = generation_cache.get(item["dependency_id"])
                if fine_generation is None or fine_generation > generation + 1e-12:
                    emission = None
                    status = "pm_dependency_invalid"
                else:
                    fine_eff = float(row["control_fine_efficiency"] or 0.0) / 100.0
                    coarse_eff = float(row["control_coarse_efficiency"] or 0.0) / 100.0
                    emission = fine_generation * (1.0 - fine_eff) + (generation - fine_generation) * (1.0 - coarse_eff)
                    status = "calculated"
            else:
                efficiency = float(row["control_efficiency"] or 0.0) / 100.0
                emission = generation * (1.0 - efficiency)
                status = "calculated"
        connection.execute(
            "INSERT INTO calculations VALUES(?,?,?,?,?,?)",
            (
                row["item_id"], generation, emission,
                "activity_value*factor_value/1000000",
                "PM25*(1-eff_fine)+(PM10-PM25)*(1-eff_coarse)" if item["pollutant"] == "PM10" else "generation*(1-control_efficiency/100)",
                status,
            ),
        )
    connection.commit()
    counts = dict(Counter(row["calculation_status"] for row in connection.execute("SELECT calculation_status FROM calculations")))
    return commit_stage(connection, stage, revision, upstream, {"calculation_counts": counts, "can_continue": True})


def excel_headers() -> list[str]:
    return [
        "核算项ID", "源ID", "目标源类", "企业", "地市", "行业代码", "设备类型", "排放口",
        "燃料槽位", "原始燃料", "标准燃料", "标准技术", "污染物", "原始消耗量", "原始单位",
        "标准活动水平", "活动水平单位", "活动水平公式", "参数ID", "因子模式", "有效因子", "因子单位",
        "因子公式", "治理技术", "治理参数ID", "去除效率(%)", "PM2.5去除效率(%)", "PM2.5-10去除效率(%)",
        "产生量(t)", "排放量(t)", "准入状态", "准入原因", "标志", "标准位置", "源文件", "源工作表", "源行",
    ]


def write_inventory_workbook(path: Path, target: str, connection: sqlite3.Connection) -> dict[str, Any]:
    rows = list(connection.execute(
        """
        SELECT i.*,d.target,d.department,d.payload_json,r.*,a.action,a.reason_code,c.generation_t,c.emission_t,
               rr.source_path,rr.source_sheet,rr.source_row,f.slot,f.raw_fuel,f.amount,f.raw_unit
        FROM calculation_items i
        JOIN device_sources d ON d.source_id=i.source_id
        JOIN raw_records rr ON rr.raw_id=d.raw_id
        LEFT JOIN fuels f ON f.fuel_id=i.fuel_id
        LEFT JOIN rule_results r ON r.item_id=i.item_id
        LEFT JOIN admission_results a ON a.item_id=i.item_id
        LEFT JOIN calculations c ON c.item_id=i.item_id
        WHERE d.target=? ORDER BY i.source_id,i.item_id
        """, (target,)
    ))
    workbook = xlsxwriter.Workbook(path)
    workbook.set_calc_mode("auto")
    header_fmt = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#1F6D70", "border": 1, "text_wrap": True, "valign": "vcenter"})
    input_fmt = workbook.add_format({"bg_color": "#FFF2CC", "border": 1})
    formula_fmt = workbook.add_format({"bg_color": "#E2F0D9", "border": 1, "num_format": "0.000000"})
    text_fmt = workbook.add_format({"border": 1})
    note = workbook.add_worksheet("00_说明")
    note.write_row(0, 0, ["项目", "内容"], header_fmt)
    note.write_row(1, 0, ["文件定位", "实验计分用轻量工作簿；完整标准答案由非计分展示运行另行导出。"], text_fmt)
    note.write_row(2, 0, ["计算依据", "同一运行数据库、冻结规则包和核算准入结果。"], text_fmt)
    note.write_row(3, 0, ["公式范围", "活动水平、产生量、治理后排放量和源级汇总保留Excel公式；完整因子匹配公式见展示运行。"], text_fmt)
    note.set_column(0, 0, 22)
    note.set_column(1, 1, 86)
    sheet = workbook.add_worksheet("02_核算项")
    headers = excel_headers()
    for col, name in enumerate(headers):
        sheet.write(0, col, name, header_fmt)
    item_row_index: dict[str, int] = {row["item_id"]: index + 2 for index, row in enumerate(rows)}
    for index, row in enumerate(rows, start=1):
        payload = json.loads(row["payload_json"])
        flags = json.loads(row["flags_json"] or "[]")
        values = [
            row["item_id"], row["source_id"], row["target"], payload.get("company"), payload.get("city"), payload.get("industry_code"),
            payload.get("equipment"), payload.get("outlet_id"), row["slot"], row["raw_fuel"], row["normalized_fuel"], row["normalized_technology"],
            row["pollutant"], row["amount"], row["raw_unit"], row["activity_value"], row["activity_unit"], row["activity_formula"],
            row["parameter_id"], row["mode"], row["factor_value"], row["factor_unit"], row["factor_formula"], row["control_technology"],
            row["control_parameter_ids"], row["control_efficiency"], row["control_fine_efficiency"], row["control_coarse_efficiency"],
            row["generation_t"], row["emission_t"], row["action"], row["reason_code"], "|".join(flags),
            f"{row['source_section'] or ''} p.{row['source_page'] or ''}", row["source_path"], row["source_sheet"], row["source_row"],
        ]
        for col, cell_value in enumerate(values):
            sheet.write(index, col, cell_value, input_fmt if col in {13, 14} else text_fmt)
        excel_row = index + 1
        if row["activity_value"] is not None:
            activity_formula = f'=IF(O{excel_row}="吨",N{excel_row}*1000,IF(O{excel_row}="万立方米",N{excel_row}*10000,""))'
            sheet.write_formula(index, 15, activity_formula, formula_fmt, row["activity_value"])
        if row["factor_value"] is not None:
            sheet.write_formula(index, 20, f"={float(row['factor_value'])}", formula_fmt, row["factor_value"])
        if row["generation_t"] is not None:
            sheet.write_formula(index, 28, f"=P{excel_row}*U{excel_row}/1000000", formula_fmt, row["generation_t"])
        if row["emission_t"] is not None:
            if row["pollutant"] == "PM10" and row["dependency_id"] in item_row_index:
                dependency_row = item_row_index[row["dependency_id"]]
                formula = f"=AC{dependency_row}*(1-AA{excel_row}/100)+(AC{excel_row}-AC{dependency_row})*(1-AB{excel_row}/100)"
            else:
                formula = f"=AC{excel_row}*(1-Z{excel_row}/100)"
            sheet.write_formula(index, 29, formula, formula_fmt, row["emission_t"])
    sheet.freeze_panes(1, 8)
    sheet.autofilter(0, 0, len(rows), len(headers) - 1)
    sheet.set_column(0, 2, 28)
    sheet.set_column(3, 7, 20)
    sheet.set_column(8, 34, 17)
    sheet.set_column(35, 36, 40)

    result_sheet = workbook.add_worksheet("03_源结果")
    result_headers = ["源ID", "企业", "地市", "行业代码", "设备类型", "排放口"] + [f"{p}_产生量(t)" for p in POLLUTANTS] + [f"{p}_排放量(t)" for p in POLLUTANTS] + ["异常标志数"]
    for col, name in enumerate(result_headers):
        result_sheet.write(0, col, name, header_fmt)
    sources = list(connection.execute("SELECT * FROM device_sources WHERE target=? ORDER BY source_id", (target,)))
    for index, source in enumerate(sources, start=1):
        payload = json.loads(source["payload_json"])
        base = [source["source_id"], payload.get("company"), payload.get("city"), payload.get("industry_code"), payload.get("equipment"), payload.get("outlet_id")]
        for col, cell_value in enumerate(base):
            result_sheet.write(index, col, cell_value, text_fmt)
        source_rows = [row for row in rows if row["source_id"] == source["source_id"]]
        for p_index, pollutant in enumerate(POLLUTANTS):
            generation = sum(float(row["generation_t"] or 0.0) for row in source_rows if row["pollutant"] == pollutant and row["generation_t"] is not None)
            emissions = [row["emission_t"] for row in source_rows if row["pollutant"] == pollutant]
            emission = sum(float(value) for value in emissions if value is not None) if any(value is not None for value in emissions) else None
            generation_col = 6 + p_index
            emission_col = 6 + len(POLLUTANTS) + p_index
            source_excel_row = index + 1
            item_source_range = f"'02_核算项'!$B$2:$B${len(rows)+1}"
            pollutant_range = f"'02_核算项'!$M$2:$M${len(rows)+1}"
            gen_range = f"'02_核算项'!$AC$2:$AC${len(rows)+1}"
            emission_range = f"'02_核算项'!$AD$2:$AD${len(rows)+1}"
            result_sheet.write_formula(index, generation_col, f'=SUMIFS({gen_range},{item_source_range},$A{source_excel_row},{pollutant_range},"{pollutant}")', formula_fmt, generation)
            if emission is not None:
                result_sheet.write_formula(index, emission_col, f'=SUMIFS({emission_range},{item_source_range},$A{source_excel_row},{pollutant_range},"{pollutant}")', formula_fmt, emission)
        exception_count = sum(len(json.loads(row["flags_json"] or "[]")) for row in source_rows)
        result_sheet.write(index, len(result_headers) - 1, exception_count, text_fmt)
    result_sheet.freeze_panes(1, 1)
    result_sheet.autofilter(0, 0, len(sources), len(result_headers) - 1)
    result_sheet.set_column(0, 0, 25)
    result_sheet.set_column(1, 5, 20)
    result_sheet.set_column(6, len(result_headers) - 1, 16)
    workbook.close()
    return {
        "path": str(path), "sha256": sha256_file(path), "source_count": len(sources),
        "item_count": len(rows), "sheet_count": 3, "export_mode": "machine",
    }


def verify_machine_workbook(path: Path) -> dict[str, Any]:
    formulas = load_workbook(path, read_only=True, data_only=False)
    formula_count = sum(
        1
        for sheet in formulas.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if isinstance(value, str) and value.startswith("=")
    )
    sheet_names = list(formulas.sheetnames)
    external_link_count = len(getattr(formulas, "_external_links", []))
    formulas.close()
    values = load_workbook(path, read_only=True, data_only=True)
    formula_errors = sum(
        1
        for sheet in values.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if isinstance(value, str) and value.startswith("#")
    )
    values.close()
    return {
        "formula_count": formula_count,
        "formula_error_count": formula_errors,
        "external_link_count": external_link_count,
        "sheet_names": sheet_names,
    }


def public_item_status(action: str, calculation_status: str, reason_code: str) -> str:
    if action == "not_applicable" or calculation_status == "not_applicable_zero":
        return "not_involved"
    if action == "allow_calculation" and calculation_status == "calculated":
        return "calculated"
    invalid_reasons = {
        "SOURCE_RELATION_MISSING",
        "LOW_NOX_FIELD_CONFLICT",
        "ACTIVITY_OUT_OF_RANGE",
        "SULFUR_OUT_OF_RANGE",
        "ASH_OUT_OF_RANGE",
        "CAPACITY_OUT_OF_RANGE",
    }
    return "source_data_invalid" if reason_code in invalid_reasons else "information_insufficient"


def write_common_result_tables(output_dir: Path, connection: sqlite3.Connection) -> dict[str, Any]:
    decisions_path = output_dir / "source_decisions.csv"
    with decisions_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_id",
                "requested_target",
                "decided_target",
                "disposition",
                "reason_code",
                "source_path",
                "source_sheet",
                "source_row",
            ]
        )
        for row in connection.execute(
            """
            SELECT s.*,r.source_path,r.source_sheet,r.source_row
            FROM source_decisions s JOIN raw_records r ON r.raw_id=s.raw_id
            ORDER BY s.source_id
            """
        ):
            writer.writerow(
                [
                    row["source_id"],
                    row["requested_target"],
                    row["decided_target"],
                    row["disposition"],
                    row["reason"],
                    row["source_path"],
                    row["source_sheet"],
                    row["source_row"],
                ]
            )

    item_path = output_dir / "calculation_items.csv"
    item_rows = list(
        connection.execute(
            """
            SELECT i.*,d.target,r.normalized_fuel,r.normalized_technology,
                   r.activity_value,r.activity_unit,r.activity_formula,r.parameter_id,
                   r.mode,r.factor_value,r.factor_unit,r.factor_formula,
                   r.control_technology,r.control_parameter_ids,r.control_efficiency,
                   r.control_fine_efficiency,r.control_coarse_efficiency,r.flags_json,
                   a.action,a.reason_code,c.generation_t,c.emission_t,c.calculation_status
            FROM calculation_items i
            JOIN device_sources d ON d.source_id=i.source_id
            LEFT JOIN rule_results r ON r.item_id=i.item_id
            LEFT JOIN admission_results a ON a.item_id=i.item_id
            LEFT JOIN calculations c ON c.item_id=i.item_id
            ORDER BY i.item_id
            """
        )
    )
    item_headers = [
        "item_id",
        "source_id",
        "target",
        "fuel_id",
        "item_type",
        "pollutant",
        "status",
        "reason_code",
        "normalized_fuel",
        "normalized_technology",
        "activity_value",
        "activity_unit",
        "activity_formula",
        "parameter_id",
        "factor_mode",
        "factor_value",
        "factor_unit",
        "factor_formula",
        "control_technology",
        "control_parameter_ids",
        "control_efficiency",
        "control_fine_efficiency",
        "control_coarse_efficiency",
        "generation_t",
        "emission_t",
        "flags",
    ]
    normalized_items: list[dict[str, Any]] = []
    with item_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=item_headers)
        writer.writeheader()
        for row in item_rows:
            status = public_item_status(
                clean(row["action"]), clean(row["calculation_status"]), clean(row["reason_code"])
            )
            record = {
                "item_id": row["item_id"],
                "source_id": row["source_id"],
                "target": row["target"],
                "fuel_id": row["fuel_id"],
                "item_type": row["item_type"],
                "pollutant": row["pollutant"],
                "status": status,
                "reason_code": row["reason_code"],
                "normalized_fuel": row["normalized_fuel"],
                "normalized_technology": row["normalized_technology"],
                "activity_value": row["activity_value"],
                "activity_unit": row["activity_unit"],
                "activity_formula": row["activity_formula"],
                "parameter_id": row["parameter_id"],
                "factor_mode": row["mode"],
                "factor_value": row["factor_value"],
                "factor_unit": row["factor_unit"],
                "factor_formula": row["factor_formula"],
                "control_technology": row["control_technology"],
                "control_parameter_ids": row["control_parameter_ids"],
                "control_efficiency": row["control_efficiency"],
                "control_fine_efficiency": row["control_fine_efficiency"],
                "control_coarse_efficiency": row["control_coarse_efficiency"],
                "generation_t": row["generation_t"] if status == "calculated" else None,
                "emission_t": row["emission_t"] if status == "calculated" else None,
                "flags": row["flags_json"],
            }
            normalized_items.append(record)
            writer.writerow(record)

    by_source_pollutant: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in normalized_items:
        by_source_pollutant[(record["source_id"], record["target"], record["pollutant"])].append(record)
    totals_path = output_dir / "source_pollutant_totals.csv"
    total_headers = [
        "source_id",
        "target",
        "pollutant",
        "status",
        "generation_t",
        "emission_t",
        "component_count",
        "reason_codes",
    ]
    with totals_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=total_headers)
        writer.writeheader()
        for (source_id, target, pollutant), records in sorted(by_source_pollutant.items()):
            statuses = {record["status"] for record in records}
            calculated = [record for record in records if record["status"] == "calculated"]
            blockers = statuses.intersection({"information_insufficient", "source_data_invalid"})
            if blockers:
                status = "source_data_invalid" if "source_data_invalid" in blockers else "information_insufficient"
                generation = emission = None
            elif calculated:
                status = "calculated"
                generation = sum(float(record["generation_t"] or 0.0) for record in calculated)
                emission = sum(float(record["emission_t"] or 0.0) for record in calculated)
            else:
                status = "not_involved"
                generation = emission = None
            writer.writerow(
                {
                    "source_id": source_id,
                    "target": target,
                    "pollutant": pollutant,
                    "status": status,
                    "generation_t": generation,
                    "emission_t": emission,
                    "component_count": len(records),
                    "reason_codes": json.dumps(
                        sorted({clean(record["reason_code"]) for record in records if clean(record["reason_code"])}),
                        ensure_ascii=False,
                    ),
                }
            )
    return {
        "source_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        "calculation_items": {"path": str(item_path), "sha256": sha256_file(item_path)},
        "source_pollutant_totals": {"path": str(totals_path), "sha256": sha256_file(totals_path)},
    }


def stage_validate_export(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    upstream = require_stage(connection, "calculate_fixed_combustion_emissions")
    stage = "validate_and_export_fixed_combustion_inventory"
    revision = stage_revision(stage, request, manifest, upstream)
    findings: list[tuple[str, str | None, str | None, str, str, str]] = []
    for row in connection.execute("SELECT i.item_id,i.source_id,i.pollutant,c.generation_t,c.emission_t,r.flags_json FROM calculation_items i LEFT JOIN calculations c ON c.item_id=i.item_id LEFT JOIN rule_results r ON r.item_id=i.item_id"):
        flags = json.loads(row["flags_json"] or "[]")
        if row["generation_t"] is not None and row["generation_t"] < -1e-12:
            findings.append((f"Q-{row['item_id']}-NEGGEN", row["source_id"], row["item_id"], "error", "NEGATIVE_GENERATION", "generation below zero"))
        if row["emission_t"] is not None and row["emission_t"] < -1e-12:
            findings.append((f"Q-{row['item_id']}-NEGEM", row["source_id"], row["item_id"], "error", "NEGATIVE_EMISSION", "emission below zero"))
        # Empirical control dispositions are ordinary labelled outcomes, not
        # exceptions. Only numerical contradictions and foundationally
        # withheld calculation items enter the final exception list.
    pm = defaultdict(dict)
    for row in connection.execute("SELECT i.source_id,i.fuel_id,i.pollutant,c.generation_t FROM calculation_items i JOIN calculations c ON c.item_id=i.item_id WHERE i.pollutant IN ('PM10','PM25')"):
        pm[(row["source_id"], row["fuel_id"])][row["pollutant"]] = row["generation_t"]
    for key, values in pm.items():
        if values.get("PM10") is not None and values.get("PM25") is not None and values["PM25"] > values["PM10"] + 1e-12:
            findings.append((f"Q-{key[0]}-{key[1]}-PM", key[0], None, "error", "PM_SIZE_RELATION", "PM2.5 exceeds PM10"))
    for finding in findings:
        connection.execute("INSERT OR IGNORE INTO quality_findings VALUES(?,?,?,?,?,?)", finding)
    connection.commit()
    output_dir = run_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    package = rule_path(manifest)
    export_mode = clean(manifest.get("export_mode")) or "presentation"
    builder = Path(__file__).with_name("build_compact_workbooks.mjs")
    bundled_node = Path("/Users/wushuo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")
    node = str(bundled_node if bundled_node.is_file() else (shutil.which("node") or "node"))
    bundled_modules = Path("/Users/wushuo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")
    workbook_specs = (
        ("INDUSTRIAL", "固定燃烧源-工业锅炉_实验结果.xlsx"),
        ("POWER", "固定燃烧源-火电、热力生产与供应_实验结果.xlsx"),
    )
    workbooks: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for target, filename in workbook_specs:
        payload = build_payload(target, connection, package, manifest)
        payloads[target] = payload
        payload_path = output_dir / f"workbook_payload_{target.lower()}.json"
        preview_dir = output_dir / "previews" / target.lower()
        workbook_path = output_dir / filename
        if export_mode == "machine":
            qa_path = output_dir / f"machine_qa_{target.lower()}.json"
            if workbook_path.is_file() and qa_path.is_file():
                builder_result = {
                    "success": True,
                    "resumed_from_completed_workbook": True,
                    "output_path": str(workbook_path),
                    "sheet_count": 3,
                    "export_mode": "machine",
                }
                workbook_qa = read_json(qa_path)
            else:
                builder_result = write_inventory_workbook(workbook_path, target, connection)
                workbook_qa = verify_machine_workbook(workbook_path)
                write_json(qa_path, workbook_qa)
        else:
            write_json(payload_path, payload)
            qa_path = preview_dir / "qa.json"
            if workbook_path.is_file() and qa_path.is_file():
                builder_result = {
                    "success": True,
                    "resumed_from_completed_workbook": True,
                    "output_path": str(workbook_path),
                    "preview_dir": str(preview_dir),
                    "sheet_count": 16,
                }
            else:
                environment = dict(os.environ)
                if bundled_modules.is_dir():
                    environment["NODE_PATH"] = str(bundled_modules)
                completed = subprocess.run(
                    [node, "--max-old-space-size=12288", str(builder), str(payload_path), str(workbook_path), str(preview_dir)],
                    cwd=str(builder.parent.parent), env=environment, text=True, capture_output=True, timeout=3600, check=False,
                )
                if completed.returncode:
                    raise RuntimeError(f"workbook export failed for {target}: {completed.stderr or completed.stdout}")
                lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
                try:
                    builder_result = json.loads(lines[-1]) if lines else None
                except json.JSONDecodeError:
                    builder_result = None
                if not isinstance(builder_result, dict):
                    builder_result = {
                        "success": True,
                        "output_path": str(workbook_path),
                        "preview_dir": str(preview_dir),
                        "sheet_count": 16,
                        "stdout_summary_missing": True,
                    }
            workbook_qa = {"presentation_qa_path": str(qa_path)}
        workbooks.append({
            "target": target, "path": str(workbook_path), "sha256": sha256_file(workbook_path),
            "source_count": len(payload["sources"]), "item_count": payload["calculation_item_count"],
            "preview_dir": str(preview_dir) if export_mode == "presentation" else None,
            "qa_path": str(qa_path), "workbook_qa": workbook_qa,
            "builder_result": builder_result, "export_mode": export_mode,
        })
    exception_path = output_dir / "最终异常清单.csv"
    with exception_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(payloads["INDUSTRIAL"]["exception_headers"])
        writer.writerows(payloads["INDUSTRIAL"]["exceptions"])
        writer.writerows(payloads["POWER"]["exceptions"])
    normalized_exception_path = output_dir / "exceptions.csv"
    with normalized_exception_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["exception_id", "source_id", "target", "root_cause", "affected_pollutants", "action_required", "detail"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for target in ("INDUSTRIAL", "POWER"):
            headers = payloads[target]["exception_headers"]
            for raw in payloads[target]["exceptions"]:
                record = dict(zip(headers, raw))
                source_id = clean(record.get("源ID"))
                root_cause = clean(record.get("准入原因"))
                writer.writerow(
                    {
                        "exception_id": canonical_hash([source_id, target, root_cause])[:20],
                        "source_id": source_id,
                        "target": target,
                        "root_cause": root_cause,
                        "affected_pollutants": clean(record.get("受影响污染物")),
                        "action_required": clean(record.get("处置")),
                        "detail": clean(record.get("说明")),
                    }
                )
    common_tables = write_common_result_tables(output_dir, connection)
    quality = {
        "error_count": connection.execute("SELECT COUNT(*) FROM quality_findings WHERE severity='error'").fetchone()[0],
        "warning_count": connection.execute("SELECT COUNT(*) FROM quality_findings WHERE severity='warning'").fetchone()[0],
        "exception_path": str(exception_path),
        "exception_sha256": sha256_file(exception_path),
        "normalized_exception_path": str(normalized_exception_path),
        "normalized_exception_sha256": sha256_file(normalized_exception_path),
        "exception_group_count": len(payloads["INDUSTRIAL"]["exceptions"]) + len(payloads["POWER"]["exceptions"]),
        "workbooks": workbooks, "common_tables": common_tables, "export_mode": export_mode,
    }
    write_json(output_dir / "quality_summary.json", quality)
    return commit_stage(connection, stage, revision, upstream, {"outputs": quality, "can_continue": quality["error_count"] == 0})


def stage_archive(request: dict[str, Any], run_path: Path, manifest: dict[str, Any], connection: sqlite3.Connection, minimal: bool = False) -> dict[str, Any]:
    upstream = require_stage(connection, "validate_and_export_fixed_combustion_inventory")
    stage = "archive_minimal_calculation_process" if minimal else "archive_complete_calculation_process"
    revision = stage_revision(stage, request, manifest, upstream)
    trace_dir = run_path / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / ("minimal_stage_log.jsonl" if minimal else "complete_calculation_process.jsonl")
    if minimal:
        rows = connection.execute("SELECT * FROM stage_commits ORDER BY rowid")
    else:
        rows = connection.execute(
            """
            SELECT i.*,d.target,d.department,d.payload_json,rr.source_path,rr.source_sheet,rr.source_row,
                   f.slot,f.raw_fuel,f.amount,f.raw_unit,f.sulfur,f.sulfur_unit,f.ash,
                   r.normalized_fuel,r.normalized_technology,r.activity_value,r.activity_unit,r.activity_formula,
                   r.parameter_id,r.mode,r.factor_value,r.factor_unit,r.factor_formula,r.source_section,r.source_page,
                   r.control_technology,r.control_parameter_ids,r.control_efficiency,r.control_fine_efficiency,r.control_coarse_efficiency,r.flags_json,
                   a.action,a.reason_code,c.generation_t,c.emission_t,c.generation_formula,c.emission_formula,c.calculation_status
            FROM calculation_items i JOIN device_sources d ON d.source_id=i.source_id
            JOIN raw_records rr ON rr.raw_id=d.raw_id LEFT JOIN fuels f ON f.fuel_id=i.fuel_id
            LEFT JOIN rule_results r ON r.item_id=i.item_id LEFT JOIN admission_results a ON a.item_id=i.item_id
            LEFT JOIN calculations c ON c.item_id=i.item_id ORDER BY i.item_id
            """
        )
    count = 0
    with trace_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    sealed = dict(manifest)
    sealed["sealed"] = True
    sealed["engine_version"] = ENGINE_VERSION
    sealed["final_stage"] = stage
    sealed["final_revision"] = revision
    sealed["trace"] = {"path": str(trace_path), "sha256": sha256_file(trace_path), "record_count": count, "mode": "minimal" if minimal else "complete"}
    write_json(run_path / "sealed_manifest.json", sealed)
    return commit_stage(connection, stage, revision, upstream, {"trace": sealed["trace"], "sealed": True})


STAGES = {
    "adapt_environmental_workbooks": stage_adapt,
    "build_device_emission_sources": stage_sources,
    "generate_pollutant_calculation_items": stage_items,
    "generate_device_pollutant_slots": lambda request, run_path, manifest, connection: stage_items(request, run_path, manifest, connection, True),
    "execute_tcses_calculation_rules": stage_rules,
    "summarize_agent_rule_options": stage_agent_rule_options,
    "record_agent_rule_choices": stage_agent_rules,
    "control_calculation_admission": stage_admission,
    "summarize_agent_admission_options": stage_agent_admission_options,
    "record_agent_admission_decisions": lambda request, run_path, manifest, connection: stage_admission(request, run_path, manifest, connection, True),
    "calculate_fixed_combustion_emissions": stage_calculate,
    "validate_and_export_fixed_combustion_inventory": stage_validate_export,
    "archive_complete_calculation_process": stage_archive,
    "archive_minimal_calculation_process": lambda request, run_path, manifest, connection: stage_archive(request, run_path, manifest, connection, True),
}


def main() -> None:
    request = json.load(sys.stdin)
    stage = clean(request.get("stage"))
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    run_path, manifest, connection = load_context(request)
    try:
        result = STAGES[stage](request, run_path, manifest, connection)
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
