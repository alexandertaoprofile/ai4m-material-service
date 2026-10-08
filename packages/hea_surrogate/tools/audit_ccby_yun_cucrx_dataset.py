"""Audit, but do not train on, the released Yun Cu-Cr-X figure dataset.

The release contains digitized curve points.  Its ``temperature`` x-axis can
refer to ageing/process temperature, and it has no per-point tensile-test
temperature field.  This tool therefore preserves a traceable candidate audit
and explicitly admits *zero* rows to the hot-end strength model until an
article-level reviewer resolves that semantic distinction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/raw/copper_hot_end_20260910/sources/Cu-Cr-X dataset.xlsx"
DEFAULT_OUTPUT = ROOT / "data/processed/copper_yun_cucrx_ccby_v2/audit.json"
EXPECTED_SHA256 = "67603e116fbfb02d6829b1db71af7af5a9e905f2c819bb0db5a625a0d8fb160c"
ELEMENTS = ("Cu", "Ag", "Al", "B", "Ce", "Co", "Cr", "Er", "Fe", "Hf", "Mg", "Mn", "Nb", "Ni", "Sc", "Si", "Sn", "Ti", "Y", "Zn", "Zr")
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _column(ref: str) -> str:
    return "".join(char for char in ref if char.isalpha())


def _rows(path: Path) -> list[dict[str, str]]:
    with ZipFile(path) as archive:
        shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.text or "" for node in item.findall(".//x:t", NS)) for item in shared.findall("x:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    raw_rows: list[dict[str, str]] = []
    for row in sheet.findall(".//x:sheetData/x:row", NS):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", NS):
            value_node = cell.find("x:v", NS)
            value = "" if value_node is None else value_node.text or ""
            if cell.attrib.get("t") == "s" and value:
                value = strings[int(value)]
            values[_column(cell.attrib["r"])] = value
        raw_rows.append(values)
    if not raw_rows:
        raise ValueError("released workbook has no rows")
    inverse = {letter: name for letter, name in raw_rows[0].items() if name}
    return [{inverse[letter]: value for letter, value in row.items() if letter in inverse} for row in raw_rows[1:]]


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def audit(source: Path, output: Path) -> dict[str, object]:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"unexpected released workbook checksum: {digest}")
    rows = _rows(source)
    required = {"doi", "fig_num", "curve_num", "materials_unit", *ELEMENTS, "x_data", "x_name", "x_unit", "y_data", "y_name", "y_unit", "source_reliability", "flag_note"}
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"released workbook missing documented columns: {missing}")
    closed_high_cu: list[dict[str, str]] = []
    strength: list[dict[str, str]] = []
    high_confidence: list[dict[str, str]] = []
    for row in rows:
        composition = [_number(row[element]) or 0.0 for element in ELEMENTS]
        if row["materials_unit"] == "wt.%" and (_number(row["Cu"]) or 0.0) >= 80.0 and 99.0 <= sum(composition) <= 101.0:
            closed_high_cu.append(row)
            if row["y_name"] in {"UTS", "YS"} and row["y_unit"] == "MPa" and _number(row["y_data"]) is not None:
                strength.append(row)
                if (_number(row["source_reliability"]) or 0.0) >= 0.8:
                    high_confidence.append(row)
    semantic_terms = ("tensile", "strength", "stress")
    semantically_named = [row for row in strength if any(term in row.get("figure_caption", "").lower() for term in semantic_terms)]
    report: dict[str, object] = {
        "status": "article_level_test_temperature_review_required_not_hot_end_training",
        "source_file_sha256": digest,
        "source_rows": len(rows),
        "closed_copper_base_rows": len(closed_high_cu),
        "strength_candidate_rows": len(strength),
        "strength_candidate_rows_source_reliability_gte_0p8": len(high_confidence),
        "strength_candidate_figures": len({row["fig_num"] for row in strength}),
        "strength_candidates_with_tensile_strength_or_stress_in_caption": len(semantically_named),
        "strength_candidates_without_tensile_semantic_support_in_caption": len(strength) - len(semantically_named),
        "strength_y_axis_breakdown": dict(sorted(Counter(row["y_name"] for row in strength).items())),
        "strength_x_axis_breakdown": dict(sorted(Counter(f"{row['x_name']} [{row['x_unit']}]" for row in strength).items())),
        "hot_end_admitted_rows": 0,
        "exclusion_reason": "The release does not supply a per-point tensile-test temperature. Its x-axis temperature may be a process/ageing variable, so assigning it as test temperature would fabricate a model feature. In addition, many rows labeled UTS/YS have captions describing microstructure or other non-tensile figures, so source_reliability alone cannot establish label semantics.",
        "required_before_admission": ["Review the cited source article/figure context for each candidate curve.", "Establish tensile-test temperature and strain-rate evidence.", "Retain figure number, curve number, axes, reliability score and flag_note in every admitted row."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    queue_path = output.with_name("strength_figure_review_queue.csv")
    by_figure: dict[str, list[dict[str, str]]] = {}
    for row in strength:
        by_figure.setdefault(row["fig_num"], []).append(row)
    with queue_path.open("w", newline="", encoding="utf8") as handle:
        fields = ("doi", "fig_num", "candidate_points", "y_names", "x_axes", "min_source_reliability", "formulae_wt_percent", "caption_has_tensile_semantics", "figure_caption", "review_requirement")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for figure, points in sorted(by_figure.items(), key=lambda item: (-len(item[1]), item[0])):
            caption = points[0]["figure_caption"].replace("\n", " ").strip()
            semantic = any(term in caption.lower() for term in semantic_terms)
            formulae = []
            for point in points:
                formula = "-".join(f"{element}{(_number(point[element]) or 0.0):g}" for element in ELEMENTS if (_number(point[element]) or 0.0))
                if formula not in formulae:
                    formulae.append(formula)
            writer.writerow({
                "doi": points[0]["doi"],
                "fig_num": figure,
                "candidate_points": len(points),
                "y_names": ";".join(sorted({point["y_name"] for point in points})),
                "x_axes": ";".join(sorted({f"{point['x_name']} [{point['x_unit']}]" for point in points})),
                "min_source_reliability": f"{min((_number(point['source_reliability']) or 0.0) for point in points):.6f}",
                "formulae_wt_percent": " | ".join(formulae),
                "caption_has_tensile_semantics": str(semantic).lower(),
                "figure_caption": caption,
                "review_requirement": "Confirm y-axis/figure semantic match plus tensile test temperature and strain rate in cited article before any model admission.",
            })
    report["strength_figure_review_queue"] = str(queue_path.relative_to(ROOT))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, nargs="?", default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(audit(args.input, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
