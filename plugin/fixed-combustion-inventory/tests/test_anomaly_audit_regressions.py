#!/usr/bin/env python3
"""Regression contracts established by the 502-source anomaly audit."""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT / "plugin" / "fixed-combustion-inventory" / "engine"
sys.path.insert(0, str(ENGINE))

from workbook_engine import (  # noqa: E402
    COAL_MASS_BALANCE_FUELS,
    admission_disposition,
    denitrification_processes,
    match_coal,
    match_factor,
    normalize_technology,
    select_control,
    source_relationship_flags,
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class AnomalyAuditRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = ROOT / "rules" / "frozen" / "v1.0.1"

    def test_industrial_coal_department_is_canonical_and_matchable(self) -> None:
        coal = rows(self.package / "parameters" / "coal_parameters.csv")
        industrial = [row for row in coal if row["sector"] == "工业源"]
        self.assertTrue(industrial)
        self.assertEqual({row["department"] for row in industrial}, {"采矿业和制造业"})
        self.assertIsNotNone(
            match_coal(coal, "INDUSTRIAL", "采矿业和制造业", "流化床炉")
        )
        self.assertIn("焦炭", COAL_MASS_BALANCE_FUELS)

    def test_no_technology_factor_is_a_controlled_fallback(self) -> None:
        factors = rows(self.package / "parameters" / "power_heat_emission_factors.csv")
        matched = match_factor(factors, "电力生产", "煤矸石", "流化床炉", "CO")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["parameter_id"], "D-EP-GANGUE-NA-CO")

    def test_audit_reason_codes_have_explicit_dispositions(self) -> None:
        cases = [
            (["NH3_FACTOR_REQUIRES_COAL_ACTIVITY"], "withhold", "NH3_FACTOR_SCOPE_MISMATCH"),
            (["FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE"], "not_applicable", "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE"),
            (["UNRESOLVED_OTHER_FUEL_TCE"], "withhold", "OTHER_FUEL_TCE_UNRESOLVED"),
            (["COMBUSTION_TECHNOLOGY_AMBIGUOUS"], "withhold", "COMBUSTION_TECHNOLOGY_AMBIGUOUS"),
            (["T_CSES_COAL_BC_OC_PARAMETER_GAP"], "withhold", "T_CSES_COAL_BC_OC_PARAMETER_GAP"),
            (["T_CSES_COAL_PARAMETER_GAP"], "withhold", "T_CSES_COAL_PARAMETER_GAP"),
            (["FACTOR_APPLICABILITY_UNRESOLVED"], "withhold", "FACTOR_APPLICABILITY_UNRESOLVED"),
        ]
        for flags, expected_action, expected_reason in cases:
            with self.subTest(flags=flags):
                self.assertEqual(admission_disposition("fuel_pollutant", "SO2", flags),
                                 (expected_action, expected_reason))

    def test_source_relationship_invalid_overrides_fuel_not_involved(self) -> None:
        self.assertEqual(
            admission_disposition(
                "combustion",
                "SO2",
                ["FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE", "SOURCE_RELATION_MISSING"],
            ),
            ("withhold", "SOURCE_RELATION_MISSING"),
        )

    def test_admission_priority_preserves_audited_root_cause(self) -> None:
        self.assertEqual(
            admission_disposition(
                "combustion", "SO2", ["FACTOR_UNMATCHED", "FUEL_UNMAPPED"]
            ),
            ("withhold", "FUEL_UNMAPPED"),
        )
        self.assertEqual(
            admission_disposition(
                "combustion",
                "SO2",
                ["COMBUSTION_TECH_UNMAPPED", "FACTOR_APPLICABILITY_UNRESOLVED"],
            ),
            ("withhold", "FACTOR_APPLICABILITY_UNRESOLVED"),
        )

    def test_source_level_checks_cover_required_fields_coordinates_and_duplicate_slots(self) -> None:
        payload = {
            "company": "测试企业",
            "industry_code": "4411",
            "equipment": "",
            "outlet_id": "DA001",
            "longitude": 23.0,
            "latitude": 113.0,
            "fuels": [
                {"fuel": "烟煤", "amount": 10, "unit": "吨", "sulfur": 0.5, "ash": 10},
                {"fuel": "烟煤", "amount": 10, "unit": "吨", "sulfur": 0.5, "ash": 10},
            ],
        }
        self.assertEqual(
            set(source_relationship_flags(payload)),
            {
                "SOURCE_RELATION_MISSING",
                "COORDINATE_REVIEW_REQUIRED",
                "POTENTIAL_DUPLICATE_FUEL_SLOT",
            },
        )

    def test_explicit_no_scr_sncr_is_not_involved(self) -> None:
        self.assertEqual(
            admission_disposition(
                "ammonia_slip", "NH3", ["NH3_NOT_APPLICABLE_NO_SCR_SNCR"]
            ),
            ("not_applicable", "NH3_NOT_APPLICABLE_NO_SCR_SNCR"),
        )

    def test_nh3_process_resolution_distinguishes_missing_non_scr_and_scr(self) -> None:
        self.assertEqual(
            denitrification_processes({"control_candidates_raw": []}),
            ([], "DENITRIFICATION_INFO_MISSING_ZERO"),
        )
        self.assertEqual(
            denitrification_processes(
                {"control_candidates_raw": [{"process": "低氮燃烧"}]}
            ),
            ([], "NH3_NOT_APPLICABLE_NO_SCR_SNCR"),
        )
        self.assertEqual(
            denitrification_processes(
                {"control_candidates_raw": [{"process": "SNCR+SCR"}]}
            ),
            (
                ["脱硝烟气-选择性催化还原", "脱硝烟气-选择性非催化还原"],
                "",
            ),
        )

    def test_low_nox_scr_combines_only_for_nox_and_keeps_scr_particle_co_benefit(self) -> None:
        aliases = {}
        for row in rows(self.package / "mappings" / "source_control_aliases.csv"):
            aliases.setdefault((row["raw_field"], row["raw_value"]), []).append(row)
        efficiencies = rows(self.package / "parameters" / "control_efficiencies.csv")
        payload = {
            "low_nox": "是",
            "control_candidates_raw": [{
                "facility_id": "CTRL-1",
                "process": "选择性催化还原法（SCR）",
                "reported_efficiency": 80,
            }],
        }
        nox = select_control(payload, "NOX", efficiencies, aliases)
        pm25 = select_control(payload, "PM25", efficiencies, aliases)
        pm10_coarse = select_control(payload, "PM10_COARSE", efficiencies, aliases)
        self.assertEqual(nox[0], "低氮燃烧技术+选择性催化还原法")
        self.assertEqual(nox[2], 64.0)
        self.assertEqual(pm25[0], "选择性催化还原法")
        self.assertEqual(pm25[2], 57.0)
        self.assertEqual(pm10_coarse[2], 75.0)

    def test_rule_package_fuel_category_drives_petroleum_coke_technology(self) -> None:
        categories = {
            row["standard_value"]: row["category"]
            for row in rows(self.package / "mappings" / "standard_fuels.csv")
        }
        self.assertEqual(categories["石油焦"], "液体燃料")
        self.assertEqual(
            normalize_technology("石油焦", "", {}, categories),
            ("燃油锅炉", "fuel_determined"),
        )


if __name__ == "__main__":
    unittest.main()
