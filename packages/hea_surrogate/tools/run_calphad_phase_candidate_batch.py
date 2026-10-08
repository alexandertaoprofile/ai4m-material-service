"""Batch-recompute CALPHAD labels for the audited HEA phase candidates.

Each row is solved in a fresh Python subprocess. This avoids reusing solver state
across complex multicomponent equilibria and records any failure explicitly.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_INPUT = Path("reports/calphad/hea_700_900c_phase_candidates_20260826.csv")
DEFAULT_TDB = Path("/tmp/cantor_hea_calphad_2023_extract/mpea-05_pycalphad_adapted.tdb")
DEFAULT_OUTPUT = Path("reports/calphad/hea_700_900c_calphad_batch_20260826.csv")


def parse_composition(value: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in value.split(";"):
        element, fraction = item.split("=", 1)
        result[element.strip().upper()] = float(fraction.strip()) / 100.0
    if abs(sum(result.values()) - 1.0) > 2e-3:
        raise ValueError(f"atomic fractions do not sum to one: {value}")
    return result


def solve_one(record: dict[str, str], tdb: Path) -> dict[str, object]:
    import warnings

    warnings.filterwarnings("ignore", message="The order-disorder model.*")
    from pycalphad import Database, equilibrium, variables as v

    composition = parse_composition(record["composition_at_pct"])
    dbf = Database(str(tdb))
    components = sorted(composition) + ["VA"]
    independent = list(sorted(composition))[:-1]
    conditions = {v.X(element): composition[element] for element in independent}
    conditions.update({v.T: float(record["anneal_temperature_C"]) + 273.15, v.P: 101325, v.N: 1})
    result = equilibrium(dbf, components, sorted(dbf.phases), conditions)
    phase_totals: dict[str, float] = {}
    for phase_name, phase_fraction in zip(result.Phase.values.reshape(-1), result.NP.values.reshape(-1)):
        name = str(phase_name)
        fraction = float(phase_fraction)
        if name and fraction > 1e-8:
            phase_totals[name] = phase_totals.get(name, 0.0) + fraction
    gm = float(result.GM.values.reshape(-1)[0])
    return {
        "calculation_status": "ok",
        "stable_phases": ";".join(f"{name}={fraction:.6f}" for name, fraction in sorted(phase_totals.items())),
        "GM_J_per_mol": f"{gm:.6f}",
    }


def child(args: argparse.Namespace) -> int:
    record = json.loads(args.record_json)
    try:
        payload = solve_one(record, Path(args.tdb))
    except Exception as exc:  # Emit a structured, auditable failure record.
        payload = {"calculation_status": "error", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def parent(args: argparse.Namespace) -> int:
    rows = list(csv.DictReader(args.input.open(encoding="utf-8")))
    if args.record_ids:
        selected_ids = {item.strip() for item in args.record_ids.split(",") if item.strip()}
        rows = [row for row in rows if row["record_id"] in selected_ids]
        missing_ids = selected_ids - {row["record_id"] for row in rows}
        if missing_ids:
            raise ValueError(f"unknown record ids: {sorted(missing_ids)}")
    output_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        print(f"[{index}/{len(rows)}] {row['record_id']}", flush=True)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--tdb",
            str(args.tdb),
            "--record-json",
            json.dumps(row, ensure_ascii=False),
        ]
        environment = dict(os.environ)
        environment.setdefault("MPLCONFIGDIR", "/tmp/mpl-calphpad")
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout_seconds, env=environment, check=False)
            payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {
                "calculation_status": "error",
                "error": f"child_exit={completed.returncode}; stderr={completed.stderr[-1000:]}",
            }
        except subprocess.TimeoutExpired:
            payload = {"calculation_status": "timeout", "error": f"exceeded {args.timeout_seconds} seconds"}
        output_rows.append({**row, **{key: str(value) for key, value in payload.items()}})
    keys = list(rows[0]) + ["calculation_status", "stable_phases", "GM_J_per_mol", "error"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"wrote {args.output}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--tdb", type=Path, default=DEFAULT_TDB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--record-ids", help="comma-separated subset of record_id values")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--record-json")
    args = parser.parse_args()
    if args.child:
        return child(args)
    if not args.tdb.is_file():
        raise FileNotFoundError(args.tdb)
    return parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
