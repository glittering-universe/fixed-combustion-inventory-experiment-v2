#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "reference" / "build_reference_package.py"
SPEC = importlib.util.spec_from_file_location("build_reference_package_v2", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class IndependentSourceClassificationTests(unittest.TestCase):
    def test_power_production_codes_are_independent_of_input_equipment_column(self) -> None:
        for code in ("4411", "4412", "4417"):
            decision = MODULE.classify_raw_source(code, "燃气锅炉", "")
            self.assertEqual((decision.target, decision.department), ("POWER", "电力生产"))

    def test_heat_code_has_its_own_department(self) -> None:
        decision = MODULE.classify_raw_source("4430", "", "燃煤锅炉")
        self.assertEqual((decision.target, decision.department), ("POWER", "热力生产和供应"))

    def test_industrial_scope_is_derived_after_power_scope(self) -> None:
        decision = MODULE.classify_raw_source("2730", "燃气锅炉", "")
        self.assertEqual((decision.target, decision.department), ("INDUSTRIAL", "采矿业和制造业"))
        excluded = MODULE.classify_raw_source("1819", "燃生物质锅炉", "")
        self.assertEqual(excluded.target, "EXCLUDE")

    def test_source_identity_matches_the_raw_input_contract(self) -> None:
        self.assertEqual(
            MODULE.stable_raw_source_id("CREDIT-C9D343F48167", "21", "2022"),
            "SRC-RAW-90E067EFFFEEA507E10F",
        )

    def test_full_raw_candidate_classification_regression(self) -> None:
        builder = MODULE.ReferenceBuilder()
        sources = builder.read_sources("B0")
        self.assertEqual(len(sources), 5922)
        self.assertEqual(
            Counter(source.target for source in sources),
            Counter({"INDUSTRIAL": 4046, "POWER": 615, "EXCLUDE": 1261}),
        )
        self.assertEqual(
            Counter(source.values.get("_源分类部门") for source in sources if source.target == "POWER"),
            Counter({"电力生产": 549, "热力生产和供应": 66}),
        )
        self.assertEqual(builder.b2_manifest["status"], "formal")
        self.assertEqual(
            Counter((row["plan_class"], builder.baseline_targets[row["source_id"]]) for row in builder.injections),
            Counter({
                ("positive_injection", "INDUSTRIAL"): 10,
                ("positive_injection", "POWER"): 10,
                ("negative_control", "INDUSTRIAL"): 3,
                ("negative_control", "POWER"): 3,
                ("preexisting_problem", "INDUSTRIAL"): 2,
                ("preexisting_problem", "POWER"): 2,
            }),
        )
        admitted = [source for source in sources if source.target != "EXCLUDE" and builder.source_gate(source)[0] == "admitted"]
        missing_denox = next(source for source in admitted if not builder.nh3_processes(source)[0])
        explicit_non_scr = next(
            source for source in admitted
            if builder.nh3_processes(source)[0] and not builder.nh3_processes(source)[1]
        )
        missing_item = builder.nh3_component_item(missing_denox)
        non_scr_item = builder.nh3_component_item(explicit_non_scr)
        self.assertEqual(missing_item["expected_status"], "calculated")
        self.assertEqual((missing_item["expected_generation_t"], missing_item["expected_emission_t"]), ("0", "0"))
        self.assertIn("DENITRIFICATION_INFO_MISSING_ZERO", missing_item["expected_reason_codes"])
        self.assertEqual(non_scr_item["expected_status"], "not_involved")
        self.assertEqual((non_scr_item["expected_generation_t"], non_scr_item["expected_emission_t"]), ("", ""))
        self.assertIn("NH3_NOT_APPLICABLE_NO_SCR_SNCR", non_scr_item["expected_reason_codes"])

    def test_other_fuel_reported_in_tce_uses_the_reviewed_single_root(self) -> None:
        builder = MODULE.ReferenceBuilder()
        source = next(
            row for row in builder.read_sources("B0")
            if row.source_id == "SRC-RAW-016884A74403910FC27F"
        )
        other = next(fuel for fuel in builder.fuel_components(source) if fuel.raw_fuel == "其他燃料")
        self.assertEqual(other.foundational_codes, ["OTHER_FUEL_TCE_UNRESOLVED"])


class IndependentFactorRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = MODULE.RuleCatalog(ROOT / "rules" / "frozen" / "v1.0.1")

    def test_transferred_power_natural_gas_uses_power_factor(self) -> None:
        row = self.catalog.factor_row("POWER", "4411", "天然气", "燃气锅炉", "NOX")
        self.assertEqual(row["parameter_id"], "D-EP-NG-GASBOILER-NOX")
        self.assertEqual(float(row["value"]), 4.1)

    def test_heat_uses_heat_department_factor(self) -> None:
        row = self.catalog.factor_row("POWER", "4430", "煤矸石", "煤粉炉", "VOCS")
        self.assertEqual(row["parameter_id"], "D-HS-GANGUE-NA-VOCS")
        self.assertEqual(float(row["value"]), 0.18)

    def test_not_divided_by_technology_factor_is_an_explicit_fallback(self) -> None:
        row = self.catalog.factor_row("POWER", "4411", "煤矸石", "煤粉炉", "NOX")
        self.assertEqual(row["combustion_technology"], "不分技术")
        self.assertEqual(row["parameter_id"], "D-EP-GANGUE-NA-NOX")

    def test_industrial_coal_parameter_uses_canonical_department(self) -> None:
        row = self.catalog.coal_row("INDUSTRIAL", "2730", "煤粉炉")
        self.assertEqual(row["parameter_id"], "C-11")


class DependencyBoundaryTests(unittest.TestCase):
    def test_v2_builder_contains_no_scope_catalog_or_adjusted_payload_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("scope_catalog", "ind_deidentified.json", "pwr_deidentified.json", "_adjusted"):
            self.assertNotIn(forbidden, source)

    def test_v2_builder_does_not_import_professional_engine(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("plugin.fixed-combustion-inventory", source)
        self.assertNotIn("engine.workbook_engine", source)


class ReasonEquivalenceTests(unittest.TestCase):
    def test_engine_v3_reason_codes_map_to_reviewed_roots(self) -> None:
        expected = {
            "CONTROL_RELATION_CATEGORY_LEVEL": ("authorized_control_relation_category_level", False),
            "CONTROL_RELATION_AMBIGUOUS": ("control_relation_ambiguous", True),
            "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE": ("fuel_outside_fixed_combustion_scope", False),
            "NH3_FACTOR_REQUIRES_COAL_ACTIVITY": ("ammonia_slip_activity_missing", True),
            "NH3_FACTOR_SCOPE_MISMATCH": ("ammonia_slip_activity_missing", True),
            "OTHER_FUEL_TCE_UNRESOLVED": ("other_fuel_tce_unresolved", True),
            "FACTOR_APPLICABILITY_UNRESOLVED": ("factor_applicability_unresolved", True),
        }
        for code, value in expected.items():
            self.assertEqual(MODULE.REASON_EQUIVALENCE[code], value)


if __name__ == "__main__":
    unittest.main()
