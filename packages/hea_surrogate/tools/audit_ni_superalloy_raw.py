#!/usr/bin/env python3
"""Produce a strict, table-level admission queue for Ni-superalloy training.

This is intentionally an audit, not a permissive importer.  It inventories the
legacy 1101 high-temperature snapshot together with the later remote supplement
and routes tables to: exclude (welding/mixed material), review (identity or
condition incomplete), or parse_candidate.  No numeric property becomes a
training label at this stage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


NI_MARKERS = re.compile(r"inconel|haynes|hastelloy|nimonic|waspaloy|rene|udimet|mar[ _-]?m|hr[_ -]?\d", re.I)
WELD_MARKERS = re.compile(r"weld|welding|filler|electrode|braz", re.I)
IDENTITY_FIELDS = {"alloy", "material", "grade", "alloy_name", "material_name"}
CATEGORY_MARKERS = (
    "chemical_composition", "tensile", "creep", "rupture", "fatigue", "thermal",
    "elastic", "mechanical", "oxidation", "corrosion", "impact", "phase", "processing",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(name: str) -> str:
    lowered = name.casefold()
    return next((item for item in CATEGORY_MARKERS if item in lowered), "unclassified")


def inspect_csv(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames or []
        preview = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(preview) < 12:
                preview.append(" ".join(str(value) for value in row.values() if value))
    blob = " ".join([path.name, *headers, *preview])
    matched_identity = sorted(set(headers) & IDENTITY_FIELDS)
    return {
        "row_count": row_count,
        "headers": headers,
        "identity_fields": matched_identity,
        "contains_ni_marker": bool(NI_MARKERS.search(blob)),
        "contains_weld_marker": bool(WELD_MARKERS.search(blob)),
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--supplement-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    source_sets = [
        ("1101_high_temperature_snapshot", args.legacy_root),
        ("1101_remote_ni_supplement", args.supplement_root / "tables"),
    ]
    records: list[dict[str, object]] = []
    for source_name, root in source_sets:
        if not root.is_dir():
            raise SystemExit(f"missing source directory: {root}")
        for path in sorted(root.glob("*.csv")):
            info = inspect_csv(path)
            if not info["contains_ni_marker"]:
                continue
            source_text = path.name.casefold()
            if info["contains_weld_marker"]:
                disposition, rationale = "exclude_welding_or_join", "weld/filler/electrode marker found; keep outside base-metal labels"
            elif info["identity_fields"]:
                disposition, rationale = "parse_candidate", "explicit row-level identity field present; still requires unit/condition validation"
            elif NI_MARKERS.search(source_text):
                disposition, rationale = "review_identity_mapping", "Ni-alloy source name present but no explicit row-level identity field"
            else:
                disposition, rationale = "review_identity_mapping", "identity cannot be established from a row-level field"
            records.append({
                "source_set": source_name,
                "local_path": str(path),
                "sha256": sha256(path),
                "category": category(path.name),
                "row_count": info["row_count"],
                "column_count": len(info["headers"]),
                "identity_fields": ";".join(info["identity_fields"]),
                "contains_weld_marker": info["contains_weld_marker"],
                "disposition": disposition,
                "rationale": rationale,
                "columns": ";".join(info["headers"]),
            })

    args.output_dir.mkdir(parents=True)
    fields = [
        "source_set", "local_path", "sha256", "category", "row_count", "column_count",
        "identity_fields", "contains_weld_marker", "disposition", "rationale", "columns",
    ]
    write_csv(args.output_dir / "ni_superalloy_table_inventory.csv", fields, records)
    queue = [record for record in records if record["disposition"] != "exclude_welding_or_join"]
    write_csv(args.output_dir / "ni_superalloy_review_queue.csv", fields, queue)
    counts = Counter(record["disposition"] for record in records)
    categories = Counter(record["category"] for record in records)
    report = {
        "dataset": "ni_superalloy_raw_audit_v1",
        "sources": {name: str(root) for name, root in source_sets},
        "tables_with_ni_markers": len(records),
        "rows_in_those_tables": sum(int(record["row_count"]) for record in records),
        "dispositions": dict(sorted(counts.items())),
        "categories": dict(sorted(categories.items())),
        "rule": "Only parse_candidate tables may proceed to schema parsing; each resulting row still needs explicit identity, units, base-metal status, condition, and source locator before becoming a training label.",
    }
    (args.output_dir / "audit_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
