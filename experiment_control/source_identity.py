#!/usr/bin/env python3
"""Shared, target-neutral source identity contract for the raw base tables."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Mapping


PSEUDONYM_SALT = "fixed-combustion-2022"
PSEUDONYM_PREFIXES = ("CREDIT-", "ORG-", "ENT-")


def _load_domain_stable_source_id():
    module_name = "fixed_combustion_source_domain_identity"
    path = Path(__file__).resolve().parents[1] / "plugin" / "fixed-combustion-inventory" / "engine" / "source_domain.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load source identity domain module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.stable_source_id


_domain_stable_source_id = _load_domain_stable_source_id()


def clean_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def stable_pseudonym(prefix: str, value: object) -> str:
    """Return the experiment's established, deterministic privacy pseudonym."""
    cleaned = clean_scalar(value)
    if not cleaned:
        return ""
    normalized_prefix = prefix.rstrip("-").upper()
    expected = normalized_prefix + "-"
    if cleaned.startswith(expected):
        return cleaned
    digest = hashlib.sha256(f"{PSEUDONYM_SALT}|{cleaned}".encode("utf-8")).hexdigest()[:12].upper()
    return f"{normalized_prefix}-{digest}"


def pseudonymous_entity_id(*, credit: object, organization: object, company: object) -> str:
    """Choose the first available stable entity key: credit > org > company."""
    for prefix, value in (("CREDIT", credit), ("ORG", organization), ("ENT", company)):
        pseudonym = stable_pseudonym(prefix, value)
        if pseudonym:
            return pseudonym
    raise ValueError("cannot construct entity identity without credit, organization, or company")


def source_id_for_base102(
    pseudonymous_entity_id_value: object,
    *,
    sequence: object,
    inventory_year: object,
) -> str:
    """Build a stable Base-102 row identifier without a target-class label."""
    return _domain_stable_source_id(pseudonymous_entity_id_value, sequence, inventory_year)


def identity_from_base102_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Resolve the shared entity and source identifiers from one raw Base-102 row."""
    entity = pseudonymous_entity_id(
        credit=row.get("统一社会信用代码"),
        organization=row.get("组织机构代码"),
        company=row.get("填报单位详细名称"),
    )
    source_id = source_id_for_base102(
        entity,
        sequence=row.get("序号"),
        inventory_year=row.get("统计年份"),
    )
    return {"pseudonymous_entity_id": entity, "source_id": source_id}
