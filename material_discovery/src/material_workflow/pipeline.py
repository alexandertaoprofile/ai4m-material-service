"""Orchestration skeleton for the inorganic new-material mainline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .emitters import write_pipeline_manifest
from .generation import GenerationRunner, run_mattergen_generation
from .mattersim import enrich_validations_with_mattersim
from .ranking import rank_candidates
from .schemas import GenerationConstraint, NewMaterialPipelineResult
from .validation import ValidationRunner, run_pymatgen_structural_admission

logger = logging.getLogger("mattergen_workflow")


def run_new_material_pipeline(
    constraints: GenerationConstraint,
    results_root: Path,
    max_candidates: int = 8,
    generation_runner: Optional[GenerationRunner] = None,
    validation_runner: Optional[ValidationRunner] = None,
) -> NewMaterialPipelineResult:
    """Run MatterGen generation, structural admission checks, and ranking."""
    task_dir = results_root / constraints.taskid
    generation_dir = task_dir / "generation"
    validation_dir = task_dir / "validation"

    generation = run_mattergen_generation(
        constraints=constraints,
        output_dir=generation_dir,
        max_candidates=max_candidates,
        runner=generation_runner,
    )
    logger.info("[DISCOVERY][%s] MatterGen completed: status=%s candidates=%s", constraints.taskid, generation.status, len(generation.candidates))

    validations = [
        run_pymatgen_structural_admission(candidate, validation_dir, runner=validation_runner)
        for candidate in generation.candidates
    ]
    logger.info("[DISCOVERY][%s] structure admission completed: admitted=%s/%s", constraints.taskid, sum(item.is_valid is True for item in validations), len(validations))
    admitted_candidates = [
        candidate for candidate, validation in zip(generation.candidates, validations) if validation.is_valid is True
    ]
    validations = enrich_validations_with_mattersim(validations, admitted_candidates, validation_dir)
    logger.info("[DISCOVERY][%s] MatterSim/MP completed: thermodynamic_results=%s/%s", constraints.taskid, sum(item.energy_above_hull is not None for item in validations), len(admitted_candidates))
    ranked = rank_candidates(generation.candidates, validations)

    status = "ok" if ranked else generation.status
    message = "候选生成、基础结构检查与稳定性评估已完成。" if ranked else generation.message
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
