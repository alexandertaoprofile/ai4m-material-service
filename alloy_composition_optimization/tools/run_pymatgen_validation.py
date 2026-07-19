#!/usr/bin/env python3
"""Run the lightweight structural admission check inside the MatterGen env."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()

    structure = Structure.from_file(args.input)
    distances = structure.distance_matrix
    minimum = float(distances[distances > 1e-8].min())
    errors: list[str] = []
    if not structure.is_ordered:
        errors.append("Structure has partial occupancy.")
    if minimum < 1.2:
        errors.append(f"Minimum interatomic distance is too small ({minimum:.3f} Å).")
    try:
        space_group = SpacegroupAnalyzer(structure, symprec=0.1).get_space_group_symbol()
    except Exception:
        space_group = None

    print(json.dumps({
        "candidate_id": args.candidate_id,
        "status": "ok",
        "is_valid": not errors,
        "formula_pretty": structure.composition.reduced_formula,
        "space_group": space_group,
        "density": float(structure.density),
        "errors": errors,
        "artifacts": {"cif_path": str(args.input)},
        "metadata": {
            "num_sites": len(structure),
            "min_interatomic_distance": minimum,
            "ordered": structure.is_ordered,
            "execution_environment": "mattergen-py310",
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
