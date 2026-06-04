"""Orchestration skeleton for the inorganic new-material mainline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .emitters import write_pipeline_manifest
from .generation import GenerationRunner, run_mattergen_generation
from .ranking import rank_candidates
from .schemas import GenerationConstraint, NewMaterialPipelineResult
from .validation import ValidationRunner, run_adit_pymatgen_validation


def run_new_material_pipeline(
    constraints: GenerationConstraint,
    results_root: Path,
    max_candidates: int = 8,
    generation_runner: Optional[GenerationRunner] = None,
    validation_runner: Optional[ValidationRunner] = None,
) -> NewMaterialPipelineResult:
    """Run the normalized new-material pipeline boundary.

    This function is safe to call before MatterGen/ADiT are connected: it records
    explicit not-configured manifests instead of producing fake structures or
    placeholder properties.
    """
    task_dir = results_root / constraints.taskid
    generation_dir = task_dir / "generation"
    validation_dir = task_dir / "validation"

    generation = run_mattergen_generation(
        constraints=constraints,
        output_dir=generation_dir,
        max_candidates=max_candidates,
        runner=generation_runner,
    )

    validations = [
        run_adit_pymatgen_validation(candidate, validation_dir, runner=validation_runner)
        for candidate in generation.candidates
    ]
    ranked = rank_candidates(generation.candidates, validations)

    status = "ok" if ranked else generation.status
    message = "New-material pipeline completed." if ranked else generation.message
    result = NewMaterialPipelineResult(
        taskid=constraints.taskid,
        status=status,
        constraints=constraints,
        generation=generation,
        validations=validations,
        ranked_candidates=ranked,
        message=message,
        artifacts={"result_dir": str(task_dir)},
    )
    write_pipeline_manifest(result, task_dir)
    return result
