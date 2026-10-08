#!/usr/bin/env python3
"""Audit a TDB against traceable, quantitative HEA phase-fraction states.

This runner is intentionally offline.  It produces the calibration artifact
consumed by a separately versioned hard-gate configuration; it does not teach a
property surrogate from CALPHAD outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/processed/thermo_calibration/hea_thermo_calibration_registry_v1.csv"
DEFAULT_TDB = Path("/tmp/cantor_hea_calphad_2023_extract/mpea-05_pycalphad_adapted.tdb")
DEFAULT_OUTPUT = ROOT / "reports/calphad/primary_phase_fraction_tdb_audit_20260828.csv"
DEFAULT_SUMMARY = ROOT / "reports/calphad/primary_phase_fraction_tdb_audit_20260828.json"


def composition(value: str) -> dict[str, float]:
    result = {key.strip().upper(): float(amount) / 100.0 for key, amount in (item.split("=", 1) for item in value.split(";"))}
    if not math.isclose(sum(result.values()), 1.0, abs_tol=2e-3):
        raise ValueError(f"atomic fractions do not sum to one: {value}")
    return result


def observed_fractions(value: str) -> dict[str, float]:
    result = {name.strip(): float(amount) / (100.0 if float(amount) > 1 else 1.0) for name, amount in (item.split("=", 1) for item in value.split(";"))}
    if not math.isclose(sum(result.values()), 1.0, abs_tol=0.03):
        raise ValueError(f"phase fractions do not sum to one: {value}")
    return result


def canonical_phase(name: str) -> str:
    normalized = name.upper().replace("-", "_")
    if normalized.startswith("FCC"):
        return "FCC"
    # MPEA-05 calls its ordered bcc phase ``B2_BCC``; both it and BCC_A2
    # belong to the experimentally reported coarse BCC/B2 bucket.
    if normalized.startswith("BCC") or normalized.startswith("B2"):
        return "BCC"
    if normalized.startswith("SIGMA"):
        return "SIGMA"
    if normalized.startswith("L1_") or normalized.startswith("L10"):
        return "L1_0"
    return normalized


def child(record_json: str, tdb: Path) -> dict[str, object]:
    import warnings

    warnings.filterwarnings("ignore", message="The order-disorder model.*")
    from pycalphad import Database, equilibrium, variables as v

    record = json.loads(record_json)
    comp = composition(record["composition_at_pct"])
    dbf = Database(str(tdb))
    independent = sorted(comp)[:-1]
    conditions = {v.X(element): comp[element] for element in independent}
    conditions.update({v.T: float(record["temperature_C"]) + 273.15, v.P: 101325, v.N: 1})
    result = equilibrium(dbf, sorted(comp) + ["VA"], sorted(dbf.phases), conditions)
    phases: dict[str, float] = {}
    for phase, fraction in zip(result.Phase.values.reshape(-1), result.NP.values.reshape(-1)):
        phase, fraction = str(phase), float(fraction)
        if phase and fraction > 1e-8:
            phases[phase] = phases.get(phase, 0.0) + fraction
    grouped: dict[str, float] = defaultdict(float)
    for phase, fraction in phases.items():
        grouped[canonical_phase(phase)] += fraction
    return {"status": "ok", "predicted_phases": phases, "predicted_groups": dict(grouped), "GM_J_per_mol": float(result.GM.values.reshape(-1)[0])}


def solve(record: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    command = [sys.executable, str(Path(__file__).resolve()), "--child", "--tdb", str(args.tdb), "--record-json", json.dumps(record)]
    environment = dict(os.environ); environment.setdefault("MPLCONFIGDIR", "/tmp/mpl-calphad")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout_seconds, env=environment, check=False)
        if completed.returncode:
            return {"status": "error", "error": completed.stderr[-1000:] or completed.stdout[-1000:]}
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"exceeded {args.timeout_seconds} seconds"}


def main(args: argparse.Namespace) -> int:
    rows = list(csv.DictReader(args.input.open(encoding="utf-8")))
    # `manual:` rows duplicate 00270 primary states; use a single, directly
    # retained primary-PDF row per state, never a compilation/manual duplicate.
    rows = [row for row in rows if row["source_kind"] == "verified_primary_pdf" and row["phase_fractions"].strip()]
    output: list[dict[str, str]] = []
    for index, row in enumerate(rows, 1):
        print(f"[{index}/{len(rows)}] {row['registry_id']}", flush=True)
        result = solve(row, args)
        observed = {canonical_phase(k): v for k, v in observed_fractions(row["phase_fractions"]).items()}
        predicted = result.get("predicted_groups", {}) if result.get("status") == "ok" else {}
        labels = sorted(set(observed) | set(predicted))
        mae = sum(abs(observed.get(label, 0.0) - float(predicted.get(label, 0.0))) for label in labels) / len(labels) if labels else None
        output.append({
            **row,
            "calculation_status": str(result.get("status")),
            "predicted_phase_fractions": ";".join(f"{name}={amount:.6f}" for name, amount in sorted(predicted.items())),
            "GM_J_per_mol": f"{result['GM_J_per_mol']:.6f}" if result.get("status") == "ok" else "",
            "phase_fraction_mae": f"{mae:.6f}" if mae is not None else "",
            "phase_presence_exact": str(set(observed) == set(predicted)),
            "error": str(result.get("error", "")),
        })
    fields = list(output[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(output)
    solved = [row for row in output if row["calculation_status"] == "ok"]
    # Metrics are descriptive because current records are deliberately
    # process-conditioned.  A release gate may only use them after explicit
    # threshold approval and source-group holdout becomes feasible.
    maes = [float(row["phase_fraction_mae"]) for row in solved if row["phase_fraction_mae"]]
    summary = {
        "tdb": str(args.tdb), "input": str(args.input), "records": len(output), "solved": len(solved),
        "source_count": len({row["source_locator"].split(";")[0] for row in output}),
        "exact_phase_set_matches": sum(row["phase_presence_exact"] == "True" for row in solved),
        "mean_phase_fraction_mae": sum(maes) / len(maes) if maes else None,
        "median_phase_fraction_mae": sorted(maes)[len(maes) // 2] if maes else None,
        "tier_counts": dict(Counter(row["calibration_tier"] for row in output)),
        "release_decision": "pending: process-conditioned records and fewer than five independent primary sources; do not enable a production experimental-calibrated hard gate",
    }
    args.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT); parser.add_argument("--tdb", type=Path, default=DEFAULT_TDB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT); parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--timeout-seconds", type=int, default=180); parser.add_argument("--child", action="store_true"); parser.add_argument("--record-json")
    parsed = parser.parse_args()
    if parsed.child:
        try:
            print(json.dumps(child(parsed.record_json, parsed.tdb), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        raise SystemExit(0)
    if not parsed.tdb.is_file():
        raise FileNotFoundError(parsed.tdb)
    raise SystemExit(main(parsed))
