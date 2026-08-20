#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPARATOR = ROOT / "reference" / "compare_run_to_reference.py"
REFERENCE = ROOT / "reference" / "frozen" / "v2.0.0"
SPEC = importlib.util.spec_from_file_location("reference_comparator_v2", COMPARATOR)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReferenceSelectionTests(unittest.TestCase):
    def test_full_raw_run_without_target_member_list_gets_full_target_reference(self) -> None:
        _, items, totals, _ = MODULE.select_reference(
            REFERENCE, {"input_variant": "B0", "target": "POWER"}
        )
        self.assertGreater(len(items), 0)
        self.assertEqual(len(totals), 615 * 9)

    def test_scale_run_filters_by_target_neutral_candidate_ids(self) -> None:
        _, _, all_totals, _ = MODULE.select_reference(
            REFERENCE, {"input_variant": "B0", "target": "POWER"}
        )
        source_id = all_totals[0]["source_id"]
        _, _, subset, _ = MODULE.select_reference(
            REFERENCE,
            {"input_variant": "B0", "target": "POWER", "candidate_ids": [source_id]},
        )
        self.assertEqual(len(subset), 9)
        self.assertEqual({row["source_id"] for row in subset}, {source_id})

    def test_generic_exception_parser_accepts_target_neutral_raw_source_ids(self) -> None:
        source_id = "SRC-RAW-90E067EFFFEEA507E10F"
        equivalence = {
            "codes": {},
            "generic_root_rules": [{
                "regex": "ACTIVITY_MISSING",
                "canonical_root_cause": "activity_level_missing",
                "requires_user_judgment": True,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            output = run_dir / "outputs"; output.mkdir()
            with (output / "generic_exception_list.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["source_id", "root_cause"])
                writer.writeheader(); writer.writerow({"source_id": source_id, "root_cause": "ACTIVITY_MISSING"})
            groups = MODULE.actual_exception_groups(run_dir, equivalence)
        self.assertEqual(groups, {(source_id, "activity_level_missing")})


if __name__ == "__main__":
    unittest.main()
