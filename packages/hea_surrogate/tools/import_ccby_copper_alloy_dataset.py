"""Validate and normalize the Gorsse *et al.* openly released copper-alloy dataset.

This importer deliberately does not scrape figures or reconstruct values.  It accepts
only the released CSV, keeps its per-record DOI, and rejects records lacking a usable
composition or traceable source.  The resulting table is an auditable candidate pool;
later model admission additionally applies the hot-end domain and split rules.

The Figshare dataset page identifies the released data as CC0.  The associated
Scientific Data article is CC BY 4.0 and is retained in the citation string for
scientific attribution; it is not the dataset licence.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/processed/copper_hot_end_ccby_gorsse_v1/candidate_records.csv"
ELEMENTS = (
    "Cu", "Al", "Ag", "B", "Be", "Ca", "Co", "Ce", "Cr", "Fe", "Hf", "La",
    "Mg", "Mn", "Mo", "Nb", "Nd", "Ni", "P", "Pb", "Pr", "Si", "Sn", "Ti",
    "V", "Zn", "Zr",
)
PROCESS_COLUMNS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Aging", "Tag (K)", "tag (h)", "Secondary thermo-mechanical process")
TARGET_COLUMNS = ("Hardness (HV)", "Yield strength (MPa)", "Ultimate tensile strength (MPa)", "Electrical conductivity (%IACS)")
# The published file uses ``Alloy class``.  An early local fixture used the
# compact spelling below, so accept it as a backwards-compatible alias but
# normalize it before validating the published schema.
ALLOY_CLASS_COLUMNS = ("Alloy class", "Alloyclass")
REQUIRED_COLUMNS = ("Alloy formula", *ELEMENTS, *PROCESS_COLUMNS, *TARGET_COLUMNS, "DOI")


def _number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value.strip().replace(",", "."))


def _read_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    # The released Figshare CSV is CP1252 and contains en dashes in alloy
    # formulae.  Prefer UTF-8 for new releases, then faithfully fall back to
    # the publisher's legacy encoding rather than silently replacing bytes.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    sample = text[:8192]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    return list(csv.DictReader(text.splitlines(), dialect=dialect))


def normalize(source: Path, output: Path, *, min_cu: float = 80.0) -> dict[str, int | float]:
    rows = _read_rows(source)
    if not rows:
        raise ValueError("source dataset has no records")
    if "Alloy class" not in rows[0] and "Alloyclass" in rows[0]:
        for row in rows:
            row["Alloy class"] = row["Alloyclass"]
    missing = [column for column in (*REQUIRED_COLUMNS, "Alloy class") if column not in rows[0]]
    if missing:
        raise ValueError(f"source dataset missing documented columns: {missing}")

    output.parent.mkdir(parents=True, exist_ok=True)
    kept: list[dict[str, str]] = []
    for row in rows:
        composition = {element: _number(row[element]) or 0.0 for element in ELEMENTS}
        total = sum(composition.values())
        # The published data store nominal wt.% and can round to 99--101; larger
        # departures lose composition identity and must not silently enter a model.
        if not row["DOI"].strip() or composition["Cu"] < min_cu or not 99.0 <= total <= 101.0:
            continue
        normalized = {
            "alloy_formula": row["Alloy formula"].strip(),
            "alloy_class": row["Alloy class"].strip(),
            "source_doi": row["DOI"].strip(),
            "source_dataset": "Gorsse et al., Scientific Data 10, 504 (2023); Figshare dataset CC0",
            **{f"{element}_wt_percent": f"{composition[element]:.8g}" for element in ELEMENTS},
            **{column: row[column].strip() for column in PROCESS_COLUMNS},
            **{column: row[column].strip() for column in TARGET_COLUMNS},
        }
        kept.append(normalized)

    fields = list(kept[0]) if kept else ["alloy_formula", "alloy_class", "source_doi", "source_dataset", *(f"{element}_wt_percent" for element in ELEMENTS), *PROCESS_COLUMNS, *TARGET_COLUMNS]
    with output.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    report = {"source_rows": len(rows), "admitted_high_cu_rows": len(kept), "minimum_cu_wt_percent": min_cu}
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Official released CC0/CC-BY CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-cu", type=float, default=80.0)
    args = parser.parse_args()
    print(json.dumps(normalize(args.input, args.output, min_cu=args.min_cu), ensure_ascii=False))


if __name__ == "__main__":
    main()
