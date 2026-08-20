"""Stage-R rule-package validation and coverage accounting."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


REQUIRED_MANIFEST_KEYS = {
    "package_id",
    "version",
    "status",
    "standard",
    "scope",
    "files",
}
REQUIRED_RULE_KEYS = {
    "rule_id",
    "rule_type",
    "source",
    "scope",
    "conditions",
    "action",
    "on_missing",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _iter_rule_documents(candidate_path: Path):
    for path in sorted((candidate_path / "rules").glob("*.yaml")):
        payload = _load_yaml(path)
        if isinstance(payload, dict) and isinstance(payload.get("rules"), list):
            for index, item in enumerate(payload["rules"], start=1):
                yield path, index, item
        elif isinstance(payload, list):
            for index, item in enumerate(payload, start=1):
                yield path, index, item
        else:
            yield path, 1, payload


def validate_candidate(candidate_path: Path, source_pdf_path: Path, report_path: Path) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    counts = {"rules": 0, "parameters": 0, "mappings": 0, "tests": 0}
    candidate_path = candidate_path.resolve()
    source_pdf_path = source_pdf_path.resolve()
    manifest_path = candidate_path / "manifest.yaml"

    if not source_pdf_path.is_file():
        errors.append({"code": "SOURCE_PDF_MISSING", "path": str(source_pdf_path)})
    if not manifest_path.is_file():
        errors.append({"code": "MANIFEST_MISSING", "path": str(manifest_path)})
        manifest = {}
    else:
        try:
            manifest = _load_yaml(manifest_path) or {}
        except Exception as exc:
            manifest = {}
            errors.append({"code": "MANIFEST_YAML_INVALID", "detail": str(exc)})

    if isinstance(manifest, dict):
        for key in sorted(REQUIRED_MANIFEST_KEYS - set(manifest)):
            errors.append({"code": "MANIFEST_KEY_MISSING", "key": key})
        if manifest.get("status") not in {"candidate", "awaiting_professional_review", "frozen"}:
            errors.append({"code": "INVALID_CANDIDATE_STATUS", "value": manifest.get("status")})
        declared_pdf_hash = ((manifest.get("standard") or {}).get("sha256") if isinstance(manifest.get("standard"), dict) else None)
        if source_pdf_path.is_file() and declared_pdf_hash and declared_pdf_hash != _sha256(source_pdf_path):
            errors.append({"code": "SOURCE_PDF_HASH_MISMATCH"})
    else:
        errors.append({"code": "MANIFEST_NOT_OBJECT"})
        manifest = {}

    seen_rule_ids: set[str] = set()
    rule_files = list((candidate_path / "rules").glob("*.yaml")) if (candidate_path / "rules").is_dir() else []
    if not rule_files:
        errors.append({"code": "RULE_FILES_MISSING"})
    for path, index, rule in _iter_rule_documents(candidate_path):
        counts["rules"] += 1
        location = f"{path.name}#{index}"
        if not isinstance(rule, dict):
            errors.append({"code": "RULE_NOT_OBJECT", "location": location})
            continue
        missing = REQUIRED_RULE_KEYS - set(rule)
        if missing:
            errors.append({"code": "RULE_KEYS_MISSING", "location": location, "keys": sorted(missing)})
        rule_id = str(rule.get("rule_id", "")).strip()
        if not rule_id:
            errors.append({"code": "RULE_ID_EMPTY", "location": location})
        elif rule_id in seen_rule_ids:
            errors.append({"code": "RULE_ID_DUPLICATE", "rule_id": rule_id})
        else:
            seen_rule_ids.add(rule_id)
        source = rule.get("source")
        if not isinstance(source, dict) or not source.get("section"):
            errors.append({"code": "RULE_SOURCE_SECTION_MISSING", "rule_id": rule_id})
        action = rule.get("action")
        if not isinstance(action, dict) or not action.get("type"):
            errors.append({"code": "RULE_ACTION_INVALID", "rule_id": rule_id})

    for directory, key in (("parameters", "parameters"), ("mappings", "mappings")):
        folder = candidate_path / directory
        files = sorted(folder.glob("*.csv")) if folder.is_dir() else []
        if not files:
            warnings.append({"code": f"{directory.upper()}_FILES_MISSING"})
        for path in files:
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if not reader.fieldnames:
                        errors.append({"code": "CSV_HEADER_MISSING", "path": str(path)})
                        continue
                    for row_number, row in enumerate(reader, start=2):
                        if any(value not in (None, "") for value in row.values()):
                            counts[key] += 1
                        if None in row:
                            errors.append({"code": "CSV_COLUMN_OVERFLOW", "path": str(path), "row": row_number})
            except Exception as exc:
                errors.append({"code": "CSV_INVALID", "path": str(path), "detail": str(exc)})

    tests_dir = candidate_path / "tests"
    test_files = sorted(tests_dir.glob("*.jsonl")) if tests_dir.is_dir() else []
    if not test_files:
        errors.append({"code": "TEST_FILES_MISSING"})
    for path in test_files:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    case = json.loads(line)
                    counts["tests"] += 1
                except json.JSONDecodeError as exc:
                    errors.append({"code": "TEST_JSON_INVALID", "path": str(path), "line": line_number, "detail": str(exc)})
                    continue
                if not isinstance(case, dict) or not case.get("test_id") or not case.get("rule_id"):
                    errors.append({"code": "TEST_KEYS_MISSING", "path": str(path), "line": line_number})
                elif case["rule_id"] not in seen_rule_ids:
                    errors.append({"code": "TEST_RULE_UNKNOWN", "test_id": case.get("test_id"), "rule_id": case.get("rule_id")})

    declared_files = manifest.get("files", []) if isinstance(manifest, dict) else []
    if isinstance(declared_files, list):
        for item in declared_files:
            if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
                errors.append({"code": "MANIFEST_FILE_ENTRY_INVALID", "entry": item})
                continue
            file_path = candidate_path / item["path"]
            if not file_path.is_file():
                errors.append({"code": "DECLARED_FILE_MISSING", "path": item["path"]})
            elif _sha256(file_path) != item["sha256"]:
                errors.append({"code": "DECLARED_FILE_HASH_MISMATCH", "path": item["path"]})

    result = {
        "success": not errors,
        "stage": "rule_package_validation",
        "candidate_path": str(candidate_path),
        "source_pdf_sha256": _sha256(source_pdf_path) if source_pdf_path.is_file() else None,
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
        "professional_review_status": manifest.get("professional_review_status", "not_performed"),
    }
    _write_json(report_path.resolve(), result)
    return result


def _candidate_rule_ids(candidate_path: Path) -> set[str]:
    result: set[str] = set()
    for _, _, rule in _iter_rule_documents(candidate_path.resolve()):
        if isinstance(rule, dict) and str(rule.get("rule_id", "")).strip():
            result.add(str(rule["rule_id"]).strip())
    return result


def _required_rule_ids(path: Path) -> set[str]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("required_rules", payload) if isinstance(payload, dict) else payload
        return {
            str(
                item.get("requirement_id", item.get("rule_id", ""))
                if isinstance(item, dict)
                else item
            ).strip()
            for item in rows
            if str(
                item.get("requirement_id", item.get("rule_id", ""))
                if isinstance(item, dict)
                else item
            ).strip()
        }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "rule_id" not in reader.fieldnames:
            raise ValueError("required-rule inventory must contain rule_id")
        return {str(row["rule_id"]).strip() for row in reader if str(row.get("rule_id", "")).strip()}


def _candidate_text(candidate_path: Path) -> str:
    chunks: list[str] = []
    for path in sorted(item for item in candidate_path.rglob("*") if item.is_file()):
        if path.suffix.lower() not in {".yaml", ".yml", ".csv", ".json", ".jsonl", ".md", ".txt"}:
            continue
        chunks.append(path.read_text(encoding="utf-8-sig", errors="replace"))
    return "\n".join(chunks)


def _evidence_matches(candidate_path: Path, rule_ids: set[str], candidate_text: str, evidence: dict[str, Any]) -> bool:
    kind = str(evidence.get("type", ""))
    if kind == "rule_id":
        return str(evidence.get("value", "")) in rule_ids
    if kind == "file_exists":
        return (candidate_path / str(evidence.get("value", ""))).is_file()
    if kind == "text_contains_all":
        return all(str(value) in candidate_text for value in evidence.get("values", []))
    if kind == "text_absent":
        return str(evidence.get("value", "")) not in candidate_text
    if kind == "csv_row":
        path = candidate_path / str(evidence.get("file", ""))
        if not path.is_file():
            return False
        expected = {str(key): str(value) for key, value in (evidence.get("equals") or {}).items()}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return any(all(str(row.get(key, "")) == value for key, value in expected.items()) for row in csv.DictReader(handle))
    raise ValueError(f"unknown coverage evidence type: {kind}")


def compare_coverage(candidate_path: Path, required_inventory_path: Path, report_path: Path) -> dict[str, Any]:
    candidate_path = candidate_path.resolve()
    inventory_path = required_inventory_path.resolve()
    candidate_rule_ids = _candidate_rule_ids(candidate_path)
    candidate_text = _candidate_text(candidate_path)
    if inventory_path.suffix.lower() != ".json":
        candidate = candidate_rule_ids
        required = _required_rule_ids(inventory_path)
        matched = sorted(candidate & required)
        missing = sorted(required - candidate)
        result = {
            "success": True,
            "stage": "rule_coverage",
            "candidate_rule_count": len(candidate),
            "required_rule_count": len(required),
            "matched_rule_count": len(matched),
            "coverage_ratio": (len(matched) / len(required)) if required else None,
            "matched_requirement_ids": matched,
            "missing_requirement_ids": missing,
            "interpretation": "descriptive_stage_r_coverage_only",
        }
        _write_json(report_path.resolve(), result)
        return result

    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    requirements = payload.get("required_rules", [])
    details: list[dict[str, Any]] = []
    for requirement in requirements:
        match = requirement.get("candidate_match") or {}
        evidence = match.get("evidence") or []
        checks = [_evidence_matches(candidate_path, candidate_rule_ids, candidate_text, item) for item in evidence]
        covered = all(checks) if str(match.get("mode", "all")) == "all" else any(checks)
        details.append(
            {
                "requirement_id": requirement["requirement_id"],
                "dimension": requirement["dimension"],
                "category": requirement["category"],
                "requirement": requirement["requirement"],
                "source_type": requirement["source_type"],
                "source_reference": requirement["source_reference"],
                "covered": covered,
                "evidence_checks": checks,
            }
        )

    standard = [item for item in details if item["dimension"] == "standard_extraction"]
    operational = details
    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        matched = [item["requirement_id"] for item in rows if item["covered"]]
        missing = [item["requirement_id"] for item in rows if not item["covered"]]
        return {
            "required_count": len(rows),
            "matched_count": len(matched),
            "coverage_ratio": len(matched) / len(rows) if rows else None,
            "matched_requirement_ids": matched,
            "missing_requirement_ids": missing,
        }

    result = {
        "success": True,
        "stage": "rule_coverage",
        "candidate_path": str(candidate_path),
        "required_inventory_path": str(inventory_path),
        "candidate_rule_count": len(candidate_rule_ids),
        "standard_extraction": summary(standard),
        "operational_readiness": summary(operational),
        "details": details,
        "interpretation": "standard_extraction is the Agent rule-extraction coverage statistic; operational_readiness is a separate pre-freeze gap audit and is not a main-experiment ranking metric",
    }
    _write_json(report_path.resolve(), result)
    return result
