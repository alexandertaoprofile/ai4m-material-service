#!/usr/bin/env python3
"""Create a review queue for CTE records extracted from SciGlass+ raw text.

The raw archive contains AI-assisted structured extraction alongside the exact
paragraph/table payload that supplied it.  It is useful for recovering source
locators and screening which records *might* contain composition basis, test
method and temperature information.  It is not a substitute for checking the
original table or paper, so this tool never marks a row training-admitted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


OXIDES = (
    "SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO",
    "BaO", "ZnO", "TiO2", "ZrO2", "P2O5",
)
MEMBER = "rawdata/result_20241010.json"


def parse_result(value: str) -> dict[str, Any] | None:
    value = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def text_flags(source: str) -> dict[str, str]:
    low = source.casefold()
    # The strings are deliberately conservative.  A hit proves only that the
    # *source payload* has something to inspect, not that the conditions have
    # # been linked correctly by the automated extractor.
    basis = "mol_percent_mentioned" if re.search(r"\bmol\.?\s*%", low) else "wt_percent_mentioned" if re.search(r"\b(?:wt\.?|weight|mass)\s*%", low) else "missing"
    method = "dilatometry_mentioned" if re.search(r"dilatomet|\btma\b|thermomechanical", low) else "missing"
    cte_term = "mentioned" if re.search(r"coefficient of (?:thermal )?expansion|thermal expansion coefficient|\bcte\b", low) else "missing"
    unit = "mentioned" if re.search(r"(?:10\s*(?:\^|×)?\s*[−-]?\s*6|ppm\s*/?\s*(?:k|°c)|(?:10\s*[−-]?\s*6)\s*/\s*(?:k|°c))", source, re.I) else "missing"
    interval = "mentioned" if re.search(r"\b(?:from|between)\s+\d+(?:\.\d+)?\s*(?:°\s*c|º\s*c|k)\s+(?:to|and|[-–])\s+\d+(?:\.\d+)?\s*(?:°\s*c|º\s*c|k)", source, re.I) else "missing"
    return {"composition_basis_evidence": basis, "cte_term_evidence": cte_term, "cte_unit_evidence": unit,
            "test_method_evidence": method, "temperature_interval_evidence": interval}


def source_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    with zipfile.ZipFile(args.archive) as archive:
        payload = archive.read(MEMBER)
    records = json.loads(payload)
    rows: list[dict[str, object]] = []
    parse_failed = 0
    for record in records:
        result = parse_result(str(record.get("result", "")))
        if result is None:
            parse_failed += 1
            continue
        source = source_text(record.get("src"))
        flags = text_flags(source)
        for sample_id, sample in result.items():
            if not isinstance(sample, dict) or sample.get("CTEbelowTg") is None:
                continue
            try:
                cte = float(sample["CTEbelowTg"])
            except (TypeError, ValueError):
                continue
            if not 0 < cte < 30e-6:
                continue
            composition: dict[str, float] = {}
            valid = True
            for oxide in OXIDES:
                value = sample.get(oxide, 0.0)
                try:
                    composition[oxide] = float(value or 0.0)
                except (TypeError, ValueError):
                    valid = False
                    break
            if not valid:
                continue
            total = sum(composition.values())
            if not 90 <= total <= 101:
                continue
            # A single paragraph can contain several samples.  Preserve that
            # fact in the locator instead of inventing a table-row location.
            row = {"record_id": f"SGP-RAW-CTE-{len(rows)+1:04d}", "source_id": "SRC-SCIGLASS-PLUS-2025",
                   "doi": str(record.get("doi", "")).strip(), "raw_member": MEMBER,
                   "raw_para_id": record.get("para_id", ""), "raw_sample_id": sample_id,
                   "source_locator": f"DOI:{str(record.get('doi', '')).strip()}; {MEMBER}; para_id={record.get('para_id', '')}; sample={sample_id}",
                   "source_excerpt_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                   "cte_below_tg_per_K": cte, "cte_below_tg_ppm_per_K": cte * 1e6,
                   "reported_oxide_total": total, **composition, **flags}
            evidence_complete = all(value != "missing" for value in flags.values())
            row["review_status"] = "priority_manual_table_check" if evidence_complete else "manual_source_check_required"
            row["training_admission"] = "review_required"
            row["admission_blockers"] = "original_table_page_or_table_locator;sample_state;verified_composition_basis;verified_cte_temperature_interval;verified_test_method"
            rows.append(row)
    args.output_dir.mkdir(parents=True)
    fields = ["record_id", "source_id", "doi", "raw_member", "raw_para_id", "raw_sample_id", "source_locator", "source_excerpt_sha256",
              "cte_below_tg_per_K", "cte_below_tg_ppm_per_K", "reported_oxide_total", *OXIDES,
              "composition_basis_evidence", "cte_term_evidence", "cte_unit_evidence", "test_method_evidence", "temperature_interval_evidence",
              "review_status", "training_admission", "admission_blockers"]
    with (args.output_dir / "cte_traceability_review_queue.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    summary = {"package": "chip_glass_cte_traceability_queue_v1", "source_archive": str(args.archive),
               "source_member": MEMBER, "source_records": len(records), "unparseable_result_records": parse_failed,
               "cte_records_with_13_oxide_total_90_to_101": len(rows), "doi_groups": len({str(row['doi']).casefold() for row in rows if row['doi']}),
               "priority_manual_table_check": sum(row["review_status"] == "priority_manual_table_check" for row in rows),
               "training_admitted": 0,
               "policy": "Automated raw-text flags are retrieval hints only. No record is admitted until the original source establishes composition basis, CTE interval/method and glass state."}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
