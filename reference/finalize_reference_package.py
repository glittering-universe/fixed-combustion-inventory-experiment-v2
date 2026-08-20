#!/usr/bin/env python3
"""Finalize the audited reference package and create a non-circular lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_PACKAGE = HERE / "frozen" / "v1.0.1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, object]:
    return {"sha256": sha256(path), "bytes": path.stat().st_size}


def finalize(package: Path) -> None:
    manifest_path = package / "manifest.json"
    validation_path = package / "validation_report.json"
    audit_path = package / "subagent_audit.md"
    if not manifest_path.is_file() or not validation_path.is_file() or not audit_path.is_file():
        raise RuntimeError("manifest.json, validation_report.json and subagent_audit.md are required")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "pass":
        raise RuntimeError("validation_report.json is not PASS")
    audit_text = audit_path.read_text(encoding="utf-8")
    if "终审结论：通过" not in audit_text:
        raise RuntimeError("subagent_audit.md does not contain the approved final conclusion")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "audited_frozen"
    manifest["finalized_on"] = str(date.today())
    manifest["audit"] = {
        "validation_report": "validation_report.json",
        "subagent_audit": "subagent_audit.md",
        "decision": "approved",
    }
    for name in ("validation_report.json", "subagent_audit.md"):
        manifest.setdefault("generated_files", {})[name] = file_record(package / name)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    files = sorted(path for path in package.iterdir() if path.is_file() and path.name != "package_lock.json")
    lock = {
        "package_name": manifest["package_name"],
        "version": manifest["version"],
        "status": "audited_frozen",
        "lock_policy": "all package files are covered except package_lock.json itself",
        "files": {path.name: file_record(path) for path in files},
    }
    (package / "package_lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    args = parser.parse_args()
    finalize(args.package.resolve())
    print(json.dumps({"status": "audited_frozen", "package": str(args.package.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
