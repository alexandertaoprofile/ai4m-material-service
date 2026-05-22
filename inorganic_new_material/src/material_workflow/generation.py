"""MatterGen-facing generation stage contract.

The real MatterGen backend is not wired in this service yet. This module keeps the
future integration boundary explicit so the running service can stay stable while
we normalize the new-material mainline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from .schemas import GenerationConstraint, GenerationManifest


GenerationRunner = Callable[[GenerationConstraint, Path, int], GenerationManifest]


def write_generation_manifest(manifest: GenerationManifest, output_dir: Path) -> Path:
    """Persist a generation manifest for downstream validation and debugging."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "generation_manifest.json"
    path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_mattergen_generation(
    constraints: GenerationConstraint,
    output_dir: Path,
    max_candidates: int = 8,
    runner: Optional[GenerationRunner] = None,
) -> GenerationManifest:
    """Run candidate generation or return a safe not-configured manifest.

    A custom runner can be injected later by the service layer once MatterGen is
    available. Until then this function is non-destructive and produces no fake
    candidates.
    """
    if runner is not None:
        manifest = runner(constraints, output_dir, max_candidates)
        write_generation_manifest(manifest, output_dir)
        return manifest

    manifest = GenerationManifest(
        taskid=constraints.taskid,
        status="not_configured",
        candidates=[],
        message="MatterGen backend is not configured for inorganic_new_material yet.",
        metadata={"max_candidates": max_candidates},
    )
    write_generation_manifest(manifest, output_dir)
    return manifest
