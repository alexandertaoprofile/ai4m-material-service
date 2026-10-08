#!/usr/bin/env python3
"""Validate traceable chip-packaging glass records and emit lane-specific gates.

This tool deliberately does not fit a model or impute missing measurements.
It keeps bare-glass, TGV-process, and package-reliability records separate.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


OXIDES = (
    "SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO",
    "BaO", "ZnO", "TiO2", "ZrO2", "P2O5",
)
LANES = {"glass_thermal", "glass_rf", "tgv_manufacturability", "package_reliability"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def other_oxide_total(value: str) -> float | None:
    """Parse explicit ``Oxide=value mol%`` or ``wt%`` entries only.

    The evidence template deliberately keeps uncommon oxides in text so the
    core schema stays readable.  A record may nevertheless be composition
    complete when every such component is explicitly quantified in the same
    basis.  Free text, ranges and unqualified values remain a gate blocker.
    """
    value = (value or "").strip()
    if not value:
        return 0.0
    parts = [part.strip() for part in value.split(";") if part.strip()]
    total = 0.0
    for part in parts:
        match = re.fullmatch(r"[A-Za-z][A-Za-z0-9]*\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mol%|wt%)", part)
        if not match:
            return None
        total += float(match.group(1))
    return total


def blockers(row: dict[str, str]) -> list[str]:
    missing = [key for key in ("record_id", "glass_id", "source_id", "source_locator", "evidence_level") if not row.get(key, "").strip()]
    lane = row.get("model_lane", "")
    if lane not in LANES:
        missing.append("valid_model_lane")
    if row.get("composition_basis", "") not in {"wt_percent", "mol_percent"}:
        missing.append("composition_basis")
    values = [number(row.get(oxide, "")) for oxide in OXIDES]
    if all(value is None for value in values):
        missing.append("explicit_oxide_composition")
    elif any(value is None for value in values):
        missing.append("complete_oxide_composition")
    else:
        uncommon_total = other_oxide_total(row.get("other_oxides_text", ""))
        declared_total = number(row.get("composition_total", ""))
        complete_total = sum(value for value in values if value is not None) + (uncommon_total if uncommon_total is not None else 0.0)
        if uncommon_total is None:
            missing.append("quantified_other_oxides")
        elif declared_total is None or not 99.0 <= declared_total <= 101.0:
            missing.append("declared_composition_total_99_to_101")
        elif abs(complete_total - declared_total) > 0.15:
            missing.append("all_oxide_values_match_declared_total")
    if lane == "glass_thermal" and not (row.get("property_name") and row.get("temperature_min_C") and row.get("temperature_max_C")):
        missing.append("temperature_resolved_thermal_property")
    if lane == "glass_rf" and not all(row.get(key, "") for key in ("property_name", "frequency_min_Hz", "frequency_max_Hz", "temperature_min_C", "humidity_condition", "test_method")):
        missing.append("frequency_temperature_humidity_method")
    if lane == "tgv_manufacturability" and not all(row.get(key, "") for key in ("substrate_thickness_um", "tgv_diameter_um", "tgv_process", "property_name")):
        missing.append("tgv_structure_process_outcome")
    if lane == "package_reliability" and not all(row.get(key, "") for key in ("package_stack_id", "thermal_history", "reliability_test", "property_name")):
        missing.append("package_stack_history_reliability_outcome")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    records = read_csv(args.input)
    fields = list(records[0]) if records else []
    reviewed, admitted = [], []
    for row in records:
        record = dict(row)
        record_blockers = blockers(record)
        record["training_admission"] = "admitted" if not record_blockers else "review_required"
        record["admission_blockers"] = ";".join(record_blockers)
        reviewed.append(record)
        if not record_blockers:
            admitted.append(record)
    output_fields = fields + ["training_admission", "admission_blockers"]
    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir / "reviewed_records.csv", output_fields, reviewed)
    for lane in sorted(LANES):
        lane_rows = [row for row in admitted if row["model_lane"] == lane]
        write_csv(args.output_dir / f"{lane}_admitted.csv", output_fields, lane_rows)
    thermal_glasses = {row["glass_id"] for row in admitted if row["model_lane"] == "glass_thermal"}
    summary = {
        "package": "chip_glass_substrate_training_gate_v1",
        "model_fit_started": False,
        "input_records": len(records),
        "admitted_records": len(admitted),
        "review_required_records": len(reviewed) - len(admitted),
        "admitted_by_lane": dict(Counter(row["model_lane"] for row in admitted)),
        "thermal_independent_glass_compositions": len(thermal_glasses),
        "thermal_fit_ready": len(thermal_glasses) >= 50,
        "thermal_fit_requirement": "at least 50 independently composed glasses with a temperature-resolved thermal property",
        "rf_fit_requirement": "compare and validate within frequency intervals and glass families; never extrapolate 1 MHz data to 10–100 GHz",
        "tgv_reliability_policy": "if independent samples are insufficient, retain an evidence map and experimental design; do not fit a life extrapolator",
        "lane_separation": "glass thermal, RF, TGV manufacturability and package reliability are emitted as separate files",
    }
    (args.output_dir / "training_readiness.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
