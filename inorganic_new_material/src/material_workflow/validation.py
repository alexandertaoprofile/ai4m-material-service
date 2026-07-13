"""Pymatgen-based structural validation for MatterGen-produced CIF files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from .schemas import GeneratedCandidate, ValidationResult

ValidationRunner = Callable[[GeneratedCandidate, Path], ValidationResult]


def write_validation_result(result: ValidationResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result.candidate_id}.validation.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _validate_with_pymatgen(candidate: GeneratedCandidate) -> ValidationResult:
    if candidate.cif_path is None or not candidate.cif_path.exists():
        return ValidationResult(candidate_id=candidate.candidate_id, status="missing_input", is_valid=False, formula_pretty=candidate.formula_pretty, errors=["No readable CIF path is available for validation."])
    try:
        from pymatgen.core import Structure
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    except ImportError as exc:
        return ValidationResult(candidate_id=candidate.candidate_id, status="unavailable", formula_pretty=candidate.formula_pretty, errors=[f"pymatgen is unavailable: {exc}"])
    try:
        structure = Structure.from_file(candidate.cif_path)
        distances = structure.distance_matrix
        minimum = float(distances[distances > 1e-8].min())
        errors = []
        if not structure.is_ordered:
            errors.append("Structure has partial occupancy.")
        if minimum < 1.2:
            errors.append(f"Minimum interatomic distance is too small ({minimum:.3f} Å).")
        try:
            space_group = SpacegroupAnalyzer(structure, symprec=0.1).get_space_group_symbol()
        except Exception:
            space_group = None
        return ValidationResult(candidate_id=candidate.candidate_id, status="ok", is_valid=not errors, formula_pretty=structure.composition.reduced_formula, space_group=space_group, density=float(structure.density), errors=errors, artifacts={"cif_path": str(candidate.cif_path)}, metadata={"num_sites": len(structure), "min_interatomic_distance": minimum, "ordered": structure.is_ordered})
    except Exception as exc:
        return ValidationResult(candidate_id=candidate.candidate_id, status="invalid", is_valid=False, formula_pretty=candidate.formula_pretty, artifacts={"cif_path": str(candidate.cif_path)}, errors=[str(exc)])


def run_adit_pymatgen_validation(candidate: GeneratedCandidate, output_dir: Path, runner: Optional[ValidationRunner] = None) -> ValidationResult:
    """Perform lightweight ADiT-compatible admission checks using pymatgen."""
    result = (runner or (lambda item, _: _validate_with_pymatgen(item)))(candidate, output_dir)
    write_validation_result(result, output_dir)
    return result
