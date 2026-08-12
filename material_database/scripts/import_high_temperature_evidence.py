"""Import only high-confidence high-temperature evidence from 1101 exports.

Rows enter the catalogue only when the source table carries an explicit alloy
or material field and a property column with an unambiguous unit.  All other
tables are emitted as a review queue; document titles are never used as an
automatic material-identity substitute.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


IDENTITY_FIELDS = ("alloy", "material", "grade", "alloy_name", "material_name")
LINEAGE_FIELDS = {"lineage_source_table_id", "lineage_document_id", "lineage_document_name", "lineage_page_number"}


def number(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def canonical_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.casefold())


def material_id(name: str) -> str:
    return "MAT-1101-HT-" + hashlib.sha256(canonical_key(name).encode()).hexdigest()[:20]


def safe_identity(name: str) -> bool:
    """Accept a self-contained grade/name, never a table-layout fragment."""
    value = name.strip()
    if not value or "\\" in value or "{" in value or "}" in value or "$" in value:
        return False
    plain = value.casefold().replace("®", "").strip()
    if plain in {"alloy", "sheet", "plate", "bar", "rod", "wire", "material", "form"}:
        return False
    if plain.startswith(("sheet", "plate", "bar,", "rod,", "wire,")):
        return False
    if re.fullmatch(r"[0-9.]+", plain):
        return False
    # A one- or two-character code such as "25" or "6B" needs a document
    # mapping before it can safely become a material identity.
    return len(re.findall(r"[a-z]", plain)) >= 3


def property_mapping(column: str) -> tuple[str, str, float] | None:
    normalized = column.replace("_", "")
    if column.endswith("_mpa") and "yield_strength" in column:
        return "yield_strength", "MPa", 1.0
    if column.endswith("_mpa") and ("ultimate_tensile_strength" in column or "ultimatetensilestrength" in normalized):
        return "ultimate_tensile_strength", "MPa", 1.0
    if column.endswith("_mpa") and "tensile_strength" in column:
        return "tensile_strength", "MPa", 1.0
    # Source tables commonly report strength in ksi.  Convert during import so
    # catalogue constraints remain comparable in one unit system.
    if column.endswith("_ksi") and "yield_strength" in column:
        return "yield_strength", "MPa", 6.894757293168
    if column.endswith("_ksi") and ("ultimate_tensile_strength" in column or "ultimatetensilestrength" in normalized):
        return "ultimate_tensile_strength", "MPa", 6.894757293168
    if column.endswith("_ksi") and "tensile_strength" in column:
        return "tensile_strength", "MPa", 6.894757293168
    if "vickers" in column:
        return "hardness_vickers", "HV", 1.0
    return None


def temperature_k(row: dict[str, str], column: str) -> float | None:
    for field in ("test_temperature_deg_c", "temperature_deg_c", "exposure_temperature_deg_c"):
        value = number(row.get(field, ""))
        if value is not None:
            return value + 273.15
    for field in ("test_temperature_deg_f", "temperature_deg_f", "exposure_temperature_deg_f"):
        value = number(row.get(field, ""))
        if value is not None:
            return (value - 32) * 5 / 9 + 273.15
    match = re.search(r"(?:^|_)(\d+)_deg_c(?:_|$)", column)
    if match:
        return float(match.group(1)) + 273.15
    match = re.search(r"(?:^|_)(\d+)_deg_f(?:_|$)", column)
    if match:
        return (float(match.group(1)) - 32) * 5 / 9 + 273.15
    return 298.15 if column.startswith("rt_") else None


CONDITION_STATE_FIELDS = (
    "condition",
    "form",
    "product_form",
    "material_condition",
    "heat_treatment",
    "cold_reduction",
)


def condition_text(row: dict[str, str], inherited_state: dict[str, str] | None = None) -> str:
    """Keep source state labels, including non-numeric room-temperature text."""
    inherited_state = inherited_state or {}
    values = [
        row.get(field, "") or inherited_state.get(field, "") for field in CONDITION_STATE_FIELDS
    ]
    # Manufacturer exports may encode the product state in a dedicated
    # heat-treatment column (rather than the generic ``heat_treatment``).
    # Preserve it verbatim so states are never merged during screening.
    for field, value in row.items():
        if field.startswith("heat_treatment_") and value.strip():
            values.append(f"{field}={value.strip()}")
    for field in (
        "test_temperature_deg_c", "test_temperature_deg_f", "temperature_deg_c", "temperature_deg_f",
        "exposure_temperature_deg_c", "exposure_temperature_deg_f",
    ):
        value = row.get(field, "").strip()
        if value and number(value) is None:
            values.append(f"{field}={value}")
    values.append(row.get("lineage_caption", ""))
    footnote = row.get("lineage_footnote", "").strip()
    if footnote and footnote != "[not provided]":
        values.append(footnote)
    return "; ".join(item for item in values if item)


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import explicit-identity high-temperature evidence")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, required=True)
    parser.add_argument("--document-registry", type=Path,
                        help="Reviewed document-to-material mappings.  Enables only listed source tables.")
    parser.add_argument("--registry-only", action="store_true",
                        help="Import only source tables explicitly allowed by --document-registry.")
    args = parser.parse_args()
    if not (args.input / "snapshot_manifest.json").is_file() or (args.input / ".INCOMPLETE").exists():
        raise SystemExit("input must be a completed snapshot")
    if args.output.exists() or args.review_output.exists():
        raise SystemExit("refusing to overwrite an existing import bundle or review queue")

    registry: dict[str, dict[str, Any]] = {}
    if args.document_registry:
        payload = json.loads(args.document_registry.read_text(encoding="utf-8"))
        for entry in payload.get("documents", []):
            document_id = entry.get("lineage_document_id", "")
            if not document_id or not entry.get("material_id"):
                raise SystemExit("every document registry entry needs lineage_document_id and material_id")
            registry[document_id] = entry

    materials: dict[str, dict[str, str]] = {}
    aliases: list[dict[str, str]] = []
    registry_aliases_added: set[str] = set()
    points: list[dict[str, str]] = []
    review: list[dict[str, Any]] = []
    imported_tables = Counter()
    for path in sorted(args.input.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        header = list(rows[0]) if rows else []
        identity_field = next((field for field in IDENTITY_FIELDS if field in header), None)
        mapped_columns = [(field, property_mapping(field)) for field in header]
        mapped_columns = [(field, mapping) for field, mapping in mapped_columns if mapping]
        first = rows[0] if rows else {}
        registry_entry = registry.get(first.get("lineage_document_id", ""))
        table_is_reviewed = bool(
            registry_entry
            and first.get("lineage_source_table_id", "") in set(registry_entry.get("include_source_table_ids", []))
        )
        if args.registry_only and not table_is_reviewed:
            continue
        if (not identity_field and not table_is_reviewed) or not mapped_columns:
            review.append({
                "file": path.name, "row_count": len(rows), "reason": "missing explicit material identity or unambiguous property-unit mapping",
                "identity_fields": [field for field in IDENTITY_FIELDS if field in header],
                "candidate_property_fields": [field for field in header if field not in LINEAGE_FIELDS and field not in IDENTITY_FIELDS][:80],
                "lineage_document_id": first.get("lineage_document_id", ""),
                "lineage_document_name": first.get("lineage_document_name", ""),
                "lineage_source_table_id": first.get("lineage_source_table_id", ""),
            })
            continue
        # PDF tables commonly show a condition or cold-reduction value only on
        # the first row of a block.  Carry that state into the emitted
        # condition text, but retain the untouched source row in raw_row_json.
        table_condition_state: dict[str, str] = {}
        for row in rows:
            for field in CONDITION_STATE_FIELDS:
                value = row.get(field, "").strip()
                if value:
                    table_condition_state[field] = value
            source_table_id = row.get("lineage_source_table_id", "")
            document = registry.get(row.get("lineage_document_id", ""))
            allowed_tables = set(document.get("include_source_table_ids", [])) if document else set()
            if args.registry_only and (not document or source_table_id not in allowed_tables):
                continue
            if document and source_table_id not in allowed_tables:
                continue
            name = row.get(identity_field, "").strip() if identity_field else ""
            if document:
                name = document["display_name"]
            if not safe_identity(name):
                if name:
                    review.append({
                        "file": path.name, "row_count": 1,
                        "reason": "explicit identity is a short code, layout label, or formatting fragment; document-to-material mapping required",
                        "candidate_identity": name,
                        "lineage_document_id": row.get("lineage_document_id", ""),
                        "lineage_document_name": row.get("lineage_document_name", ""),
                        "lineage_source_table_id": row.get("lineage_source_table_id", ""),
                    })
                continue
            identifier = document["material_id"] if document else material_id(name)
            document_id = row.get("lineage_document_id", "")
            if document and document_id not in registry_aliases_added:
                for alias in [document["display_name"], *document.get("aliases", [])]:
                    aliases.append({"material_id": identifier, "alias": alias, "alias_type": "reviewed_document_identity", "source": args.document_registry.name})
                registry_aliases_added.add(document_id)
            if identifier not in materials and not (document and document.get("existing_catalog_material")):
                materials[identifier] = {
                    "material_id": identifier, "display_name": name,
                    "family": document.get("family", "high-temperature alloy evidence") if document else "high-temperature alloy evidence",
                    "grade": document.get("grade", name) if document else name,
                    "UNS/standard": document.get("standard", "") if document else "",
                    "product_state": document.get("product_state", "condition in property evidence") if document else "condition in property evidence",
                    "source_id": row.get("lineage_document_id", ""), "data_role": "1101 high-temperature explicit evidence",
                    "temperature_coverage": "", "composition_available": "not_recorded", "process_metadata": "",
                    "notes": "Imported only from rows with an explicit material field.", "raw_source_file": path.name,
                    "raw_sheet": "", "raw_row_number": row.get("lineage_source_item_index", ""),
                    "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
                }
                aliases.append({"material_id": identifier, "alias": name, "alias_type": identity_field, "source": args.input.name})
            for column, mapping in mapped_columns:
                # A source table that gives both units is one observation, not
                # two competing measurements. Prefer its already-normalized
                # MPa value and use ksi only when it is the sole value.
                if column.endswith("_ksi") and f"{column[:-4]}_mpa" in row:
                    continue
                value = number(row.get(column, ""))
                if value is None:
                    continue
                property_name, unit, conversion = mapping
                value *= conversion
                condition = condition_text(row, table_condition_state)
                points.append({
                    "material_id": identifier, "property": property_name, "value": f"{value:g}", "unit": unit,
                    "temperature_K": "" if temperature_k(row, column) is None else f"{temperature_k(row, column):.5g}",
                    "uncertainty": "", "data_kind": "source_table_value", "condition": condition,
                    "source_id": row.get("lineage_document_id", ""),
                    "source_locator": f"{row.get('lineage_source_table_id', '')}; page {row.get('lineage_page_number', '')}",
                    "notes": f"source column={column}; imported through {'reviewed document mapping' if document else 'explicit-identity mapping'}",
                    "raw_source_file": path.name, "raw_sheet": "", "raw_row_number": row.get("lineage_source_item_index", ""),
                    "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
                })
                imported_tables[path.name] += 1

    fields_material = ("material_id", "display_name", "family", "grade", "UNS/standard", "product_state", "source_id", "data_role", "temperature_coverage", "composition_available", "process_metadata", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")
    fields_point = ("material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")
    counts = {
        "materials": write_csv(args.output / "materials.csv", fields_material, materials.values()),
        "property_points": write_csv(args.output / "property_points.csv", fields_point, points),
        "curve_data": write_csv(args.output / "curve_data.csv", (), ()),
        "composition_long": write_csv(args.output / "composition_long.csv", (), ()),
        "material_aliases": write_csv(args.output / "material_aliases.csv", ("material_id", "alias", "alias_type", "source"), aliases),
    }
    args.review_output.parent.mkdir(parents=True, exist_ok=True)
    args.review_output.write_text(json.dumps({
        "snapshot": args.input.name, "queued_tables": review,
        "rule": "Document names are review hints only; create an explicit document-to-material/state mapping before import.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "import_manifest.json").write_text(json.dumps({"input_snapshot": args.input.name, "counts": counts, "mapped_point_columns": dict(imported_tables)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "review_tables": len(review)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
