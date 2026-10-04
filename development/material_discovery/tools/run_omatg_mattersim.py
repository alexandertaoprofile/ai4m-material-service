"""Bounded batch MatterSim relaxation and independent MP-reference screening."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, action="append", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--reference-system", default="")
    parser.add_argument("--reference-mode", default="official")
    args = parser.parse_args()
    import numpy as np
    from ase.filters import ExpCellFilter
    from ase.io import write
    from mattersim.applications.batch_relax import BatchRelaxer, DummyBatchCalculator
    from mattersim.forcefield.potential import Potential
    from pymatgen.core import Structure
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.analysis.structure_matcher import StructureMatcher
    from crystal_checks import check_structure
    from mp2020_reference import phase_diagram, corrected_entry

    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Path(os.environ["MATTERSIM_CHECKPOINT"])
    if not checkpoint.is_file():
        raise ValueError("MatterSim local checkpoint missing; automatic download disabled")
    potential = Potential.from_checkpoint(load_path=str(checkpoint), device="cuda", load_training_state=False)
    fmax = float(os.getenv("OMATG_RELAX_FMAX", "0.05"))
    max_steps = int(os.getenv("OMATG_RELAX_MAX_STEPS", "500"))
    class BoundedRelaxer(BatchRelaxer):
        steps = 0
        def step_batch(self):
            super().step_batch()
            self.steps += 1
            if self.steps >= max_steps:
                self.finished = True
                self.optimizer_instances = []
                self.is_active_instance = []
    structures = [Structure.from_file(path) for path in args.input]
    atoms_list = [AseAtomsAdaptor.get_atoms(item) for item in structures]
    relaxer = BoundedRelaxer(potential=potential, filter="EXPCELLFILTER", fmax=fmax)
    trajectories = relaxer.relax(atoms_list)
    candidates = []
    relaxed_structures = []
    for index, (path, original) in enumerate(zip(args.input, structures)):
        row = {"source_cif": str(path.resolve()), "source_name": path.name}
        try:
            atoms = trajectories[index][-1]
            energy = float(atoms.info["total_energy"])
            if not math.isfinite(energy):
                raise ValueError("Nonfinite MatterSim energy")
            atoms.calc = DummyBatchCalculator()
            max_force = float(np.linalg.norm(ExpCellFilter(atoms).get_forces(), axis=1).max())
            converged = bool(max_force <= fmax)
            relaxed = AseAtomsAdaptor.get_structure(atoms)
            check = check_structure(relaxed)
            relaxed_path = args.output_dir / (path.stem + ".relaxed.cif")
            relaxed.to(filename=str(relaxed_path), fmt="cif")
            # Always retain the exact candidate's relaxed structure, including failures.
            row.update(formula_pretty=relaxed.composition.reduced_formula, relaxed_structure_path=str(relaxed_path.resolve()),
                relaxed_total_energy_ev=energy, relaxed_energy_per_atom_ev=energy/len(relaxed),
                relaxation_converged=converged, max_generalized_force_ev_angstrom=max_force,
                fmax_ev_angstrom=fmax, relaxation_steps=len(trajectories[index])-1, relaxed_geometry=check,
                method="MatterSim v1.0.0-1M MLFF + MP2020-corrected competing phases; hybrid estimate, not DFT",
                evidence_level="D：混合能量基准的凸包估算", model="MatterSim v1.0.0-1M",
                checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
            row["alignn_structure"] = {"lattice_mat": relaxed.lattice.matrix.tolist(),
                "coords": relaxed.frac_coords.tolist(), "elements": [site.specie.symbol for site in relaxed], "cartesian": False}
            if not converged or not check["is_valid"]:
                row.update(status="not_converged" if not converged else "invalid_relaxed_structure", energy_above_hull_ev=None, formation_energy_per_atom_ev=None)
            else:
                entry = corrected_entry(relaxed, energy)
                system = args.reference_system.split("-") if args.reference_system else [element.symbol for element in relaxed.composition.elements]
                if not {element.symbol for element in relaxed.composition.elements} <= set(system):
                    raise ValueError("Relaxed elements exceed requested reference chemical system")
                diagram = phase_diagram(system)
                raw_hull = float(diagram.get_e_above_hull(entry, allow_negative=True))
                row.update(status="ok", energy_above_hull_ev=max(0.0, raw_hull), raw_energy_above_hull_ev=raw_hull,
                    formation_energy_per_atom_ev=float(diagram.get_form_energy_per_atom(entry)),
                    energy_correction_ev=float(entry.correction), reference_dataset="MP2020correction (MP + Alexandria)",
                    reference_chemical_system="-".join(sorted(system)),
                    preparation_traceability={"candidate_crystallography": {"formula_pretty": check["formula_pretty"],
                        "space_group_symbol": check["space_group"], "space_group_number": check["metadata"]["space_group_number"], "sites": len(relaxed)},
                        "same_system_stable_phases": [{"formula_pretty": item.composition.reduced_formula, "material_id": str(item.entry_id or "")} for item in sorted(diagram.stable_entries, key=lambda item: item.composition.reduced_formula)][:12]})
            relaxed_structures.append((row, relaxed))
        except Exception as exc:
            row.update(status="unavailable", error=str(exc), energy_above_hull_ev=None, formation_energy_per_atom_ev=None)
        candidates.append(row)
        (args.output_dir / "mattersim_partial.json").write_text(json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2), encoding="utf-8")
    matcher = StructureMatcher(ltol=0.2, stol=0.3, angle_tol=5, primitive_cell=True)
    representatives = []
    for row, structure in sorted(relaxed_structures, key=lambda item: (item[0].get("energy_above_hull_ev") if item[0].get("energy_above_hull_ev") is not None else math.inf, item[0]["source_name"])):
        duplicate = next((other["source_name"] for other, reference in representatives if matcher.fit(structure, reference)), None)
        row["duplicate_of"] = duplicate
        if duplicate is None:
            representatives.append((row, structure))
    output = {"status": "ok", "backend": "mattersim", "candidates": candidates,
        "structure_matcher": {"ltol": 0.2, "stol": 0.3, "angle_tol": 5, "primitive_cell": True},
        "independent_structure_groups": len(representatives), "maximum_batch_relax_steps": max_steps}
    (args.output_dir / "mattersim_results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "groups": len(representatives)}))


if __name__ == "__main__":
    main()
