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


def _preference_comparison_value(property_name: str, value: float, unit: Any) -> float | None:
    """Normalize only preference values that otherwise use mixed common units.

    Stored evidence remains untouched for display.  Density is commonly
    ingested as either g/cm³ or kg/m³; comparing their raw magnitudes would
    make every g/cm³ row appear lighter than every kg/m³ row.
    """
    if property_name == "hardness":
        # HRC/HRB/HB facts stay visible in material cards, but raw values from
        # those scales cannot be silently mixed with Vickers rankings.
        return value if normalize_name(unit) == "hv" else None
    if property_name != "density":
        return value
    normalized_unit = normalize_name(unit)
    if normalized_unit in {"kgm3", "kgm3"}:
        return value
    if normalized_unit in {"gcm3", "gcm3"}:
        return value * 1000.0
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
        raw_materials = [row for bundle in bundles for row in _read(bundle / "materials.csv")]
        # Incremental evidence packs may intentionally add new property rows
        # for a material already present in an older pack. Keep one identity
        # row while retaining every source-preserving property record.
        material_by_id: dict[str, dict[str, str]] = {}
        for row in raw_materials:
            material_id = row.get("material_id", "")
            existing = material_by_id.get(material_id)
            # A later A/B source-backed identity supersedes an earlier D-only
            # seed identity while its D estimates remain separately retained.
            if existing is None or (
                existing.get("data_role") == "D级工程估算材料身份"
                and row.get("data_role") != "D级工程估算材料身份"
            ):
                material_by_id[material_id] = row
        self.materials = list(material_by_id.values())
        raw_points = [row for bundle in bundles for row in _read(bundle / "property_points.csv")]
        point_by_source: dict[tuple[str, str, str, str, str, str, str], dict[str, str]] = {}
        for row in raw_points:
            key = (
                row.get("material_id", ""), row.get("property", ""),
                row.get("source_id", ""), row.get("source_locator", ""), row.get("raw_row_number", ""),
                row.get("temperature_K", ""), row.get("value", ""),
            )
            point_by_source.setdefault(key, row)
        self.points = list(point_by_source.values())
        raw_curves = [row for bundle in bundles for row in _read(bundle / "curve_data.csv")]
        curve_by_source: dict[tuple[str, str, str, str, str, str, str], dict[str, str]] = {}
        for row in raw_curves:
            key = (
                row.get("material_id", ""), row.get("property", ""),
                row.get("source_id", ""), row.get("source_locator", ""), row.get("raw_row_number", ""),
                row.get("temperature_K", ""), row.get("value_SI", ""),
            )
            curve_by_source.setdefault(key, row)
        self.curves = list(curve_by_source.values())
        self.compositions = [row for bundle in bundles for row in _read(bundle / "composition_long.csv")]
        self.aliases = [row for bundle in bundles for row in _read(bundle / "material_aliases.csv")]
        # D-level engineering estimates live in a separate file rather than
        # property_points.csv.  They may be shown to users, but this physical
        # separation prevents them from ever entering evaluate()/ranking.
        self.engineering_estimates = [
            row for bundle in bundles for row in _read(bundle / "engineering_estimates.csv")
            if row.get("material_id")
        ]
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
        properties = {constraint.property}
        # Older source packs preserve Vickers hardness under the more specific
        # property name.  It is still direct evidence for a generic customer
        # hardness query; other hardness scales remain separate.
        if constraint.property == "hardness":
            properties.add("hardness_vickers")
        # Polymer and filament data sheets commonly call the ISO/ASTM result
        # simply "tensile_strength".  For a customer request for generic
        # ultimate tensile strength it is the relevant tensile failure value;
        # yield strength is deliberately not folded into this fallback.
        if constraint.property == "ultimate_tensile_strength":
            properties.add("tensile_strength")
        rows = [row for row in self.points if row.get("material_id") == material_id and row.get("property") in properties]
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
        # Different heat treatments or specimen states must remain separate
        # curves even when they share a property name.
        grouped: dict[tuple[str, str], list[tuple[float, float, dict[str, str]]]] = {}
        for row in self.curves:
            if row.get("material_id") != material_id:
                continue
            temperature, value = _number(row.get("temperature_K")), _number(row.get("value_SI"))
            if temperature is not None and value is not None:
                key = (row.get("property") or "", row.get("condition") or "")
                grouped.setdefault(key, []).append((temperature, value, row))
        for (property_name, condition), rows in grouped.items():
            rows.sort(key=lambda item: item[0])
            first, last = rows[0], rows[-1]
            values = [item[1] for item in rows]
            evidence.append({
                "property": property_name, "coverage": "temperature_curve",
                "temperature_range_K": [first[0], last[0]], "point_count": len(rows),
                # The temperature span describes the test condition, not the
                # property value.  Keep both so presentation never labels a
                # 260–820 °C measurement range as a conductivity range.
                "value_range": [min(values), max(values)],
                # Keep values tied to the ends of the temperature interval.
                # A value range alone is ambiguous for a non-monotonic curve.
                "temperature_endpoints": [
                    {"temperature_K": first[0], "value": first[1]},
                    {"temperature_K": last[0], "value": last[1]},
                ],
                "unit": first[2].get("SI_unit"), "condition": condition,
                "source": {"first": first[2], "last": last[2]},
            })
        return sorted(evidence, key=lambda item: (str(item["property"]), str(item["coverage"])))

    def engineering_estimates_for(self, material_id: str) -> list[dict[str, Any]]:
        """Return separately stored D-level values for presentation only."""
        estimates: list[dict[str, Any]] = []
        for row in self.engineering_estimates:
            if row.get("material_id") != material_id:
                continue
            item: dict[str, Any] = dict(row)
            for key in ("value_min", "value_max", "temperature_min_K", "temperature_max_K"):
                value = _number(item.get(key))
                if value is not None:
                    item[key] = value
            estimates.append(item)
        return estimates

    def search(self, *, names: list[str], families: list[str], standards: list[str], constraints: list[PropertyConstraint], preferences: list[PreferenceGoal] | None = None, top_k: int) -> dict[str, Any]:
        resolved_ids, resolution_trace = self.resolve_names(names)
        family_keys = {normalize_name(item) for item in families if item}
        printing_consumables = "3dprintingconsumables" in family_keys
        additive_materials = "additivemanufacturingmaterials" in family_keys
        family_keys -= {"3dprintingconsumables", "additivemanufacturingmaterials"}
        standard_keys = {normalize_name(item) for item in standards if item}
        candidates = self.materials
        # A supplied but unmatched/ambiguous material name must never degrade
        # into an unfiltered catalogue listing. That behavior made unrelated
        # steel and superalloy records appear for an unknown solid electrolyte.
        if names:
            candidates = [row for row in candidates if row["material_id"] in resolved_ids]
        if family_keys:
            candidates = [row for row in candidates if normalize_name(row.get("family")) in family_keys]
        if printing_consumables or additive_materials:
            def printable(row: dict[str, str]) -> bool:
                text = " ".join(str(row.get(key) or "") for key in ("display_name", "family", "grade", "product_state", "process_metadata")).casefold()
                polymer_markers = ("fdm", "fff", "sls", "sla", "耗材", "filament", "线材", "树脂", "工程塑料", "尼龙", "pekk", "peek", "pei", "pps", "abs", "asa", "onyx")
                additive_markers = (*polymer_markers, "增材", "metal x", "alsi10mg")
                return any(marker in text for marker in (polymer_markers if printing_consumables else additive_markers))
            candidates = [row for row in candidates if printable(row)]
        if standard_keys:
            candidates = [row for row in candidates if normalize_name(row.get("UNS/standard")) in standard_keys]
        # The material-core import deliberately preserves source records, and
        # can therefore coexist with an already curated record for the exact
        # same identity/state.  A broad evidence landscape must not show that
        # one material twice merely because it has two import lineages.  Keep
        # exact name searches untouched: callers may be intentionally asking
        # for a specific imported identity.
        if not names:
            deduplicated: dict[tuple[str, str, str, str], dict[str, str]] = {}
            for material in candidates:
                fingerprint = (
                    normalize_name(material.get("display_name")),
                    normalize_name(material.get("grade")),
                    normalize_name(material.get("product_state")),
                    normalize_name(material.get("UNS/standard")),
                )
                existing = deduplicated.get(fingerprint)
                if existing is None or (
                    existing.get("data_role") == "1101 material-core evidence"
                    and material.get("data_role") != "1101 material-core evidence"
                ):
                    deduplicated[fingerprint] = material
            candidates = list(deduplicated.values())
        preferences = preferences or []
        results = []
        for material in candidates:
            assessment = self.evaluate(material["material_id"], constraints)
            result = {
                "material": material,
                "available_properties": self.property_evidence(material["material_id"]),
                "engineering_estimates": self.engineering_estimates_for(material["material_id"]),
                **assessment,
            }
            result["evidence_score"] = sum(item["status"] == "pass" for item in assessment["evidence"])
            preference_evidence = []
            preference_sort_key = []
            for preference in preferences:
                measured = self._curve_value(material["material_id"], PropertyConstraint(preference.property, ">=", 0.0)) or self._point_value(material["material_id"], PropertyConstraint(preference.property, ">=", 0.0))
                if measured is None:
                    preference_evidence.append({"property": preference.property, "direction": preference.direction, "status": "missing"})
                    preference_sort_key.append(float("inf"))
                    continue
                value = _preference_comparison_value(
                    preference.property, float(measured["value"]), measured.get("unit"),
                )
                if value is None:
                    preference_evidence.append({"property": preference.property, "direction": preference.direction, "status": "unit_incomparable"})
                    preference_sort_key.append(float("inf"))
                    continue
                preference_evidence.append({
                    "property": preference.property, "direction": preference.direction, "status": "observed",
                    "observed": {key: item for key, item in measured.items() if key != "source"}, "source": measured["source"],
                })
                preference_sort_key.append(-value if preference.direction == "maximize" else value)
            result["preference_evidence"] = preference_evidence
            result["_preference_sort_key"] = tuple(preference_sort_key)
            result["_preference_missing_count"] = sum(
                item.get("status") != "observed" for item in preference_evidence
            )
            results.append(result)
        # A partially covered candidate must not outrank a candidate that has
        # evidence for every stated preference merely because one available
        # property has a large raw value.
        results.sort(key=lambda item: (not item["eligible"], item["_preference_missing_count"], item["_preference_sort_key"], -item["evidence_score"], item["material"]["material_id"]))
        candidate_count_before_limit = len(results)
        # Build every funnel from the full evaluated set, before the UI's
        # display limit is applied.  A funnel labelled "候选" must never say
        # 10 merely because the response renders the top 10 cards.
        preference_funnel_counts: list[dict[str, Any]] = []
        constraint_funnel_counts: list[dict[str, Any]] = []
        if preferences and not constraints:
            remaining = list(results)
            for preference in preferences:
                remaining = [
                    item for item in remaining
                    if any(
                        evidence.get("property") == preference.property and evidence.get("status") == "observed"
                        for evidence in item.get("preference_evidence", [])
                    )
                ]
                preference_funnel_counts.append({"property": preference.property, "count": len(remaining)})
        elif constraints:
            remaining = list(results)
            for constraint in constraints:
                remaining = [
                    item for item in remaining
                    if any(
                        evidence.get("property") == constraint.property
                        and evidence.get("status") == "pass"
                        and evidence.get("requested", {}).get("operator") == constraint.operator
                        and evidence.get("requested", {}).get("value") == constraint.value
                        for evidence in item.get("evidence", [])
                    )
                ]
                constraint_funnel_counts.append({"constraint": constraint.__dict__, "count": len(remaining)})
        comparable_candidate_count = len(results)
        complete_preference_candidate_count = 0
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
                        "family": result["material"].get("family"),
                        "missing_properties": [item.get("property") for item in result["preference_evidence"] if item.get("status") == "missing"],
                    })
            results = ranked_results
            comparable_candidate_count = len(results)
            complete_preference_candidate_count = sum(
                item["_preference_missing_count"] == 0 for item in results
            )
            # A broad preference search can otherwise be filled entirely by
            # literature-formula records that happen to carry one property.
            # Keep a small, visible comparison set of catalogue commercial
            # materials whenever the caller did not restrict the material
            # family or name themselves.
            if not names and not families and not standards and len(results) > top_k:
                commercial = [
                    item for item in results
                    if item["material"].get("data_role") != "1101 material-core evidence"
                ]
                reserve = min(3, len(commercial), top_k)
                leading = results[:top_k - reserve]
                selected_ids = {item["material"]["material_id"] for item in leading}
                leading.extend(item for item in commercial if item["material"]["material_id"] not in selected_ids)
                results = leading[:top_k]
        truncated = comparable_candidate_count > top_k
        for rank, result in enumerate(results, start=1):
            result["preference_rank"] = rank if preferences else None
            result.pop("_preference_sort_key", None)
            result.pop("_preference_missing_count", None)
        return {
            "name_resolution": resolution_trace,
            "candidates": results[:top_k],
            "preference_data_gaps": preference_data_gaps,
            "candidate_count_before_limit": candidate_count_before_limit,
            "comparable_candidate_count": comparable_candidate_count,
            "complete_preference_candidate_count": complete_preference_candidate_count,
            "preference_funnel_counts": preference_funnel_counts,
            "constraint_funnel_counts": constraint_funnel_counts,
            "truncated": truncated,
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
