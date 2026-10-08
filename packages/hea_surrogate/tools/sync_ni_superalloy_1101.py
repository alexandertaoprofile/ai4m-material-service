#!/usr/bin/env python3
"""Create an immutable local raw snapshot of Ni-alloy tables exposed by 1101.

This script deliberately does *not* transform or merge records.  The resulting
files are an acquisition layer for later reviewed parsing into a trainable
Ni-superalloy dataset.  Welding tables are listed in the manifest but excluded
from the base-metal download set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


NI_PATTERN = re.compile(
    r"inconel|haynes|hastelloy|nimonic|waspaloy|rene|udimet|mar[ -]?m|superalloy|nickel",
    re.IGNORECASE,
)


def get_json(url: str, timeout: int) -> object:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bytes(url: str, timeout: int) -> bytes:
    request = Request(url, headers={"Accept": "text/csv,application/octet-stream"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def count_csv_rows(payload: bytes) -> int | None:
    try:
        text = payload.decode("utf-8-sig")
        return max(sum(1 for _ in csv.reader(text.splitlines())) - 1, 0)
    except (UnicodeDecodeError, csv.Error):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://36.103.236.211:1101")
    parser.add_argument(
        "--output-dir",
        default="data/raw/ni_superalloy_1101_20260831",
        type=Path,
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    table_url = f"{base_url}/v1/database/tables"
    tables = get_json(table_url, args.timeout)
    if not isinstance(tables, list):
        raise RuntimeError("1101 table endpoint did not return a list")

    ni_tables = [
        table
        for table in tables
        if table.get("schema_name") == "metal_alloy_other"
        and NI_PATTERN.search(" ".join(str(value) for value in table.values()))
    ]
    welding_tables = [
        table
        for table in tables
        if table.get("schema_name") == "welding_joining"
        and NI_PATTERN.search(" ".join(str(value) for value in table.values()))
    ]
    support_tables = [
        table
        for table in tables
        if (table.get("schema_name"), table.get("table_name"))
        in {("material", "material"), ("material", "composition_component")}
    ]
    selected = ni_tables + support_tables

    manifest = {
        "dataset": "ni_superalloy_raw_v1",
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "source": {"base_url": base_url, "metadata_url": table_url},
        "selection": {
            "base_metal_ni_tables": len(ni_tables),
            "support_tables": [f"{x['schema_name']}.{x['table_name']}" for x in support_tables],
            "excluded_welding_tables": [f"{x['schema_name']}.{x['table_name']}" for x in welding_tables],
            "rule": "metal_alloy_other tables matching named Ni-alloy families; welding is retained only as an exclusion ledger",
        },
        "tables": [],
    }
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    output = args.output_dir
    tables_dir = output / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    (output / "remote_table_metadata.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def download(table: dict) -> dict:
        schema, name = table["schema_name"], table["table_name"]
        url = f"{base_url}/v1/database/tables/{quote(schema, safe='')}/{quote(name, safe='')}/content"
        payload = get_bytes(url, args.timeout)
        path = tables_dir / f"{schema}__{name}.csv"
        path.write_bytes(payload)
        return {
            "qualified_name": f"{schema}.{name}",
            "url": url,
            "local_path": str(path),
            "estimated_row_count": table.get("estimated_row_count"),
            "downloaded_row_count": count_csv_rows(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    failures = []
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        futures = {executor.submit(download, table): table for table in selected}
        for future in as_completed(futures):
            table = futures[future]
            try:
                record = future.result()
                manifest["tables"].append(record)
                print(f"OK {record['qualified_name']} rows={record['downloaded_row_count']}")
            except Exception as error:  # keep partial acquisition auditable
                failure = {
                    "qualified_name": f"{table['schema_name']}.{table['table_name']}",
                    "error": repr(error),
                }
                failures.append(failure)
                print(f"FAILED {failure['qualified_name']}: {failure['error']}", file=sys.stderr)
    manifest["tables"].sort(key=lambda row: row["qualified_name"])
    manifest["failures"] = failures
    (output / "acquisition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Snapshot: {output}; downloaded={len(manifest['tables'])}; failed={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
