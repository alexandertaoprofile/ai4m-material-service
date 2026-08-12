"""Build a traceable, non-inferential conductivity/viscosity pairing view.

Only records with identical raw source, components, composition fields,
temperature, and pressure are paired.  Missing mixture fractions are never
filled in; the output explicitly marks them as requiring composition review.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


CONDUCTIVITY_FILE = "material.fluid_conductivity.csv"
VISCOSITY_FILE = "material.fluid_viscosity.csv"
OUTPUT_FIELDS = [
    "source_id", "component_1", "component_2", "component_3", "pure_component_or_mixture",
    "composition_basis", "component_1_fraction", "component_2_fraction", "component_3_fraction",
    "temperature_k", "pressure_pa", "composition_status", "conductivity_records",
    "conductivity_s_m_min", "conductivity_s_m_max", "resistivity_ohm_m_min", "resistivity_ohm_m_max",
    "viscosity_records", "dynamic_viscosity_mpa_s_min", "dynamic_viscosity_mpa_s_max",
    "conductivity_manual_review", "viscosity_manual_review", "screening_status",
    "electrical_threshold_status", "viscosity_threshold_status", "provisional_screen_status",
]


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple((row.get(field) or "").strip() for field in (
        "source_id", "component_1", "component_2", "component_3", "pure_component_or_mixture",
        "composition_basis", "component_1_fraction", "component_2_fraction", "component_3_fraction",
        "temperature_k", "pressure_pa",
    ))


def _composition_status(row: dict[str, str]) -> str:
    if row.get("pure_component_or_mixture") == "pure":
        return "pure_component"
    named = sum(bool((row.get(f"component_{index}") or "").strip()) for index in (1, 2, 3))
    fractioned = sum(bool((row.get(f"component_{index}_fraction") or "").strip()) for index in (1, 2, 3))
    return "complete" if named == fractioned else "partial_requires_review"


def _append(groups: dict[tuple[str, ...], list[dict[str, str]]], path: Path, value_field: str) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("experimental_or_predicted") != "experimental":
                continue
            value = _number(row.get(value_field))
            temperature = _number(row.get("temperature_k"))
            if value is None or value <= 0 or temperature is None or not 293.15 <= temperature <= 303.15:
                continue
            groups[_key(row)].append(row)


def _range(rows: list[dict[str, str]], field: str) -> tuple[str, str]:
    values = [_number(row.get(field)) for row in rows]
    numbers = [value for value in values if value is not None]
    return (str(min(numbers)), str(max(numbers))) if numbers else ("", "")


def _ids(rows: list[dict[str, str]]) -> str:
    return "|".join(sorted({row.get("record_id") or "" for row in rows if row.get("record_id")}))


def _review(rows: list[dict[str, str]]) -> str:
    return "yes" if any(row.get("manual_review_required") == "yes" for row in rows) else "no"


def _numeric_screen(conductivity_min: str, viscosity_max: str, composition_status: str) -> tuple[str, str, str]:
    electrical = "pass" if float(conductivity_min) >= 0.1 else "fail"
    viscosity = "priority_pass" if float(viscosity_max) <= 130 else ("boundary" if float(viscosity_max) <= 150 else "fail")
    if electrical != "pass" or viscosity == "fail":
        return electrical, viscosity, "numeric_not_passed"
    band = "priority" if viscosity == "priority_pass" else "boundary"
    suffix = "needs_composition_review" if composition_status == "partial_requires_review" else "pending_quality_review"
    return electrical, viscosity, f"provisional_{band}_{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact room-temperature transport evidence pairs")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    conductivity: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    viscosity: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    _append(conductivity, args.input / CONDUCTIVITY_FILE, "conductivity_s_m")
    _append(viscosity, args.input / VISCOSITY_FILE, "dynamic_viscosity_mpa_s")

    shared_keys = sorted(set(conductivity) & set(viscosity))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for key in shared_keys:
            conductivity_rows = conductivity[key]
            viscosity_rows = viscosity[key]
            source = conductivity_rows[0]
            conductivity_min, conductivity_max = _range(conductivity_rows, "conductivity_s_m")
            resistivity_min, resistivity_max = _range(conductivity_rows, "resistivity_ohm_m")
            viscosity_min, viscosity_max = _range(viscosity_rows, "dynamic_viscosity_mpa_s")
            status = _composition_status(source)
            electrical_status, viscosity_status, provisional_status = _numeric_screen(conductivity_min, viscosity_max, status)
            writer.writerow({
                "source_id": source.get("source_id"),
                "component_1": source.get("component_1"),
                "component_2": source.get("component_2"),
                "component_3": source.get("component_3"),
                "pure_component_or_mixture": source.get("pure_component_or_mixture"),
                "composition_basis": source.get("composition_basis"),
                "component_1_fraction": source.get("component_1_fraction"),
                "component_2_fraction": source.get("component_2_fraction"),
                "component_3_fraction": source.get("component_3_fraction"),
                "temperature_k": source.get("temperature_k"),
                "pressure_pa": source.get("pressure_pa"),
                "composition_status": status,
                "conductivity_records": _ids(conductivity_rows),
                "conductivity_s_m_min": conductivity_min,
                "conductivity_s_m_max": conductivity_max,
                "resistivity_ohm_m_min": resistivity_min,
                "resistivity_ohm_m_max": resistivity_max,
                "viscosity_records": _ids(viscosity_rows),
                "dynamic_viscosity_mpa_s_min": viscosity_min,
                "dynamic_viscosity_mpa_s_max": viscosity_max,
                "conductivity_manual_review": _review(conductivity_rows),
                "viscosity_manual_review": _review(viscosity_rows),
                "screening_status": "eligible_for_review" if status in {"complete", "pure_component"} else "needs_composition_review",
                "electrical_threshold_status": electrical_status,
                "viscosity_threshold_status": viscosity_status,
                "provisional_screen_status": provisional_status,
            })
    print(f"wrote {len(shared_keys)} exact room-temperature transport pairs to {args.output}")


if __name__ == "__main__":
    main()
