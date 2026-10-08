#!/usr/bin/env python3
"""Build a coarse experimental phase-calibration candidate set for CALPHAD.

This deliberately does *not* turn experimental phase labels into Gibbs-energy
labels or phase-fraction labels.  It selects records that can be compared only
at the coarse phase-class level inside the documented element domain of
``mpea-05.tdb``: Al, Co, Cr, Fe, Mn, Ni, V, and C.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ALLOWED_ELEMENTS = {"Al", "Co", "Cr", "Fe", "Mn", "Ni", "V", "C"}
ELEMENT_COLUMNS = {
    "Al", "Co", "Cr", "Fe", "Ni", "Cu", "Mn", "Ti", "V", "Nb", "Mo",
    "Zr", "Hf", "Ta", "W", "C", "Mg", "Zn", "Si", "Re", "N", "Sc", "Li",
    "Sn", "Be",
}
SOURCE_ID = "machaka_hea_phases_2021_mmc1"
SOURCE_URL = "https://ars.els-cdn.com/content/image/1-s2.0-S2352340921006302-mmc1.csv"
ARTICLE_DOI = "10.1016/j.dib.2021.107346"


def number(value: str | None) -> float:
    try:
        return float((value or "").strip())
    except ValueError:
        return 0.0


def numeric_temperature(value: str | None) -> float | None:
    text = (value or "").strip()
    return float(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else None


def in_tdb_domain(row: dict[str, str]) -> bool:
    if any(number(row.get(element)) != 0 for element in ELEMENT_COLUMNS - ALLOWED_ELEMENTS):
        return False
    return sum(number(row.get(element)) for element in ALLOWED_ELEMENTS) > 0


def composition_formula(row: dict[str, str]) -> str:
    pieces = []
    for element in ("Al", "Co", "Cr", "Fe", "Mn", "Ni", "V", "C"):
        amount = number(row.get(element))
        if amount:
            pieces.append(f"{element}{amount:.8g}")
    return " ".join(pieces)


def composition_at_pct(row: dict[str, str]) -> str:
    """CALPHAD batch-tool input, explicitly retained as atomic percent."""
    return ";".join(
        f"{element}={number(row.get(element)) * 100:.8g}"
        for element in ("Al", "Co", "Cr", "Fe", "Mn", "Ni", "V", "C")
        if number(row.get(element))
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--min-c", type=float, default=700.0)
    parser.add_argument("--max-c", type=float, default=900.0)
    args = parser.parse_args()

    raw_bytes = args.input.read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    with args.input.open("r", encoding="latin-1", newline="") as handle:
        source_rows = list(csv.DictReader(handle))

    selected: list[dict[str, str]] = []
    for row in source_rows:
        temperature_c = numeric_temperature(row.get("Annealing_Temp"))
        if not in_tdb_domain(row) or temperature_c is None:
            continue
        if not args.min_c <= temperature_c <= args.max_c:
            continue
        # These four fields are the minimum usable provenance/condition set.
        if not all((row.get(field) or "").strip() for field in (
            "Annealing_Time_(min)", "Phases", "References",
        )):
            continue
        enriched = dict(row)
        enriched.update({
            "source_dataset": SOURCE_ID,
            "source_article_doi": ARTICLE_DOI,
            "source_record_id": row.get("Alloy ID", "").strip(),
            "composition_at_fraction": composition_formula(row),
            "composition_at_pct": composition_at_pct(row),
            "annealing_temperature_c": f"{temperature_c:g}",
            # Compatibility fields for the isolated CALPHAD batch runner.
            "record_id": f"machaka_2021_{row.get('Alloy ID', '').strip().replace(' ', '_')}",
            "anneal_temperature_C": f"{temperature_c:g}",
            "observed_phase_coarse": row.get("Phases", "").strip(),
            "calphad_comparison_level": "coarse_phase_presence_only",
            "not_valid_for": "Gibbs-energy loss; chemical-potential residual; phase-fraction MAE",
            "selection_note": "TDB element domain + 700-900 C + annealing time + coarse phase label + DOI",
        })
        selected.append(enriched)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(source_rows[0].keys()) + [
        "source_dataset", "source_article_doi", "source_record_id",
        "composition_at_fraction", "composition_at_pct", "annealing_temperature_c",
        "record_id", "anneal_temperature_C", "observed_phase_coarse",
        "calphad_comparison_level", "not_valid_for", "selection_note",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)

    manifest = {
        "source_dataset": SOURCE_ID,
        "source_url": SOURCE_URL,
        "source_article_doi": ARTICLE_DOI,
        "raw_file": str(args.input),
        "raw_sha256": raw_hash,
        "raw_rows": len(source_rows),
        "selected_rows": len(selected),
        "selection": {
            "element_domain": sorted(ALLOWED_ELEMENTS),
            "annealing_temperature_c_inclusive": [args.min_c, args.max_c],
            "required_source_fields": ["Annealing_Temp", "Annealing_Time_(min)", "Phases", "References"],
        },
        "phase_label_limit": "Four coarse labels only; no individual CALPHAD phase identity or fraction.",
        "phase_counts": dict(Counter(row["observed_phase_coarse"] for row in selected)),
        "unique_source_dois": len({row["References"].strip() for row in selected}),
        "unique_alloy_strings": len({row["Alloy "].strip() for row in selected}),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
