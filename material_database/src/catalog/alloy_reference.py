"""Deterministic catalogue-family selection for alloy-design requests.

This is catalogue policy rather than service orchestration: it selects the
already-recorded material families that can be shown before an alloy
composition workflow continues.  It never resolves a proposed composition as
an existing commodity grade.
"""
from __future__ import annotations

import re
from typing import Any


_ALLOY_ELEMENT_SEQUENCE = re.compile(
    r"(?<![A-Za-z])(?:Fe|Ni|Co|Cr|Al|Cu|Ti|Mo|Nb|W|V)(?:\s*[-/、]\s*(?:Fe|Ni|Co|Cr|Al|Cu|Ti|Mo|Nb|W|V)){1,}(?![A-Za-z])",
    flags=re.IGNORECASE,
)


def catalog_reference_for_alloy_request(text: str) -> dict[str, Any] | None:
    """Return compatible recorded material families for a clear alloy request."""
    element_match = _ALLOY_ELEMENT_SEQUENCE.search(text)
    alloy_words = ("合金", "高温金属", "金属材料", "镍基", "铁基", "钴基", "不锈钢")
    if not element_match and not any(word in text for word in alloy_words):
        return None

    families: list[str] = []
    reasons: list[str] = []
    element_text = element_match.group(0) if element_match else ""
    elements = {
        item.casefold()
        for item in re.findall(r"Fe|Ni|Co|Cr|Al|Cu|Ti|Mo|Nb|W|V", element_text, flags=re.IGNORECASE)
    }
    high_temperature_alloy = (
        "高温" in text
        or "高熵" in text
        or "多主元" in text
        or bool({"ni", "co", "cr", "mo", "nb", "w"} & elements)
    )

    if high_temperature_alloy or any(word in text for word in ("镍基", "钴基", "inconel")):
        families.append("镍基高温合金")
        reasons.append("高温/多主元合金需求以已入库镍基高温合金作可追溯基准")
    if "fe" in elements or any(word in text for word in ("铁基", "不锈钢")):
        families.append("奥氏体不锈钢")
        reasons.append("含铁合金需求以已入库奥氏体不锈钢作金属基准")
    if not element_match and "铝合金" in text:
        families.append("铝合金")
        reasons.append("铝合金需求映射至已入库铝合金基准")
    if not element_match and "钛合金" in text:
        families.append("α+β钛合金")
        reasons.append("钛合金需求映射至已入库钛合金基准")

    families = list(dict.fromkeys(families))
    if not families:
        return None
    return {
        "mode": "alloy_catalog_reference",
        "target": element_text or "合金需求",
        "families": families,
        "reason": "；".join(reasons),
    }
