#!/usr/bin/env python3
"""Compare two normalized runs for repeat stability or B1 semantic retention."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


FIELDS = (
    "status",
    "method_category",
    "activity_record",
    "parameter_record",
    "control_record",
    "generation_t",
    "emission_t",
    "standard_reference",
    "reason_codes",
)


def number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def numbers_equal(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, abs(left) * 1e-6, abs(right) * 1e-6)


def semantic_equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(semantic_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        unmatched = list(right)
        for value in left:
            for index, candidate in enumerate(unmatched):
                if semantic_equal(value, candidate):
                    unmatched.pop(index)
                    break
            else:
                return False
        return not unmatched
    left_number = number(left)
    right_number = number(right)
    if left_number is not None and right_number is not None:
        return numbers_equal(left_number, right_number)
    return str(left or "").strip() == str(right or "").strip()


def decode(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def read_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = {}
    for row in rows:
        key = (row["source_id"], row["pollutant"])
        if key in result:
            raise ValueError(f"duplicate normalized key in {path}: {key}")
        result[key] = row
    return result


def compare(left_path: Path, right_path: Path) -> dict[str, Any]:
    left = read_rows(left_path)
    right = read_rows(right_path)
    all_keys = sorted(set(left) | set(right))
    mismatch_by_field: Counter[str] = Counter()
    mismatch_examples: list[dict[str, Any]] = []
    passed = 0
    for key in all_keys:
        if key not in left or key not in right:
            mismatch_by_field["row_presence"] += 1
            if len(mismatch_examples) < 30:
                mismatch_examples.append({"source_id": key[0], "pollutant": key[1], "fields": ["row_presence"]})
            continue
        mismatched = []
        for field in FIELDS:
            if not semantic_equal(decode(left[key].get(field, "")), decode(right[key].get(field, ""))):
                mismatch_by_field[field] += 1
                mismatched.append(field)
        if mismatched:
            if len(mismatch_examples) < 30:
                mismatch_examples.append({"source_id": key[0], "pollutant": key[1], "fields": mismatched})
        else:
            passed += 1
    total = len(all_keys)
    return {
        "left": str(left_path),
        "right": str(right_path),
        "comparison_fields": list(FIELDS),
        "total_units": total,
        "passed_units": passed,
        "failed_units": total - passed,
        "agreement_rate": (passed / total) if total else None,
        "mismatch_by_field": dict(mismatch_by_field),
        "mismatch_examples": mismatch_examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(args.left.resolve(), args.right.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
