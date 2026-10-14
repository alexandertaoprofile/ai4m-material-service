"""Import unambiguous manufacturer values for LUVOCOM 3F PEEK CF 9676 BK.

The source calls its ISO 3167 specimens ``MPTS`` and does not identify them as
FDM printed specimens.  Consequently no material-level result in this bundle
is labelled as a printed-part property; the FFF temperatures are kept only as
product processing context.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


MATERIAL_ID = "MAT-LUVOCOM-3F-PEEK-CF-9676-BK"
SOURCE_ID = "SRC-LUVOCOM-3F-PEEK-CF-9676-BK-TDS"
SOURCE_FILE = "TDS_LUVOCOM_3F_PEEK_CF_9676_BK_3D4Makers.pdf"
SOURCE_URL = "https://cdn.shopify.com/s/files/1/0762/2839/files/TDS_LUVOCOM_3F_PEEK_CF_9676_BK_3D4Makers.pdf?v=1623004547"
FIELDS = (
    "material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind",
    "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet",
    "raw_row_number", "raw_row_json",
)


def point(property_name: str, value: float, unit: str, *, method: str, condition: str, temperature_k: float = 296.15) -> dict[str, str]:
    return {
        "material_id": MATERIAL_ID, "property": property_name, "value": f"{value:g}", "unit": unit,
        "temperature_K": f"{temperature_k:g}", "uncertainty": "",
        "data_kind": "manufacturer_typical_value", "condition": condition, "source_id": SOURCE_ID,
        "source_locator": "TDS preliminary data sheet; page 1", "notes": "Manufacturer typical value; TDS identifies an ISO 3167 MPTS specimen, not an FDM printed specimen.",
        "raw_source_file": SOURCE_FILE, "raw_sheet": "", "raw_row_number": "1",
        "raw_row_json": json.dumps({"property": property_name, "value": value, "unit": unit, "method": method}, ensure_ascii=False, sort_keys=True),
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

    standard = "LUVOCOM 3F PEEK CF 9676 BK; carbon-reinforced PEEK; black; MPTS ISO 3167 A"
    points = [
        point("density", 1.36, "g/cm³", method="ISO 1183", condition=f"{standard}; physical property"),
        point("melt_flow_index", 14.3, "g/10 min", method="ISO 1133; 380 °C/10 kg; pellet", condition=f"{standard}; pellet melt-flow test", temperature_k=653.15),
        point("tensile_strength", 126, "MPa", method="ISO 527", condition=f"{standard}; 23 °C/50% RH"),
        point("elongation", 3.9, "%", method="ISO 527", condition=f"{standard}; 23 °C/50% RH"),
        point("youngs_modulus", 7.8, "GPa", method="ISO 527", condition=f"{standard}; 23 °C/50% RH"),
        point("charpy_impact_strength", 66, "kJ/m²", method="ISO 179 1 eU", condition=f"{standard}; 23 °C/50% RH"),
        point("charpy_impact_strength_notched", 7, "kJ/m²", method="ISO 179 eA", condition=f"{standard}; 23 °C/50% RH"),
        point("heat_deflection_temperature", 280, "°C", method="ISO 75 HDT A", condition=f"{standard}; test load as defined by ISO 75 HDT A", temperature_k=553.15),
        point("continuous_service_temperature", 250, "°C", method="UL 746B", condition=f"{standard}; continuous-service rating", temperature_k=523.15),
        point("maximum_short_term_use_temperature", 280, "°C", method="manufacturer TDS", condition=f"{standard}; maximum short-term use temperature", temperature_k=553.15),
    ]
    if len(points) != 10:
        raise ValueError("unexpected point count")
    material = {
        "material_id": MATERIAL_ID, "display_name": "LUVOCOM 3F PEEK CF 9676 BK 碳纤维增强 PEEK", "family": "FFF/FDM 短切碳纤维增强 PEEK",
        "grade": "LUVOCOM 3F PEEK CF 9676 BK", "UNS/standard": "", "product_state": "材料级 ISO 3167 MPTS 标准试样；不是 FDM 成件性质",
        "source_id": SOURCE_ID, "data_role": "manufacturer 3D-print filament material evidence", "temperature_coverage": "23–280 °C（各性质见条件）",
        "composition_available": "PEEK; carbon reinforced (3D4Makers product page: 15% carbon reinforced)",
        "process_metadata": "FFF 建议：喷嘴 400–450 °C；热床 >120 °C；预干燥 120 °C、4–8 h；技术表给出 360–400 °C 加工温区",
        "notes": "仅导入技术表中单位、方法和数值对应关系清晰的数值；不将 ISO 标准试样性能冒充为打印方向性能。", "raw_source_file": SOURCE_FILE,
        "raw_sheet": "", "raw_row_number": "1", "raw_row_json": json.dumps({"url": SOURCE_URL, "pages": 2, "document_status": "preliminary data sheet"}, ensure_ascii=False),
    }
    aliases = [
        ("LUVOCOM 3F PEEK CF 9676 BK", "trade_name"), ("PEEK-CF", "material_family_alias"),
        ("CF-PEEK", "material_family_alias"), ("碳纤维增强PEEK", "chinese_common_name"),
    ]
    args.output.mkdir(parents=True)
    with (args.output / "materials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(material)); writer.writeheader(); writer.writerow(material)
    with (args.output / "property_points.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS); writer.writeheader(); writer.writerows(points)
    for name in ("curve_data.csv", "composition_long.csv"):
        with (args.output / name).open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow([])
    with (args.output / "material_aliases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("material_id", "alias", "alias_type", "source")); writer.writeheader()
        writer.writerows({"material_id": MATERIAL_ID, "alias": alias, "alias_type": kind, "source": SOURCE_ID} for alias, kind in aliases)
    sha256 = hashlib.sha256(args.source.read_bytes()).hexdigest()
    (args.output / "import_manifest.json").write_text(json.dumps({
        "material_id": MATERIAL_ID, "source": {"id": SOURCE_ID, "url": SOURCE_URL, "file": SOURCE_FILE, "sha256": sha256, "pages": [1, 2]},
        "counts": {"materials": 1, "property_points": len(points), "aliases": len(aliases)},
        "included": "Only values with unambiguous property/method/unit associations in page 1.",
        "excluded": "Censored electrical properties, range-valued MVR/shrinkage, and ambiguous thermal-conductivity/CTE layout cells were not converted to catalogue points.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"material_id": MATERIAL_ID, "property_points": len(points), "sha256": sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
