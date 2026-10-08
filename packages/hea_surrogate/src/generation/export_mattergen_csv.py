"""Export audited HEA DFT structures into MatterGen's CIF-CSV input format."""
from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from .prepare_hea_dft import DEFAULT_OUTPUT, DEFAULT_SOURCE, EXPECTED_MD5, file_md5


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPORT = DEFAULT_OUTPUT / "mattergen_csv_5plus_32"


def _selected(index: Path, *, min_elements: int, max_sites: int) -> dict[str, tuple[str, float]]:
    selected: dict[str, tuple[str, float]] = {}
    with index.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            energy_above_hull = row.get("energy_above_hull")
            if int(row["nelements"]) >= min_elements and int(row["nions"]) <= max_sites and energy_above_hull not in (None, ""):
                selected[row["fid"]] = (row["split"], float(energy_above_hull))
    return selected


def export(
    source: Path,
    index: Path,
    output: Path,
    *,
    min_elements: int = 5,
    max_sites: int = 32,
) -> dict:
    """Write ``train.csv``, ``val.csv`` and ``test.csv`` with valid relaxed CIFs."""
    if file_md5(source) != EXPECTED_MD5:
        raise ValueError("Source checksum mismatch; refuse to convert an incomplete or unreviewed HEA DFT CSV.")
    selected = _selected(index, min_elements=min_elements, max_sites=max_sites)
    if not selected:
        raise ValueError("No selected structures; check min-elements/max-sites against the prepared index.")

    # MatterGen's CrystalDataset.from_csv expects these precise split names.
    output.mkdir(parents=True, exist_ok=True)
    paths = {"train": output / "train.csv", "validation": output / "val.csv", "test": output / "test.csv"}
    handles = {name: path.open("w", encoding="utf-8", newline="") for name, path in paths.items()}
    writers = {name: csv.DictWriter(handle, fieldnames=["material_id", "cif", "energy_above_hull"]) for name, handle in handles.items()}
    for writer in writers.values():
        writer.writeheader()

    # Keep pymatgen optional at import time: the regular HEA tabular service
    # does not need this dependency, while the MatterGen environment has it.
    from pymatgen.core import Structure
    from pymatgen.io.cif import CifWriter

    converted: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    try:
        with source.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                fid = row.get("fid")
                selected_row = selected.get(fid or "")
                if selected_row is None:
                    continue
                split, energy_above_hull = selected_row
                try:
                    raw_structure = ast.literal_eval(row["structure_as_dict"])
                    structure = Structure.from_dict(raw_structure)
                    if len(structure) > max_sites:
                        rejected["site_count_changed_after_parse"] += 1
                        continue
                    writers[split].writerow({
                        "material_id": fid,
                        "cif": str(CifWriter(structure)),
                        "energy_above_hull": energy_above_hull,
                    })
                    converted[split] += 1
                except Exception as exc:  # Keep the source id and reason in the audit, never silently omit it.
                    rejected[type(exc).__name__] += 1
    finally:
        for handle in handles.values():
            handle.close()

    manifest = {
        "dataset_id": "cantor_hea_dft_2023_04_06_mattergen_csv",
        "source": str(source.resolve()),
        "source_md5": file_md5(source),
        "index": str(index.resolve()),
        "selection": {"min_elements": min_elements, "max_sites": max_sites},
        "output_files": {name: str(path) for name, path in paths.items()},
        "converted_counts": dict(sorted(converted.items())),
        "rejected_counts": dict(sorted(rejected.items())),
        "format": "MatterGen CrystalDataset.from_csv: material_id,cif,energy_above_hull",
    }
    (output / "export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export selected relaxed HEA structures to MatterGen CIF CSV files.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--index", type=Path, default=DEFAULT_OUTPUT / "structure_index.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--min-elements", type=int, default=5)
    parser.add_argument("--max-sites", type=int, default=32)
    args = parser.parse_args(argv)
    print(json.dumps(export(args.source, args.index, args.output, min_elements=args.min_elements, max_sites=args.max_sites), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
