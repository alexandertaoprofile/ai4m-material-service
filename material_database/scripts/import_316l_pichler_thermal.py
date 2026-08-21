"""Import traceable thermophysical tables for NIST SRM 1155a 316L.

This is intentionally source-specific: the paper's two-column specific-heat
table and density table do not expose a generic material column, but the PDF,
document ID, table numbers and specimen identity are all reviewed.  The
importer retains the actual temperature of every row and never merges it with
the separate, state-unknown 316 upload.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


MATERIAL_ID = "MAT-316L-SRM1155A"
DOCUMENT_ID = "87f95a5042a3"
DOCUMENT_NAME = "pichler19_measurements of thermophysical properties of solid and liquid nist srm 316l stainless steel.pdf"
TABLE_DENSITY = "87f95a5042a3-table-0006"
TABLE_CP = "87f95a5042a3-table-0007"

FIELDS = (
    "material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind",
    "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet",
    "raw_row_number", "raw_row_json",
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def source_file(input_dir: Path, suffix: str) -> Path:
    matches = list(input_dir.glob(f"*{suffix}.csv"))
    if len(matches) != 1:
        raise ValueError(f"expected one source CSV ending {suffix!r}, got {len(matches)}")
    return matches[0]


def row_payload(
    *, property_name: str, value: float, unit: str, temperature_k: float, uncertainty: str,
    condition: str, source_table: str, page: str, source: Path, raw_index: str, raw: dict[str, str], notes: str,
) -> dict[str, str]:
    return {
        "material_id": MATERIAL_ID, "property": property_name, "value": f"{value:g}", "unit": unit,
        "temperature_K": f"{temperature_k:g}", "uncertainty": uncertainty, "data_kind": "source_table_value",
        "condition": condition, "source_id": DOCUMENT_ID, "source_locator": f"{source_table}; page {page}",
        "notes": notes, "raw_source_file": source.name, "raw_sheet": "",
        "raw_row_number": raw_index, "raw_row_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (args.input / "snapshot_manifest.json").is_file() or (args.input / ".INCOMPLETE").exists():
        raise SystemExit("input must be a completed snapshot")
    if args.output.exists():
        raise SystemExit("refusing to overwrite an existing import bundle")

    density_source = source_file(args.input, "d6ed33ba")
    cp_source = source_file(args.input, "7c7e167c")
    density_rows = read_rows(density_source)
    cp_rows = read_rows(cp_source)
    if any(row.get("lineage_source_table_id") != TABLE_DENSITY for row in density_rows):
        raise ValueError("unexpected density source table")
    if any(row.get("lineage_source_table_id") != TABLE_CP for row in cp_rows):
        raise ValueError("unexpected specific-heat source table")

    points: list[dict[str, str]] = []
    for row in density_rows:
        temperature = float(row["t_k"])
        # Table 6 covers solid, transition and liquid data.  State is labelled
        # from Table 2's source solidus/liquidus values, not guessed from the
        # density trend.
        if temperature <= 1675:
            phase = "solid; source solidus 1675 K"
        elif temperature >= 1708:
            phase = "liquid; source liquidus 1708 K"
        else:
            phase = "solid-liquid transition; source solidus 1675 K / liquidus 1708 K"
        points.append(row_payload(
            property_name="density", value=float(row["d_t_kg_m_3"]), unit="kg/m³", temperature_k=temperature,
            uncertainty="2.5% (k=2; Table 4, solid density uncertainty; phase-specific review required near transition)",
            condition=f"NIST SRM 1155a AISI 316L; {phase}; OPA; Table 6 collected thermophysical data",
            source_table=TABLE_DENSITY, page=row["lineage_page_number"], source=density_source,
            raw_index=row["lineage_source_item_index"], raw=row,
            notes="D(T) density; source table retains measured temperature and associated thermophysical values.",
        ))
    for row in cp_rows:
        for temperature_field, value_field, uncertainty_field in (
            ("t_k", "c_p_kj_kg_1_k_1", "delta_c_p_kj_kg_1_k_1"),
            ("t_k_2", "c_p_kj_kg_1_k_1_2", "delta_c_p_kj_kg_1_k_1_2"),
        ):
            temperature_text, value_text = row.get(temperature_field, ""), row.get(value_field, "")
            if not temperature_text or not value_text:
                continue
            uncertainty = float(row[uncertainty_field]) * 1000
            points.append(row_payload(
                property_name="specific_heat", value=float(value_text) * 1000, unit="J/(kg·K)",
                temperature_k=float(temperature_text), uncertainty=f"±{uncertainty:g} J/(kg·K) (k=2)",
                condition="NIST SRM 1155a AISI 316L; solid-state range; DSC; Table 7 specific heat capacity",
                source_table=TABLE_CP, page=row["lineage_page_number"], source=cp_source,
                raw_index=row["lineage_source_item_index"], raw=row,
                notes="cp(T) from DSC; original values converted from kJ/(kg·K) to J/(kg·K).",
            ))

    args.output.mkdir(parents=True)
    for name, fields in (
        ("materials.csv", ("material_id", "display_name", "family", "grade", "UNS/standard", "product_state", "source_id", "data_role", "temperature_coverage", "composition_available", "process_metadata", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")),
        ("curve_data.csv", ()), ("composition_long.csv", ()),
        ("material_aliases.csv", ("material_id", "alias", "alias_type", "source")),
    ):
        with (args.output / name).open("w", encoding="utf-8", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()
    with (args.output / "property_points.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(points)
    (args.output / "import_manifest.json").write_text(json.dumps({
        "input_snapshot": args.input.name, "document_id": DOCUMENT_ID, "document_name": DOCUMENT_NAME,
        "material_id": MATERIAL_ID, "counts": {"property_points": len(points)},
        "included_tables": [TABLE_DENSITY, TABLE_CP],
        "excluded": "Thermal expansion is reported only as V(T)/V0 in Table 6 and is not converted to a linear CTE.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"property_points": len(points), "material_id": MATERIAL_ID}, ensure_ascii=False))


if __name__ == "__main__":
    main()
