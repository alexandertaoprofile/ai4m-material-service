"""Frontend and manifest emitters for the new-material pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .schemas import NewMaterialPipelineResult


JsonDict = Dict[str, Any]


def build_frontend_payload(result: NewMaterialPipelineResult) -> JsonDict:
    """Build a compact payload suitable for websocket/frontend rendering."""
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    return {
        "taskid": result.taskid,
        "status": result.status,
        "message": result.message,
        "constraints": result.constraints.to_dict(),
        "top_candidate": top.to_dict() if top else None,
        "candidate_count": len(result.generation.candidates),
        "validated_count": len(result.validations),
        "artifacts": dict(result.artifacts),
    }


def write_pipeline_manifest(result: NewMaterialPipelineResult, output_dir: Path) -> Path:
    """Persist the normalized pipeline result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "new_material_pipeline_manifest.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
