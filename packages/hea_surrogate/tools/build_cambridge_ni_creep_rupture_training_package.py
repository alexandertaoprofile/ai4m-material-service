#!/usr/bin/env python3
"""Build a traceable Cambridge Ni-superalloy rupture-life training package.

Only whole-alloy cast/DS/single-crystal records with temperature, stress and
rupture-life labels are admitted.  The source workbook stores ten fields per
creep slot; rupture life is the sixth field, not the third one.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = {"cc": "cast", "ds": "directionally_solidified", "sc": "single_crystal"}


def number(value: str) -> float | None:
    try:
        result = float(str(value).strip())
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def heat_treatment(row: list[str]) -> str:
    terms: list[str] = []
    for family, start, stages in (("solution", 56, 3), ("precipitation", 65, 4)):
        for stage in range(stages):
            for suffix, value in zip(("temp_C", "time_h", "heating_C_per_h"), row[start + stage * 3 : start + stage * 3 + 3]):
                parsed = number(value)
                if parsed not in (None, 0):
                    terms.append(f"{family}_stage_{stage + 1}_{suffix}={parsed}")
    return "; ".join(terms)


def split_for(tag: str) -> str:
    bucket = int(hashlib.sha256(tag.encode("utf8")).hexdigest(), 16) % 10
    return "test" if bucket == 0 else "validation" if bucket == 1 else "train"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(args.archive) as archive:
        member = next(name for name in archive.namelist() if name.endswith("alloy_spreadsheet_v3.1.csv"))
        with archive.open(member) as source:
            rows = list(csv.reader(line.decode("utf-8-sig") for line in source))[4:]

    admitted: list[dict] = []
    review: list[dict] = []
    for source_row, row in enumerate(rows, start=5):
        if len(row) < 394:
            continue
        # Rows naming a phase or a parent alloy are not whole-alloy labels.
        if row[51].strip() or row[50].strip():
            continue
        route = row[53].strip().casefold()
        if route not in ROUTES:
            continue
        composition = {f"{element}_wt_percent": number(row[77 + index]) for index, element in enumerate(ELEMENTS)}
        composition_total = sum(value for value in composition.values() if value is not None)
        common = {
            "source_archive": str(args.archive), "source_member": member, "source_row": source_row,
            "tag": row[0].strip(), "alloy_name": row[1].strip(),
            "reference_1_name": row[2].strip(), "reference_1_doi": row[3].strip(), "reference_1_link": row[5].strip(),
            "casting_code": route, "manufacturing_route": ROUTES[route],
            "casting_gradient_K_per_mm": number(row[54]), "heat_treatment": heat_treatment(row),
            **composition,
        }
        for slot in range(14):
            offset = 254 + slot * 10
            temperature_C = number(row[offset])
            stress_MPa = number(row[offset + 1])
            time_for_1pct_creep_h = number(row[offset + 4])
            rupture_life_h = number(row[offset + 5])
            present = any(value is not None for value in (temperature_C, stress_MPa, time_for_1pct_creep_h, rupture_life_h))
            if not present:
                continue
            record = {
                **common, "slot": slot + 1, "temperature_C": temperature_C,
                "temperature_K": temperature_C + 273.15 if temperature_C is not None else None,
                "stress_MPa": stress_MPa, "time_for_1pct_creep_h": time_for_1pct_creep_h,
                "rupture_life_h": rupture_life_h, "composition_sum_source_fraction": composition_total,
            }
            complete = all(value is not None and value > 0 for value in (temperature_C, stress_MPa, rupture_life_h))
            composition_complete = .97 <= composition_total <= 1.03
            if complete and composition_complete:
                admitted.append({**record, "log10_rupture_life_h": math.log10(rupture_life_h), "split": split_for(record["tag"]), "admission": "admitted_whole_alloy_rupture_life"})
            else:
                blockers = []
                if not complete: blockers.append("missing_or_nonpositive_temperature_stress_or_rupture_life")
                if not composition_complete: blockers.append("composition_sum_not_near_one_source_fraction")
                review.append({**record, "admission": "review_only", "blocker": ";".join(blockers)})

    if not admitted:
        raise SystemExit("no complete whole-alloy rupture-life records")
    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir / "rupture_life_admitted.csv", admitted)
    write_csv(args.output_dir / "rupture_life_review_queue.csv", review)
    tags = sorted({record["tag"] for record in admitted})
    write_csv(args.output_dir / "tag_disjoint_split_v1.csv", [{"tag": tag, "split": split_for(tag), "rule": "SHA256(tag) modulo 10; test=0 validation=1"} for tag in tags])
    summary = {
        "package": "cambridge_ni_superalloy_creep_rupture_v1",
        "source": "Cambridge Superalloy Database v3.1, DOI 10.17863/CAM.92063",
        "archive_sha256": digest,
        "target": "log10 rupture life in hours",
        "admitted_records": len(admitted), "admitted_tags": len(tags),
        "route_counts": dict(Counter(record["casting_code"] for record in admitted)),
        "split_counts": dict(Counter(record["split"] for record in admitted)),
        "review_only_records": len(review),
        "admission_rule": "whole alloy; cc/ds/sc only; complete positive temperature, stress and rupture life; source wt.% fractions sum to 0.97..1.03",
        "boundary": "A rupture-life label is time-dependent and does not enter the UTS/proof-strength model as a feature.",
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    (args.output_dir / "README.md").write_text(
        "# 镍基高温合金蠕变断裂寿命训练包\n\n"
        "目标是 `log10(rupture_life_h)`。输入含 wt.% 成分、制造路线、热处理、温度和施加载荷。"
        "仅保留整体合金、正值温度/应力/断裂寿命齐全的记录；按 Tag 分组切分。\n",
        encoding="utf8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
