"""Prepare a traceable, leakage-resistant index for the Cantor HEA DFT data.

The source CSV remains immutable under ``data/raw``.  This command only emits
an index of accepted source rows, so a later MatterGen converter can stream the
large relaxed-structure field from the original file instead of copying it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data/raw/cantor_hea_dft/2023-04-06/hea.2023-04-06.csv"
DEFAULT_OUTPUT = ROOT / "data/processed/hea_mattergen_v1"
EXPECTED_MD5 = "4754d35ac163bb8804ef2f24ace659f7"
REQUIRED_COLUMNS = {
    "fid", "reduced_formula", "chemical_system", "lattice", "nelements",
    "NIONS", "e_per_atom", "Ef_per_atom", "e_above_hull", "structure_as_dict",
}
INDEX_COLUMNS = (
    "fid", "split", "reduced_formula", "chemical_system", "lattice", "nelements",
    "nions", "e_per_atom", "formation_energy_per_atom", "energy_above_hull",
)


def file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _integer(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _split_for_system(chemical_system: str) -> str:
    """Assign a deterministic system-grouped 80/10/10 split."""
    bucket = int(hashlib.sha256(chemical_system.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def prepare(source: Path, output: Path, *, verify_checksum: bool = True) -> dict:
    """Create a compact training index and an auditable source manifest."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"HEA DFT source not found: {source}")
    checksum = file_md5(source)
    if verify_checksum and checksum != EXPECTED_MD5:
        raise ValueError(
            f"Source checksum does not match the published HEA DFT file: {checksum}. "
            "Finish/resume the download before preparing training data, or use --skip-checksum only for a reviewed mirror."
        )

    output.mkdir(parents=True, exist_ok=True)
    index_path = output / "structure_index.csv"
    lattice_counts: Counter[str] = Counter()
    element_count_counts: Counter[int] = Counter()
    split_counts: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    accepted = total = 0

    with source.open("r", encoding="utf-8", newline="") as input_handle:
        reader = csv.DictReader(input_handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"HEA DFT CSV misses required columns: {', '.join(missing)}")
        with index_path.open("w", encoding="utf-8", newline="") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=INDEX_COLUMNS)
            writer.writeheader()
            for row in reader:
                total += 1
                nelements = _integer(row.get("nelements"))
                nions = _integer(row.get("NIONS"))
                chemical_system = (row.get("chemical_system") or "").strip()
                if not row.get("fid") or not chemical_system:
                    rejected["missing_identity"] += 1
                    continue
                if nelements is None or not 2 <= nelements <= 7:
                    rejected["element_count_outside_2_to_7"] += 1
                    continue
                if nions is None or nions <= 0:
                    rejected["invalid_site_count"] += 1
                    continue
                if not (row.get("structure_as_dict") or "").strip():
                    rejected["missing_relaxed_structure"] += 1
                    continue
                split = _split_for_system(chemical_system)
                lattice = (row.get("lattice") or "").strip() or "UNSPECIFIED"
                writer.writerow({
                    "fid": row["fid"], "split": split,
                    "reduced_formula": (row.get("reduced_formula") or "").strip(),
                    "chemical_system": chemical_system, "lattice": lattice,
                    "nelements": nelements, "nions": nions,
                    "e_per_atom": _number(row.get("e_per_atom")),
                    "formation_energy_per_atom": _number(row.get("Ef_per_atom")),
                    "energy_above_hull": _number(row.get("e_above_hull")),
                })
                accepted += 1
                split_counts[split] += 1
                lattice_counts[lattice] += 1
                element_count_counts[nelements] += 1

    manifest = {
        "dataset_id": "cantor_hea_dft_2023_04_06",
        "source": str(source),
        "source_md5": checksum,
        "checksum_verified": verify_checksum,
        "source_rows": total,
        "accepted_rows": accepted,
        "selection": {
            "nelements": "2..7 inclusive",
            "requires": ["fid", "chemical_system", "positive NIONS", "relaxed structure_as_dict"],
            "split_policy": "chemical-system grouped deterministic 80/10/10",
        },
        "split_counts": dict(sorted(split_counts.items())),
        "lattice_counts": dict(sorted(lattice_counts.items())),
        "element_count_counts": {str(key): value for key, value in sorted(element_count_counts.items())},
        "rejected_counts": dict(sorted(rejected.items())),
        "index": str(index_path),
        "next_step": "Convert selected relaxed structures to MatterGen training records; do not use this index alone as a model input.",
    }
    (output / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare HEA DFT structure index for MatterGen fine-tuning.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-checksum", action="store_true", help="Only for a reviewed mirror; normal downloads must retain checksum verification.")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.source, args.output, verify_checksum=not args.skip_checksum), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
