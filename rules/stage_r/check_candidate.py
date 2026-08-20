# -*- coding: utf-8 -*-
"""Final structural self-check of the Stage-R candidate package."""
import io, csv, json, yaml, os, hashlib

base = "/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验/rules/stage_r/candidate"
os.chdir(base)

mani = yaml.safe_load(io.open("manifest.yaml", encoding="utf-8"))
assert mani["package_id"] == "tcses-144-2024-fixed-combustion"
assert mani["status"] == "awaiting_professional_review"
assert mani["standard"]["sha256"] == "bc46346443e76ccf063ae5e2b1d1c5d7a8544fb4f7cf6b11239c80bca66181a9"
missing = [f["path"] for f in mani["files"] if not os.path.exists(f["path"])]
assert not missing, missing
bad = [f["path"] for f in mani["files"]
       if hashlib.sha256(open(f["path"], "rb").read()).hexdigest() != f["sha256"]]
assert not bad, bad

rule_ids = set()
for fn in os.listdir("rules"):
    doc = yaml.safe_load(io.open(os.path.join("rules", fn), encoding="utf-8"))
    for r in doc["rules"]:
        rule_ids.add(r["rule_id"])
tests = [json.loads(l) for l in io.open("tests/rule_tests.jsonl", encoding="utf-8") if l.strip()]
bad_t = [t["test_id"] for t in tests if t["rule_id"] not in rule_ids]
assert not bad_t, bad_t

ok_types = {"scope", "method", "formula", "control", "quality_control"}
ok_actions = {"select_scope", "select_method", "assign_formula", "assign_control", "check_quality"}
per_file = {}
for fn in sorted(os.listdir("rules")):
    doc = yaml.safe_load(io.open(os.path.join("rules", fn), encoding="utf-8"))
    per_file[fn] = len(doc["rules"])
    for r in doc["rules"]:
        assert r["rule_type"] in ok_types, (fn, r["rule_id"])
        assert r["action"]["type"] in ok_actions, (fn, r["rule_id"])
        assert "physical_pdf_pages" in r["source"] and r["source"]["physical_pdf_pages"], (fn, r["rule_id"])

heads = {
    "parameters/power_heat_emission_factors.csv": ["parameter_id", "sector", "department", "fuel", "combustion_technology", "pollutant", "mode", "value", "unit", "capacity_condition", "source_section", "physical_pdf_page"],
    "parameters/industrial_boiler_emission_factors.csv": ["parameter_id", "sector", "department", "fuel", "combustion_technology", "pollutant", "mode", "value", "unit", "capacity_condition", "source_section", "physical_pdf_page"],
    "parameters/coal_parameters.csv": ["parameter_id", "sector", "department", "combustion_technology", "sulfur_to_bottom_ash", "ash_to_bottom_ash", "pm25_fraction", "pm10_fraction", "bc_fraction_of_pm25", "oc_fraction_of_pm25", "source_section", "physical_pdf_page"],
    "parameters/control_efficiencies.csv": ["parameter_id", "control_technology", "pollutant", "efficiency_percent", "particle_fraction", "source_section", "physical_pdf_page"],
    "parameters/ammonia_slip_factors.csv": ["parameter_id", "process", "activity_fuel", "pollutant", "value", "unit", "source_section", "physical_pdf_page"],
    "mappings/standard_fuels.csv": ["standard_value", "category", "source_section", "physical_pdf_page"],
    "mappings/standard_combustion_technologies.csv": ["standard_value", "category", "source_section", "physical_pdf_page"],
    "mappings/standard_control_technologies.csv": ["standard_value", "category", "source_section", "physical_pdf_page"],
}
for fn, hdr in heads.items():
    with io.open(fn, encoding="utf-8") as f:
        got = next(csv.reader(f))
    assert got == hdr, (fn, got)

with io.open("parameters/coal_parameters.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
keys = ["sulfur_to_bottom_ash", "ash_to_bottom_ash", "pm25_fraction", "pm10_fraction", "bc_fraction_of_pm25", "oc_fraction_of_pm25"]
empties = [(r["parameter_id"], k) for r in rows for k in keys if r[k] == ""]
assert empties == [("C-04", "bc_fraction_of_pm25"), ("C-04", "oc_fraction_of_pm25")], empties

with io.open("parameters/power_heat_emission_factors.csv", encoding="utf-8") as f:
    ph = list(csv.DictReader(f))
for r in ph:
    if r["mode"] in ("coal_sulfur_balance", "coal_particle_balance", "capacity_lookup"):
        assert r["value"] == "", r
    else:
        assert r["value"] != "", r

print("ALL CHECKS PASSED")
print("rules:", len(rule_ids), "| tests:", len(tests), "| per-file rules:", per_file)
