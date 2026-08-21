"""Import the official Fiberon PA6-CF20 FDM data sheet as a state-aware bundle.

The manufacturer puts dry and wet test results as well as X-Y/Z print
directions on the same one-page TDS.  This importer deliberately keeps those
states in each evidence row: generic screening properties only receive X-Y
values, while Z-direction values keep direction-specific property names.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


MATERIAL_ID = "MAT-FIBERON-PA6-CF20"
SOURCE_ID = "SRC-FIBERON-PA6-CF20-TDS-V1.1"
SOURCE_FILE = "TDS_FIBERON_PA6-CF20_V1.1_EN.pdf"
SOURCE_URL = "https://fiberon.polymaker.com/wp-content/uploads/TDS_FIBERON-PA6-CF20_V1.1_EN.pdf"
FIELDS = (
    "material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind",
    "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet",
    "raw_row_number", "raw_row_json",
)


def point(
    property_name: str, value: float, unit: str, *, condition: str, method: str,
    uncertainty: str = "", temperature_k: float = 298.15, note: str = "",
) -> dict[str, str]:
    raw = {"property": property_name, "value": value, "unit": unit, "method": method}
    return {
        "material_id": MATERIAL_ID, "property": property_name, "value": f"{value:g}", "unit": unit,
        "temperature_K": f"{temperature_k:g}", "uncertainty": uncertainty,
        "data_kind": "manufacturer_typical_value", "condition": condition, "source_id": SOURCE_ID,
        "source_locator": "TDS V1.1; page 1", "notes": note or "Manufacturer typical value; not a design specification.",
        "raw_source_file": SOURCE_FILE, "raw_sheet": "", "raw_row_number": "1",
        "raw_row_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
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

    dry = "Fiberon PA6-CF20 printed specimen; annealed 100 °C for 16 h; dry status; 100% infill; print 300 °C; bed 50 °C; two shells; fan off; page 1"
    wet = "Fiberon PA6-CF20 printed specimen; annealed 100 °C for 16 h then immersed in water 60 °C for 48 h; average moisture content 5.30%; 100% infill; print 300 °C; bed 50 °C; two shells; fan off; page 1"
    points = [
        point("density", 1.17, "g/cm³", condition="Fiberon PA6-CF20 filament; 23 °C", method="ISO 1183; GB/T 1033", temperature_k=296.15),
        point("melt_flow_index", 20.5, "g/10 min", condition="Fiberon PA6-CF20 filament; 300 °C; 2.16 kg", method="TDS physical-properties table", temperature_k=573.15),
        point("water_absorption", 3.3, "%", condition="Fiberon PA6-CF20; equilibrium water absorption from source curve", method="TDS moisture-absorption curve", note="Equilibrium value shown on the manufacturer curve; duration/conditioning beyond curve axes is not specified."),
        point("glass_transition_temperature", 74.2, "°C", condition="Fiberon PA6-CF20 filament; DSC 10 °C/min", method="DSC", temperature_k=347.35),
        point("melting_temperature", 218.5, "°C", condition="Fiberon PA6-CF20 filament; DSC 10 °C/min", method="DSC", temperature_k=491.65),
        point("crystallization_temperature", 184.6, "°C", condition="Fiberon PA6-CF20 filament; DSC 10 °C/min", method="DSC", temperature_k=457.75),
        point("decomposition_temperature", 446.2, "°C", condition="Fiberon PA6-CF20 filament; TGA 20 °C/min", method="TGA", temperature_k=719.35),
        point("vicat_softening_temperature", 219.2, "°C", condition="Fiberon PA6-CF20 filament", method="ISO 306; GB/T 1633", temperature_k=492.35),
        point("heat_deflection_temperature", 173, "°C", condition="Fiberon PA6-CF20; 1.8 MPa", method="ISO 75", temperature_k=446.15),
        point("heat_deflection_temperature", 215, "°C", condition="Fiberon PA6-CF20; 0.45 MPa", method="ISO 75", temperature_k=488.15),
        point("youngs_modulus", 8636.5, "MPa", condition=f"{dry}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±211.4 MPa"),
        point("tensile_strength", 109.3, "MPa", condition=f"{dry}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±2.4 MPa"),
        point("elongation", 2.1, "%", condition=f"{dry}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±0.2 %"),
        point("flexural_modulus", 7037.6, "MPa", condition=f"{dry}; X-Y direction", method="ISO 178; GB/T 9341", uncertainty="±205.4 MPa"),
        point("flexural_strength", 161.0, "MPa", condition=f"{dry}; X-Y direction", method="ISO 178; GB/T 9341", uncertainty="±3.9 MPa"),
        point("charpy_impact_strength_notched", 11.0, "kJ/m²", condition=f"{dry}; X-Y direction", method="ISO 179; GB/T 1043", uncertainty="±0.3 kJ/m²"),
        point("z_axis_youngs_modulus", 3759.5, "MPa", condition=f"{dry}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±118.5 MPa", note="Direction-specific printed-part value; excluded from generic X-Y modulus screening."),
        point("z_axis_tensile_strength", 54.0, "MPa", condition=f"{dry}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±5.2 MPa", note="Direction-specific printed-part value; excluded from generic X-Y tensile screening."),
        point("z_axis_elongation", 1.9, "%", condition=f"{dry}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±0.4 %", note="Direction-specific printed-part value."),
        point("z_axis_flexural_modulus", 2975.3, "MPa", condition=f"{dry}; Z direction", method="ISO 178; GB/T 9341", uncertainty="±174.3 MPa", note="Direction-specific printed-part value."),
        point("z_axis_flexural_strength", 71.3, "MPa", condition=f"{dry}; Z direction", method="ISO 178; GB/T 9341", uncertainty="±17.7 MPa", note="Direction-specific printed-part value."),
        point("youngs_modulus", 2508.1, "MPa", condition=f"{wet}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±82.6 MPa"),
        point("tensile_strength", 54.7, "MPa", condition=f"{wet}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±1.1 MPa"),
        point("elongation", 7.0, "%", condition=f"{wet}; X-Y direction", method="ISO 527; GB/T 1040", uncertainty="±0.9 %"),
        point("flexural_modulus", 2286.2, "MPa", condition=f"{wet}; X-Y direction", method="ISO 178; GB/T 9341", uncertainty="±185.2 MPa"),
        point("flexural_strength", 64.9, "MPa", condition=f"{wet}; X-Y direction", method="ISO 178; GB/T 9341", uncertainty="±4.9 MPa"),
        point("charpy_impact_strength_notched", 35.6, "kJ/m²", condition=f"{wet}; X-Y direction", method="ISO 179; GB/T 1043", uncertainty="±1.2 kJ/m²"),
        point("z_axis_youngs_modulus", 1056.1, "MPa", condition=f"{wet}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±127.9 MPa", note="Direction-specific printed-part value; excluded from generic X-Y modulus screening."),
        point("z_axis_tensile_strength", 25.5, "MPa", condition=f"{wet}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±1.2 MPa", note="Direction-specific printed-part value; excluded from generic X-Y tensile screening."),
        point("z_axis_elongation", 6.7, "%", condition=f"{wet}; Z direction", method="ISO 527; GB/T 1040", uncertainty="±1.7 %", note="Direction-specific printed-part value."),
        point("z_axis_flexural_modulus", 801.1, "MPa", condition=f"{wet}; Z direction", method="ISO 178; GB/T 9341", uncertainty="±24.1 MPa", note="Direction-specific printed-part value."),
        point("z_axis_flexural_strength", 29.2, "MPa", condition=f"{wet}; Z direction", method="ISO 178; GB/T 9341", uncertainty="±1.0 MPa", note="Direction-specific printed-part value."),
    ]
    if len(points) != 32:
        raise ValueError(f"expected 32 property points, got {len(points)}")

    material = {
        "material_id": MATERIAL_ID, "display_name": "Fiberon PA6-CF20 碳纤维增强尼龙6", "family": "FDM 短切碳纤维增强 PA6（尼龙6）",
        "grade": "PA6-CF20", "UNS/standard": "", "product_state": "FDM 打印件；方向、退火与干湿状态见各性质条件",
        "source_id": SOURCE_ID, "data_role": "manufacturer 3D-print material evidence", "temperature_coverage": "23–446.2 °C（各性质见条件）",
        "composition_available": "20 wt% carbon fiber reinforced PA6", "process_metadata": "喷嘴 280–300 °C；热床 40–50 °C；干燥 100 °C/10 h；建议退火 100 °C/16 h；推荐硬化钢或红宝石喷嘴",
        "notes": "仅将 X-Y 方向的拉伸/弯曲数据映射为通用筛选性质；Z 向数据单独保留。", "raw_source_file": SOURCE_FILE,
        "raw_sheet": "", "raw_row_number": "1", "raw_row_json": json.dumps({"url": SOURCE_URL, "version": "V1.1"}, ensure_ascii=False),
    }
    aliases = [
        ("Fiberon PA6-CF20", "trade_name"), ("PA6-CF20", "grade"), ("CF-PA6", "material_family_alias"),
        ("碳纤维增强PA6", "chinese_common_name"), ("碳纤维增强尼龙6", "chinese_common_name"),
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
        "material_id": MATERIAL_ID, "source": {"id": SOURCE_ID, "url": SOURCE_URL, "file": SOURCE_FILE, "sha256": sha256, "page": 1},
        "counts": {"materials": 1, "property_points": len(points), "aliases": len(aliases)},
        "included": "All numeric physical, thermal, and dry/wet mechanical values visibly stated in the manufacturer TDS.",
        "excluded": "Flame rating and censored surface resistivity are retained in source only because this numeric point schema has no categorical/censored-value representation.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"material_id": MATERIAL_ID, "property_points": len(points), "sha256": sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
