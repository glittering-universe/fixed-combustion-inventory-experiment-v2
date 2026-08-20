from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from human_baseline.normalize_human_v2 import (
    load_raw_context,
    map_human_workbook,
    normalize_all,
)


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
RAW_102 = (
    WORKSPACE
    / "01 环境统计数据"
    / "2022年基表查询工业企业锅炉_燃气轮机污染物和温室气体排放及治理情况(基102表)2023102002.xlsx"
)
RAW_101_ENTERPRISE = (
    WORKSPACE
    / "01 环境统计数据"
    / "2022年基表查询工业企业污染物和温室气体排放及治理情况(基101表)2023102001.xlsx"
)
RAW_101_CONTROL = (
    WORKSPACE
    / "01 环境统计数据"
    / "2022年基表查询工业企业污染物和温室气体排放及治理情况(基101表)2023102002.xlsx"
)
ORIGINALS = ROOT / "human_baseline" / "original_packages"
EXPERIMENT_A = ORIGINALS / "实验结果" / "02_实验A_端到端清单编制"
INDUSTRIAL_A = (
    EXPERIMENT_A
    / "01_工业锅炉"
    / "04_既有专家主导型"
    / "结果"
    / "固定燃烧源-工业锅炉_调整.xlsx"
)
POWER_A = (
    EXPERIMENT_A
    / "02_火电热力"
    / "04_既有专家主导型"
    / "结果"
    / "固定燃烧源-火电、热力生产与供应_调整.xlsx"
)


@unittest.skipUnless(
    all(path.exists() for path in (RAW_102, RAW_101_ENTERPRISE, RAW_101_CONTROL, INDUSTRIAL_A, POWER_A)),
    "workspace regression data are not available",
)
class HumanMappingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = load_raw_context(RAW_102, RAW_101_ENTERPRISE, RAW_101_CONTROL)

    def test_raw_catalog_has_stable_unique_candidate_ids(self) -> None:
        self.assertEqual(5922, len(self.context.candidates))
        self.assertEqual(5922, len({item.source_id for item in self.context.candidates}))
        self.assertEqual(18734, self.context.enterprise_rows)
        self.assertEqual(43902, self.context.control_rows)
        self.assertTrue(all(item.source_id.startswith("SRC-RAW-") for item in self.context.candidates))

    def test_experiment_a_workbooks_map_back_to_raw_rows(self) -> None:
        industrial = map_human_workbook(INDUSTRIAL_A, "INDUSTRIAL", self.context)
        power = map_human_workbook(POWER_A, "POWER", self.context)

        self.assertEqual(4160, len(industrial.scope_source_ids))
        self.assertEqual(426, len(power.scope_source_ids))
        self.assertEqual(4160, len(set(industrial.scope_source_ids)))
        self.assertEqual(426, len(set(power.scope_source_ids)))
        self.assertFalse(industrial.mapping_exceptions)
        self.assertFalse(power.mapping_exceptions)

        # First industrial result row is raw Base-102 row 2; the first power
        # result row is raw Base-102 row 38. These literals protect against a
        # return to workbook-relative SRC-IND/SRC-PWR row IDs.
        self.assertEqual(2, industrial.rows[0].raw_excel_row)
        self.assertEqual(38, power.rows[0].raw_excel_row)
        self.assertEqual("SRC-RAW-90E067EFFFEEA507E10F", industrial.rows[0].source_id)
        self.assertEqual("SRC-RAW-3E66D36FC4F7A71C87FB", power.rows[0].source_id)

    def test_batch_normalization_writes_four_declared_artifacts_per_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "normalized_v2"
            summary = normalize_all(
                original_root=ORIGINALS,
                output_root=output,
                raw_102=RAW_102,
                raw_101_enterprise=RAW_101_ENTERPRISE,
                raw_101_control=RAW_101_CONTROL,
            )
            self.assertEqual(14, summary["package_count"])
            self.assertEqual(14, len(list(output.rglob("source_decisions.csv"))))
            self.assertEqual(14, len(list(output.rglob("calculation_totals.csv"))))
            self.assertEqual(14, len(list(output.rglob("exceptions.csv"))))
            self.assertEqual(14, len(list(output.rglob("normalization_notes.json"))))
            self.assertFalse(any("original_packages" in str(path) for path in output.rglob("*")))
            expected = {"INDUSTRIAL": 4160, "POWER": 426}
            for package in summary["packages"]:
                self.assertEqual(expected[package["target"]], package["scope_included"])
                # Missing reported totals may remain observable exceptions, but
                # the raw-source identity mapping itself must be complete.
                exception_path = output / package["output"] / "exceptions.csv"
                self.assertNotIn("SOURCE_MAPPING_FAILED", exception_path.read_text(encoding="utf-8-sig"))
                notes = json.loads((output / package["output"] / "normalization_notes.json").read_text(encoding="utf-8"))
                self.assertEqual(5922, notes["timing"]["candidate_record_count"])
                self.assertAlmostEqual(68221.44, notes["timing"]["total_seconds"])
                self.assertEqual(
                    "extrapolated_from_user_supplied_per_candidate_rate",
                    notes["timing"]["basis"],
                )

    def test_normalization_refuses_to_write_inside_preserved_originals(self) -> None:
        with self.assertRaisesRegex(ValueError, "disjoint"):
            normalize_all(
                original_root=ORIGINALS,
                output_root=ORIGINALS,
                raw_102=RAW_102,
                raw_101_enterprise=RAW_101_ENTERPRISE,
                raw_101_control=RAW_101_CONTROL,
                replace=True,
            )


if __name__ == "__main__":
    unittest.main()
