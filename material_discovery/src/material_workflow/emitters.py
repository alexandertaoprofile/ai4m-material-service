"""Frontend and manifest emitters for the new-material pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .schemas import NewMaterialPipelineResult


JsonDict = Dict[str, Any]


def build_scientific_conclusion(result: NewMaterialPipelineResult) -> JsonDict:
    """Create a conservative, frontend-ready decision from computed evidence."""
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    if top is None or top.validation is None:
        return {"decision": "no_candidate", "text": "未生成可进入验证阶段的候选结构。", "evidence_level": "none"}
    validation = top.validation
    ehull = validation.energy_above_hull
    formation = validation.formation_energy_per_atom
    threshold = float((result.constraints.target_properties or {}).get("energy_above_hull", 0.05))
    if ehull is None:
        return {
            "decision": "structure_only",
            "text": "候选已通过基础结构检查；尚未完成稳定性评估，因此还不能判断它是否值得合成验证。",
            "evidence_level": "pymatgen_structure_check",
        }
    if ehull <= threshold:
        decision = "shortlist_for_dft"
        thermal = "稳定性初筛表现良好，建议进入 DFT 与目标性能验证。"
    else:
        decision = "comparison_candidate"
        thermal = "本轮没有候选达到稳定性初筛阈值；它仍是当前批次中最接近该目标的比较候选。"
    formula = top.candidate.formula_pretty or validation.formula_pretty or top.candidate.candidate_id
    formation_text = f"形成能（相对组成元素的能量变化）为 {formation:.4f} eV/atom，" if formation is not None else ""
    return {
        "decision": decision,
        "candidate_id": top.candidate.candidate_id,
        "formula": formula,
        "energy_above_hull_ev_per_atom": ehull,
        "formation_energy_per_atom_ev": formation,
        "text": (
            f"{formula}：{formation_text}稳定性距离 E_hull（高于凸包能）为 {ehull:.4f} eV/atom；{thermal}"
        ),
        "evidence_level": "mattersim_mp_hybrid",
    }


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
        "scientific_conclusion": build_scientific_conclusion(result),
        "artifacts": dict(result.artifacts),
    }


def write_pipeline_manifest(result: NewMaterialPipelineResult, output_dir: Path) -> Path:
    """Persist the normalized pipeline result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "new_material_pipeline_manifest.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
