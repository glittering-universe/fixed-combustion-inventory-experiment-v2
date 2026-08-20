from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = ROOT / "plugin" / "fixed-combustion-inventory" / "engine"
sys.path.insert(0, str(ENGINE_DIR))

from workbook_engine import connect  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "normalize_run_output", ROOT / "evaluation" / "normalize_run_output.py"
)
assert SPEC and SPEC.loader
NORMALIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NORMALIZE)


class SpecializedNormalizationTests(unittest.TestCase):
    def test_not_involved_component_does_not_pollute_calculated_total_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            connection = connect(run)
            connection.execute(
                "INSERT INTO raw_records VALUES(?,?,?,?,?,?)",
                ("RAW-1", "B102-2002", "input.xlsx", "Sheet1", 2, "{}"),
            )
            connection.execute(
                "INSERT INTO device_sources VALUES(?,?,?,?,?)",
                ("SRC-RAW-00000000000000000001", "RAW-1", "INDUSTRIAL", "工业源", "{}"),
            )
            for fuel_id in ("FUEL-1", "FUEL-2"):
                connection.execute(
                    "INSERT INTO fuels VALUES(?,?,?,?,?,?,?,?,?)",
                    (fuel_id, "SRC-RAW-00000000000000000001", fuel_id, "燃料", 1, "吨", None, "", None),
                )
            for item_id, fuel_id in (("ITEM-1", "FUEL-1"), ("ITEM-2", "FUEL-2")):
                connection.execute(
                    "INSERT INTO calculation_items VALUES(?,?,?,?,?,?)",
                    (item_id, "SRC-RAW-00000000000000000001", fuel_id, "combustion", "SO2", None),
                )
            connection.execute(
                """
                INSERT INTO rule_results(
                    item_id,normalized_fuel,normalized_technology,activity_value,activity_unit,
                    activity_formula,parameter_id,mode,factor_value,factor_unit,factor_formula,
                    source_section,source_page,control_technology,control_parameter_ids,
                    control_efficiency,control_fine_efficiency,control_coarse_efficiency,
                    control_label,control_candidates_json,operation_rate,flags_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("ITEM-1", "天然气", "燃气锅炉", 1000, "kg", "activity", "P1", "constant", 2,
                 "g/kg", "factor", "T/CSES", "1", "", "[]", 0, None, None, "无控制", "[]", 1, "[]"),
            )
            connection.execute(
                """
                INSERT INTO rule_results(
                    item_id,normalized_fuel,normalized_technology,activity_value,activity_unit,
                    activity_formula,parameter_id,mode,factor_value,factor_unit,factor_formula,
                    source_section,source_page,control_technology,control_parameter_ids,
                    control_efficiency,control_fine_efficiency,control_coarse_efficiency,
                    control_label,control_candidates_json,operation_rate,flags_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("ITEM-2", "生物燃料", "不分技术", 1000, "kg", "activity", None, None, None,
                 None, None, None, None, "", "[]", 0, None, None, "不涉及", "[]", 1,
                 json.dumps(["FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE"])),
            )
            connection.execute(
                "INSERT INTO admission_results VALUES(?,?,?,?)",
                ("ITEM-1", "allow_calculation", "RULE_CHAIN_COMPLETE", "[]"),
            )
            connection.execute(
                "INSERT INTO admission_results VALUES(?,?,?,?)",
                ("ITEM-2", "not_applicable", "FUEL_OUTSIDE_FIXED_FOSSIL_SCOPE", "[]"),
            )
            connection.execute(
                "INSERT INTO calculations VALUES(?,?,?,?,?,?)",
                ("ITEM-1", 0.002, 0.002, "generation", "emission", "calculated"),
            )
            connection.execute(
                "INSERT INTO calculations VALUES(?,?,?,?,?,?)",
                ("ITEM-2", 0, 0, "", "", "not_applicable_zero"),
            )
            connection.commit()
            connection.close()

            rows = NORMALIZE.specialized_rows(
                run,
                {"run_id": "TEST", "target": "INDUSTRIAL", "experiment_method": "full"},
            )
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["status"], "calculated")
            self.assertEqual(row["generation_t"], 0.002)
            self.assertEqual(
                [record["parameter_id"] for record in json.loads(row["parameter_record"])],
                ["P1"],
            )


if __name__ == "__main__":
    unittest.main()
