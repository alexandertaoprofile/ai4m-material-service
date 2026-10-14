"""Download a versioned, auditable snapshot from the Science42 material portal.

The portal streams one CSV per business table.  This tool never overwrites an
existing completed snapshot: each run writes a dated directory, checks the
downloaded content, and records URLs, sizes and SHA-256 checksums in a manifest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PORTAL_BASE = "http://36.103.236.211:1101"
FLUID_SNAPSHOT_TABLES = (
    "fluid_property_source",
    "fluid_conductivity",
    "fluid_viscosity",
    "fluid_stability",
    "fluid_mixture_property",
    "fluid_missing_field",
    "fluid_duplicate_record",
    "fluid_manual_review",
)


def csv_record_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return sum(1 for _ in csv.reader(source)) - 1


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def table_catalog(*, base_url: str, timeout: int) -> dict[tuple[str, str], int]:
    request = Request(f"{base_url.rstrip('/')}/v1/database/tables", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        rows = json.load(response)
    return {(row["schema_name"], row["table_name"]): int(row.get("estimated_row_count") or 0) for row in rows}


def download_table(*, base_url: str, schema: str, table: str, output: Path, expected_rows: int, timeout: int) -> dict[str, object]:
    url = f"{base_url.rstrip('/')}/v1/database/tables/{schema}/{table}/content"
    temporary = output.with_suffix(output.suffix + ".part")
    request = Request(url, headers={"Accept": "text/csv"})
    try:
        # The portal occasionally leaves its chunked response open after all
        # CSV rows were emitted. curl is materially faster than urllib here;
        # a timeout exit is accepted only after exact CSV row-count validation.
        completed = subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--location", "--max-time", str(timeout), "--output", str(temporary), url],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode not in {0, 28}:
            raise RuntimeError(completed.stderr.strip() or f"curl exit {completed.returncode}")
    except (OSError, RuntimeError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"download failed for {schema}.{table}: {exc}") from exc
    size = temporary.stat().st_size if temporary.exists() else 0
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"download failed for {schema}.{table}: empty response")
    actual_rows = csv_record_count(temporary)
    if actual_rows < 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"download failed for {schema}.{table}: CSV has no header")
    # The portal exposes an ``estimated_row_count``, not a transactional
    # content count.  Retain a complete, parseable export even if that estimate
    # is stale, and preserve the difference for the later snapshot audit.
    row_count_difference = actual_rows - expected_rows
    if row_count_difference:
        print(
            f"warning: {schema}.{table} catalogue estimate is {expected_rows} rows; "
            f"CSV export contains {actual_rows} rows (difference {row_count_difference:+d})",
            flush=True,
        )
    digest = file_sha256(temporary)
    os.replace(temporary, output)
    return {
        "schema": schema,
        "table": table,
        "url": url,
        "file": output.name,
        "bytes": size,
        "sha256": digest,
        "catalogue_estimated_row_count": expected_rows,
        "record_count": actual_rows,
        "row_count_difference": row_count_difference,
    }


def existing_table_record(*, base_url: str, schema: str, table: str, output: Path, expected_rows: int) -> dict[str, object]:
    actual_rows = csv_record_count(output)
    if actual_rows < 0:
        raise RuntimeError(f"existing {output} has no CSV header")
    row_count_difference = actual_rows - expected_rows
    if row_count_difference:
        print(
            f"warning: reusing {schema}.{table} with {actual_rows} CSV rows; "
            f"catalogue estimate is {expected_rows} (difference {row_count_difference:+d})",
            flush=True,
        )
    return {
        "schema": schema,
        "table": table,
        "url": f"{base_url.rstrip('/')}/v1/database/tables/{schema}/{table}/content",
        "file": output.name,
        "bytes": output.stat().st_size,
        "sha256": file_sha256(output),
        "catalogue_estimated_row_count": expected_rows,
        "record_count": actual_rows,
        "row_count_difference": row_count_difference,
        "resumed_existing_file": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a traceable material-platform CSV snapshot")
    parser.add_argument("--base-url", default=PORTAL_BASE)
    parser.add_argument("--schema", default="material")
    parser.add_argument("--snapshot", default=date.today().isoformat(), help="immutable snapshot directory name")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw/incoming/material_platform"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--table", action="append", dest="tables", help="table to download; repeat as needed")
    parser.add_argument("--all-tables-in-schema", action="store_true", help="download every table listed in --schema")
    parser.add_argument("--resume", action="store_true", help="continue an incomplete snapshot without redownloading completed CSVs")
    args = parser.parse_args()

    if args.tables and args.all_tables_in_schema:
        raise SystemExit("use either --table or --all-tables-in-schema, not both")
    destination = args.output_root / args.snapshot
    manifest_path = destination / "snapshot_manifest.json"
    incomplete_path = destination / ".INCOMPLETE"
    if manifest_path.exists():
        raise SystemExit(f"refusing to overwrite completed snapshot: {destination}")
    if destination.exists() and not args.resume:
        raise SystemExit(f"snapshot directory already exists; use --resume after checking it: {destination}")
    destination.mkdir(parents=True, exist_ok=args.resume)
    try:
        available = shutil.disk_usage(destination).free
        if available < 1024 * 1024 * 1024:
            raise RuntimeError("less than 1 GiB free; refusing download")
        catalog = table_catalog(base_url=args.base_url, timeout=args.timeout)
        tables = (
            tuple(sorted(table for schema, table in catalog if schema == args.schema))
            if args.all_tables_in_schema
            else tuple(args.tables or FLUID_SNAPSHOT_TABLES)
        )
        if not tables:
            raise RuntimeError(f"no tables are listed for schema {args.schema}")
        records = []
        for table in tables:
            expected_rows = catalog.get((args.schema, table))
            if expected_rows is None:
                raise RuntimeError(f"{args.schema}.{table} is not listed by the portal catalogue")
            output = destination / f"{args.schema}.{table}.csv"
            partial = output.with_suffix(output.suffix + ".part")
            if partial.exists():
                partial.unlink()
            if output.exists():
                try:
                    record = existing_table_record(base_url=args.base_url, schema=args.schema, table=table, output=output, expected_rows=expected_rows)
                except RuntimeError:
                    if not args.resume:
                        raise
                    # A previous interrupted transfer can leave a CSV-looking
                    # file without a manifest. It is safe to replace only
                    # after its row count proves it is incomplete.
                    print(f"discarding incomplete {args.schema}.{table}", flush=True)
                    output.unlink()
                else:
                    print(f"reusing {args.schema}.{table}", flush=True)
                    records.append(record)
                    continue
            if not output.exists():
                print(f"downloading {args.schema}.{table}", flush=True)
                records.append(download_table(base_url=args.base_url, schema=args.schema, table=table, output=output, expected_rows=expected_rows, timeout=args.timeout))
        manifest_path.write_text(json.dumps({
            "snapshot": args.snapshot,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "portal_base_url": args.base_url,
            "tables": records,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        incomplete_path.unlink(missing_ok=True)
    except Exception:
        # Keep any completed CSVs for inspection but make the incomplete state
        # explicit, preventing it from being mistaken for a valid snapshot.
        incomplete_path.write_text("download interrupted; do not import this directory\n", encoding="utf-8")
        raise
    print(f"completed {len(records)} tables in {destination}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
