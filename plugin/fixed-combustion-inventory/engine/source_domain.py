"""Source-classification domain rules shared by the workbook engine and tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


POWER_PRODUCTION_CODES = {"4411", "4412", "4417"}
HEAT_PRODUCTION_CODE = "4430"
INDUSTRIAL_BOILER_EQUIPMENT = {"燃煤锅炉", "燃气锅炉", "燃油锅炉", "其他锅炉"}


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _industry_code(value: object) -> str:
    text = _clean(value).split(".")[0]
    return text.zfill(4) if text.isdigit() else text


def _identity_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def stable_source_id(pseudonymous_entity_id: object, sequence: object, year: object) -> str:
    """Create the target-neutral identifier shared by ingestion and evaluation."""
    entity = _identity_scalar(pseudonymous_entity_id)
    serial = _identity_scalar(sequence)
    inventory_year = _identity_scalar(year)
    if not entity or not serial or not inventory_year:
        raise ValueError("source identity requires pseudonymous entity id, Base-102 sequence, and inventory year")
    digest = hashlib.sha256(f"{entity}|{serial}|{inventory_year}".encode("utf-8")).hexdigest()[:20].upper()
    return f"SRC-RAW-{digest}"


@dataclass(frozen=True)
class SourceDecision:
    target: str
    department: str
    equipment: str
    reason: str


def classify_source(
    *,
    industry_code: object,
    industrial_equipment: object,
    power_equipment: object,
) -> SourceDecision:
    """Classify one raw Base-102 candidate without using a target member list.

    GB/T 4754 D4411, D4412 and D4417 are electricity-production activities;
    D4430 is heat production and supply.  The equipment value is selected only
    after the industry scope decision, with a fallback to the other Base-102
    equipment column so that a record's original workbook family cannot decide
    its target class.
    """

    code = _industry_code(industry_code)
    industrial = _clean(industrial_equipment)
    power = _clean(power_equipment)
    equipment = power or industrial

    if code in POWER_PRODUCTION_CODES:
        return SourceDecision("POWER", "电力生产", equipment, "T/CSES 7.1 electricity-production industry")
    if code == HEAT_PRODUCTION_CODE:
        return SourceDecision("POWER", "热力生产和供应", equipment, "T/CSES 7.1 heat-production industry")

    try:
        numeric = int(code)
    except ValueError:
        numeric = -1
    candidate_equipment = industrial or power
    if 600 <= numeric <= 4399 and candidate_equipment in INDUSTRIAL_BOILER_EQUIPMENT:
        return SourceDecision(
            "INDUSTRIAL",
            "采矿业和制造业",
            candidate_equipment,
            "T/CSES 8.3 mining/manufacturing boiler",
        )
    return SourceDecision("EXCLUDE", "", candidate_equipment, "outside current fixed-combustion scope")
