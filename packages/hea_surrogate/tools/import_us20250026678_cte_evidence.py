#!/usr/bin/env python3
"""Import the traceable 0--300 C CTE examples in US20250026678A1.

This importer is intentionally source-specific.  It reads the retained Google
Patents HTML snapshot (which preserves the machine-readable versions of
Tables 2--12) and emits one thermal-property record per example with a CTE.
The accompanying PDF is retained as the primary publication copy.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from bs4 import BeautifulSoup


CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
ALL_OXIDE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def cells(row: object) -> list[str]:
    return [cell.get_text("", strip=True).replace("−", "-") for cell in row.select(":scope > th, :scope > td")]  # type: ignore[attr-defined]


def value_or_blank(value: str) -> str:
    return "" if value in {"", "—", "-"} else value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    soup = BeautifulSoup(args.html.read_text(encoding="utf-8"), "html.parser")
    output: list[dict[str, str]] = []
    # HTML table indices 3,5,...,23 are publication Tables 2--12.  Each
    # contains composition and matching physical-property columns.
    for index in range(3, 24, 2):
        table_number = 2 + (index - 3) // 2
        parsed = [cells(row) for row in soup.select("table")[index].select("tr")]
        physical_index = next(i for i, row in enumerate(parsed) if row and row[0] in {"Physical Properties", "Physical"})
        sample_ids = parsed[physical_index][1:]
        count = len(sample_ids)
        composition: dict[str, list[str]] = {}
        for row in parsed[:physical_index]:
            if len(row) == count + 1 and ALL_OXIDE.fullmatch(row[0] or ""):
                composition[row[0]] = row[1:]
        cte_row = next((row for row in parsed[physical_index + 1:] if row and row[0].startswith("CTE 0-300")), None)
        if cte_row is None or len(cte_row) != count + 1:
            continue
        for position, sample in enumerate(sample_ids):
            cte_text = value_or_blank(cte_row[position + 1])
            if not cte_text:
                continue
            cte = float(cte_text) * 1e-7  # stated document convention: 10^-7 / C
            values = {oxide: value_or_blank(series[position]) for oxide, series in composition.items()}
            if any(value == "" for value in values.values()):
                continue
            unknown = [(oxide, value) for oxide, value in values.items() if oxide not in CORE]
            uncommon = ";".join(f"{oxide}={value} mol%" for oxide, value in unknown)
            total = sum(float(value) for value in values.values())
            record = {"record_id": f"US20250026678-CTE-{sample}", "glass_id": f"US20250026678_sample_{sample}",
                      "glass_display_name": f"US20250026678A1 实例 {sample}", "glass_family": "无碱铝硼硅酸盐玻璃（专利实例族）",
                      "composition_basis": "mol_percent", "other_oxides_text": uncommon, "composition_total": f"{total:.4f}",
                      "composition_source_batch_or_lot": f"US20250026678A1 Table {table_number}, example {sample}",
                      "process_state": "专利实验玻璃；CTE 按文献规定的 ASTM E228 条件报告，个别退火历程未逐样品公开",
                      "model_lane": "glass_thermal", "property_name": "CTE_linear_0_to_300C", "property_value": f"{cte:.9g}", "property_unit": "1/K",
                      "temperature_min_C": "0", "temperature_max_C": "300", "test_method": "ASTM E228（专利定义）",
                      "source_id": "SRC-PATENT-US20250026678", "source_locator": f"US20250026678A1 PDF; Google Patents HTML Table {table_number}; example {sample}; CTE 0-300 C row",
                      "evidence_level": "B_published_patent_example", "data_license": "public patent publication", "review_notes": "Composition is mol% on oxide basis. CTE table convention is 10^-7 / C; converted to 1/K."}
            for oxide in CORE:
                record[oxide] = values.get(oxide, "0")
            output.append(record)
    if len(output) < 50:
        raise SystemExit(f"expected at least 50 CTE examples, got {len(output)}")
    fields = ["record_id", "glass_id", "glass_display_name", "glass_family", "composition_basis", *CORE, "other_oxides_text", "composition_total", "composition_source_batch_or_lot", "process_state", "model_lane", "property_name", "property_value", "property_unit", "temperature_min_C", "temperature_max_C", "test_method", "source_id", "source_locator", "evidence_level", "data_license", "review_notes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(output)
    print(f"wrote {len(output)} CTE evidence records to {args.output}")


if __name__ == "__main__":
    main()
