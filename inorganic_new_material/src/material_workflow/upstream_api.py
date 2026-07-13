"""Adapters shared by HTTP and WebSocket entrypoints for upstream services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .constraints import upstream_contract
from .emitters import build_frontend_payload, build_scientific_conclusion, write_pipeline_manifest
from .pipeline import run_new_material_pipeline
from .presentation import build_discovery_story, render_presentation_assets


def run_upstream_request(payload: Dict[str, Any], results_root: Path):
    constraints, provenance = upstream_contract(payload)
    max_candidates = int(payload.get("max_candidates") or payload.get("new_material", {}).get("max_candidates", 8))
    if not 1 <= max_candidates <= 64:
        raise ValueError("max_candidates must be between 1 and 64")
    result = run_new_material_pipeline(constraints, results_root, max_candidates=max_candidates)
    result.artifacts["upstream"] = provenance
    result.artifacts["presentation"] = render_presentation_assets(result)
    write_pipeline_manifest(result, Path(result.artifacts["result_dir"]))
    return result


def result_summary(result) -> str:
    lines = [
        "### MatterGen 新材料生成结果",
        f"- 任务：`{result.taskid}`",
        f"- 状态：`{result.status}`",
        f"- 生成候选：{len(result.generation.candidates)} 个",
        f"- 通过基础结构准入：{sum(item.is_valid is True for item in result.validations)} 个",
    ]
    lines.extend(["", build_discovery_story(result)])
    if result.ranked_candidates:
        lines.append("\n| 排名 | 候选 | 化学式 | 结构准入 | 形成能 (eV/atom) | 高于凸包 (eV/atom) |")
        lines.append("|---|---|---|---|---|---|")
        for item in result.ranked_candidates:
            validation = item.validation
            formation = f"{validation.formation_energy_per_atom:.4f}" if validation and validation.formation_energy_per_atom is not None else "待计算"
            hull = f"{validation.energy_above_hull:.4f}" if validation and validation.energy_above_hull is not None else "待计算"
            lines.append(f"| {item.rank} | {item.candidate.candidate_id} | {item.candidate.formula_pretty or 'N/A'} | {'通过' if validation and validation.is_valid else '未通过/待定'} | {formation} | {hull} |")
    conclusion = build_scientific_conclusion(result)
    lines.append(f"\n结论：{conclusion['text']}")
    return "\n".join(lines)


def response_payload(result) -> Dict[str, Any]:
    return {"frontend": build_frontend_payload(result), "manifest": result.to_dict()}
