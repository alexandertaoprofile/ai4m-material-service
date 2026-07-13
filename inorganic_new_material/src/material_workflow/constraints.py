"""Normalize direct and upstream-service material-discovery requests."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, Mapping, Tuple

from .schemas import GenerationConstraint

_ELEMENT = re.compile(r"^[A-Z][a-z]?$")
_ELEMENT_TOKEN = re.compile(r"[A-Z][a-z]?")
_FORMULA = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])")
_CONSTRAINT_KEYS = ("new_material", "mattergen", "generation_constraints", "constraints")


def _elements(values: Iterable[Any]) -> list[str]:
    result = []
    for value in values:
        element = str(value).strip().capitalize()
        if not _ELEMENT.fullmatch(element):
            raise ValueError(f"Invalid element symbol: {value!r}")
        if element not in result:
            result.append(element)
    return result


def _numeric_mapping(value: Any, field_name: str, *, allow_none: bool = False) -> Dict[str, float | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: Dict[str, float] = {}
    for name, item in value.items():
        if item is None and allow_none:
            result[str(name)] = None
            continue
        if isinstance(item, bool):
            raise ValueError(f"{field_name}.{name!r} must be numeric")
        try:
            result[str(name)] = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}.{name!r} must be numeric") from exc
    return result


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("idea") or value.get("content") or value.get("text") or value.get("query") or "")
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item))
    return str(value or "")


def _formula_from_text(text: str) -> str | None:
    for formula in _FORMULA.findall(text):
        elements = _ELEMENT_TOKEN.findall(re.sub(r"\d+(?:\.\d+)?", "", formula))
        if len(elements) >= 2:
            return formula
    return None


def _constraint_source(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge a standard upstream envelope with its optional explicit contract."""
    source = dict(payload)
    for key in _CONSTRAINT_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            source.update(nested)
            break
    if not source.get("target_properties") and isinstance(source.get("property_targets"), Mapping):
        source["target_properties"] = source["property_targets"]
    if not source.get("validation_targets") and isinstance(source.get("validation_requirements"), Mapping):
        source["validation_targets"] = source["validation_requirements"]
    return source


def constraint_from_payload(payload: Dict[str, Any]) -> GenerationConstraint:
    """Convert a direct HTTP or existing ``/start`` envelope into constraints.

    Explicit ``constraints`` win.  For legacy upstream payloads, only a formula
    is conservatively inferred from ``idea``; numeric property targets are never
    guessed from prose.
    """
    source = _constraint_source(payload)
    taskid = str(source.get("taskid") or payload.get("taskid") or uuid.uuid4().hex).strip()
    if not taskid or taskid in {".", ".."} or "/" in taskid or "\\" in taskid:
        raise ValueError("taskid must be a non-empty identifier without path separators")
    raw_requirement = _text(source.get("raw_requirement") or payload.get("idea") or payload.get("instruction"))
    target_formula = str(source.get("target_formula") or "").strip() or None
    candidate_formulas = source.get("candidate_formulas") or source.get("formulas") or []
    if not target_formula and isinstance(candidate_formulas, list) and candidate_formulas:
        target_formula = str(candidate_formulas[0]).strip() or None
    if not target_formula:
        target_formula = _formula_from_text(raw_requirement)
    allowed = source.get("allowed_elements") or source.get("elements") or []
    if isinstance(allowed, str):
        allowed = [item for item in re.split(r"[-,，\s]+", allowed) if item]
    return GenerationConstraint(
        taskid=taskid,
        raw_requirement=raw_requirement,
        target_formula=target_formula,
        allowed_elements=_elements(allowed),
        excluded_elements=_elements(source.get("excluded_elements") or source.get("forbidden_elements") or []),
        target_properties=_numeric_mapping(source.get("target_properties"), "target_properties"),
        validation_targets=_numeric_mapping(source.get("validation_targets"), "validation_targets", allow_none=True),
        notes=[str(note) for note in (source.get("notes") or [])],
    )


def upstream_contract(payload: Mapping[str, Any]) -> Tuple[GenerationConstraint, Dict[str, Any]]:
    """Return normalized constraints plus request provenance safe for manifests."""
    constraint = constraint_from_payload(dict(payload))
    return constraint, {
        "user_name": str(payload.get("user_name") or ""),
        "has_file_metadata": bool(payload.get("file_metadata")),
        "upstream_contract": next((key for key in _CONSTRAINT_KEYS if isinstance(payload.get(key), Mapping)), "legacy_envelope"),
    }
