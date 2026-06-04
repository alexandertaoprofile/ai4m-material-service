"""Validation stage contract for generated inorganic materials."""

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


def run_adit_pymatgen_validation(
    candidate: GeneratedCandidate,
    output_dir: Path,
    runner: Optional[ValidationRunner] = None,
) -> ValidationResult:
    """Validate one generated candidate without inventing missing properties."""
    if runner is not None:
        result = runner(candidate, output_dir)
        write_validation_result(result, output_dir)
        return result

    if candidate.cif_path is None:
        result = ValidationResult(
            candidate_id=candidate.candidate_id,
            status="missing_input",
            is_valid=False,
            formula_pretty=candidate.formula_pretty,
            errors=["No CIF path is available for validation."],
        )
    else:
        result = ValidationResult(
            candidate_id=candidate.candidate_id,
            status="not_configured",
            formula_pretty=candidate.formula_pretty,
            artifacts={"cif_path": str(candidate.cif_path)},
            errors=["ADiT/pymatgen validation runner is not configured yet."],
        )

    write_validation_result(result, output_dir)
    return result
