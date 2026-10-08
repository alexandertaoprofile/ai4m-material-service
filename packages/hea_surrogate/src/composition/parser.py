"""Strict composition parsing and conversion to atomic percent."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping

ATOMIC_WEIGHTS = {
    "Al": 26.9815385, "B": 10.81, "C": 12.011, "Co": 58.933194,
    "Cr": 51.9961, "Cu": 63.546, "Fe": 55.845, "Hf": 178.49,
    "Mn": 54.938044, "Mo": 95.95, "Nb": 92.90637, "Ni": 58.6934,
    "Pd": 106.42, "Re": 186.207, "Si": 28.085, "Ta": 180.94788,
    "Ti": 47.867, "V": 50.9415, "W": 183.84, "Zr": 91.224,
    "Y": 88.90584, "Zn": 65.38, "Ag": 107.8682, "Au": 196.96657,
    "Pt": 195.084, "Sn": 118.71,
    "Ca": 40.078, "Ga": 69.723, "Li": 6.94, "Mg": 24.305,
    "Nd": 144.242, "Sc": 44.955908,
}
_TOKEN = re.compile(r"([A-Z][a-z]?)(?:\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+))?")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


class CompositionParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedComposition:
    original: str
    basis: str
    atomic_percent: dict[str, float]

    @property
    def canonical_formula(self) -> str:
        return "".join(f"{e}{v:g}" for e, v in sorted(self.atomic_percent.items()))


def _clean(text: str) -> tuple[str, str]:
    value = str(text).translate(_SUBSCRIPTS).strip()
    if not value or value.lower() in {"nan", "none"}:
        raise CompositionParseError("empty composition")
    basis = "at"
    lowered = value.lower()
    if re.search(r"(?:at\.?\s*%|atomic\s*%)", lowered):
        basis = "at"
    elif re.search(r"(?:wt\.?\s*%|weight\s*%)", lowered):
        basis = "wt"
    value = re.sub(r"(?:at\.?\s*%|atomic\s*%|wt\.?\s*%|weight\s*%)", "", value, flags=re.I)
    value = value.replace("−", "-").replace("·", "").replace(",", "").replace(";", "")
    value = re.sub(r"\s+", "", value)
    return value, basis


def parse_composition(text: str, *, basis: str | None = None) -> ParsedComposition:
    """Parse compact alloy notation, rejecting ambiguous or malformed input.

    Bare coefficients are relative atomic amounts unless the expression carries
    a `wt.%` marker.  Coefficients need not sum to 100; they are normalized.
    """
    original = str(text)
    value, marked_basis = _clean(original)
    basis = basis or marked_basis
    if basis not in {"at", "wt"}:
        raise CompositionParseError(f"unsupported basis: {basis}")
    def parse_segment(segment: str) -> dict[str, float]:
        values: dict[str, float] = {}
        pos = 0
        while pos < len(segment):
            if segment[pos] == "(":
                end = segment.find(")", pos + 1)
                if end < 0:
                    raise CompositionParseError(f"unclosed group in composition: {original!r}")
                group = parse_segment(segment[pos + 1:end])
                coefficient_match = re.match(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", segment[end + 1:])
                coefficient = float(coefficient_match.group()) if coefficient_match else 1.0
                if coefficient_match: end += len(coefficient_match.group())
                group_total = sum(group.values())
                if not group_total or coefficient < 0:
                    raise CompositionParseError(f"invalid group in composition: {original!r}")
                for element, amount in group.items():
                    values[element] = values.get(element, 0.0) + coefficient * amount / group_total
                pos = end + 1
                continue
            match = _TOKEN.match(segment, pos)
            if not match:
                raise CompositionParseError(f"unparsed characters in composition: {original!r}")
            element, amount = match.groups()
            if element not in ATOMIC_WEIGHTS:
                raise CompositionParseError(f"unsupported element {element}")
            number = float(amount) if amount else 1.0
            if not math.isfinite(number) or number < 0:
                raise CompositionParseError(f"invalid amount for {element}")
            if number:
                values[element] = values.get(element, 0.0) + number
            pos = match.end()
        return values

    values = parse_segment(value)
    if basis == "wt":
        values = {e: v / ATOMIC_WEIGHTS[e] for e, v in values.items()}
    total = sum(values.values())
    if total <= 0:
        raise CompositionParseError("composition sum is not positive")
    atomic_percent = {e: 100.0 * v / total for e, v in values.items()}
    if not math.isclose(sum(atomic_percent.values()), 100.0, abs_tol=1e-8):
        raise CompositionParseError("atomic percent normalization failed")
    return ParsedComposition(original=original, basis=basis, atomic_percent=dict(sorted(atomic_percent.items())))
