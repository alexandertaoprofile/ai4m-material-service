"""Import the reviewed NIST Al 6061-T6 thermal-conductivity fit.

The source table explicitly identifies Al 6061-T6, gives its coefficients,
temperature coverage and the equation printed on the archived source page.
This importer evaluates that source equation at a fixed, inspectable grid; it
does not infer an unrecorded property such as hardness.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


MATERIAL_ID = "MAT-AL6061-T6"
PROPERTY = "thermal_conductivity"
SOURCE_ID = "SRC-AL6061-NIST"
SOURCE_LOCATOR = "AL6061物性数据；p.1；table-0001"
CONDITION = "T6；NIST published curve fit；fit error relative to data 0.5%"
GRID_K = (4.0, 20.0, 77.0, 150.0, 200.0, 250.0, 293.15, 300.0)

FIELDS = (
    "material_id", "property", "raw_temperature", "raw_temperature_unit", "temperature_K",
    "raw_value", "raw_unit", "value_SI", "SI_unit", "uncertainty_raw", "condition",
    "data_kind", "source_id", "source_locator", "transformation", "raw_source_file",
    "raw_sheet", "raw_row_number", "raw_row_json",
)


def _coefficients(source: Path) -> list[float]:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    values = {str(row.get("units") or "").strip(): row.get("thermal_conductivity_w_m_k") for row in rows}
    coefficients = []
    for name in "abcdefghi":
        try:
            coefficients.append(float(str(values[name]).strip()))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"missing or invalid coefficient {name!r} in {source}") from exc
    if values.get("data range") != "4-300" or values.get("equation range") != "1-300":
        raise ValueError("unexpected NIST thermal-conductivity range; review source before import")
    return coefficients


def _value(temperature_K: float, coefficients: list[float]) -> float:
    logarithm = math.log10(temperature_K)
    return 10 ** sum(coefficient * logarithm ** power for power, coefficient in enumerate(coefficients))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    coefficients = _coefficients(args.source)
    existing: list[dict[str, str]] = []
    if args.output.is_file():
        with args.output.open(encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
    duplicate = any(
        row.get("material_id") == MATERIAL_ID and row.get("property") == PROPERTY and row.get("source_id") == SOURCE_ID
        for row in existing
    )
    if duplicate:
        raise SystemExit("Al 6061-T6 NIST thermal curve already present; refusing duplicate import")
    source_relative = str(args.source)
    for temperature_K in GRID_K:
        value = _value(temperature_K, coefficients)
        existing.append({
            "material_id": MATERIAL_ID, "property": PROPERTY,
            "raw_temperature": f"{temperature_K:g}", "raw_temperature_unit": "K",
            "temperature_K": f"{temperature_K:g}", "raw_value": f"{value:.12g}",
            "raw_unit": "W/(m·K)", "value_SI": f"{value:.12g}", "SI_unit": "W/(m·K)",
            "uncertainty_raw": "0.5% curve-fit error relative to source data", "condition": CONDITION,
            "data_kind": "derived_from_published_curve_fit", "source_id": SOURCE_ID,
            "source_locator": SOURCE_LOCATOR,
            "transformation": "log10(y)=a+b log10(T)+...+i(log10(T))^8; evaluated at fixed source-range grid",
            "raw_source_file": source_relative, "raw_sheet": "table-0001", "raw_row_number": "1-13",
            "raw_row_json": "published NIST coefficients a-i; source data range 4-300 K; equation range 1-300 K",
        })
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        # Preserve the established catalogue CSV line ending so a single
        # reviewed curve import remains a small, auditable Git diff.
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(existing)
    print(f"imported {len(GRID_K)} Al 6061-T6 thermal-conductivity curve points into {args.output}")


if __name__ == "__main__":
    main()
