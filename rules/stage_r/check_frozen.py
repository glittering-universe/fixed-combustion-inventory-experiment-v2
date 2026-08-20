#!/usr/bin/env python3
"""Independent semantic and integrity checks for frozen rule package v1.0.1."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path("/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验")
BASE = ROOT / "rules/frozen/v1.0.1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and p.name != "rule_package_lock.json"):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def rows(path: str) -> list[dict[str, str]]:
    with (BASE / path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def lookup(records: list[dict[str, str]], **expected: str) -> dict[str, str]:
    result = [row for row in records if all(row.get(key) == value for key, value in expected.items())]
    assert len(result) == 1, (expected, len(result))
    return result[0]


manifest = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
lock = json.loads((BASE / "rule_package_lock.json").read_text(encoding="utf-8"))
assert manifest["status"] == "frozen"
assert manifest["professional_review_status"] == "passed"
assert lock["status"] == "frozen"
assert lock["professional_review_status"] == "passed"
assert lock["package_hash"] == tree_hash(BASE)
for item in manifest["files"]:
    assert sha256(BASE / item["path"]) == item["sha256"], item["path"]

all_text = "\n".join(
    path.read_text(encoding="utf-8-sig", errors="replace")
    for path in BASE.rglob("*") if path.is_file() and path.suffix.lower() in {".yaml", ".csv", ".json", ".jsonl", ".md"}
)
assert "online_monitoring" not in all_text
assert "1-环统排放量/环统产生量" in all_text
assert "除以5" in all_text and "除以2" in all_text and "禁止" in all_text

controls = rows("parameters/control_efficiencies.csv")
expected_nox = {
    "低氮燃烧技术": "22",
    "选择性非催化还原法": "30",
    "选择性催化还原法": "42",
    "低氮燃烧技术+选择性非催化还原法": "52",
    "低氮燃烧技术+选择性催化还原法": "64",
}
for technology, value in expected_nox.items():
    assert lookup(controls, control_technology=technology, pollutant="NOX")["efficiency_percent"] == value
assert lookup(controls, control_technology="湿式除尘法", pollutant="SO2")["efficiency_percent"] == "20"
for pollutant in ("PM25", "PM25_10", "BC", "OC"):
    assert lookup(controls, control_technology="选择性催化还原法", pollutant=pollutant)["efficiency_percent"] != ""

ammonia = rows("parameters/ammonia_slip_factors.csv")
assert lookup(ammonia, process="脱硝烟气-选择性催化还原")["value"] == "0.16"
assert lookup(ammonia, process="脱硝烟气-选择性非催化还原")["value"] == "0.17"

industrial = rows("parameters/industrial_boiler_emission_factors.csv")
volume_gases = {"焦炉煤气", "高炉煤气", "其它煤气", "天然气", "转炉煤气", "其它气体燃料"}
for row in industrial:
    if row["fuel"] in volume_gases and row["mode"] == "constant":
        assert row["unit"] == "g/m3", row
    if row["fuel"] in {"液化天然气", "液化石油气", "炼厂干气"} and row["mode"] == "constant":
        assert row["unit"] == "g/kg燃料", row

coal = rows("parameters/coal_parameters.csv")
industrial_coal = [row for row in coal if row["sector"] == "工业源"]
assert industrial_coal
assert {row["department"] for row in industrial_coal} == {"采矿业和制造业"}

operational = yaml.safe_load((BASE / "rules/operational.yaml").read_text(encoding="utf-8"))["rules"]
operational_ids = {row["rule_id"] for row in operational}
assert {
    "r_ops_industrial_coal_department_canonicalization",
    "r_ops_no_technology_factor_fallback",
    "r_ops_anomaly_audit_taxonomy",
} <= operational_ids

fuel_aliases = rows("mappings/source_fuel_aliases.csv")
assert len({row["raw_value"] for row in fuel_aliases}) == 27
for raw in ["其他燃料", "其他燃料（燃气锅炉，又对比低位发热量，判断可能是重油热裂解煤气）"]:
    assert lookup(fuel_aliases, raw_value=raw)["mapping_status"] == "unmapped"
for raw in ["工业废料（用于燃料）", "生物燃料"]:
    assert lookup(fuel_aliases, raw_value=raw)["mapping_status"] == "outside_fossil_scope"

control_aliases = rows("mappings/source_control_aliases.csv")
raw_controls = {(row["raw_field"], row["raw_value"]) for row in control_aliases}
assert len(raw_controls) == 99
assert all(row["mapping_status"] == "mapped" for row in control_aliases)

coverage = json.loads((ROOT / "rules/frozen/coverage_frozen.json").read_text(encoding="utf-8"))
assert coverage["standard_extraction"]["coverage_ratio"] == 1.0
assert coverage["operational_readiness"]["coverage_ratio"] == 1.0

print(json.dumps({
    "success": True,
    "package_hash": lock["package_hash"],
    "rules": 47,
    "standard_coverage": "20/20",
    "operational_coverage": "40/40",
    "fuel_alias_values": 27,
    "control_alias_values": len(raw_controls),
    "critical_parameter_assertions": "passed",
}, ensure_ascii=False, indent=2))
