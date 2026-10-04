#!/usr/bin/env python3
"""Relax MatterGen CIFs with MatterSim and emit per-candidate stability data.

This script deliberately runs inside the dedicated MatterGen micromamba
environment.  The service process itself must not import MatterSim.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import itertools
import json
import os
from pathlib import Path
import pickle
import shutil
import tempfile


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
    parser.add_argument(
        "--reference-system",
        default="",
        help=(
            "Optional parent chemical system for the official convex-hull cache, "
            "for example Co-Cr-Fe-Mn-Ni. Candidate elements must be a subset."
        ),
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


def _load_cached_mp2020_reference():
    """Open the official MP2020 reference without unpacking it for every task.

    MatterGen distributes this reference as an 833 MB gzip-compressed LMDB.
    Its preset loader expands it to roughly 3.85 GB in a disposable directory
    on each process invocation.  The service starts a fresh evaluator process
    per request, so retain one read-only expanded LMDB under a service-owned
    cache directory instead.  A file lock ensures concurrent first requests
    prepare that cache only once.
    """
    from mattergen.evaluation.reference import presets
    from mattergen.evaluation.reference.reference_dataset import ReferenceDataset
    from mattergen.evaluation.reference.reference_dataset_serializer import (
        LMDBBackedReferenceDatasetImpl,
    )

    cache_dir = Path(
        os.environ.get("MATTERSIM_REFERENCE_CACHE_DIR", "/data/mattersim_reference_cache")
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "reference_MP2020correction"
    reference_gzip = (
        Path(presets.__file__).resolve().parent
        / "../../../data-release/alex-mp/reference_MP2020correction.gz"
    ).resolve()
    if not reference_gzip.is_file():
        raise RuntimeError(f"MatterGen MP2020 reference archive is missing: {reference_gzip}")

    with (cache_dir / ".reference_MP2020correction.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not cache_file.is_file():
            staging_dir = Path(tempfile.mkdtemp(prefix="mp2020-", dir=cache_dir))
            staging_file = staging_dir / cache_file.name
            try:
                with gzip.open(reference_gzip, "rb") as source, staging_file.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                os.replace(staging_file, cache_file)
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return ReferenceDataset(
        name="MP2020correction",
        impl=LMDBBackedReferenceDatasetImpl(cache_file, cleanup_dir=False),
    )


def _canonical_chemical_system(chemical_system: str) -> str:
    """Return the reference-LMDB spelling for a hyphen-separated element set."""
    elements = [item.strip().capitalize() for item in chemical_system.split("-") if item.strip()]
    if not elements:
        raise ValueError("A chemical system must contain at least one element.")
    return "-".join(sorted(set(elements)))


def _load_cached_phase_diagram(reference, chemical_system: str):
    """Load or build a service-owned official phase diagram for one system.

    The expensive Qhull construction depends only on the reference dataset and
    chemical system, never on a generated candidate.  Persisting it makes
    repeated candidate evaluations use the same MP2020-corrected convex hull.
    """
    from mattergen.evaluation.utils.utils import expand_into_subsystems
    from pymatgen.analysis.phase_diagram import PhaseDiagram

    chemical_system = _canonical_chemical_system(chemical_system)
    cache_root = Path(
        os.environ.get("MATTERSIM_PHASE_DIAGRAM_CACHE_DIR", "/data/mattersim_phase_diagram_cache")
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_file = cache_root / f"MP2020correction__{chemical_system}.pickle"
    with (cache_root / f".{chemical_system}.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if cache_file.is_file():
            with cache_file.open("rb") as handle:
                payload = pickle.load(handle)
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == 1
                and payload.get("reference") == "MP2020correction"
                and payload.get("chemical_system") == chemical_system
                and payload.get("phase_diagram") is not None
            ):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                return payload["phase_diagram"]
            raise RuntimeError(f"Invalid cached MP2020 phase diagram: {cache_file}")

        subsystems = expand_into_subsystems(chemical_system)
        reference_entries = [
            entry
            for subsystem in subsystems
            for key in ["-".join(sorted(subsystem))]
            for entry in reference.entries_by_chemsys.get(key, [])
            if entry.energy == entry.energy  # skip NaN-energy disordered entries
        ]
        if not reference_entries:
            raise RuntimeError(f"No MP2020 reference entries found for {chemical_system}.")
        phase_diagram = PhaseDiagram(reference_entries)
        staging_file = cache_root / f".{chemical_system}.{os.getpid()}.pickle"
        try:
            with staging_file.open("wb") as handle:
                pickle.dump(
                    {
                        "schema_version": 1,
                        "reference": "MP2020correction",
                        "chemical_system": chemical_system,
                        "phase_diagram": phase_diagram,
                    },
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            os.replace(staging_file, cache_file)
        finally:
            staging_file.unlink(missing_ok=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return phase_diagram


def _official_reference_results(reference, relaxed, total_energies, original_structures, reference_system: str):
    """Evaluate candidates against persistent MP2020-corrected hulls."""
    from pymatgen.io.ase import AseAtomsAdaptor

    from mattergen.evaluation.utils.metrics_structure_summary import get_metrics_structure_summaries

    pymatgen_structures = [
        structure if hasattr(structure, "composition") else AseAtomsAdaptor.get_structure(structure)
        for structure in relaxed
    ]
    summaries = get_metrics_structure_summaries(
        structures=pymatgen_structures,
        energies=total_energies,
        original_structures=original_structures,
    )
    parent_system = _canonical_chemical_system(reference_system) if reference_system else ""
    result = []
    for summary in summaries:
        candidate_system = _canonical_chemical_system(
            "-".join(element.symbol for element in summary.entry.composition.elements)
        )
        hull_system = parent_system or candidate_system
        if not set(candidate_system.split("-")).issubset(hull_system.split("-")):
            raise RuntimeError(
                f"Candidate system {candidate_system} is not a subset of requested hull {hull_system}."
            )
        phase_diagram = _load_cached_phase_diagram(reference, hull_system)
        result.append(
            {
                "formation_energy_per_atom_ev": float(phase_diagram.get_form_energy_per_atom(summary.entry)),
                "energy_above_hull_ev": float(phase_diagram.get_e_above_hull(summary.entry, allow_negative=True)),
                "reference_dataset": "MatterGen MP2020correction (MP + Alexandria)",
                "reference_chemical_system": hull_system,
                "method": "MatterSim MLFF relaxation and energy; not DFT",
            }
        )
    return result


def _install_mp_api_pymatgen_compat() -> None:
    """Let older MP API payloads resolve historical pymatgen module paths.

    Older serialized MP entries reference modules that were moved in recent
    pymatgen releases.  ``MontyDecoder`` imports the recorded module name, so
    aliases must be installed before ``MPRester.get_entries_in_chemsys``.
    """
    import sys
    import types

    from pymatgen.entries.computed_entries import ComputedEntry, ComputedStructureEntry
    from pymatgen.entries import compatibility as entries_compatibility

    module = types.ModuleType("pymatgen.core.entries")
    module.ComputedEntry = ComputedEntry
    module.ComputedStructureEntry = ComputedStructureEntry
    sys.modules.setdefault("pymatgen.core.entries", module)
    # ``pymatgen.analysis.compatibility`` was moved to
    # ``pymatgen.entries.compatibility``.  MP payloads created with the old
    # path still occur in the API response.
    sys.modules.setdefault("pymatgen.analysis.compatibility", entries_compatibility)


def _mp_api_reference_results(structures, total_energies, original_structures):
    """Evaluate each candidate against only its MP competing phases.

    This is intentionally a MatterSim--MP hybrid approximation: the generated
    candidate retains its MatterSim energy, while competing phases come from
    MP's DFT entries.  It avoids loading the 846k-entry official reference set
    in an online service.
    """
    from dotenv import load_dotenv
    from mp_api.client import MPRester
    from pymatgen.core import Composition
    from pymatgen.entries.computed_entries import ComputedEntry
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.analysis.phase_diagram import PhaseDiagram
    from pymatgen.analysis.structure_matcher import StructureMatcher
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    from mattergen.evaluation.utils.metrics_structure_summary import get_metrics_structure_summaries

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    api_key = os.environ.get("MP_API_KEY") or os.environ.get("MAPI_KEY") or os.environ.get("MP_API_TOKEN")
    if not api_key:
        raise RuntimeError("MP_API_KEY is required for MatterSim MP-reference evaluation.")
    # MatterSim relaxation returns ASE Atoms, while MatterGen's metrics helper
    # expects pymatgen Structures.  Convert here so both the phase-diagram
    # calculation and the subsequent crystallographic traceability use the
    # relaxed structure consistently.
    pymatgen_structures = [
        structure if hasattr(structure, "composition") else AseAtomsAdaptor.get_structure(structure)
        for structure in structures
    ]
    summaries = get_metrics_structure_summaries(
        structures=pymatgen_structures,
        energies=total_energies,
        original_structures=original_structures,
    )
    reference_by_chemsys = {}
    # Do not use ``get_entries_in_chemsys`` here.  The deployed mp-api and
    # pymatgen versions disagree on the serialized ``energy_adjustments``
    # type (dict vs EnergyAdjustment), causing that convenience method to
    # fail while decoding otherwise valid MP responses.  The thermo endpoint
    # provides the exact fields required to construct PhaseDiagram entries.
    with MPRester(
        api_key,
        monty_decode=False,
        use_document_model=False,
        mute_progress_bars=True,
    ) as mpr:
        for summary in summaries:
            elements = tuple(sorted(element.symbol for element in summary.entry.composition.elements))
            if elements not in reference_by_chemsys:
                records = mpr.materials.thermo.search(
                    # ``thermo.search`` treats one chemsys string as an exact
                    # element set.  A phase diagram needs entries from every
                    # non-empty subset (elements and binaries included), as
                    # ``get_entries_in_chemsys`` used to provide.
                    chemsys=[
                        "-".join(subset)
                        for size in range(1, len(elements) + 1)
                        for subset in itertools.combinations(elements, size)
                    ],
                    all_fields=False,
                    fields=["material_id", "composition", "energy_per_atom"],
                )
                entries = []
                for record in records:
                    if not isinstance(record, dict):
                        record = record.model_dump()
                    composition = record.get("composition")
                    energy_per_atom = record.get("energy_per_atom")
                    if not composition or energy_per_atom is None:
                        continue
                    parsed_composition = Composition(composition)
                    entries.append(ComputedEntry(
                        composition=parsed_composition,
                        energy=float(energy_per_atom) * parsed_composition.num_atoms,
                        entry_id=record.get("material_id"),
                    ))
                if not entries:
                    raise RuntimeError(
                        f"Materials Project returned no usable thermo entries for {'-'.join(elements)}."
                    )
                reference_by_chemsys[elements] = entries
    def _structure_fingerprint(structure):
        try:
            analyzer = SpacegroupAnalyzer(structure, symprec=0.1, angle_tolerance=5)
            return {
                "formula_pretty": structure.composition.reduced_formula,
                "space_group_symbol": analyzer.get_space_group_symbol(),
                "space_group_number": analyzer.get_space_group_number(),
                "crystal_system": analyzer.get_crystal_system(),
                "sites": len(structure),
            }
        except Exception as exc:
            return {"formula_pretty": structure.composition.reduced_formula, "sites": len(structure), "error": str(exc)}

    def _traceability(structure, phase_diagram):
        """Return truthful preparation-oriented references, never a synthetic route."""
        def _material_id(entry):
            raw = getattr(entry, "entry_id", "")
            if isinstance(raw, dict):
                return str(raw.get("identifier") or "")
            identifier = getattr(raw, "identifier", None)
            return str(identifier if identifier is not None else raw or "")

        fingerprint = _structure_fingerprint(structure)
        candidate_formula = structure.composition.reduced_formula
        matcher = StructureMatcher(ltol=0.2, stol=0.3, angle_tol=5)
        prototype_match = None
        for entry in phase_diagram.all_entries:
            if entry.composition.reduced_formula != candidate_formula:
                continue
            reference_structure = getattr(entry, "structure", None)
            if reference_structure is None:
                continue
            try:
                if matcher.fit(structure, reference_structure):
                    prototype_match = {
                        "material_id": _material_id(entry),
                        "formula_pretty": candidate_formula,
                        "match_method": "StructureMatcher: same reduced composition and lattice/coordination match",
                    }
                    break
            except Exception:
                continue
        stable_phases = []
        for entry in phase_diagram.stable_entries:
            stable_phases.append({
                "material_id": _material_id(entry),
                "formula_pretty": entry.composition.reduced_formula,
                "energy_per_atom_ev": float(entry.energy_per_atom),
            })
        stable_phases.sort(key=lambda item: (len(item["formula_pretty"]), item["formula_pretty"], item["material_id"]))
        return {
            "candidate_crystallography": fingerprint,
            "prototype_match": prototype_match,
            "same_system_stable_phases": stable_phases[:12],
            "scope_note": (
                "Prototype matching uses Materials Project entries returned for the same element system. "
                "Stable phases are thermodynamic competitors, not a proposed precursor or synthesis pathway."
            ),
        }

    result = []
    for structure, summary in zip(pymatgen_structures, summaries):
        elements = tuple(sorted(element.symbol for element in summary.entry.composition.elements))
        phase_diagram = PhaseDiagram(reference_by_chemsys[elements])
        result.append(
            {
                "formation_energy_per_atom_ev": float(phase_diagram.get_form_energy_per_atom(summary.entry)),
                "energy_above_hull_ev": float(phase_diagram.get_e_above_hull(summary.entry, allow_negative=True)),
                "reference_dataset": "Materials Project competing phases (scoped API query)",
                "method": "MatterSim MLFF candidate energy + Materials Project DFT competing phases; not DFT",
                "preparation_traceability": _traceability(structure, phase_diagram),
            }
        )
    return result


def _write_relaxed_structure(path: Path, structure) -> None:
    """Persist one relaxed structure, accepting either ASE or pymatgen data."""
    from ase.io import write as ase_write

    if hasattr(structure, "lattice") and hasattr(structure, "composition"):
        from pymatgen.io.ase import AseAtomsAdaptor

        structure = AseAtomsAdaptor.get_atoms(structure)
    ase_write(path, structure, format="extxyz")


def main() -> None:
    args = _arguments()
    temporary_root = Path(os.environ.get("MATTERSIM_TMPDIR", "/data/mattersim_tmp"))
    temporary_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(temporary_root)
    cif_paths = sorted(args.input)
    if not cif_paths:
        raise SystemExit("No CIF files supplied")

    from pymatgen.core import Structure

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
    # ``relaxed_structures.extxyz`` is a multi-frame convenience artifact.  It
    # must never be used to identify a candidate: reading its final frame for
    # every candidate can attach another candidate's geometry to a GLB.  Keep
    # one immutable relaxed structure per source CIF for downstream rendering.
    relaxed_by_source = {}
    for path, relaxed_structure in zip(cif_paths, relaxed):
        candidate_path = args.output_dir / f"{path.stem}.relaxed.extxyz"
        _write_relaxed_structure(candidate_path, relaxed_structure)
        relaxed_by_source[str(path.resolve())] = str(candidate_path.resolve())
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
                "relaxed_structure_path": relaxed_by_source[str(path.resolve())],
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
        reference = _load_cached_mp2020_reference()
        thermodynamics = _official_reference_results(
            reference,
            relaxed,
            total_energies,
            structures,
            args.reference_system,
        )

    candidates = []
    for index, (path, structure, energy, thermo) in enumerate(zip(cif_paths, relaxed, total_energies, thermodynamics)):
        candidates.append(
            {
                "source_cif": str(path.resolve()),
                "source_name": path.name,
                "formula_pretty": structure.composition.reduced_formula,
                "relaxed_structure_path": relaxed_by_source[str(path.resolve())],
                "relaxed_total_energy_ev": float(energy),
                "relaxed_energy_per_atom_ev": float(energy / len(structure)),
                "formation_energy_per_atom_ev": thermo["formation_energy_per_atom_ev"],
                "energy_above_hull_ev": thermo["energy_above_hull_ev"],
                "is_stable_at_threshold": bool(thermo["energy_above_hull_ev"] <= args.stability_threshold),
                "reference_dataset": thermo["reference_dataset"],
                "reference_chemical_system": thermo.get("reference_chemical_system"),
                "method": thermo["method"],
                "preparation_traceability": thermo.get("preparation_traceability"),
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
