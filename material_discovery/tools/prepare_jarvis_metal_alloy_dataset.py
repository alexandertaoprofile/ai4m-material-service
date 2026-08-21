#!/usr/bin/env python3
"""Build an auditable MatterGen training package from a JARVIS structure CSV.

Run this in ``mattergen-py310`` after downloading a *complete* export of the
platform's ``material.jarvis_structure`` table. A truncated stream cannot
silently become a final training dataset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from pymatgen.core import Element, Lattice, Structure
from pymatgen.io.cif import CifWriter


DEFAULT_ALLOWED_EHULL = 0.10
EXCLUDED_SYMBOLS = {"Tc", "Pm"}
MAX_ALLOWED_ATOMIC_NUMBER = 83


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chemical_system(symbols: Iterable[str]) -> str:
    return "-".join(sorted(set(symbols), key=lambda symbol: Element(symbol).Z))


def parse_structure(row: dict[str, str]) -> Structure:
    raw = json.loads(row["structure"])
    symbols, coordinates, lattice = raw["elements"], raw["coords"], raw["lattice_mat"]
    if not (isinstance(symbols, list) and isinstance(coordinates, list) and len(symbols) == len(coordinates) and symbols):
        raise ValueError("elements/coords are missing or have different lengths")
    if any(not isinstance(coord, list) or len(coord) != 3 or not all(math.isfinite(float(x)) for x in coord) for coord in coordinates):
        raise ValueError("coordinates are not finite 3D vectors")
    if not (isinstance(lattice, list) and len(lattice) == 3 and all(isinstance(v, list) and len(v) == 3 for v in lattice)):
        raise ValueError("lattice is not a 3x3 matrix")
    matrix = [[float(value) for value in vector] for vector in lattice]
    if abs(float(np.linalg.det(matrix))) <= 1e-8:
        raise ValueError("lattice volume is zero")
    return Structure(Lattice(matrix), symbols, coordinates, coords_are_cartesian=bool(raw.get("cartesian", True)), to_unit_cell=True)


def normalize_structure(structure: Structure) -> Structure:
    """Use MatterGen's primitive-cell convention before applying the site cap."""
    return structure.get_primitive_structure(tolerance=0.01).get_reduced_structure(reduction_algo="niggli")


def allowed_ordered_metal_alloy(structure: Structure) -> tuple[bool, str]:
    elements = {site.specie for site in structure}
    if len(elements) < 2:
        return False, "not_multielement"
    if any(element.symbol in EXCLUDED_SYMBOLS or element.Z > MAX_ALLOWED_ATOMIC_NUMBER for element in elements):
        return False, "unsupported_element"
    if not all(element.is_metal for element in elements):
        return False, "contains_nonmetal"
    return True, "accepted"


def finite_number(row: dict[str, str], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError(f"{field} is not finite")
    return value


def split_by_chemical_system(records: list[dict[str, Any]], seed: int) -> dict[str, list[dict[str, Any]]]:
    """Keep each chemical system in one split to prevent prototype leakage."""
    by_system: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_system[record["chemical_system"]].append(record)
    systems = list(by_system)
    random.Random(seed).shuffle(systems)
    targets, sizes = {"train": len(records) * 0.8, "val": len(records) * 0.1}, Counter()
    result = {"train": [], "val": [], "test": []}
    for system in systems:
        split = "train" if sizes["train"] < targets["train"] else "val" if sizes["val"] < targets["val"] else "test"
        result[split].extend(by_system[system])
        sizes[split] += len(by_system[system])
    return result


def output_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_id": record["material_id"],
        "formation_energy_per_atom": record["formation_energy_per_atom"],
        "dft_band_gap": record["dft_band_gap"],
        "pretty_formula": record["pretty_formula"],
        "energy_above_hull": record["energy_above_hull"],
        "chemical_system": record["chemical_system"],
        "space_group": record["space_group"],
        "elements": json.dumps(record["elements"]),
        "cif": record["cif"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Complete material.jarvis_structure CSV export")
    parser.add_argument("--output-dir", type=Path, required=True, help="Model package data directory")
    parser.add_argument("--expected-records", type=int, required=True, help="Exact row count of the downloaded source snapshot")
    parser.add_argument("--max-energy-above-hull", type=float, default=DEFAULT_ALLOWED_EHULL)
    parser.add_argument("--max-sites", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument("--limit-records", type=int, default=None, help="Debug only: process at most this many input rows")
    parser.add_argument("--allow-incomplete-input", action="store_true", help="Debug only; output manifest remains non-final")
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Input CSV does not exist: {args.input}")
    source_hash_before = sha256_file(args.input)

    with args.input.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"jid", "formula", "space_group_number", "formation_energy_per_atom", "band_gap", "energy_above_hull", "structure"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"Input CSV misses required fields: {sorted(missing)}")
        rows = list(reader)
    if args.limit_records is not None:
        if args.limit_records < 1:
            raise SystemExit("--limit-records must be positive")
        if not args.allow_incomplete_input:
            raise SystemExit("--limit-records requires --allow-incomplete-input")
        rows = rows[:args.limit_records]
    source_hash_after_read = sha256_file(args.input)
    if source_hash_after_read != source_hash_before and not args.allow_incomplete_input:
        raise SystemExit("Input CSV changed while it was being read; wait for a stable export before building a final dataset.")
    if len(rows) != args.expected_records and not args.allow_incomplete_input:
        raise SystemExit(f"Input has {len(rows)} records, expected exactly {args.expected_records}; refuse non-reproducible final dataset.")

    excluded: Counter[str] = Counter()
    accepted: list[dict[str, Any]] = []
    seen_jid: set[str] = set()
    for row in rows:
        jid = row["jid"].strip()
        if not jid or jid in seen_jid:
            excluded["duplicate_or_missing_jid"] += 1
            continue
        seen_jid.add(jid)
        try:
            structure = normalize_structure(parse_structure(row))
            allowed, reason = allowed_ordered_metal_alloy(structure)
            if not allowed:
                excluded[reason] += 1
                continue
            if len(structure) > args.max_sites:
                excluded["too_many_actual_sites"] += 1
                continue
            ehull = finite_number(row, "energy_above_hull")
            if ehull > args.max_energy_above_hull:
                excluded["energy_above_hull"] += 1
                continue
            symbols = sorted({site.specie.symbol for site in structure}, key=lambda symbol: Element(symbol).Z)
            accepted.append({
                "material_id": jid,
                "formation_energy_per_atom": finite_number(row, "formation_energy_per_atom"),
                "dft_band_gap": finite_number(row, "band_gap"),
                "pretty_formula": structure.composition.reduced_formula,
                "energy_above_hull": ehull,
                "chemical_system": chemical_system(symbols),
                "space_group": int(row["space_group_number"]),
                "elements": symbols,
                "cif": str(CifWriter(structure)),
            })
        except Exception as exc:
            excluded[f"invalid_structure:{type(exc).__name__}"] += 1
    if not accepted:
        raise SystemExit("No structures passed the training-data admission policy")
    source_hash_after_processing = sha256_file(args.input)
    if source_hash_after_processing != source_hash_before and not args.allow_incomplete_input:
        raise SystemExit("Input CSV changed during structure processing; do not publish this dataset. Re-run on a stable export.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    splits = split_by_chemical_system(accepted, args.seed)
    fields = list(output_row(accepted[0]))
    split_manifest: dict[str, dict[str, Any]] = {}
    for name, records in splits.items():
        path = args.output_dir / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(output_row(record) for record in records)
        ids = sorted(record["material_id"] for record in records)
        split_manifest[name] = {
            "count": len(records),
            "chemical_system_count": len({record["chemical_system"] for record in records}),
            "jid_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        }
    manifest = {
        "dataset_id": "jarvis_metal_alloy_mattergen_v0",
        "status": "debug_incomplete" if len(rows) < args.expected_records else "ready_for_cache_conversion",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(args.input), "sha256": source_hash_before, "record_count": len(rows), "expected_record_count": args.expected_records},
        "admission": {"max_energy_above_hull": args.max_energy_above_hull, "max_actual_sites": args.max_sites, "excluded_symbols": sorted(EXCLUDED_SYMBOLS), "max_atomic_number": MAX_ALLOWED_ATOMIC_NUMBER},
        "accepted_count": len(accepted),
        "excluded_counts": dict(sorted(excluded.items())),
        "splits": split_manifest,
        "seed": args.seed,
        "converter": "prepare_jarvis_metal_alloy_dataset.py",
    }
    (args.output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
