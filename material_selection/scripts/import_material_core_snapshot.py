"""Import a traceable 1101 material-core snapshot into catalogue CSV bundles.

This importer intentionally excludes ``dataset_record``: it contains feature
and training records, not verified material-property evidence.  The generated
bundle is additive and never overwrites the curated product catalogue.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable


REQUIRED_TABLES = (
    "material", "property_observation", "property_curve",
    "composition_component", "reference",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


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


def material_id(source_id: str) -> str:
    return f"MAT-1101-{source_id}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a downloaded 1101 material-core snapshot")
    parser.add_argument("--input", type=Path, required=True, help="immutable snapshot directory")
    parser.add_argument("--output", type=Path, required=True, help="new catalogue bundle directory")
    args = parser.parse_args()

    manifest_path = args.input / "snapshot_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing completed snapshot manifest: {manifest_path}")
    if (args.input / ".INCOMPLETE").exists():
        raise SystemExit(f"snapshot remains incomplete: {args.input}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing bundle: {args.output}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    table_names = {item.get("table") for item in manifest.get("tables", [])}
    missing = set(REQUIRED_TABLES) - table_names
    if missing:
        raise SystemExit(f"snapshot manifest is missing required tables: {sorted(missing)}")
    source = {name: read_csv(args.input / f"material.{name}.csv") for name in REQUIRED_TABLES}
    raw_name = args.input.name
    component_ids = {row["material_id"] for row in source["composition_component"]}

    material_rows = []
    aliases = []
    for row in source["material"]:
        identifier = material_id(row["id"])
        name = row.get("display_name") or row.get("canonical_formula") or identifier
        material_rows.append({
            "material_id": identifier, "display_name": name, "family": row.get("family", ""),
            "grade": row.get("grade", ""), "UNS/standard": row.get("standard", ""),
            "product_state": row.get("product_state", ""), "source_id": row.get("source_artifact_id", ""),
            "data_role": "1101 material-core evidence", "temperature_coverage": "",
            "composition_available": "yes" if row["id"] in component_ids else "not_recorded",
            "process_metadata": "", "notes": "entity imported from immutable 1101 snapshot",
            "raw_source_file": "material.material.csv", "raw_sheet": "",
            "raw_row_number": row.get("source_row_number", ""),
            "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        })
        for value, alias_type in ((row.get("display_name"), "display_name"), (row.get("canonical_formula"), "canonical_formula"), (row.get("grade"), "grade"), (row.get("standard"), "standard")):
            if value:
                aliases.append({"material_id": identifier, "alias": value, "alias_type": alias_type, "source": raw_name})

    point_rows = ({
        "material_id": material_id(row["material_id"]), "property": row["property_code"],
        "value": row["value"], "unit": row["unit"], "temperature_K": row["temperature_k"],
        "uncertainty": row["uncertainty"], "data_kind": row["data_kind"], "condition": row["condition"],
        "source_id": row["source_artifact_id"], "source_locator": f"reference_id={row['reference_id']}",
        "notes": "reference_id is globally linked within this snapshot; calculation-labelled property codes remain calculation-labelled",
        "raw_source_file": "material.property_observation.csv", "raw_sheet": "",
        "raw_row_number": row["source_row_number"], "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
    } for row in source["property_observation"])
    curve_rows = ({
        "material_id": material_id(row["material_id"]), "property": row["property_code"],
        "raw_temperature": row["temperature_k"], "raw_temperature_unit": "K", "temperature_K": row["temperature_k"],
        "raw_value": row["value"], "raw_unit": row["unit"], "value_SI": row["value"], "SI_unit": row["unit"],
        "uncertainty_raw": row["uncertainty"], "condition": row["condition"], "data_kind": row["data_kind"],
        "source_id": row["source_artifact_id"], "source_locator": f"1101 row {row['source_row_number']}",
        "transformation": "source-normalized value retained", "raw_source_file": "material.property_curve.csv",
        "raw_sheet": "", "raw_row_number": row["source_row_number"],
        "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
    } for row in source["property_curve"])
    composition_rows = ({
        "material_id": material_id(row["material_id"]), "component": row["component"], "min": row["minimum"],
        "max": row["maximum"], "nominal": row["nominal"], "uncertainty": "", "basis": row["basis"],
        "data_kind": row["data_kind"], "source_id": row["source_artifact_id"],
        "source_locator": f"1101 row {row['source_row_number']}", "notes": "",
        "raw_source_file": "material.composition_component.csv", "raw_sheet": "",
        "raw_row_number": row["source_row_number"], "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
    } for row in source["composition_component"])

    counts = {
        "materials": write_csv(args.output / "materials.csv", (
            "material_id", "display_name", "family", "grade", "UNS/standard", "product_state", "source_id", "data_role", "temperature_coverage", "composition_available", "process_metadata", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"), material_rows),
        "property_points": write_csv(args.output / "property_points.csv", (
            "material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"), point_rows),
        "curve_data": write_csv(args.output / "curve_data.csv", (
            "material_id", "property", "raw_temperature", "raw_temperature_unit", "temperature_K", "raw_value", "raw_unit", "value_SI", "SI_unit", "uncertainty_raw", "condition", "data_kind", "source_id", "source_locator", "transformation", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"), curve_rows),
        "composition_long": write_csv(args.output / "composition_long.csv", (
            "material_id", "component", "min", "max", "nominal", "uncertainty", "basis", "data_kind", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"), composition_rows),
        "material_aliases": write_csv(args.output / "material_aliases.csv", ("material_id", "alias", "alias_type", "source"), aliases),
        "references": write_csv(args.output / "references.csv", tuple(source["reference"][0]) if source["reference"] else (), source["reference"]),
    }
    (args.output / "import_manifest.json").write_text(json.dumps({
        "input_snapshot": raw_name, "input_snapshot_tables": manifest["tables"],
        "counts": counts,
        "excluded_tables": {"dataset_record": "feature/training data, not mature-material evidence"},
        "reference_join": "reference_id is globally linked in this snapshot; source_artifact_id is retained as provenance, not as the join key",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
