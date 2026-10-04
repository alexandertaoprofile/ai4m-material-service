"""Service-side DNG presence conditioning, not a trained conditional checkpoint.

Partial species anchors are chosen before joint lattice/position integration.
Never relabel atoms in exported structures to manufacture compliant candidates.
"""
from __future__ import annotations

import math
import random
from functools import reduce


def presence_anchor_plan(allowed, required, minimum, atoms, samples, seed):
    """Return reproducible partial-site templates; unanchored species remain free.

    Coverage rotates across optional elements. At most half the sites are anchored
    unless the requested element count itself needs more. No property claims or
    chemical ratios are inferred from application prose.
    """
    allowed = list(dict.fromkeys(allowed))
    required = list(dict.fromkeys(required))
    minimum = max(minimum, len(required))
    if not required and minimum <= 1:
        return [{} for _ in range(samples)]
    if minimum > min(atoms, len(allowed)) or not set(required) <= set(allowed):
        raise ValueError("Presence anchors cannot satisfy the requested element set")
    optional = [number for number in allowed if number not in required]
    templates = []
    for index in range(samples):
        rng = random.Random(seed * 1000003 + index)
        need = minimum - len(required)
        offset = index % len(optional) if optional else 0
        chosen = required + (optional[offset:] + optional[:offset])[:need]
        maximum = max(len(chosen), atoms // 2)
        budget = len(chosen) + index % (maximum - len(chosen) + 1)
        labels = chosen + [rng.choice(chosen) for _ in range(budget - len(chosen))]
        sites = rng.sample(range(atoms), budget)
        templates.append(dict(zip(sites, labels)))
    return templates


def composition_errors(counts, constraints):
    elements = {element for element, count in counts.items() if count > 0}
    failures = []
    allowed = set(constraints.get("allowed_elements", []))
    if allowed and not elements <= allowed:
        failures.append("含允许集合之外的元素：" + "、".join(sorted(elements - allowed)))
    missing = set(constraints.get("required_elements", [])) - elements
    if missing:
        failures.append("缺少必含元素：" + "、".join(sorted(missing)))
    excluded = set(constraints.get("excluded_elements", [])) & elements
    if excluded:
        failures.append("含禁止元素：" + "、".join(sorted(excluded)))
    minimum = constraints.get("min_distinct_elements", 1)
    if len(elements) < minimum:
        failures.append(f"组成仅含 {len(elements)} 种元素，要求至少 {minimum} 种")
    return failures


def composition_summary(candidates):
    """Count compositions separately from cells/structure families or novelty."""
    rows = [candidate.get("metadata", {}).get("element_counts", {}) for candidate in candidates]
    rows = [{key: int(value) for key, value in row.items() if value > 0} for row in rows if row]
    signatures = set()
    for row in rows:
        divisor = reduce(math.gcd, row.values())
        signatures.add(tuple(sorted((key, value // divisor) for key, value in row.items())))
    return {"parsed_count": len(rows), "multi_element_count": sum(len(row) > 1 for row in rows),
        "unary_count": sum(len(row) == 1 for row in rows), "distinct_composition_count": len(signatures),
        "distinct_element_sets": len({tuple(sorted(row)) for row in rows})}
