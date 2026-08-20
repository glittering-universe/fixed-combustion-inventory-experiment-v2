from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("normalize_run_output", ROOT / "evaluation" / "normalize_run_output.py")
assert SPEC and SPEC.loader
NORMALIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NORMALIZE)
SCORE_SPEC = importlib.util.spec_from_file_location("score_dne", ROOT / "evaluation" / "score_dne.py")
assert SCORE_SPEC and SCORE_SPEC.loader
SCORE = importlib.util.module_from_spec(SCORE_SPEC)
SCORE_SPEC.loader.exec_module(SCORE)


class GenericOutputNormalizationTests(unittest.TestCase):
    def write_run(self, rows: list[dict[str, object]]) -> tuple[tempfile.TemporaryDirectory, Path, dict[str, object]]:
        temporary = tempfile.TemporaryDirectory()
        run_dir = Path(temporary.name)
        (run_dir / "outputs").mkdir()
        headers = sorted({key for row in rows for key in row})
        with (run_dir / "outputs" / "generic_inventory_items.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        manifest = {"run_id": "G-1", "target": "INDUSTRIAL", "experiment_method": "generic_tool_agent"}
        return temporary, run_dir, manifest

    def test_formal_json_arrays_preserve_multifuel_and_pm10_component_controls(self) -> None:
        activities = [
            {"component_id": "fuel-1", "value": 1000, "unit": "kg"},
            {"component_id": "fuel-2", "value": 2000, "unit": "kg"},
        ]
        parameters = [
            {"component_id": "fuel-1", "parameter_id": "P1", "mode": "constant", "value": 2, "unit": "g/kg"},
            {"component_id": "fuel-2", "parameter_id": "P2", "mode": "constant", "value": 3, "unit": "g/kg"},
        ]
        controls = [
            {"component_id": "fuel-1", "method": "dust", "fine_efficiency": 0.8, "coarse_efficiency": 0.6},
            {"component_id": "fuel-2", "method": "dust", "fine_efficiency": 0.7, "coarse_efficiency": 0.5},
        ]
        temporary, run_dir, manifest = self.write_run([{
            "source_id": "SRC-RAW-1", "target": "INDUSTRIAL", "pollutant": "PM10", "status": "calculated",
            "method": "factor", "activity_record_json": json.dumps(activities),
            "parameter_record_json": json.dumps(parameters), "control_record_json": json.dumps(controls),
            "generation_t": "0.008", "emission_t": "0.003", "standard_reference": "T/CSES", "reason_code": "",
        }])
        try:
            row = NORMALIZE.generic_rows(run_dir, manifest)[0]
        finally:
            temporary.cleanup()
        self.assertEqual(json.loads(row["activity_record"]), activities)
        self.assertEqual(json.loads(row["parameter_record"]), parameters)
        self.assertEqual(json.loads(row["control_record"]), controls)
        self.assertTrue(row["minimum_recalculation_complete"])

    def test_legacy_single_value_columns_are_wrapped_as_one_component(self) -> None:
        temporary, run_dir, manifest = self.write_run([{
            "source_id": "SRC-RAW-2", "target": "INDUSTRIAL", "pollutant": "SO2", "status": "calculated",
            "method": "factor", "activity_value": "1000", "activity_unit": "kg",
            "parameter_value": "2", "parameter_unit": "g/kg", "control_method": "none",
            "control_efficiency": "0", "generation_t": "0.002", "emission_t": "0.002",
            "standard_reference": "T/CSES", "reason_code": "",
        }])
        try:
            row = NORMALIZE.generic_rows(run_dir, manifest)[0]
        finally:
            temporary.cleanup()
        self.assertEqual(json.loads(row["activity_record"]), [{"value": "1000", "unit": "kg"}])
        self.assertEqual(json.loads(row["parameter_record"]), [{"value": "2", "unit": "g/kg"}])
        self.assertEqual(json.loads(row["control_record"]), [{"method": "none", "efficiency": "0"}])
        self.assertTrue(row["minimum_recalculation_complete"])

    def test_dne_recalculates_multifuel_pm10_from_normalized_component_arrays(self) -> None:
        activities = [
            {"component_id": "fuel-1", "value": 1000, "unit": "kg"},
            {"component_id": "fuel-2", "value": 2000, "unit": "kg"},
        ]
        pm10_parameters = [
            {"component_id": "fuel-1", "value": 2, "unit": "g/kg"},
            {"component_id": "fuel-2", "value": 3, "unit": "g/kg"},
        ]
        pm25_parameters = [
            {"component_id": "fuel-1", "value": 1, "unit": "g/kg"},
            {"component_id": "fuel-2", "value": 1.5, "unit": "g/kg"},
        ]
        pm10_controls = [
            {"component_id": "fuel-1", "fine_efficiency": 0.8, "coarse_efficiency": 0.6},
            {"component_id": "fuel-2", "fine_efficiency": 0.7, "coarse_efficiency": 0.5},
        ]
        pm25_controls = [
            {"component_id": "fuel-1", "efficiency": 0.8},
            {"component_id": "fuel-2", "efficiency": 0.7},
        ]
        common = {"source_id": "SRC-RAW-3", "target": "INDUSTRIAL", "status": "calculated",
                  "method": "factor", "activity_record_json": json.dumps(activities),
                  "standard_reference": "T/CSES", "reason_code": ""}
        temporary, run_dir, manifest = self.write_run([
            {**common, "pollutant": "PM10", "parameter_record_json": json.dumps(pm10_parameters),
             "control_record_json": json.dumps(pm10_controls), "generation_t": "0.008", "emission_t": "0.003"},
            {**common, "pollutant": "PM2.5", "parameter_record_json": json.dumps(pm25_parameters),
             "control_record_json": json.dumps(pm25_controls), "generation_t": "0.004", "emission_t": "0.0011"},
        ])
        try:
            rows = NORMALIZE.generic_rows(run_dir, manifest)
        finally:
            temporary.cleanup()
        totals = {f"{row['source_id']}::{row['pollutant']}": row for row in rows}
        self.assertTrue(SCORE.independent_recalculation(totals["SRC-RAW-3::PM10"], totals))


if __name__ == "__main__":
    unittest.main()
