"""Memory-bounded MatterGen cache builder for HEA CIF CSV exports."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

import numpy as np

from .export_mattergen_csv import DEFAULT_EXPORT


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = ROOT / "data/processed/hea_mattergen_v1/mattergen_cache/cantor_hea_5plus_32_v1"


def build_split(csv_path: Path, output: Path) -> dict[str, int]:
    """Build a CrystalDataset cache without retaining all pymatgen objects."""
    from pymatgen.io.cif import CifParser
    from mattergen.common.data.types import PropertyValues

    positions, cells, atomic_numbers, num_atoms, structure_ids, e_hulls = [], [], [], [], [], []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            structure = CifParser.from_str(row["cif"]).parse_structures(primitive=True, on_error="ignore")[0]
            structure = structure.get_primitive_structure().get_reduced_structure()
            positions.append(structure.frac_coords)
            cells.append(structure.lattice.matrix)
            atomic_numbers.append(structure.atomic_numbers)
            num_atoms.append(len(structure))
            structure_ids.append(row["material_id"])
            e_hulls.append(float(row["energy_above_hull"]))

    output.mkdir(parents=True, exist_ok=True)
    np.save(output / "pos.npy", np.row_stack(positions))
    np.save(output / "cell.npy", np.asarray(cells))
    np.save(output / "atomic_numbers.npy", np.concatenate(atomic_numbers))
    np.save(output / "num_atoms.npy", np.asarray(num_atoms))
    np.save(output / "structure_id.npy", np.asarray(structure_ids))
    PropertyValues(values=np.asarray(e_hulls), property_source_doc_id="energy_above_hull").to_json(output / "energy_above_hull.json")
    return {"structures": len(num_atoms), "max_sites": max(num_atoms), "atoms": int(sum(num_atoms))}


def build(csv_root: Path, cache_root: Path) -> dict[str, dict[str, int]]:
    result = {}
    for source_name, split in (("train.csv", "train"), ("val.csv", "val"), ("test.csv", "test")):
        result[split] = build_split(csv_root / source_name, cache_root / split)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build bounded-memory MatterGen caches from HEA CIF CSV files.")
    parser.add_argument("--csv-root", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)
    print(build(args.csv_root, args.cache_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
