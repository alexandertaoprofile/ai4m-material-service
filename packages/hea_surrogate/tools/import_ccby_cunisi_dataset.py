"""Normalize the CC-BY Cu-Ni-Si dataset released through Mendeley Data.

This is an *ambient auxiliary* training layer, not a substitute for the
temperature-labelled hot-end evidence table.  Every emitted row retains the
source DOI, processing fields and the official dataset checksum so it can be
joined only under an explicitly room-temperature model scope.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/processed/copper_ambient_cunisi_ccby_v1/candidate_records.csv"
EXPECTED_SHA256 = "317aa54182debc71b3ebdd2f1695a04008011711749d903347304d42850061ee"
ELEMENTS = ("Cu", "Al", "Co", "Cr", "Mg", "Ni", "Si", "Sn", "Zn")
PROCESS_COLUMNS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Aging", "Tag (K)", "tag (h)", "Secondary thermo-mechanical process")
TARGET_COLUMNS = ("Hardness (HV)", "Yield strength (MPa)", "Ultimate tensile strength (MPa)", "Electrical conductivity (%IACS)")
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _column(ref: str) -> str:
    return "".join(character for character in ref if character.isalpha())


def _xlsx_rows(path: Path) -> list[dict[str, str]]:
    with ZipFile(path) as archive:
        strings_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.text or "" for node in item.findall(".//x:t", NS)) for item in strings_root.findall("x:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[str, str]] = []
    for row in sheet.findall(".//x:sheetData/x:row", NS):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", NS):
            value_node = cell.find("x:v", NS)
            value = "" if value_node is None else value_node.text or ""
            if cell.attrib.get("t") == "s" and value:
                value = strings[int(value)]
            values[_column(cell.attrib["r"])] = value
        rows.append(values)
    if not rows:
        raise ValueError("released workbook has no rows")
    headers = rows[0]
    inverse_headers = {letter: name for letter, name in headers.items() if name}
    return [{inverse_headers[letter]: value for letter, value in row.items() if letter in inverse_headers} for row in rows[1:]]


def _number(value: str) -> float:
    return float(value.strip())


def normalize(source: Path, output: Path) -> dict[str, int | str]:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"unexpected workbook checksum: {digest}")
    rows = _xlsx_rows(source)
    required = set(ELEMENTS) | set(PROCESS_COLUMNS[:-1]) | set(TARGET_COLUMNS) | {"DOI"}
    missing = sorted(required - set(rows[0])) if rows else sorted(required)
    if missing:
        raise ValueError(f"released workbook missing documented columns: {missing}")

    kept: list[dict[str, str]] = []
    for row in rows:
        # The final worksheet line is a citation note, not an experimental row.
        if not row.get("DOI", "").startswith(("http://", "https://")):
            continue
        composition = {element: _number(row[element]) for element in ELEMENTS}
        if not 99.0 <= sum(composition.values()) <= 101.0:
            continue
        ys = _number(row["Yield strength (MPa)"])
        uts = _number(row["Ultimate tensile strength (MPa)"])
        if min(ys, uts) <= 0 or uts < ys:
            continue
        process = {name: row.get(name, "").strip() for name in PROCESS_COLUMNS}
        # The source misspells this header; map it to the documented field.
        process["Secondary thermo-mechanical process"] = row.get("Secondar. thermo-mecha0ical process", "").strip()
        kept.append({
            "alloy_formula": "-".join(f"{element}{amount:g}" for element, amount in composition.items() if amount),
            "alloy_class": "Cu-Ni-Si precipitation-strengthened copper alloy",
            "test_temperature_c": "room_temperature_source_scope",
            "source_doi": row["DOI"].strip(),
            "source_dataset": "Kolev, Cu-Ni-Si Alloys Properties Dataset, Mendeley Data 10.17632/7tdd6vngzm.1, CC BY 4.0",
            "source_file_sha256": digest,
            **{f"{element}_wt_percent": f"{amount:.8g}" for element, amount in composition.items()},
            **process,
            **{name: row[name].strip() for name in TARGET_COLUMNS},
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(kept[0]) if kept else []
    with output.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    report = {
        "source_rows": len(rows),
        "admitted_room_temperature_rows": len(kept),
        "independent_source_file_sha256": digest,
        "model_scope": "ambient_auxiliary_only",
    }
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Official Mendeley CC-BY XLSX")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(normalize(args.input, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
