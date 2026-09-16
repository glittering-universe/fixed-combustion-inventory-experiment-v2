import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "curate_results.py"
SPEC = importlib.util.spec_from_file_location("curate_results", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CuratedArchiveTests(unittest.TestCase):
    def test_archive_roundtrip_and_restore_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runs" / "result.csv"
            source.parent.mkdir()
            source.write_bytes(b"pollutant,value\nSO2,0\n")
            dest = root / "delivery"
            path = dest / "08_完整封存" / "test.tar.zst"
            original = source.read_bytes()
            packed = MODULE.archive([source], path, root)
            packed["destination"] = str(path.relative_to(dest))
            MODULE.save(dest / "文件索引.json", {"archives": [packed]})
            source.unlink()
            with patch.object(MODULE, "ROOT", root), patch.object(MODULE, "DEST", dest):
                MODULE.restore()
                self.assertEqual(source.read_bytes(), original)
                source.write_bytes(b"new user change")
                with self.assertRaisesRegex(RuntimeError, "existing file differs"):
                    MODULE.restore()
                self.assertEqual(source.read_bytes(), b"new user change")

    def test_cleanup_requires_verified_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary)
            MODULE.save(dest / "GitHub上传核验.json", {"status": "pending"})
            with patch.object(MODULE, "DEST", dest):
                with self.assertRaisesRegex(RuntimeError, "verified remote upload"):
                    MODULE.cleanup()

    def test_cleanup_refuses_unarchived_file_before_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dest = root / "delivery"
            source = root / "runs" / "new_result.csv"
            source.parent.mkdir()
            source.write_bytes(b"irreplaceable new result")
            MODULE.save(dest / "GitHub上传核验.json", {"status": "pass"})
            MODULE.save(dest / "文件索引.json", {"archives": [], "files": [], "cleanup_paths": ["runs"]})
            with patch.object(MODULE, "ROOT", root), patch.object(MODULE, "DEST", dest):
                with self.assertRaisesRegex(RuntimeError, "unarchived or changed cleanup file"):
                    MODULE.cleanup()
                self.assertEqual(source.read_bytes(), b"irreplaceable new result")


if __name__ == "__main__":
    unittest.main()
