#!/usr/bin/env python3
"""Relax MatterGen CIFs with MatterSim and emit per-candidate stability data.

This script deliberately runs inside the dedicated MatterGen micromamba
environment.  The service process itself must not import MatterSim.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, action="append", type=Path, help="CIF path; repeat for each candidate")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--stability-threshold", default=0.05, type=float)
    parser.add_argument(
        "--reference-mode",
        choices=("mp_api", "official"),
        default="mp_api",
        help="mp_api queries only competing phases for each candidate; official loads the full MatterGen reference.",
    )
    return parser.parse_args()


def _patch_mattergen_lmdb_loader() -> None:
    """Avoid a MatterGen v1.0.3 duplicate-LMDB-open incompatibility.

    The upstream loader opens the LMDB and then opens that same file again to
    read metadata.  Recent python-lmdb rejects the second open in one process.
    Reusing the existing read-only environment has identical semantics.
    """
    from mattergen.evaluation.reference.reference_dataset_serializer import (
        LMDBBackedReferenceDatasetImpl,
    )
    from mattergen.evaluation.utils.lmdb_utils import lmdb_get

    def build_index(self, _lmdb_path):
        with self.env.begin() as txn:
            chemical_systems = lmdb_get(txn, "chemical_systems")
            result = {}
            for chemsys in chemical_systems:
                reduced_formulas = lmdb_get(txn, f"{chemsys}.reduced_formulas")
                result[chemsys] = {
                    formula: lmdb_get(txn, f"{chemsys}.{formula}.length")
                    for formula in reduced_formulas
                }
        return result

    LMDBBackedReferenceDatasetImpl._build_num_entries_by_chemsys_reduced_formulas = build_index


def _install_mp_api_pymatgen_compat() -> None:
    """Let older MP API payloads resolve their historical pymatgen module path."""
    import sys
    import types

    from pymatgen.entries.computed_entries import ComputedEntry, ComputedStructureEntry

    module = types.ModuleType("pymatgen.core.entries")
    module.ComputedEntry = ComputedEntry
    module.ComputedStructureEntry = ComputedStructureEntry
    sys.modules.setdefault("pymatgen.core.entries", module)


def _mp_api_reference_results(structures, total_energies, original_structures):
    """Evaluate each candidate against only its MP competing phases.

    This is intentionally a MatterSim--MP hybrid approximation: the generated
    candidate retains its MatterSim energy, while competing phases come from
    MP's DFT entries.  It avoids loading the 846k-entry official reference set
    in an online service.
    """
    from dotenv import load_dotenv
    from mp_api.client import MPRester
    from pymatgen.analysis.phase_diagram import PhaseDiagram

    from mattergen.evaluation.utils.metrics_structure_summary import get_metrics_structure_summaries

    _install_mp_api_pymatgen_compat()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    api_key = os.environ.get("MP_API_KEY") or os.environ.get("MAPI_KEY") or os.environ.get("MP_API_TOKEN")
    if not api_key:
        raise RuntimeError("MP_API_KEY is required for MatterSim MP-reference evaluation.")
    summaries = get_metrics_structure_summaries(
        structures=structures,
        energies=total_energies,
        original_structures=original_structures,
    )
    reference_by_chemsys = {}
    with MPRester(api_key) as mpr:
        for summary in summaries:
            elements = tuple(sorted(element.symbol for element in summary.entry.composition.elements))
            if elements not in reference_by_chemsys:
                reference_by_chemsys[elements] = mpr.get_entries_in_chemsys(list(elements))
    result = []
    for summary in summaries:
        elements = tuple(sorted(element.symbol for element in summary.entry.composition.elements))
        phase_diagram = PhaseDiagram(reference_by_chemsys[elements])
        result.append(
            {
                "formation_energy_per_atom_ev": float(phase_diagram.get_form_energy_per_atom(summary.entry)),
                "energy_above_hull_ev": float(phase_diagram.get_e_above_hull(summary.entry, allow_negative=True)),
                "reference_dataset": "Materials Project competing phases (scoped API query)",
                "method": "MatterSim MLFF candidate energy + Materials Project DFT competing phases; not DFT",
            }
        )
    return result


def main() -> None:
    args = _arguments()
    temporary_root = Path(os.environ.get("MATTERSIM_TMPDIR", "/data/mattersim_tmp"))
    temporary_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(temporary_root)
    cif_paths = sorted(args.input)
    if not cif_paths:
        raise SystemExit("No CIF files supplied")

    from pymatgen.core import Structure

    from mattergen.evaluation.metrics.evaluator import MetricsEvaluator
    from mattergen.evaluation.reference.presets import ReferenceMP2020Correction
    from mattergen.evaluation.utils.relaxation import relax_structures

    _patch_mattergen_lmdb_loader()
    structures = [Structure.from_file(path) for path in cif_paths]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    relaxed_path = args.output_dir / "relaxed_structures.extxyz"
    relaxed, total_energies = relax_structures(
        structures,
        device=args.device,
        output_path=str(relaxed_path),
    )
    relaxation_output = {
        "status": "relaxed",
        "backend": "mattersim",
        "method": "MatterSim MLFF relaxation and energy; not DFT",
        "relaxed_structures": str(relaxed_path.resolve()),
        "candidates": [
            {
                "source_cif": str(path.resolve()),
                "source_name": path.name,
                "formula_pretty": structure.composition.reduced_formula,
                "relaxed_total_energy_ev": float(energy),
                "relaxed_energy_per_atom_ev": float(energy / len(structure)),
            }
            for path, structure, energy in zip(cif_paths, relaxed, total_energies)
        ],
    }
    (args.output_dir / "mattersim_relaxation.json").write_text(
        json.dumps(relaxation_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.reference_mode == "mp_api":
        thermodynamics = _mp_api_reference_results(relaxed, total_energies, structures)
    else:
        reference = ReferenceMP2020Correction()
        evaluator = MetricsEvaluator.from_structures_and_energies(
            structures=relaxed,
            energies=total_energies,
            original_structures=structures,
            reference=reference,
            stability_threshold=args.stability_threshold,
        )
        summaries = evaluator.energy_capability._structure_summaries
        e_above_hull = evaluator.energy_capability.energy_above_hull
        thermodynamics = []
        for index, summary in enumerate(summaries):
            phase_diagram = evaluator.energy_capability._get_phase_diagram(summary.chemical_system)
            thermodynamics.append(
                {
                    "formation_energy_per_atom_ev": float(phase_diagram.get_form_energy_per_atom(summary.entry)),
                    "energy_above_hull_ev": float(e_above_hull[index]),
                    "reference_dataset": "MatterGen MP2020correction (MP + Alexandria)",
                    "method": "MatterSim MLFF relaxation and energy; not DFT",
                }
            )

    candidates = []
    for index, (path, structure, energy, thermo) in enumerate(zip(cif_paths, relaxed, total_energies, thermodynamics)):
        candidates.append(
            {
                "source_cif": str(path.resolve()),
                "source_name": path.name,
                "formula_pretty": structure.composition.reduced_formula,
                "relaxed_total_energy_ev": float(energy),
                "relaxed_energy_per_atom_ev": float(energy / len(structure)),
                "formation_energy_per_atom_ev": thermo["formation_energy_per_atom_ev"],
                "energy_above_hull_ev": thermo["energy_above_hull_ev"],
                "is_stable_at_threshold": bool(thermo["energy_above_hull_ev"] <= args.stability_threshold),
                "reference_dataset": thermo["reference_dataset"],
                "method": thermo["method"],
            }
        )

    output = {
        "status": "ok",
        "backend": "mattersim",
        "stability_threshold_ev_per_atom": args.stability_threshold,
        "relaxed_structures": str(relaxed_path.resolve()),
        "candidates": candidates,
    }
    (args.output_dir / "mattersim_results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
