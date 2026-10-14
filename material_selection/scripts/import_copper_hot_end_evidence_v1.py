"""Import the reviewed copper hot-wall grade evidence into the 1105 catalogue.

Only source-anchored grade/state facts are written.  GRCop-84 conductivity
nodes are a reproducible evaluation of the cited published equation, not a
new surrogate prediction.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

MATERIAL_FIELDS = ["material_id", "display_name", "family", "grade", "UNS/standard", "product_state", "source_id", "data_role", "temperature_coverage", "composition_available", "process_metadata", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"]
POINT_FIELDS = ["material_id", "property", "value", "unit", "temperature_K", "uncertainty", "data_kind", "condition", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"]
CURVE_FIELDS = ["material_id", "property", "raw_temperature", "raw_temperature_unit", "temperature_K", "raw_value", "raw_unit", "value_SI", "SI_unit", "uncertainty_raw", "condition", "data_kind", "source_id", "source_locator", "transformation", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"]
COMPOSITION_FIELDS = ["material_id", "component", "min", "max", "nominal", "uncertainty", "basis", "data_kind", "source_id", "source_locator", "notes", "raw_source_file", "raw_sheet", "raw_row_number", "raw_row_json"]
ALIAS_FIELDS = ["material_id", "alias", "alias_type", "source"]

SOURCES = {
    "SRC-CU-GRCOP84-THERMAL": "NASA/CR-2000-210055, pp. 1, 7; Eq. 17; https://ntrs.nasa.gov/citations/20000064095",
    "SRC-CU-COMPARISON": "NASA/TM-2007-214663, pp. 15-17, Tables 1, 3, 4, 7; https://ntrs.nasa.gov/citations/20070017311",
    "SRC-CU-NARLOY-LCF": "NASA CR-132555, Tables I-II; https://ntrs.nasa.gov/citations/19740017910",
    "SRC-CU-GRCOP-COMP": "NASA/TM-2019-220318, Table 1; https://ntrs.nasa.gov/citations/20190033380",
}


def _write(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def _raw(source: str, locator: str) -> str:
    return json.dumps({"source": SOURCES[source], "source_locator": locator}, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    output = args.output; output.mkdir(parents=True, exist_ok=True)
    grades = [
        ("MAT-CU-GRCOP84", "GRCop-84 铜基热壁合金", "GRCop-84", "as-received / powder-metallurgy reference", {"Cu": 87.5, "Cr": 6.65, "Nb": 5.85}),
        ("MAT-CU-GRCOP42", "GRCop-42 铜基热壁合金", "GRCop-42", "powder-metallurgy reference", {"Cu": 94.1, "Cr": 3.2, "Nb": 2.7}),
        ("MAT-CU-NARLOYZ", "NARloy-Z 铜基热壁合金", "NARloy-Z", "centrifugally cast, hot rolled, solution annealed and aged", {"Cu": 96.5, "Ag": 3.0, "Zr": .5}),
        ("MAT-CU-CUCRZR", "CuCrZr 铜铬锆合金", "Cu-1Cr-0.1Zr", "as-received reference", {"Cu": 98.9, "Cr": 1.0, "Zr": .1}),
    ]
    materials = [{"material_id": mid, "display_name": name, "family": "铜基热壁/再生冷却合金", "grade": grade, "UNS/standard": "", "product_state": state, "source_id": "SRC-CU-GRCOP-COMP", "data_role": "来源核验牌号证据", "temperature_coverage": "properties vary by source record", "composition_available": "是，wt.%", "process_metadata": state, "notes": "牌号、状态和温区必须与各性质来源记录一起解释。", "raw_source_file": "copper_hot_end_catalog.json", "raw_sheet": "source-anchored catalogue", "raw_row_number": "", "raw_row_json": _raw("SRC-CU-GRCOP-COMP", "Table 1")} for mid, name, grade, state, _composition in grades]
    compositions = []
    aliases = []
    for mid, name, grade, _state, composition in grades:
        aliases.append({"material_id": mid, "alias": grade, "alias_type": "grade", "source": "reviewed copper evidence v1"})
        # Do not introduce a second bare ``CuCrZr`` alias: the 1105 catalogue
        # already carries a different, source-backed Kemper product state
        # under that short name.  The NASA record stays searchable by its
        # actual Cu-1Cr-0.1Zr designation instead of becoming ambiguous.
        if mid != "MAT-CU-CUCRZR":
            aliases.append({"material_id": mid, "alias": name.replace(" 铜基热壁合金", ""), "alias_type": "display shorthand", "source": "reviewed copper evidence v1"})
        for element, amount in composition.items():
            compositions.append({"material_id": mid, "component": element, "min": "", "max": "", "nominal": amount, "uncertainty": "", "basis": "mass %", "data_kind": "nominal composition", "source_id": "SRC-CU-GRCOP-COMP", "source_locator": "NASA/TM-2019-220318, Table 1", "notes": "", "raw_source_file": "copper_hot_end_catalog.json", "raw_sheet": "grades", "raw_row_number": "", "raw_row_json": _raw("SRC-CU-GRCOP-COMP", "Table 1")})
    points = []
    yield_records = (("MAT-CU-GRCOP84", "as-received / powder-metallurgy reference", [25, 200, 400, 600, 800], [196.2, 172.3, 139.5, 87.1, 27.1], "SRC-CU-COMPARISON", "NASA/TM-2007-214663, Tables 3-4"), ("MAT-CU-NARLOYZ", "centrifugally cast, hot rolled, solution annealed and aged", [20, 482, 538, 593], [198.3, 148.8, 130.0, 106.5], "SRC-CU-NARLOY-LCF", "NASA CR-132555, Tables I-II"), ("MAT-CU-CUCRZR", "Cu-1Cr-0.1Zr; as-received reference", [25, 200, 500, 650, 800], [549.9, 452.2, 283.2, 124.9, 69.7], "SRC-CU-COMPARISON", "NASA/TM-2007-214663, Tables 3-4"))
    for mid, condition, temps, values, source, locator in yield_records:
        for temp, value in zip(temps, values):
            points.append({"material_id": mid, "property": "yield_strength", "value": value, "unit": "MPa", "temperature_K": temp + 273.15, "uncertainty": "", "data_kind": "measured", "condition": condition, "source_id": source, "source_locator": locator, "notes": "", "raw_source_file": "copper_hot_end_catalog.json", "raw_sheet": "grades", "raw_row_number": "", "raw_row_json": _raw(source, locator)})
    for strain, cycles in zip([.7, .85, 1., 1.2, 2., 2.5, 3.5], [3601, 2469, 1169, 1126, 331, 253, 99]):
        locator = "NASA CR-132555, Tables I-II"
        points.append({"material_id": "MAT-CU-NARLOYZ", "property": "low_cycle_fatigue_life", "value": cycles, "unit": "cycles", "temperature_K": 811.15, "uncertainty": "", "data_kind": "measured", "condition": f"total strain range {strain}% ; centrifugally cast, hot rolled, solution annealed and aged", "source_id": "SRC-CU-NARLOY-LCF", "source_locator": locator, "notes": "low-cycle fatigue strain-life point", "raw_source_file": "copper_hot_end_catalog.json", "raw_sheet": "grades", "raw_row_number": "", "raw_row_json": _raw("SRC-CU-NARLOY-LCF", locator)})
    curves = []
    formula = "6893 - 3466 ln(T) + 599.5 ln(T)^2 - 34.18 ln(T)^3"
    for temp in (296., 473., 673., 873., 1073., 1173.):
        value = 6893 - 3466 * math.log(temp) + 599.5 * math.log(temp) ** 2 - 34.18 * math.log(temp) ** 3
        locator = "NASA/CR-2000-210055, p. 7, Eq. 17"
        curves.append({"material_id": "MAT-CU-GRCOP84", "property": "thermal_conductivity", "raw_temperature": temp, "raw_temperature_unit": "K", "temperature_K": temp, "raw_value": value, "raw_unit": "W/(m·K)", "value_SI": value, "SI_unit": "W/(m·K)", "uncertainty_raw": "", "condition": "GRCop-84 source published curve; state as recorded in source", "data_kind": "published_formula_recalculation", "source_id": "SRC-CU-GRCOP84-THERMAL", "source_locator": locator, "transformation": formula, "raw_source_file": "copper_hot_end_catalog.json", "raw_sheet": "GRCop-84 thermal conductivity", "raw_row_number": "", "raw_row_json": _raw("SRC-CU-GRCOP84-THERMAL", locator)})
    _write(output / "materials.csv", MATERIAL_FIELDS, materials); _write(output / "property_points.csv", POINT_FIELDS, points); _write(output / "curve_data.csv", CURVE_FIELDS, curves); _write(output / "composition_long.csv", COMPOSITION_FIELDS, compositions); _write(output / "material_aliases.csv", ALIAS_FIELDS, aliases)
    (output / "import_manifest.json").write_text(json.dumps({"scope": "reviewed copper hot-wall grade evidence migrated from 1111 evidence route", "counts": {"materials": len(materials), "property_points": len(points), "curve_points": len(curves), "composition_rows": len(compositions), "aliases": len(aliases)}, "sources": SOURCES}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
