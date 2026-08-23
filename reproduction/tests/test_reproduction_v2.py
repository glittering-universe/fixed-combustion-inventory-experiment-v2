from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load_module("repro_build_v2", "reproduction/build_reproduction_manifest.py")
CHECK = load_module("repro_check_v2", "reproduction/check_environment.py")
COPY = load_module("repro_copy_human_v2", "reproduction/copy_human_inputs.py")
SEAL = load_module("repro_seal_v2", "experiment_control/seal_formal_setup.py")


class ReproductionManifestV2Tests(unittest.TestCase):
    def test_dependency_inventory_preserves_formal_v2_and_uses_active_dne_v2_1(self) -> None:
        files = set(BUILD.FILES)
        for required in (
            "requirements.lock.txt",
            "reproduction/bootstrap_environment.sh",
            "inputs/prepared/raw_input_manifest.json",
            "inputs/prepared/source_identity_index.json",
            "evaluation/metric_spec_v2.json",
            "evaluation/metric_spec_v2_1.json",
            "evaluation/score_dne.py",
            "evaluation/aggregate_experiment_results_v2.py",
            "reference/frozen/v2.0.0/package_lock.json",
            "human_baseline/normalize_human_v2.py",
            "human_baseline/normalized_v2/normalization_index.json",
        ):
            self.assertIn(required, files)
        for retired in (
            "inputs/prepared/scope_catalog.json",
            "evaluation/final_time_thresholds.json",
            "evaluation/aggregate_experiment_results.py",
            "evaluation/normalize_human_outputs.py",
        ):
            self.assertNotIn(retired, files)
        payload = BUILD.build_payload(ROOT, allow_missing=True)
        self.assertEqual("2.1.0", payload["manifest_version"])
        self.assertEqual("raw_base_tables_v2", payload["input_contract"])
        self.assertEqual("DNE_v2.1", payload["evaluation_contract"])
        self.assertEqual("DNE_v2", payload["formal_setup_evaluation_contract"])
        self.assertTrue(payload["runtime"]["python"].endswith("/.venv/bin/python"))
        self.assertNotIn(".venv/pyvenv.cfg", files)

    def test_formal_setup_collection_has_no_scope_or_old_threshold_file(self) -> None:
        paths = [str(path.relative_to(ROOT)) for path in SEAL.collect_setup_files(ROOT, allow_missing=True)]
        self.assertIn("inputs/prepared/raw_input_manifest.json", paths)
        self.assertIn("inputs/prepared/source_identity_index.json", paths)
        self.assertEqual(6, sum(path.startswith("inputs/prepared/") and path.endswith(".xlsx") for path in paths))
        self.assertIn("evaluation/metric_spec_v2.json", paths)
        self.assertIn("human_baseline/normalized_v2/normalization_index.json", paths)
        self.assertIn("requirements.lock.txt", paths)
        self.assertIn("reproduction/bootstrap_environment.sh", paths)
        self.assertNotIn(".venv/pyvenv.cfg", paths)
        self.assertNotIn("inputs/prepared/scope_catalog.json", paths)
        self.assertNotIn("evaluation/final_time_thresholds.json", paths)


class HumanCopyV2Tests(unittest.TestCase):
    def test_copy_uses_normalized_v2_without_overwriting_original_packages(self) -> None:
        original_index = json.loads(
            (ROOT / "human_baseline" / "normalized_v2" / "normalization_index.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "fresh"
            summary = COPY.copy_human_packages(ROOT, target)
            self.assertEqual(14, summary["human_runs"])
            self.assertEqual(14, len(list((target / "实验结果").rglob("source_decisions.csv"))))
            self.assertEqual(14, len(list((target / "实验结果").rglob("calculation_totals.csv"))))
            self.assertEqual(14, len(list((target / "实验结果").rglob("run_manifest.json"))))
            self.assertEqual(14, len(list((target / "实验结果").rglob("execution_metrics.json"))))
            self.assertEqual(14, len(list((target / "实验结果").rglob("experiment_seal.json"))))
            sample_seal = json.loads(next((target / "实验结果").rglob("experiment_seal.json")).read_text(encoding="utf-8"))
            self.assertEqual("2.0.0", sample_seal["seal_version"])
            self.assertTrue(sample_seal["method_bundle_hash"])
            self.assertTrue(sample_seal["execution_metrics_sha256"])
            self.assertFalse(list((target / "实验结果").rglob("*.xlsx")))
            self.assertEqual(
                original_index["original_workbook_hashes"],
                json.loads((ROOT / "human_baseline" / "normalized_v2" / "normalization_index.json").read_text(encoding="utf-8"))["original_workbook_hashes"],
            )

    def test_copy_refuses_existing_human_result_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "fresh"
            COPY.copy_human_packages(ROOT, target)
            with self.assertRaisesRegex(FileExistsError, "refuses to overwrite"):
                COPY.copy_human_packages(ROOT, target)


class EnvironmentContractV2Tests(unittest.TestCase):
    def test_contract_scan_rejects_retired_scope_and_threshold_dependencies(self) -> None:
        errors = CHECK.contract_errors({
            "manifest_version": "2.1.0",
            "input_contract": "raw_base_tables_v2",
            "evaluation_contract": "DNE_v2.1",
            "formal_setup_evaluation_contract": "DNE_v2",
            "files": [
                {"path": "inputs/prepared/scope_catalog.json"},
                {"path": "evaluation/final_time_thresholds.json"},
            ],
        })
        self.assertTrue(any("scope" in error for error in errors))
        self.assertTrue(any("threshold" in error for error in errors))

    def test_contract_scan_accepts_v2_1_evaluation_over_v2_formal_setup(self) -> None:
        self.assertEqual([], CHECK.contract_errors({
            "manifest_version": "2.1.0",
            "input_contract": "raw_base_tables_v2",
            "evaluation_contract": "DNE_v2.1",
            "formal_setup_evaluation_contract": "DNE_v2",
            "files": [
                {"path": "inputs/prepared/raw_input_manifest.json"},
                {"path": "evaluation/aggregate_experiment_results_v2.py"},
                {"path": "evaluation/metric_spec_v2_1.json"},
            ],
        }))


if __name__ == "__main__":
    unittest.main()
