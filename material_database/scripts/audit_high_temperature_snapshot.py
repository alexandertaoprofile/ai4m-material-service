"""Classify high-temperature table exports before any material import.

The platform exports one table per extracted document table.  A table is only
safe for automated material evidence import when its rows carry an explicit
material identity; document names alone are not a stable grade/state key.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


LINEAGE_PREFIX = "lineage_"
IDENTITY_FIELDS = {"alloy", "material", "grade", "alloy_name", "material_name"}
TEMPERATURE_MARKERS = ("temperature", "temp")
NON_PROPERTY_FIELDS = IDENTITY_FIELDS | {"form", "condition", "material_condition", "heat_treatment", "processing", "specimen"}


def classify(header: list[str]) -> dict[str, object]:
    data_fields = [field for field in header if not field.startswith(LINEAGE_PREFIX)]
    identity = [field for field in data_fields if field in IDENTITY_FIELDS]
    temperature = [field for field in data_fields if any(marker in field for marker in TEMPERATURE_MARKERS)]
    properties = [
        field for field in data_fields
        if field not in NON_PROPERTY_FIELDS
        and field not in temperature
        and field not in {""}
    ]
    return {
        "identity_fields": identity,
        "temperature_fields": temperature,
        "candidate_property_fields": properties,
        "automated_import_eligible": bool(identity and properties),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit high-temperature material table exports")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = args.input / "snapshot_manifest.json"
    if not manifest.is_file() or (args.input / ".INCOMPLETE").exists():
        raise SystemExit("input must be a completed snapshot")
    tables = []
    for path in sorted(args.input.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            header = next(reader, [])
            row_count = sum(1 for _ in reader)
        assessment = classify(header)
        tables.append({"file": path.name, "row_count": row_count, **assessment})
    counts = Counter("eligible" if row["automated_import_eligible"] else "mapping_required" for row in tables)
    report = {
        "snapshot": args.input.name,
        "table_count": len(tables),
        "row_count": sum(row["row_count"] for row in tables),
        "summary": dict(counts),
        "rule": "Only tables with explicit material identity and candidate property fields may be automatically imported.",
        "tables": tables,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": dict(counts), "rows": report["row_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
