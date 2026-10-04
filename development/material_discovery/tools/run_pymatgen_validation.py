#!/usr/bin/env python3
"""Run the lightweight structural admission check inside the MatterGen env."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.analysis.local_env import CovalentRadius
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


MIN_COVALENT_DISTANCE_RATIO = 0.75


def pair_distance_violations(structure: Structure) -> list[dict]:
    """Find implausibly close pairs using element-specific covalent radii.

    A single universal cutoff incorrectly rejects normal bonds involving H.
    The conservative 75%-of-covalent-radii-sum threshold instead detects
    likely atom overlap while retaining chemically plausible short bonds.
    """
    violations: list[dict] = []
    for first in range(len(structure)):
        for second in range(first + 1, len(structure)):
            first_element = structure[first].specie.symbol
            second_element = structure[second].specie.symbol
            first_radius = CovalentRadius.radius.get(first_element)
            second_radius = CovalentRadius.radius.get(second_element)
            if first_radius is None or second_radius is None:
                continue
            distance = float(structure.get_distance(first, second))
            lower_bound = MIN_COVALENT_DISTANCE_RATIO * (first_radius + second_radius)
            if distance < lower_bound:
                violations.append({
                    "sites": [first, second],
                    "elements": [first_element, second_element],
                    "distance_angstrom": distance,
                    "minimum_allowed_angstrom": lower_bound,
                })
    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()

    structure = Structure.from_file(args.input)
    distances = structure.distance_matrix
    minimum = float(distances[distances > 1e-8].min())
    close_pairs = pair_distance_violations(structure)
    errors: list[str] = []
    if not structure.is_ordered:
        errors.append("Structure has partial occupancy.")
    if close_pairs:
        pair = close_pairs[0]
        errors.append(
            "Atoms are implausibly close for their elements "
            f"({pair['elements'][0]}-{pair['elements'][1]}: "
            f"{pair['distance_angstrom']:.3f} Å; minimum "
            f"{pair['minimum_allowed_angstrom']:.3f} Å)."
        )
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
            "distance_check": "pairwise_covalent_radius",
            "min_covalent_distance_ratio": MIN_COVALENT_DISTANCE_RATIO,
            "close_pair_violations": close_pairs,
            "ordered": structure.is_ordered,
            "execution_environment": "mattergen-py310",
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
