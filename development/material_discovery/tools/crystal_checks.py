"""Shared periodic geometry check for generated and relaxed crystals."""
from __future__ import annotations

import math
import numpy as np
from pymatgen.analysis.local_env import CovalentRadius
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


def check_structure(structure) -> dict:
    errors, violations = [], []
    if not structure.is_ordered or not len(structure):
        raise ValueError("Only nonempty fully ordered crystals are supported")
    if not np.isfinite(structure.lattice.matrix).all() or not np.isfinite(structure.frac_coords).all() or structure.volume <= 1e-6:
        raise ValueError("Nonfinite or singular crystal cell")
    # Bound image enumeration before calling neighbor_list: highly skewed or
    # near-collapsed generated cells otherwise exhaust CPU memory.
    singular_values = np.linalg.svd(structure.lattice.matrix, compute_uv=False)
    if min(singular_values) < 0.5 or max(structure.lattice.abc) > 100:
        raise ValueError("晶胞超出当前几何检查安全范围（最小奇异值≥0.5 Å、边长≤100 Å）；未进入近邻枚举。")
    minimum = math.inf
    # Neighbor search includes periodic images, identical sites and self-images.
    centers, neighbors, images, distances = structure.get_neighbor_list(5.0, exclude_self=True)
    for first, second, image, distance in zip(centers, neighbors, images, distances):
        minimum = min(minimum, float(distance))
        symbol1, symbol2 = structure[int(first)].specie.symbol, structure[int(second)].specie.symbol
        lower = 0.75 * (CovalentRadius.radius.get(symbol1, 0.3) + CovalentRadius.radius.get(symbol2, 0.3))
        if distance < lower and (int(first) <= int(second)):
            violations.append({"sites": [int(first), int(second)], "image": [int(item) for item in image],
                "elements": [symbol1, symbol2], "distance_angstrom": float(distance), "minimum_allowed_angstrom": lower})
    if violations:
        errors.append(f"周期边界下 {len(violations)} 个近邻距离低于共价半径下限。")
    analyzer = SpacegroupAnalyzer(structure, symprec=0.1, angle_tolerance=5)
    inversion = any(np.allclose(operation.rotation_matrix, -np.eye(3)) for operation in analyzer.get_symmetry_operations())
    return {"is_valid": not errors, "errors": errors, "formula_pretty": structure.composition.reduced_formula,
        "space_group": analyzer.get_space_group_symbol(), "density": float(structure.density),
        "metadata": {"num_sites": len(structure), "ordered": structure.is_ordered,
            "min_interatomic_distance": None if minimum == math.inf else minimum,
            "distance_check": "periodic_neighbors_including_self_images", "close_pair_violations": violations,
            "min_covalent_distance_ratio": 0.75, "space_group_number": analyzer.get_space_group_number(),
            "non_centrosymmetric": not inversion, "symprec_angstrom": 0.1, "angle_tolerance_deg": 5,
            "element_counts": {key: int(value) for key, value in structure.composition.get_el_amt_dict().items()},
            "lattice_parameters": [float(item) for item in structure.lattice.abc + structure.lattice.angles],
            "volume_angstrom3": float(structure.volume)}}
