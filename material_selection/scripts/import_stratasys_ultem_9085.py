"""Import the downloaded Stratasys ULTEM 9085 material data sheet.

Every mechanical value retains F900/T16, layer-height and build-orientation;
only the in-plane XZ results are exposed as generic FDM screening properties.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

MID = "MAT-STRATASYS-ULTEM-9085-NATURAL"
SOURCE = "MDS_Stratasys_ULTEM_9085_0925A.pdf"
SOURCE_ID = "SRC-STRATASYS-ULTEM-9085-MDS-0925A"
FIELDS = ("material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json")

def row(prop, value, unit, condition, method, page, temperature=296.15, uncertainty=""):
    return {"material_id": MID, "property": prop, "value": str(value), "unit": unit,
            "temperature_K": str(temperature), "uncertainty": uncertainty,
            "data_kind": "manufacturer_typical_value", "condition": condition,
            "source_id": SOURCE_ID, "source_locator": f"Material Data Sheet; page {page}",
            "notes": "Manufacturer typical value; use only with the stated FDM build state.",
            "raw_source_file": SOURCE, "raw_sheet": "", "raw_row_number": str(page),
            "raw_row_json": json.dumps({"property": prop, "value": value, "unit": unit, "method": method}, ensure_ascii=False, sort_keys=True)}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not a.source.is_file() or a.source.name != SOURCE: raise SystemExit(f"expected {SOURCE}")
    if a.output.exists(): raise SystemExit("refusing to overwrite an existing import bundle")
    physical = "ULTEM 9085 Natural; F900 printed, 0.254 mm layer; orientation as stated"
    xz = physical + "; T16 tip; XZ orientation"
    zx = physical + "; T16 tip; ZX orientation"
    points = [
        row("density", 1.27, "g/cm³", physical + "; 23 °C", "ASTM D792", 7),
        row("heat_deflection_temperature", 178.2, "°C", physical + "; XY; 0.45 MPa", "ASTM D648 Method B", 6, 451.35),
        row("heat_deflection_temperature", 170.2, "°C", physical + "; XY; 1.8 MPa", "ASTM D648 Method B", 6, 443.35),
        row("glass_transition_temperature", 177.3, "°C", physical, "ASTM D7426 inflection", 6, 450.45),
        row("thermal_expansion_coefficient", 44.45, "ppm/K", physical + "; XY; -50–60 °C", "ASTM E831", 6),
        row("thermal_expansion_coefficient", 32.31, "ppm/K", physical + "; XY; 60–160 °C", "ASTM E831", 6),
        row("thermal_conductivity", .2109, "W/(m·K)", physical + "; 30 °C", "ASTM E1952", 6, 303.15),
        row("thermal_diffusivity", .132, "mm²/s", physical + "; 30 °C", "ASTM E1952", 7, 303.15),
        row("yield_strength", 69.2, "MPa", xz + "; tensile", "ASTM D638", 8, uncertainty="±1.0 MPa"),
        row("tensile_strength", 68.1, "MPa", xz + "; break", "ASTM D638", 8, uncertainty="±1.6 MPa"),
        row("elongation", 5.4, "%", xz + "; at break", "ASTM D638", 8, uncertainty="±0.50 %"),
        row("youngs_modulus", 2.52, "GPa", xz + "; tensile", "ASTM D638", 8, uncertainty="±0.062 GPa"),
        row("flexural_strength", 104, "MPa", xz + "; break", "ASTM D790 Procedure A", 8, uncertainty="±2.2 MPa"),
        row("flexural_modulus", 2.40, "GPa", xz, "ASTM D790 Procedure A", 8, uncertainty="±0.032 GPa"),
        row("compressive_strength", 139, "MPa", xz + "; yield", "ASTM D695", 8, uncertainty="±9.4 MPa"),
        row("z_axis_tensile_strength", 39.4, "MPa", zx + "; break", "ASTM D638", 8, uncertainty="±8.7 MPa"),
        row("z_axis_youngs_modulus", 2.41, "GPa", zx + "; tensile", "ASTM D638", 8, uncertainty="±0.15 GPa"),
    ]
    material = {"material_id": MID, "display_name": "Stratasys ULTEM 9085 Natural（PEI）", "family": "FDM 高性能 PEI/ULTEM 工程塑料", "grade": "ULTEM 9085 Resin Natural", "UNS/standard": "", "product_state": "F900 FDM 打印件；T16/0.254 mm；方向见每条性质条件", "source_id": SOURCE_ID, "data_role": "manufacturer 3D-print material evidence", "temperature_coverage": "-50–178.4 °C（各性质见条件）", "composition_available": "PEI (polyetherimide)", "process_metadata": "F900；T16；0.254 mm layer height；XZ/ZX directions retained", "notes": "FDM 各向异性已按方向保留；ZX 值不并入通用 XZ 筛选。", "raw_source_file": SOURCE, "raw_sheet": "", "raw_row_number": "6", "raw_row_json": json.dumps({"url": "https://www.stratasys.com/siteassets/materials/materials-catalog/fdm-materials/ultem-9085/mds_fdm_ultem9085_0925a.pdf?v=4ab0d4", "pages": [6,7,8]})}
    a.output.mkdir(parents=True)
    for name, fields, data in (("materials.csv", tuple(material), [material]), ("property_points.csv", FIELDS, points), ("material_aliases.csv", ("material_id", "alias", "alias_type", "source"), [{"material_id": MID, "alias": x, "alias_type": "trade_name", "source": SOURCE_ID} for x in ("ULTEM 9085", "PEI 9085", "ULTEM™ 9085")]), ("curve_data.csv", (), []), ("composition_long.csv", (), [])):
        with (a.output / name).open("w", encoding="utf-8", newline="") as h:
            w = csv.DictWriter(h, fieldnames=fields); w.writeheader(); w.writerows(data)
    (a.output / "import_manifest.json").write_text(json.dumps({"material_id": MID, "source": {"file": SOURCE, "sha256": hashlib.sha256(a.source.read_bytes()).hexdigest(), "pages": [6,7,8]}, "counts": {"materials": 1, "property_points": len(points)}, "included": "Explicit physical and F900/T16 mechanical values; directional state retained.", "excluded": "Fire, smoke, chemical and curve-only data."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
