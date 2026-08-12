"""Traceable querying for the structured mature-material catalogue.

The catalogue intentionally treats a material grade, heat-treatment state and
source record as separate evidence.  It never silently merges near names such
as 316 and 316L.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from src.catalog.property_vocabulary import vocabulary_aliases


PROPERTY_ALIASES = {
    "density": "density",
    "密度": "density",
    "specific_heat": "specific_heat",
    "specificheat": "specific_heat",
    "比热": "specific_heat",
    "thermal_conductivity": "thermal_conductivity",
    "thermalconductivity": "thermal_conductivity",
    "导热率": "thermal_conductivity",
    "导热": "thermal_conductivity",
    "interfacial_bond_strength": "interfacial_bond_strength",
    "界面结合力": "interfacial_bond_strength",
    "层间结合力": "interfacial_bond_strength",
    "结合力": "interfacial_bond_strength",
    "thermal_diffusivity": "thermal_diffusivity",
    "thermaldiffusivity": "thermal_diffusivity",
    "热扩散率": "thermal_diffusivity",
    "yield_strength": "yield_strength",
    "yieldstrength": "yield_strength",
    "yield_strength_mpa": "yield_strength",
    "yieldstrengthmpa": "yield_strength",
    "屈服强度": "yield_strength",
    "hardness": "hardness",
    "硬度": "hardness",
    "hardness_vickers": "hardness_vickers",
    "tensile_strength": "tensile_strength",
    "ultimate_tensile_strength": "ultimate_tensile_strength",
    "ultimatetensilestrength": "ultimate_tensile_strength",
    "uts": "ultimate_tensile_strength",
    "抗拉强度": "ultimate_tensile_strength",
    "极限抗拉强度": "ultimate_tensile_strength",
    "tensilestrength": "tensile_strength",
    "拉伸强度": "tensile_strength",
    "youngs_modulus": "youngs_modulus",
    "youngsmodulus": "youngs_modulus",
    "杨氏模量": "youngs_modulus",
    "elongation": "elongation",
    "延伸率": "elongation",
    "plastic_elongation": "plastic_elongation",
    "塑性延伸率": "plastic_elongation",
    "grain_size": "grain_size",
    "晶粒尺寸": "grain_size",
    "density_calculated": "density_calculated",
    "计算密度": "density_calculated",
    "heat_deflection_temperature": "heat_deflection_temperature",
    "heatdeflectiontemperature": "heat_deflection_temperature",
    "热变形温度": "heat_deflection_temperature",
}


def normalize_name(value: Any) -> str:
    """Normalize only spelling/punctuation; never erase grade-defining letters."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


PROPERTY_ALIASES.update({normalize_name(alias): property_name for alias, property_name in vocabulary_aliases().items()})


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _read(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass(frozen=True)
class PropertyConstraint:
    property: str
    operator: str
    value: float
    unit: str | None = None
    temperature_K: float | None = None


@dataclass(frozen=True)
class PreferenceGoal:
    property: str
    direction: str


class MatureMaterialCatalog:
    """In-memory, source-preserving catalogue suitable for the current scale."""

    def __init__(self, root: Path):
        self.root = root
        bundles = [root]
        for bundle_root in (root / "material_core", root / "high_temperature"):
            if bundle_root.is_dir():
                bundles.extend(sorted(bundle_root.glob("*/")))
        self.materials = [row for bundle in bundles for row in _read(bundle / "materials.csv")]
        self.points = [row for bundle in bundles for row in _read(bundle / "property_points.csv")]
        self.curves = [row for bundle in bundles for row in _read(bundle / "curve_data.csv")]
        self.compositions = [row for bundle in bundles for row in _read(bundle / "composition_long.csv")]
        self.aliases = [row for bundle in bundles for row in _read(bundle / "material_aliases.csv")]
        self._by_id = {row["material_id"]: row for row in self.materials}
        self._alias_index = self._build_alias_index()

    @property
    def ready(self) -> bool:
        return bool(self.materials)

    def _build_alias_index(self) -> dict[str, list[dict[str, str]]]:
        index: dict[str, list[dict[str, str]]] = {}
        def add(alias: str, material_id: str, alias_type: str, provenance: str) -> None:
            key = normalize_name(alias)
            if key and material_id in self._by_id:
                index.setdefault(key, []).append({
                    "material_id": material_id, "alias": alias,
                    "alias_type": alias_type, "provenance": provenance,
                })

        for row in self.materials:
            for field, alias_type in (("display_name", "display_name"), ("grade", "grade"), ("UNS/standard", "standard")):
                if row.get(field):
                    add(row[field], row["material_id"], alias_type, "materials.csv")
        for row in self.aliases:
            if row.get("alias") and row.get("material_id"):
                add(row["alias"], row["material_id"], row.get("alias_type") or "manual", row.get("source") or "material_aliases.csv")
        return index

    def resolve_names(self, names: Iterable[str]) -> tuple[list[str], list[dict[str, Any]]]:
        """Resolve exact aliases; ambiguous aliases are reported, not guessed."""
        ids: list[str] = []
        trace: list[dict[str, Any]] = []
        for supplied in names:
            matches = self._alias_index.get(normalize_name(supplied), [])
            material_ids = list(dict.fromkeys(item["material_id"] for item in matches))
            # The immutable 1101 import preserves a second identity row for
            # some already-curated records.  If identity, temper/state and
            # standard all agree, this is a duplicate import, not a material
            # ambiguity.  Prefer the curated record for presentation while
            # retaining the original source rows in the catalogue.
            fingerprints = {
                (
                    normalize_name(self._by_id[material_id].get("display_name")),
                    normalize_name(self._by_id[material_id].get("grade")),
                    normalize_name(self._by_id[material_id].get("product_state")),
                    normalize_name(self._by_id[material_id].get("UNS/standard")),
                )
                for material_id in material_ids
            }
            duplicate_import = len(material_ids) > 1 and len(fingerprints) == 1
            if duplicate_import:
                material_ids.sort(key=lambda material_id: (
                    self._by_id[material_id].get("data_role") == "1101 material-core evidence",
                    material_id,
                ))
                material_ids = material_ids[:1]
            trace.append({
                "input": supplied,
                "normalized": normalize_name(supplied),
                "matches": matches,
                "resolved_materials": [
                    {
                        "material_id": material_id,
                        "display_name": self._by_id[material_id].get("display_name"),
                        "grade": self._by_id[material_id].get("grade"),
                        "product_state": self._by_id[material_id].get("product_state"),
                    }
                    for material_id in material_ids
                ],
                "status": "matched" if len(material_ids) == 1 else ("ambiguous" if material_ids else "unmatched"),
            })
            if len(material_ids) == 1 and material_ids[0] not in ids:
                ids.append(material_ids[0])
        return ids, trace

    def aliases_mentioned_in(self, text: str) -> list[str]:
        """Recover only unambiguous catalog aliases from free text."""
        normalized_text = normalize_name(text)
        selected_keys: list[str] = []
        aliases: list[str] = []
        for key, entries in sorted(self._alias_index.items(), key=lambda item: len(item[0]), reverse=True):
            if key not in normalized_text or any(key in selected for selected in selected_keys):
                continue
            if len({entry["material_id"] for entry in entries}) == 1:
                selected_keys.append(key)
                aliases.append(entries[0]["alias"])
        return aliases

    def _curve_value(self, material_id: str, constraint: PropertyConstraint) -> dict[str, Any] | None:
        rows = [row for row in self.curves if row.get("material_id") == material_id and row.get("property") == constraint.property]
        if not rows:
            return None
        target = constraint.temperature_K
        parsed = []
        for row in rows:
            temperature, value = _number(row.get("temperature_K")), _number(row.get("value_SI"))
            if temperature is not None and value is not None:
                parsed.append((temperature, value, row))
        if not parsed:
            return None
        parsed.sort(key=lambda item: item[0])
        if target is None:
            _, value, row = parsed[0]
            return {"value": value, "unit": row.get("SI_unit"), "temperature_K": parsed[0][0], "coverage": "nearest_measured", "source": row}
        for temperature, value, row in parsed:
            if abs(temperature - target) < 1e-6:
                return {"value": value, "unit": row.get("SI_unit"), "temperature_K": temperature, "coverage": "measured_exact", "source": row}
        if parsed[0][0] < target < parsed[-1][0]:
            for left, right in zip(parsed, parsed[1:]):
                if left[0] <= target <= right[0]:
                    ratio = (target - left[0]) / (right[0] - left[0])
                    return {
                        "value": left[1] + ratio * (right[1] - left[1]), "unit": left[2].get("SI_unit"),
                        "temperature_K": target, "coverage": "interpolated_within_range",
                        "source": {"left": left[2], "right": right[2]},
                    }
        nearest = min(parsed, key=lambda item: abs(item[0] - target))
        return {"value": nearest[1], "unit": nearest[2].get("SI_unit"), "temperature_K": nearest[0], "coverage": "out_of_range", "source": nearest[2]}

    def _point_value(self, material_id: str, constraint: PropertyConstraint) -> dict[str, Any] | None:
        rows = [row for row in self.points if row.get("material_id") == material_id and row.get("property") == constraint.property]
        parsed = []
        for row in rows:
            value = _number(row.get("value"))
            temperature = _number(row.get("temperature_K"))
            if value is not None:
                parsed.append((temperature, value, row))
        if not parsed:
            return None
        target = constraint.temperature_K
        if target is not None:
            exact = [item for item in parsed if item[0] is not None and abs(item[0] - target) < 1e-6]
            if exact:
                temperature, value, row = exact[0]
                return {"value": value, "unit": row.get("unit"), "temperature_K": temperature, "coverage": "measured_exact", "source": row}
            with_temperature = [item for item in parsed if item[0] is not None]
            if with_temperature:
                temperature, value, row = min(with_temperature, key=lambda item: abs(item[0] - target))
                return {"value": value, "unit": row.get("unit"), "temperature_K": temperature, "coverage": "nearest_measured", "source": row}
        temperature, value, row = parsed[0]
        return {"value": value, "unit": row.get("unit"), "temperature_K": temperature, "coverage": "measured_exact" if temperature is not None else "condition_unspecified", "source": row}

    @staticmethod
    def _passes(value: float, operator: str, target: float) -> bool:
        return {">=": value >= target, ">": value > target, "<=": value <= target, "<": value < target, "=": abs(value - target) < 1e-9}.get(operator, False)

    def evaluate(self, material_id: str, constraints: list[PropertyConstraint]) -> dict[str, Any]:
        evidence = []
        accepted = True
        for constraint in constraints:
            measured = self._curve_value(material_id, constraint) or self._point_value(material_id, constraint)
            if measured is None:
                evidence.append({"property": constraint.property, "status": "missing", "requested": constraint.__dict__})
                accepted = False
                continue
            compatible = not constraint.unit or normalize_name(constraint.unit) == normalize_name(measured.get("unit"))
            comparable = compatible and measured["coverage"] != "out_of_range"
            passed = comparable and self._passes(float(measured["value"]), constraint.operator, constraint.value)
            evidence.append({
                "property": constraint.property, "status": "pass" if passed else ("out_of_range" if measured["coverage"] == "out_of_range" else ("unit_mismatch" if not compatible else "fail")),
                "requested": constraint.__dict__, "observed": {key: value for key, value in measured.items() if key != "source"},
                "source": measured["source"],
            })
            accepted = accepted and passed
        return {"eligible": accepted, "evidence": evidence}

    def property_evidence(self, material_id: str) -> list[dict[str, Any]]:
        """Return every stored property with its source, without inventing values.

        A query without filters is still useful to callers: it must return the
        catalogue's recorded properties rather than only material identity.
        Curve rows are summarized as a temperature range so a large curve does
        not turn an ordinary material lookup into a massive response.
        """
        evidence: list[dict[str, Any]] = []
        for row in self.points:
            if row.get("material_id") != material_id:
                continue
            value = _number(row.get("value"))
            if value is None:
                continue
            evidence.append({
                "property": row.get("property"), "value": value,
                "unit": row.get("unit"), "temperature_K": _number(row.get("temperature_K")),
                "coverage": "measured_point" if row.get("temperature_K") else "condition_unspecified",
                "condition": row.get("condition"), "source": row,
            })
        grouped: dict[str, list[tuple[float, float, dict[str, str]]]] = {}
        for row in self.curves:
            if row.get("material_id") != material_id:
                continue
            temperature, value = _number(row.get("temperature_K")), _number(row.get("value_SI"))
            if temperature is not None and value is not None:
                grouped.setdefault(row.get("property") or "", []).append((temperature, value, row))
        for property_name, rows in grouped.items():
            rows.sort(key=lambda item: item[0])
            first, last = rows[0], rows[-1]
            evidence.append({
                "property": property_name, "coverage": "temperature_curve",
                "temperature_range_K": [first[0], last[0]], "point_count": len(rows),
                "unit": first[2].get("SI_unit"), "condition": first[2].get("condition"),
                "source": {"first": first[2], "last": last[2]},
            })
        return sorted(evidence, key=lambda item: (str(item["property"]), str(item["coverage"])))

    def search(self, *, names: list[str], families: list[str], standards: list[str], constraints: list[PropertyConstraint], preferences: list[PreferenceGoal] | None = None, top_k: int) -> dict[str, Any]:
        resolved_ids, resolution_trace = self.resolve_names(names)
        family_keys = {normalize_name(item) for item in families if item}
        standard_keys = {normalize_name(item) for item in standards if item}
        candidates = self.materials
        # A supplied but unmatched/ambiguous material name must never degrade
        # into an unfiltered catalogue listing. That behavior made unrelated
        # steel and superalloy records appear for an unknown solid electrolyte.
        if names:
            candidates = [row for row in candidates if row["material_id"] in resolved_ids]
        if family_keys:
            candidates = [row for row in candidates if normalize_name(row.get("family")) in family_keys]
        if standard_keys:
            candidates = [row for row in candidates if normalize_name(row.get("UNS/standard")) in standard_keys]
        preferences = preferences or []
        results = []
        for material in candidates:
            assessment = self.evaluate(material["material_id"], constraints)
            result = {"material": material, "available_properties": self.property_evidence(material["material_id"]), **assessment}
            result["evidence_score"] = sum(item["status"] == "pass" for item in assessment["evidence"])
            preference_evidence = []
            preference_sort_key = []
            for preference in preferences:
                measured = self._curve_value(material["material_id"], PropertyConstraint(preference.property, ">=", 0.0)) or self._point_value(material["material_id"], PropertyConstraint(preference.property, ">=", 0.0))
                if measured is None:
                    preference_evidence.append({"property": preference.property, "direction": preference.direction, "status": "missing"})
                    preference_sort_key.append(float("inf"))
                    continue
                value = float(measured["value"])
                preference_evidence.append({
                    "property": preference.property, "direction": preference.direction, "status": "observed",
                    "observed": {key: item for key, item in measured.items() if key != "source"}, "source": measured["source"],
                })
                preference_sort_key.append(-value if preference.direction == "maximize" else value)
            result["preference_evidence"] = preference_evidence
            result["_preference_sort_key"] = tuple(preference_sort_key)
            results.append(result)
        results.sort(key=lambda item: (not item["eligible"], item["_preference_sort_key"], -item["evidence_score"], item["material"]["material_id"]))
        preference_data_gaps: list[dict[str, Any]] = []
        if preferences and not constraints:
            ranked_results = []
            for result in results:
                observed = any(item.get("status") == "observed" for item in result["preference_evidence"])
                if observed:
                    ranked_results.append(result)
                else:
                    preference_data_gaps.append({
                        "material_id": result["material"].get("material_id"),
                        "display_name": result["material"].get("display_name"),
                        "missing_properties": [item.get("property") for item in result["preference_evidence"] if item.get("status") == "missing"],
                    })
            results = ranked_results
        for rank, result in enumerate(results, start=1):
            result["preference_rank"] = rank if preferences else None
            result.pop("_preference_sort_key", None)
        return {
            "name_resolution": resolution_trace,
            "candidates": results[:top_k],
            "preference_data_gaps": preference_data_gaps,
        }


def parse_property_constraints(payload: Any, default_temperature_K: float | None) -> list[PropertyConstraint]:
    """Accept the planned list contract and the legacy property_filters mapping."""
    rows = []
    if isinstance(payload, dict):
        for property_name, spec in payload.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("min") is not None:
                rows.append({"property": property_name, "operator": ">=", "value": spec["min"], **spec})
            if spec.get("max") is not None:
                rows.append({"property": property_name, "operator": "<=", "value": spec["max"], **spec})
    elif isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
    constraints = []
    for row in rows:
        property_name = PROPERTY_ALIASES.get(normalize_name(row.get("property")), str(row.get("property") or ""))
        value = _number(row.get("value"))
        operator = str(row.get("operator") or "").strip()
        if not property_name or value is None or operator not in {">=", ">", "<=", "<", "="}:
            raise ValueError(f"invalid property constraint: {row!r}")
        temperature = _number(row.get("temperature_K"))
        if temperature is None and row.get("temperature_C") is not None:
            value_c = _number(row.get("temperature_C"))
            temperature = value_c + 273.15 if value_c is not None else None
        constraints.append(PropertyConstraint(property_name, operator, value, str(row.get("unit") or "").strip() or None, temperature if temperature is not None else default_temperature_K))
    return constraints


def parse_preference_goals(payload: Any) -> list[PreferenceGoal]:
    """Validate explicit directional goals; goals rank evidence but never filter it."""
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("preference_goals must be a list")
    goals = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("preference_goals items must be objects")
        property_name = PROPERTY_ALIASES.get(normalize_name(row.get("property")), str(row.get("property") or ""))
        direction = str(row.get("direction") or "").strip().lower()
        if not property_name or direction not in {"maximize", "minimize"}:
            raise ValueError(f"invalid preference goal: {row!r}")
        goals.append(PreferenceGoal(property_name, direction))
    return goals
