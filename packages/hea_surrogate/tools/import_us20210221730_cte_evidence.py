#!/usr/bin/env python3
"""Import Table 2's exact-composition 25--300 C CTE examples from US20210221730A1."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from bs4 import BeautifulSoup

CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
SOURCE_OXIDES = set(CORE) | {"SrO", "SnO2", "Fe2O3"}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--html", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    if args.output.exists(): raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    soup = BeautifulSoup(args.html.read_text(encoding="utf-8"), "html.parser")
    rows = [[cell.get_text("", strip=True).replace("−", "-") for cell in row.select(":scope > th, :scope > td")] for row in soup.select("table")[1].select("tr")]
    output: list[dict[str, str]] = []
    for start, header in enumerate(rows):
        if not header or header[0] != "Component (mol %)": continue
        samples = header[1:]; count = len(samples)
        cte_at = next(i for i in range(start + 1, len(rows)) if rows[i] and rows[i][0].startswith("CTE ("))
        composition: dict[str, list[str]] = {}
        for row in rows[start + 1:cte_at]:
            if len(row) == count + 1 and row[0] in SOURCE_OXIDES:
                composition[row[0]] = row[1:]
        for position, sample in enumerate(samples):
            cte_text = rows[cte_at][position + 1]
            if not cte_text: continue
            values = {oxide: series[position] for oxide, series in composition.items()}
            if not values or any(value in {"", "—"} for value in values.values()): continue
            total = sum(float(value) for value in values.values())
            other = ";".join(f"{oxide}={value} mol%" for oxide, value in values.items() if oxide not in CORE)
            record = {"record_id": f"US20210221730-CTE-{sample}", "glass_id": f"US20210221730_example_{sample}",
                      "glass_display_name": f"US20210221730A1 实例 {sample}", "glass_family": "铝硼硅酸盐玻璃（专利实例族）", "composition_basis": "mol_percent",
                      "other_oxides_text": other, "composition_total": f"{total:.4f}", "composition_source_batch_or_lot": f"US20210221730A1 Table 2, example {sample}",
                      "process_state": "专利实验玻璃；CTE 按 ASTM E228-85 报告，个别退火历程未逐样品公开", "model_lane": "glass_thermal",
                      "property_name": "CTE_linear_25_to_300C", "property_value": f"{float(cte_text) * 1e-7:.9g}", "property_unit": "1/K", "temperature_min_C": "25", "temperature_max_C": "300",
                      "test_method": "ASTM E228-85（专利定义）", "source_id": "SRC-PATENT-US20210221730",
                      "source_locator": f"US20210221730A1 PDF; Google Patents HTML Table 2; example {sample}; CTE row", "evidence_level": "B_published_patent_example", "data_license": "public patent publication",
                      "review_notes": "Composition is mol% on oxide basis. CTE table convention is 10^-7/°C; converted to 1/K."}
            for oxide in CORE: record[oxide] = values.get(oxide, "0")
            output.append(record)
    if len(output) < 50: raise SystemExit(f"expected at least 50 CTE examples, got {len(output)}")
    fields = ["record_id","glass_id","glass_display_name","glass_family","composition_basis",*CORE,"other_oxides_text","composition_total","composition_source_batch_or_lot","process_state","model_lane","property_name","property_value","property_unit","temperature_min_C","temperature_max_C","test_method","source_id","source_locator","evidence_level","data_license","review_notes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(output)
    print(f"wrote {len(output)} CTE evidence records to {args.output}")

if __name__ == "__main__": main()
