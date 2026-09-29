"""Normalize equivalent reason expressions in recorded method outputs."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


ALIASES = (
    (r'NO_FUEL_DATA|NO_VALID_FUEL_(?:DATA(?:_FOR_CALCULATION_\(NAN_OR_ZERO\))?|CONSUMPTION)|FUEL_CONSUMPTION_(?:NAN|MISSING)|FUEL_CONSUMPTION_QUANTITY_IS_MISSING(?:_\(NAN\))?', 'ACTIVITY_MISSING'),
    (r'NEGATIVE_FUEL_(?:CONSUMPTION(?:_(?:VALUE|QUANTITY)(?:_IN_SOURCE_DATA)?)?|QTY(?:_FUEL\d+)?)', 'ACTIVITY_OUT_OF_RANGE'),
    (r'(?:SULFUR_CONTENT_MISSING(?:_FOR_MATERIAL_BALANCE)?|(?:SO2_)?MISSING_SULFUR_DATA|SULFUR_MISSING_OR_ZERO)', 'SULFUR_MISSING'),
    (r'(?:ASH_CONTENT_MISSING(?:_FOR_MATERIAL_BALANCE)?|(?:(?:PM25|PM10|BC|OC)_)?MISSING_ASH_DATA|ASH_MISSING_OR_ZERO)', 'ASH_MISSING'),
    (r'NO_MB_PARAM|MATERIAL_BALANCE_PARAMETER_MISSING', 'FACTOR_INPUT_MISSING'),
)


def normalize_reason_text(value):
    text = re.sub(r'\s*([:;])\s*', r'\1', str(value or '').strip())
    text = re.sub(r'[-\s]+', '_', text).upper()
    for pattern, code in ALIASES:
        text = re.sub(r'(?<![A-Z0-9_])(?:' + pattern + r')(?![A-Z0-9_])', code, text)
    return text


@lru_cache(maxsize=1)
def default_equivalence():
    path = Path(__file__).resolve().parents[1] / 'reference/frozen/v2.0.0/reason_code_equivalence.json'
    return json.loads(path.read_text(encoding='utf-8'))


def reason_entries(value, equivalence=None):
    equivalence = default_equivalence() if equivalence is None else equivalence
    codes = equivalence.get('codes', {})
    canonical = {r['canonical_root_cause']: r for r in codes.values()}
    parts = value if isinstance(value, list) else str(value or '').split(';')
    result = {}
    for part in parts:
        raw = str(part).strip()
        if not raw:
            continue
        if raw in canonical:
            result[raw] = bool(canonical[raw].get('requires_user_judgment'))
            continue
        text = normalize_reason_text(raw)
        matched = False
        # Component and pollutant prefixes are retained in the input text.
        for code, mapping in codes.items():
            if re.search(r'(?<![A-Z0-9_])' + re.escape(code.upper()) + r'(?![A-Z0-9_])', text):
                result[mapping['canonical_root_cause']] = bool(mapping.get('requires_user_judgment'))
                matched = True
        if matched:
            continue
        rule = next((r for r in equivalence.get('generic_root_rules', [])
                     if re.search(r['regex'], raw, re.I) or re.search(r['regex'], text, re.I)), None)
        if rule:
            result[rule['canonical_root_cause']] = bool(rule.get('requires_user_judgment'))
        else:
            result['unmapped::' + text] = text not in {'RULE_CHAIN_COMPLETE', 'CALCULATED'}
    return result


def canonical_reason_codes(value):
    if isinstance(value, str) and value.strip().startswith('['):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            # A free-text reason can start with a bracketed source count.
            pass
    return sorted(reason_entries(value))
