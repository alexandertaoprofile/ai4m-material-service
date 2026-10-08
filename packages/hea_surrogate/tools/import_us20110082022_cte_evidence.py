#!/usr/bin/env python3
"""Import exact 0--300 C CTE examples from US20110082022A1 Table 1.

Table 1 is split across five HTML table elements.  The source itself defines
the oxide mol% basis, CTE interval and ASTM fibre-elongation method.
"""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from bs4 import BeautifulSoup

CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
SOURCE = set(CORE) | {"SrO", "SnO2"}


def cells(row: object) -> list[str]:
    return [cell.get_text("", strip=True).replace("−", "-") for cell in row.select(":scope > th, :scope > td")]  # type: ignore[attr-defined]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    soup = BeautifulSoup(args.html.read_text(encoding="utf-8"), "html.parser")
    output: list[dict[str, str]] = []
    for index in range(2, 7):
        rows = [cells(row) for row in soup.select("table")[index].select("tr")]
        starts = [i for i, row in enumerate(rows) if "Composition (mol %)" in row]
        for start in starts:
            header = rows[start]
            first = header.index("Composition (mol %)")
            samples = header[first + 1:]
            if not samples or not all(sample.isdigit() for sample in samples):
                continue
            count = len(samples)
            end = next((i for i in starts if i > start), len(rows))
            block = rows[start:end]
            fields = {row[first]: row[first + 1:] for row in block if len(row) >= first + count + 1 and row[first] in SOURCE | {"CTE"}}
            cte = fields.get("CTE")
            if not cte:
                continue
            for position, sample in enumerate(samples):
                if position >= len(cte) or cte[position] in {"", "—", "-"}:
                    continue
                composition = {oxide: fields.get(oxide, ["0"] * count)[position] or "0" for oxide in SOURCE}
                if any(value in {"—", "-"} for value in composition.values()):
                    continue
                total = sum(float(value) for value in composition.values())
                other = ";".join(f"{oxide}={value} mol%" for oxide, value in composition.items() if oxide not in CORE and float(value))
                record = {"record_id": f"US20110082022-CTE-{sample}", "glass_id": f"US20110082022_sample_{sample}", "glass_display_name": f"US20110082022A1 实例 {sample}", "glass_family": "无碱铝硼硅酸盐显示玻璃（专利实例族）", "composition_basis": "mol_percent", "other_oxides_text": other, "composition_total": f"{total:.4f}", "composition_source_batch_or_lot": f"US20110082022A1 Table 1, example {sample}", "process_state": "专利实验玻璃；熔制、退火和连续熔炉样品状态按 Table 1 前的分例说明；CTE 按纤维伸长法报告", "model_lane": "glass_thermal", "property_name": "CTE_linear_0_to_300C", "property_value": f"{float(cte[position]) * 1e-7:.9g}", "property_unit": "1/K", "temperature_min_C": "0", "temperature_max_C": "300", "test_method": "ASTM E228-85 fibre elongation（专利定义）", "source_id": "SRC-PATENT-US20110082022", "source_locator": f"US20110082022A1 PDF; Google Patents HTML Table 1 segment {index - 1}; example {sample}; CTE row", "evidence_level": "B_published_patent_example", "data_license": "public patent publication", "review_notes": "Oxide mol% composition; CTE 0–300°C in 10^-7/°C converted to 1/K. Samples missing CTE remain excluded."}
                for oxide in CORE:
                    record[oxide] = composition.get(oxide, "0")
                output.append(record)
    if len(output) != 43:
        raise SystemExit(f"expected 43 CTE examples, got {len(output)}")
    columns = ["record_id", "glass_id", "glass_display_name", "glass_family", "composition_basis", *CORE, "other_oxides_text", "composition_total", "composition_source_batch_or_lot", "process_state", "model_lane", "property_name", "property_value", "property_unit", "temperature_min_C", "temperature_max_C", "test_method", "source_id", "source_locator", "evidence_level", "data_license", "review_notes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(output)
    print(f"wrote {len(output)} CTE evidence records")


if __name__ == "__main__":
    main()
