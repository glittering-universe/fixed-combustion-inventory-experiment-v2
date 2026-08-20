from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ENGINE = Path(__file__).resolve().parents[1] / "engine"


def load_domain():
    path = ENGINE / "source_domain.py"
    spec = importlib.util.spec_from_file_location("fixed_combustion_source_domain", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceDomainTests(unittest.TestCase):
    def setUp(self):
        self.domain = load_domain()

    def test_4412_heat_and_power_cogeneration_uses_power_generation_factors(self):
        decision = self.domain.classify_source(
            industry_code="4412",
            industrial_equipment="燃气锅炉",
            power_equipment="",
        )
        self.assertEqual(decision.target, "POWER")
        self.assertEqual(decision.department, "电力生产")
        self.assertEqual(decision.equipment, "燃气锅炉")

    def test_4412_from_power_device_column_has_the_same_department(self):
        decision = self.domain.classify_source(
            industry_code="4412",
            industrial_equipment="",
            power_equipment="燃煤锅炉",
        )
        self.assertEqual(decision.target, "POWER")
        self.assertEqual(decision.department, "电力生产")
        self.assertEqual(decision.equipment, "燃煤锅炉")

    def test_4430_is_heat_production_and_supply(self):
        decision = self.domain.classify_source(
            industry_code="4430",
            industrial_equipment="",
            power_equipment="燃煤锅炉",
        )
        self.assertEqual(decision.target, "POWER")
        self.assertEqual(decision.department, "热力生产和供应")

    def test_manufacturing_boiler_is_industrial_even_if_recorded_in_power_column(self):
        decision = self.domain.classify_source(
            industry_code="2720",
            industrial_equipment="",
            power_equipment="燃气锅炉",
        )
        self.assertEqual(decision.target, "INDUSTRIAL")
        self.assertEqual(decision.department, "采矿业和制造业")
        self.assertEqual(decision.equipment, "燃气锅炉")

    def test_non_boiler_record_is_outside_current_scope(self):
        decision = self.domain.classify_source(
            industry_code="5000",
            industrial_equipment="",
            power_equipment="",
        )
        self.assertEqual(decision.target, "EXCLUDE")

    def test_stable_source_id_does_not_encode_target_or_row_position(self):
        source_id = self.domain.stable_source_id(
            pseudonymous_entity_id="CREDIT-ABC123",
            sequence="10",
            year="2022",
        )
        self.assertEqual(source_id, "SRC-RAW-051636921B7B213905B5")


if __name__ == "__main__":
    unittest.main()
