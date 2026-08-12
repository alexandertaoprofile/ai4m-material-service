"""Shared, conservative parsing primitives for stated numeric screening limits.

Workflows own their property vocabulary and units; this module owns only the
meaning of common comparison and interval expressions.  It never creates a
threshold from a qualitative preference.
"""
from __future__ import annotations

import re
from typing import Any


BOUND_OPERATOR = r"小于|低于|不高于|不超过|不大于|至多|最多|上限(?:为)?|≤|<=|<|大于|高于|不低于|不少于|至少|最少|下限(?:为)?|≥|>=|>"
RANGE_JOINER = r"(?:-|–|—|~|～|至|到|和|与)"


def bound_operator(value: str) -> str:
    """Normalize an explicit linguistic comparator without widening it."""
    return ">=" if value in {"大于", "高于", "不低于", "不少于", "至少", "最少", "下限", "下限为", "≥", ">=", ">"} else "<="


def range_constraints(*, key: str, name: str, unit: str, lower: float, upper: float) -> list[dict[str, Any]]:
    """Return two hard bounds for a stated interval, preserving its unit."""
    lower, upper = sorted((float(lower), float(upper)))
    return [
        {key: name, "operator": ">=", "value": lower, "unit": unit},
        {key: name, "operator": "<=", "value": upper, "unit": unit},
    ]


def bound_constraints(matches: list[re.Match[str]], *, key: str, name: str, unit: str) -> list[dict[str, Any]]:
    """Translate regex matches with named ``operator``/``value`` captures."""
    return [
        {key: name, "operator": bound_operator(match.group("operator")), "value": float(match.group("value")), "unit": unit}
        for match in matches
    ]
