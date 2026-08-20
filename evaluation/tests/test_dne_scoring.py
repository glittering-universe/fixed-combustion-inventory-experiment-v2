#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "evaluation" / "score_dne.py"
SPEC = importlib.util.spec_from_file_location("score_dne", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DneScoringTests(unittest.TestCase):
    def test_pollutant_specific_control_ignores_irrelevant_fields(self) -> None:
        left = {"efficiency": 0.0, "fine_efficiency": 0.8, "coarse_efficiency": 0.6}
        right = {"efficiency": None, "fine_efficiency": 0.8, "coarse_efficiency": 0.6}
        self.assertEqual(
            MODULE.pollutant_control_signature("PM10", left),
            MODULE.pollutant_control_signature("PM10", right),
        )
        self.assertNotEqual(
            MODULE.pollutant_control_signature("SO2", left),
            MODULE.pollutant_control_signature("SO2", right),
        )
        self.assertEqual(
            MODULE.pollutant_control_signature("PM2.5", left),
            MODULE.pollutant_control_signature(
                "PM2.5", {"efficiency": None, "fine_efficiency": 0.8, "coarse_efficiency": 0.1}
            ),
        )

    def test_relative_time_efficiency_uses_log_empirical_anchors(self) -> None:
        self.assertEqual(MODULE.relative_time_efficiency(10.0, 10.0, 1.0), 0.0)
        self.assertEqual(MODULE.relative_time_efficiency(1.0, 10.0, 1.0), 1.0)
        self.assertAlmostEqual(MODULE.relative_time_efficiency(math.sqrt(10), 10.0, 1.0), 0.5)
        self.assertEqual(MODULE.relative_time_efficiency(0.5, 10.0, 1.0), 1.0)
        self.assertEqual(MODULE.relative_time_efficiency(20.0, 10.0, 1.0), 0.0)
        self.assertIsNone(MODULE.relative_time_efficiency(2.0, 1.0, 1.0))

    def test_core_score_uses_frozen_three_dimension_weights(self) -> None:
        self.assertAlmostEqual(MODULE.core_score(1.0, 0.5, 0.0), 52.5)
        self.assertIsNone(MODULE.core_score(None, 0.5, 0.0))

    def test_atomic_summary_keeps_not_applicable_out_of_denominator(self) -> None:
        summary = MODULE.summarize_atoms([
            MODULE.Atom("d1", "terminal", True),
            MODULE.Atom("d2", "basis", False),
            MODULE.Atom("d3", "control", None),
        ])
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["applicable"], 2)
        self.assertEqual(summary["not_applicable"], 1)
        self.assertAlmostEqual(summary["score"], 0.5)

    def test_missing_calculated_result_fails_completeness_but_not_unobservable_atoms(self) -> None:
        expected = {
            "SRC-1::NOx": {
                "source_id": "SRC-1", "pollutant": "NOx", "expected_status": "calculated",
                "expected_generation_t": "2.0", "expected_emission_t": "1.0",
            }
        }
        result = MODULE.score_numeric_atoms(expected, {}, method="generic_tool_agent")
        by_kind = {item.kind: item for item in result}
        self.assertFalse(by_kind["result_completeness"].passed)
        self.assertIsNone(by_kind["reference_value"].passed)
        self.assertIsNone(by_kind["internal_recalculation"].passed)

    def test_noncalculated_result_with_number_fails_numeric_completeness(self) -> None:
        expected = {
            "SRC-1::NH3": {
                "source_id": "SRC-1", "pollutant": "NH3", "expected_status": "information_insufficient",
                "expected_generation_t": "", "expected_emission_t": "",
            }
        }
        actual = {
            "SRC-1::NH3": {
                "source_id": "SRC-1", "pollutant": "NH3", "status": "information_insufficient",
                "generation_t": "0", "emission_t": "0",
            }
        }
        atoms = MODULE.score_numeric_atoms(expected, actual, method="generic_tool_agent")
        self.assertFalse(next(item for item in atoms if item.kind == "result_completeness").passed)

    def test_unexpected_blank_result_row_still_fails_output_completeness(self) -> None:
        actual = {
            "SRC-X::SO2": {
                "source_id": "SRC-X", "pollutant": "SO2", "status": "information_insufficient",
                "generation_t": "", "emission_t": "",
            }
        }
        atoms = MODULE.score_numeric_atoms({}, actual, method="generic_tool_agent")
        self.assertEqual(len(atoms), 1)
        self.assertFalse(atoms[0].passed)

    def test_human_missing_process_basis_is_na_not_failure(self) -> None:
        expected = {
            "SRC-1::SO2": {
                "source_id": "SRC-1", "pollutant": "SO2", "expected_status": "calculated",
                "method_ids": ["METHOD-1"], "parameter_ids": ["PARAM-1"],
                "control_signatures": [(0.5,)],
            }
        }
        actual = {
            "SRC-1::SO2": {
                "source_id": "SRC-1", "pollutant": "SO2", "status": "calculated",
                "method_ids": [], "parameter_ids": [], "control_signatures": [],
            }
        }
        atoms = MODULE.score_decision_atoms(
            expected, actual, expected_sources={}, actual_destinations={},
            expected_exception_groups=set(), actual_exception_groups=set(), method="expert_led",
        )
        by_kind = {item.kind: item for item in atoms}
        self.assertTrue(by_kind["terminal_disposition"].passed)
        self.assertIsNone(by_kind["calculation_basis"].passed)
        self.assertIsNone(by_kind["control_application"].passed)

    def test_target_specific_human_source_decision_does_not_confuse_other_class_with_exclusion(self) -> None:
        expected_sources = {
            "SRC-I": {"expected_target": "INDUSTRIAL"},
            "SRC-P": {"expected_target": "POWER"},
            "SRC-X": {"expected_target": "EXCLUDE"},
        }
        actual = {
            "SRC-I": {"selected_for_target": True, "run_target": "INDUSTRIAL"},
            "SRC-P": {"selected_for_target": False, "run_target": "INDUSTRIAL"},
            "SRC-X": {"selected_for_target": False, "run_target": "INDUSTRIAL"},
        }
        atoms = MODULE.score_decision_atoms(
            {}, {}, expected_sources=expected_sources, actual_destinations=actual,
            expected_exception_groups=set(), actual_exception_groups=set(), method="expert_led",
        )
        self.assertEqual(sum(item.passed is True for item in atoms), 3)

    def test_macro_and_micro_aggregations_are_both_reported(self) -> None:
        target_rows = {
            "INDUSTRIAL": {
                "D": {"score": 1.0, "passed": 90, "applicable": 100},
                "N": {"score": 0.8, "passed": 80, "applicable": 100},
                "E": 0.6,
                "record_count": 100,
            },
            "POWER": {
                "D": {"score": 0.5, "passed": 5, "applicable": 10},
                "N": {"score": 1.0, "passed": 10, "applicable": 10},
                "E": 1.0,
                "record_count": 10,
            },
        }
        result = MODULE.aggregate_targets(target_rows)
        self.assertAlmostEqual(result["macro"]["D"], 0.75)
        self.assertAlmostEqual(result["micro"]["D"], 95 / 110)
        self.assertAlmostEqual(result["macro"]["E"], 0.8)
        self.assertAlmostEqual(result["micro"]["E"], 70 / 110)


if __name__ == "__main__":
    unittest.main()
