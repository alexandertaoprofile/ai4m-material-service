"""Extract the structured commodity workbook without requiring openpyxl."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
TABLES = ("Materials", "Composition_Long", "Property_Points", "Curve_Data")


def _column(ref: str) -> int:
    letters = "".join(char for char in ref if char.isalpha())
    value = 0
    for char in letters: value = value * 26 + ord(char) - 64
    return value - 1


def _value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    value = cell.find("x:v", NS)
    if kind == "inlineStr": return "".join(t.text or "" for t in cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
    raw = "" if value is None else (value.text or "")
    return shared[int(raw)] if kind == "s" and raw else raw


def read_xlsx_table(workbook: Path, wanted_sheet: str) -> list[dict[str, str]]:
    with ZipFile(workbook) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")) for item in root]
        wb = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in rels.findall("rel:Relationship", NS)}
        sheet = next((item for item in wb.findall("x:sheets/x:sheet", NS) if item.attrib["name"] == wanted_sheet), None)
        if sheet is None: raise ValueError(f"sheet not found: {wanted_sheet}")
        target = targets[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]].lstrip("/")
        tree = ET.fromstring(archive.read(target if target.startswith("xl/") else "xl/" + target))
    rows: list[list[str]] = []
    for row in tree.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = _column(cell.attrib["r"])
            values.extend([""] * max(0, index + 1 - len(values)))
            values[index] = _value(cell, shared)
        rows.append(values)
    header_index = next((i for i, row in enumerate(rows) if row and row[0] == "material_id"), None)
    if header_index is None: raise ValueError(f"canonical header missing: {wanted_sheet}")
    header = rows[header_index]
    return [{key: row[i] if i < len(row) else "" for i, key in enumerate(header)} for row in rows[header_index + 1:] if any(row)]


def run(workbook: Path, output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for sheet in TABLES:
        rows = read_xlsx_table(workbook, sheet)
        for row_number, row in enumerate(rows, start=4):
            row["raw_source_file"] = workbook.name
            row["raw_sheet"] = sheet
            row["raw_row_number"] = str(row_number)
            row["raw_row_json"] = json.dumps({k: v for k, v in row.items() if not k.startswith("raw_")}, ensure_ascii=False, sort_keys=True)
        filename = sheet.lower() + ".csv"
        with (output_dir / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["material_id"])
            writer.writeheader(); writer.writerows(rows)
        counts[sheet] = len(rows)
    (output_dir / "catalog_cleaning_summary.json").write_text(json.dumps({"source": str(workbook), "tables": counts, "pdf_status": "not_ingested_pending_traceable_extraction"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts
