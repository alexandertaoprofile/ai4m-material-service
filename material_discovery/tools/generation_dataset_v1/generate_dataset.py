#!/usr/bin/env python3
"""Build a reproducible, structure-only MatterGen candidate dataset.

This tool intentionally stops after generation, lightweight structural checks,
normalisation, de-duplication and reporting.  It never imports or invokes
ALIGNN, MatterSim, CALPHAD, DFT, phonons, MD, or the web-service pipeline.
Run it inside the dedicated MatterGen Python environment.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Iterable


TARGET_HEA_ELEMENTS = {"Co", "Cr", "Fe", "Mn", "Ni"}
CERAMIC_ELEMENTS = {"Al", "Si", "B", "N", "O"}
MIN_COVALENT_DISTANCE_RATIO = 0.75


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_elements(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        symbol = str(value).strip().capitalize()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def parse_chemical_system(value: str) -> list[str]:
    elements = canonical_elements(value.split("-"))
    if not elements:
        raise ValueError("chemical system must contain at least one element")
    return elements


def parse_checkpoint_epoch(value: str) -> str | int:
    """Keep MatterGen's symbolic epochs, but pass numeric epochs as integers."""
    value = value.strip()
    return int(value) if value.isdigit() else value


def is_equiatomic(fractions: dict[str, float], *, tolerance: float = 1e-6) -> bool:
    return bool(fractions) and max(fractions.values()) - min(fractions.values()) <= tolerance


def distance_from_equiatomic(fractions: dict[str, float]) -> float | None:
    if not fractions:
        return None
    ideal = 1.0 / len(fractions)
    return float(sum(abs(value - ideal) for value in fractions.values()) / 2.0)


def stable_digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def composition_hash(fractions: dict[str, float]) -> str:
    return stable_digest({element: round(fractions[element], 8) for element in sorted(fractions)})


def structure_hash(structure) -> str:
    """A deterministic audit fingerprint, not a symmetry-aware duplicate test."""
    sites = sorted(
        (site.specie.symbol, *(round(float(value) % 1.0, 6) for value in site.frac_coords))
        for site in structure
    )
    return stable_digest({
        "lattice": [[round(float(value), 6) for value in row] for row in structure.lattice.matrix],
        "sites": sites,
    })


def classify_ceramic(elements: set[str]) -> str:
    if {"Si", "Al", "O", "N"}.issubset(elements):
        return "SiAlON_related"
    if "N" in elements and "O" in elements:
        return "oxynitride"
    if "Al" in elements and "N" in elements:
        return "AlN_related"
    if "B" in elements and "N" in elements:
        return "BN_related"
    if "Si" in elements and "N" in elements:
        return "SiN_related"
    if "N" in elements:
        return "mixed_nitride"
    return "other"


def check_geometry(structure) -> tuple[bool, float | None, list[dict[str, Any]]]:
    from pymatgen.analysis.local_env import CovalentRadius

    if structure.volume <= 0 or len(structure) == 0:
        return False, None, [{"reason": "non_positive_volume_or_empty_structure"}]
    distances = structure.distance_matrix
    nonzero = distances[distances > 1e-8]
    minimum = float(nonzero.min()) if len(nonzero) else None
    violations: list[dict[str, Any]] = []
    for first in range(len(structure)):
        for second in range(first + 1, len(structure)):
            one, two = structure[first].specie.symbol, structure[second].specie.symbol
            radius_one, radius_two = CovalentRadius.radius.get(one), CovalentRadius.radius.get(two)
            if radius_one is None or radius_two is None:
                continue
            distance = float(structure.get_distance(first, second))
            lower = MIN_COVALENT_DISTANCE_RATIO * (radius_one + radius_two)
            if distance < lower:
                violations.append({
                    "elements": [one, two],
                    "distance_angstrom": distance,
                    "minimum_allowed_angstrom": lower,
                })
    return not violations, minimum, violations


def build_record(
    structure,
    *,
    candidate_id: str,
    scenario: str,
    model_type: str,
    checkpoint: str,
    seed: int,
    cif_path: Path,
    timestamp: str,
) -> dict[str, Any]:
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    elements = sorted({site.specie.symbol for site in structure})
    fractions = {element: float(structure.composition.get_atomic_fraction(element)) for element in elements}
    geometry_valid, minimum_distance, close_pairs = check_geometry(structure)
    try:
        analyzer = SpacegroupAnalyzer(structure, symprec=0.1, angle_tolerance=5)
        space_group = {"symbol": analyzer.get_space_group_symbol(), "number": analyzer.get_space_group_number()}
    except Exception as exc:  # symmetry is optional metadata, not a parse failure
        space_group = {"symbol": None, "number": None, "error": str(exc)}
    exact = set(elements) == TARGET_HEA_ELEMENTS
    scenario_flags: dict[str, Any]
    if scenario == "aerospace_alloy":
        scenario_flags = {
            "exact_target_elements": exact,
            "contains_all_target_elements": TARGET_HEA_ELEMENTS.issubset(elements),
            "has_extra_elements": bool(set(elements) - TARGET_HEA_ELEMENTS),
            "is_equiatomic": is_equiatomic(fractions),
            "distance_from_equiatomic": distance_from_equiatomic(fractions),
        }
    else:
        scenario_flags = {
            "contains_N": "N" in elements,
            "contains_O": "O" in elements,
            "contains_Al": "Al" in elements,
            "contains_Si": "Si" in elements,
            "contains_B": "B" in elements,
            "elements_within_requested_space": set(elements).issubset(CERAMIC_ELEMENTS),
            "material_family": classify_ceramic(set(elements)),
        }
    return {
        "candidate_id": candidate_id,
        "scenario": scenario,
        "generator": "mattergen",
        "model_type": model_type,
        "checkpoint": checkpoint,
        "seed": seed,
        "composition": {
            "formula": structure.composition.formula,
            "reduced_formula": structure.composition.reduced_formula,
            "elements": elements,
            "atomic_fractions": fractions,
            "num_elements": len(elements),
        },
        "structure": {
            "cif_path": str(cif_path),
            "num_atoms": len(structure),
            "lattice_parameters": {
                "a": float(structure.lattice.a), "b": float(structure.lattice.b), "c": float(structure.lattice.c),
                "alpha": float(structure.lattice.alpha), "beta": float(structure.lattice.beta), "gamma": float(structure.lattice.gamma),
            },
            "volume": float(structure.volume),
            "density": float(structure.density),
            "space_group": space_group,
            "structure_hash": structure_hash(structure),
        },
        "validation": {
            "parse_success": True,
            "composition_valid": bool(elements),
            "geometry_valid": geometry_valid,
            "valid_structure": bool(structure.is_ordered and geometry_valid),
            "ordered": bool(structure.is_ordered),
            "minimum_interatomic_distance": minimum_distance,
            "close_pair_violations": close_pairs,
        },
        "scenario_flags": scenario_flags,
        "duplicate": {
            "composition_hash": composition_hash(fractions),
            "structure_hash": structure_hash(structure),
            "is_composition_duplicate": False,
            "is_structure_duplicate": False,
            "unique_candidate": False,
        },
        "generation": {"timestamp": timestamp, "model_version": checkpoint},
        "predictions": {},
    }


def mark_duplicates(records: list[dict[str, Any]], structures: list[Any]) -> None:
    """Mark same-composition and same-structure entries without dropping records."""
    from pymatgen.analysis.structure_matcher import StructureMatcher

    by_composition: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        by_composition[record["duplicate"]["composition_hash"]].append(index)
    matcher = StructureMatcher(ltol=0.2, stol=0.3, angle_tol=5)
    for indexes in by_composition.values():
        representatives: list[int] = []
        for index in indexes:
            duplicate = records[index]["duplicate"]
            duplicate["is_composition_duplicate"] = bool(representatives)
            duplicate["is_structure_duplicate"] = any(matcher.fit(structures[index], structures[prior]) for prior in representatives)
            if not duplicate["is_structure_duplicate"]:
                representatives.append(index)
            duplicate["unique_candidate"] = bool(
                records[index]["validation"]["valid_structure"] and not duplicate["is_structure_duplicate"]
            )


def write_records(records: list[dict[str, Any]], output_dir: Path) -> None:
    processed = output_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    with (processed / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    rows = []
    for record in records:
        flags, validation, duplicate = record["scenario_flags"], record["validation"], record["duplicate"]
        rows.append({
            "candidate_id": record["candidate_id"],
            "scenario": record["scenario"],
            "formula": record["composition"]["formula"],
            "reduced_formula": record["composition"]["reduced_formula"],
            "elements": "-".join(record["composition"]["elements"]),
            "num_atoms": record["structure"]["num_atoms"],
            "density_g_cm3": record["structure"]["density"],
            "space_group": record["structure"]["space_group"].get("symbol"),
            "valid_structure": validation["valid_structure"],
            "minimum_interatomic_distance": validation["minimum_interatomic_distance"],
            "composition_hash": duplicate["composition_hash"],
            "structure_hash": duplicate["structure_hash"],
            "is_composition_duplicate": duplicate["is_composition_duplicate"],
            "is_structure_duplicate": duplicate["is_structure_duplicate"],
            "unique_candidate": duplicate["unique_candidate"],
            **flags,
        })
    fields = sorted({key for row in rows for key in row})
    with (processed / "candidates.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def write_summary(records: list[dict[str, Any]], output_dir: Path, manifest: dict[str, Any]) -> None:
    summary_dir = output_dir / "summary"; summary_dir.mkdir(parents=True, exist_ok=True)
    valid = [item for item in records if item["validation"]["valid_structure"]]
    unique = [item for item in records if item["duplicate"]["unique_candidate"]]
    payload: dict[str, Any] = {
        "scenario": manifest["scenario"], "model_type": manifest["model_type"],
        "total_generated": len(records), "parse_success": len(records),
        "valid_structure": len(valid), "invalid_structure": len(records) - len(valid),
        "unique_structures": len(unique),
        "structure_uniqueness_rate": len(unique) / len(records) if records else 0.0,
        "unique_compositions": len({item["duplicate"]["composition_hash"] for item in records}),
        "element_count_distribution": dict(sorted(Counter(item["composition"]["num_elements"] for item in records).items())),
        "space_group_distribution": dict(Counter(item["structure"]["space_group"].get("symbol") or "unresolved" for item in records)),
        "num_atoms_distribution": dict(sorted(Counter(item["structure"]["num_atoms"] for item in records).items())),
        "density_distribution": [item["structure"]["density"] for item in records],
    }
    if manifest["scenario"] == "aerospace_alloy":
        exact = [item for item in records if item["scenario_flags"]["exact_target_elements"]]
        exact_equiatomic = [item for item in exact if item["scenario_flags"]["is_equiatomic"]]
        payload.update({
            "exact_target_elements": len(exact),
            "exact_target_hit_rate": len(exact) / len(records) if records else 0.0,
            "contains_all_target_elements": sum(item["scenario_flags"]["contains_all_target_elements"] for item in records),
            "equiatomic_count": sum(item["scenario_flags"]["is_equiatomic"] for item in records),
            "exact_target_equiatomic_count": len(exact_equiatomic),
            "exact_target_equiatomic_rate": len(exact_equiatomic) / len(records) if records else 0.0,
            "composition_distribution": dict(Counter(item["composition"]["reduced_formula"] for item in records)),
        })
    else:
        payload["material_families"] = dict(Counter(item["scenario_flags"]["material_family"] for item in records))
    (summary_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (summary_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle); writer.writerow(["metric", "value"])
        for key, value in payload.items(): writer.writerow([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    lines = [f"# {manifest['scenario']} Generation Dataset v1", "", f"- Model: `{manifest['model_type']}`", f"- Checkpoint: `{manifest['checkpoint']}`", f"- Seed: `{manifest['seed']}`", f"- Generated: {payload['total_generated']}", f"- Valid structures: {payload['valid_structure']}", f"- Unique structures: {payload['unique_structures']}"]
    if manifest["scenario"] == "aerospace_alloy":
        lines.append(f"- Exact Co-Cr-Fe-Mn-Ni: {payload['exact_target_elements']} ({payload['exact_target_hit_rate']:.1%})")
        lines.append(f"- Exact five-element and equiatomic: {payload['exact_target_equiatomic_count']} ({payload['exact_target_equiatomic_rate']:.1%})")
    else: lines.append(f"- Material families: `{json.dumps(payload['material_families'], ensure_ascii=False)}`")
    lines.extend(["", "This dataset contains periodic atomic-structure candidates only. No property predictor, thermodynamic evaluator, CALPHAD, DFT, SQS, MD, or phonon calculation was run."])
    (summary_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("aerospace_alloy", "chip_packaging"), required=True)
    parser.add_argument("--model-type", required=True)
    parser.add_argument("--model-path", type=Path, required=True, help="Local MatterGen checkpoint directory; no network download is used.")
    parser.add_argument("--checkpoint-epoch", default="last", type=parse_checkpoint_epoch)
    parser.add_argument("--chemical-system", required=True)
    parser.add_argument("--energy-above-hull", type=float, required=True)
    parser.add_argument("--guidance-scale", type=float, default=0.0)
    parser.add_argument("--num-samples", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    if args.num_samples < 1 or args.batch_size < 1 or args.num_samples % args.batch_size:
        raise ValueError("num-samples must be positive and divisible by batch-size for an exact candidate count")
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to reuse an existing output directory: {args.output_dir}")
    if args.energy_above_hull < 0:
        raise ValueError("energy-above-hull must be non-negative")
    if not (args.model_path / "config.yaml").is_file():
        raise FileNotFoundError(f"Missing MatterGen config: {args.model_path / 'config.yaml'}")
    checkpoint = args.model_path / "checkpoints"
    if args.checkpoint_epoch == "last" and not (checkpoint / "last.ckpt").is_file():
        raise FileNotFoundError(f"Missing last checkpoint under {checkpoint}")
    if args.checkpoint_epoch != "last" and not any(checkpoint.glob(f"epoch={args.checkpoint_epoch}-*.ckpt")):
        raise FileNotFoundError(f"Missing requested checkpoint epoch {args.checkpoint_epoch} under {checkpoint}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir, cif_dir = args.output_dir / "raw", args.output_dir / "cif"
    raw_dir.mkdir(); cif_dir.mkdir()
    elements = parse_chemical_system(args.chemical_system)
    if args.scenario == "aerospace_alloy" and set(elements) != TARGET_HEA_ELEMENTS:
        raise ValueError("aerospace_alloy v1 is restricted to Co-Cr-Fe-Mn-Ni")
    if args.scenario == "chip_packaging" and not set(elements).issubset(CERAMIC_ELEMENTS):
        raise ValueError("chip_packaging v1 accepts only the Al-Si-B-N-O chemical space")
    manifest = {
        "dataset_version": "generation_dataset_v1", "scenario": args.scenario, "model_type": args.model_type,
        "checkpoint": str(args.model_path), "checkpoint_epoch": args.checkpoint_epoch,
        "chemical_system": elements, "energy_above_hull": args.energy_above_hull,
        "guidance_scale": args.guidance_scale, "num_samples": args.num_samples, "batch_size": args.batch_size,
        "seed": args.seed, "started_at": utc_now(), "predictions": {},
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    import numpy as np
    import torch
    from mattergen.common.utils.data_classes import MatterGenCheckpointInfo
    from mattergen.generator import CrystalGenerator
    from pymatgen.io.cif import CifWriter

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(args.seed)
    generator = CrystalGenerator(
        checkpoint_info=MatterGenCheckpointInfo(args.model_path.resolve(), args.checkpoint_epoch),
        properties_to_condition_on={"chemical_system": elements, "energy_above_hull": args.energy_above_hull},
        batch_size=args.batch_size, num_batches=args.num_samples // args.batch_size,
        diffusion_guidance_factor=args.guidance_scale, record_trajectories=False,
    )
    structures = generator.generate(output_dir=str(raw_dir))
    timestamp = utc_now(); records: list[dict[str, Any]] = []
    for index, structure in enumerate(structures, start=1):
        candidate_id = f"{args.scenario}-{index:06d}"
        cif_path = cif_dir / f"{candidate_id}.cif"
        CifWriter(structure).write_file(cif_path)
        records.append(build_record(structure, candidate_id=candidate_id, scenario=args.scenario, model_type=args.model_type, checkpoint=str(args.model_path), seed=args.seed, cif_path=cif_path.resolve(), timestamp=timestamp))
    mark_duplicates(records, structures)
    manifest.update({"completed_at": utc_now(), "generated_count": len(records)})
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_records(records, args.output_dir); write_summary(records, args.output_dir, manifest)


if __name__ == "__main__":
    run(parse_args())
