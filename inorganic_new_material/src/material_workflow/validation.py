"""Pymatgen-based structural validation for MatterGen-produced CIF files."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .schemas import GeneratedCandidate, ValidationResult

ValidationRunner = Callable[[GeneratedCandidate, Path], ValidationResult]
logger = logging.getLogger("mattergen_workflow")


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


def _validate_in_mattergen_environment(candidate: GeneratedCandidate) -> ValidationResult:
    """Keep pymatgen in the dedicated MatterGen environment, not the API env."""
    if candidate.cif_path is None or not candidate.cif_path.exists():
        return ValidationResult(candidate_id=candidate.candidate_id, status="missing_input", is_valid=False, formula_pretty=candidate.formula_pretty, errors=["No readable CIF path is available for validation."])
    project_root = Path(__file__).resolve().parents[2]
    helper = project_root / "tools" / "run_pymatgen_validation.py"
    env_prefix = os.environ.get("MATTERGEN_ENV_PREFIX", "/data/mamba/envs/mattergen-py310").strip()
    command = (["micromamba", "run", "-p", env_prefix] if env_prefix else []) + [
        "python", str(helper), "--input", str(candidate.cif_path), "--candidate-id", candidate.candidate_id,
    ]
    timeout = int(os.environ.get("PYMATGEN_VALIDATION_TIMEOUT_SEC", "120"))
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        message = f"MatterGen-environment structural validation exceeded {timeout}s."
        logger.error("[DISCOVERY][%s] %s", candidate.candidate_id, message)
        return ValidationResult(candidate_id=candidate.candidate_id, status="unavailable", formula_pretty=candidate.formula_pretty, artifacts={"cif_path": str(candidate.cif_path)}, errors=[message])
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout or "validation worker failed").strip()
        logger.error("[DISCOVERY][%s] structural admission worker failed: %s", candidate.candidate_id, reason[-500:])
        return ValidationResult(candidate_id=candidate.candidate_id, status="unavailable", formula_pretty=candidate.formula_pretty, artifacts={"cif_path": str(candidate.cif_path)}, errors=[f"MatterGen-environment structural validation failed: {reason[-1200:]}."])
    try:
        payload = json.loads(completed.stdout)
        result = ValidationResult(**payload)
        logger.info("[DISCOVERY][%s] structure admission=%s formula=%s", candidate.candidate_id, result.is_valid, result.formula_pretty)
        return result
    except (json.JSONDecodeError, TypeError) as exc:
        return ValidationResult(candidate_id=candidate.candidate_id, status="unavailable", formula_pretty=candidate.formula_pretty, artifacts={"cif_path": str(candidate.cif_path)}, errors=[f"Could not read MatterGen-environment validation result: {exc}"])


def run_pymatgen_structural_admission(candidate: GeneratedCandidate, output_dir: Path, runner: Optional[ValidationRunner] = None) -> ValidationResult:
    """Perform lightweight structural admission checks using pymatgen."""
    result = (runner or (lambda item, _: _validate_in_mattergen_environment(item)))(candidate, output_dir)
    write_validation_result(result, output_dir)
    return result
