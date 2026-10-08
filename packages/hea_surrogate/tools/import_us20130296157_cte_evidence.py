#!/usr/bin/env python3
"""Import 0--300 C CTE examples from US20130296157A1 Tables 2--21."""
from __future__ import annotations
import argparse, csv
from pathlib import Path
from bs4 import BeautifulSoup

CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
OXIDES = set(CORE) | {"SrO", "SnO2", "Fe2O3", "ZrO2", "CeO2", "As2O3", "Sb2O3"}


def cells(row: object) -> list[str]:
    return [cell.get_text("", strip=True).replace("−", "-") for cell in row.select(":scope > th, :scope > td")]  # type: ignore[attr-defined]


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--html", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    soup = BeautifulSoup(args.html.read_text(encoding="utf-8"), "html.parser")
    output: list[dict[str, str]] = []
    # Google Patents represents Table 2 through Table 21 as alternating
    # example-header and data tables: indices 2/3, 4/5, ..., 40/41.
    for header_index in range(2, 42, 2):
        header_rows = [cells(row) for row in soup.select("table")[header_index].select("tr")]
        example_row = next(row for row in header_rows if row and row[0] == "Example")
        examples = example_row[1:]
        data_rows = [cells(row) for row in soup.select("table")[header_index + 1].select("tr")]
        fields = {row[1]: row[2:] for row in data_rows if len(row) >= 3 and row[1] in OXIDES | {"CTE (disk)"}}
        cte = fields.get("CTE (disk)")
        if not cte: continue
        for pos, example in enumerate(examples):
            if pos >= len(cte) or cte[pos] in {"", "—", "-"}: continue
            composition = {oxide: fields.get(oxide, ["0"] * len(examples))[pos] or "0" for oxide in OXIDES}
            if any(value in {"—", "-"} for value in composition.values()): continue
            total = sum(float(value) for value in composition.values())
            other = ";".join(f"{oxide}={value} mol%" for oxide, value in composition.items() if oxide not in CORE and float(value))
            record = {"record_id": f"US20130296157-CTE-{example}", "glass_id": f"US20130296157_sample_{example}", "glass_display_name": f"US20130296157A1 实例 {example}", "glass_family": "无碱铝硼硅酸盐玻璃（高热化学稳定专利实例族）", "composition_basis": "mol_percent", "other_oxides_text": other, "composition_total": f"{total:.4f}", "composition_source_batch_or_lot": f"US20130296157A1 Table {2 + (header_index - 2)//2}, example {example}", "process_state": "专利实验玻璃；每个样品的熔融/退火状态按说明和同表热历史字段报告", "model_lane": "glass_thermal", "property_name": "CTE_linear_0_to_300C", "property_value": f"{float(cte[pos]) * 1e-7:.9g}", "property_unit": "1/K", "temperature_min_C": "0", "temperature_max_C": "300", "test_method": "disk CTE method（专利定义；测试标准未逐项列出）", "source_id": "SRC-PATENT-US20130296157", "source_locator": f"US20130296157A1 PDF; Google Patents HTML Table {2 + (header_index - 2)//2}; example {example}; CTE (disk) row", "evidence_level": "B_published_patent_example", "data_license": "public patent publication", "review_notes": "Oxide mol% composition; publication defines CTE over 0–300°C in 10^-7/°C, converted to 1/K. Only samples with explicit CTE included."}
            for oxide in CORE: record[oxide] = composition.get(oxide, "0")
            output.append(record)
    if len(output) < 100: raise SystemExit(f"expected at least 100 CTE examples, got {len(output)}")
    columns = ["record_id", "glass_id", "glass_display_name", "glass_family", "composition_basis", *CORE, "other_oxides_text", "composition_total", "composition_source_batch_or_lot", "process_state", "model_lane", "property_name", "property_value", "property_unit", "temperature_min_C", "temperature_max_C", "test_method", "source_id", "source_locator", "evidence_level", "data_license", "review_notes"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(output)
    print(f"wrote {len(output)} CTE evidence records")


if __name__ == "__main__": main()
