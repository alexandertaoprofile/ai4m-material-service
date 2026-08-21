"""Import the verified 3D4Makers PEEK Filament technical data sheet.

The TDS is for a filament product but it does not identify each test specimen
as an FDM specimen.  Material-property rows therefore retain their stated ISO
condition and are not represented as direction-specific printed-part results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


MATERIAL_ID = "MAT-3D4MAKERS-PEEK"
SOURCE_ID = "SRC-3D4MAKERS-PEEK-FILAMENT-TDS"
SOURCE_FILE = "TDS_3D4Makers_PEEK_Filament_1.pdf"
SOURCE_URL = "https://cdn.shopify.com/s/files/1/0762/2839/files/TDS_PEEK_Filament_1.pdf?5159116558357548495"
FIELDS = ("material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")


def point(prop: str, value: float, unit: str, *, condition: str, method: str, temperature_k: float = 296.15) -> dict[str, str]:
    return {
        "material_id": MATERIAL_ID, "property": prop, "value": f"{value:g}", "unit": unit, "temperature_K": f"{temperature_k:g}", "uncertainty": "",
        "data_kind": "manufacturer_typical_value", "condition": condition, "source_id": SOURCE_ID, "source_locator": "TDS PEEK Filament; page 1",
        "notes": "Manufacturer typical value; test state is only the source-stated ISO condition and is not asserted as an FDM direction result.",
        "raw_source_file": SOURCE_FILE, "raw_sheet": "", "raw_row_number": "1", "raw_row_json": json.dumps({"property": prop, "value": value, "unit": unit, "method": method}, ensure_ascii=False, sort_keys=True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file() or args.source.name != SOURCE_FILE:
        raise SystemExit(f"expected verified source PDF named {SOURCE_FILE}")
    if args.output.exists():
        raise SystemExit("refusing to overwrite an existing import bundle")

    base = "3D4Makers PEEK Filament; PEEK; source material test state as specified"
    points = [
        point("yield_strength", 105, "MPa", condition=f"{base}; tensile yield; 23 °C", method="ISO 527"),
        point("elongation", 30, "%", condition=f"{base}; tensile break; 23 °C", method="ISO 527"),
        point("youngs_modulus", 4.1, "GPa", condition=f"{base}; tensile; 23 °C", method="ISO 527"),
        point("flexural_strength", 130, "MPa", condition=f"{base}; at 3.5% strain; 23 °C", method="ISO 178"),
        point("flexural_modulus", 3.9, "GPa", condition=f"{base}; 23 °C", method="ISO 178"),
        point("compressive_strength", 130, "MPa", condition=f"{base}; 23 °C", method="ISO 604"),
        point("charpy_impact_strength_notched", 4.2, "kJ/m²", condition=f"{base}; notched; 23 °C", method="ISO 179/1eA"),
        point("izod_impact_strength_notched", 5.0, "kJ/m²", condition=f"{base}; notched; 23 °C", method="ISO 180/A"),
        point("melting_temperature", 343, "°C", condition=f"{base}", method="ISO 11357", temperature_k=616.15),
        point("glass_transition_temperature", 143, "°C", condition=f"{base}; onset", method="ISO 11357", temperature_k=416.15),
        point("thermal_expansion_coefficient", 50, "ppm/K", condition=f"{base}; along flow; below Tg", method="ISO 11359"),
        point("heat_deflection_temperature", 156, "°C", condition=f"{base}; as moulded; 1.8 MPa", method="ISO 75A-f", temperature_k=429.15),
        point("thermal_conductivity", 0.32, "W/(m·K)", condition=f"{base}; along flow; 23 °C", method="ISO 22007-4"),
        point("relative_thermal_index_electrical", 260, "°C", condition=f"{base}; electrical", method="UL 746B", temperature_k=533.15),
        point("melt_viscosity", 130, "Pa·s", condition=f"{base}; 400 °C", method="ISO 11443", temperature_k=673.15),
        point("density", 1.30, "g/cm³", condition=f"{base}; crystalline", method="ISO 1183"),
        point("shore_d_hardness", 85, "Shore D", condition=f"{base}; 23 °C", method="ISO 868"),
        point("water_absorption", 0.45, "%", condition=f"{base}; immersion saturation; 23 °C", method="ISO 62-1"),
    ]
    if len(points) != 18:
        raise ValueError("unexpected point count")
    material = {
        "material_id": MATERIAL_ID, "display_name": "3D4Makers PEEK 线材（VICTREX PEEK 151G 基料）", "family": "FFF/FDM 未增强 PEEK 高性能热塑性材料",
        "grade": "3D4Makers PEEK Filament", "UNS/standard": "VICTREX PEEK 151G base resin", "product_state": "厂商技术表 ISO 标准条件；非方向专属 FDM 成件性质",
        "source_id": SOURCE_ID, "data_role": "manufacturer 3D-print filament material evidence", "temperature_coverage": "23–400 °C（各性质见条件）", "composition_available": "unfilled PEEK; manufacturer identifies VICTREX PEEK 151G base resin",
        "process_metadata": "喷嘴 360–400 °C；热床 120 °C；建议封闭热腔约 100 °C；打印速度 15–30 mm/s；PEI 贴合面", "notes": "TDS 页面明确面向 FDM/FFF，但各 ISO 性能点不宣称打印方向；不与 CF-PEEK 混用。",
        "raw_source_file": SOURCE_FILE, "raw_sheet": "", "raw_row_number": "1", "raw_row_json": json.dumps({"url": SOURCE_URL, "pages": 2}, ensure_ascii=False),
    }
    aliases = [("PEEK", "material_family_alias"), ("3D4Makers PEEK", "trade_name"), ("未增强PEEK", "chinese_common_name"), ("VICTREX PEEK 151G", "base_resin")]
    args.output.mkdir(parents=True)
    with (args.output / "materials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(material)); writer.writeheader(); writer.writerow(material)
    with (args.output / "property_points.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS); writer.writeheader(); writer.writerows(points)
    for name in ("curve_data.csv", "composition_long.csv"):
        with (args.output / name).open("w", encoding="utf-8", newline="") as handle: csv.writer(handle).writerow([])
    with (args.output / "material_aliases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("material_id", "alias", "alias_type", "source")); writer.writeheader()
        writer.writerows({"material_id": MATERIAL_ID, "alias": alias, "alias_type": kind, "source": SOURCE_ID} for alias, kind in aliases)
    sha256 = hashlib.sha256(args.source.read_bytes()).hexdigest()
    (args.output / "import_manifest.json").write_text(json.dumps({"material_id": MATERIAL_ID, "source": {"id": SOURCE_ID, "url": SOURCE_URL, "file": SOURCE_FILE, "sha256": sha256, "pages": [1, 2]}, "counts": {"materials": 1, "property_points": len(points), "aliases": len(aliases)}, "included": "All explicit numeric material and processing values on page 1 with a property, method, unit, and condition.", "excluded": "Page 2 recommendations add no new numeric property evidence; its process observations are retained in material process metadata."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"material_id": MATERIAL_ID, "property_points": len(points), "sha256": sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
