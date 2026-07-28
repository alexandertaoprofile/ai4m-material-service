"""Adapters shared by HTTP and WebSocket entrypoints for upstream services."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from .constraints import upstream_contract
from .emitters import build_frontend_payload, build_scientific_conclusion, write_pipeline_manifest
from .pipeline import run_new_material_pipeline
from .presentation import (
    build_discovery_conclusion,
    build_discovery_story,
    render_presentation_assets,
    write_preparation_traceability_report,
    write_terminal_progress,
)

logger = logging.getLogger("mattergen_workflow")


def run_upstream_request(payload: Dict[str, Any], results_root: Path):
    constraints, provenance = upstream_contract(payload)
    # A conversational front end should not accidentally launch an 8-candidate
    # GPU job when the user supplied only prose. Structured callers can still
    # request a larger batch explicitly.
    max_candidates = int(payload.get("max_candidates") or payload.get("new_material", {}).get("max_candidates") or os.environ.get("MATTERGEN_DEFAULT_CANDIDATES", "1"))
    if not 1 <= max_candidates <= 64:
        raise ValueError("max_candidates must be between 1 and 64")
    started = time.monotonic()
    logger.info("[DISCOVERY][%s] accepted: elements=%s properties=%s candidates=%s", constraints.taskid, constraints.allowed_elements, constraints.target_properties, max_candidates)
    result = run_new_material_pipeline(constraints, results_root, max_candidates=max_candidates)
    logger.info("[DISCOVERY][%s] scientific stages finished in %.1fs; generated=%s admitted=%s", constraints.taskid, time.monotonic() - started, len(result.generation.candidates), sum(item.is_valid is True for item in result.validations))
    result.artifacts["upstream"] = provenance
    result.artifacts["preparation_traceability_report"] = str(write_preparation_traceability_report(result))
    logger.info("[DISCOVERY][%s] rendering frontend assets", constraints.taskid)
    result.artifacts["presentation"] = render_presentation_assets(result)
    write_pipeline_manifest(result, Path(result.artifacts["result_dir"]))
    completed = result.status == "ok"
    write_terminal_progress(
        Path(result.artifacts["result_dir"]),
        status="completed" if completed else "failed",
        description=("候选结构、热力学初筛结果和可视化资产已生成。" if completed else result.message),
    )
    logger.info("[DISCOVERY][%s] completed: status=%s", constraints.taskid, result.status)
    return result


def result_summary(result) -> str:
    status_text = "已完成" if result.status == "ok" else "未完成"
    lines = [
        "### 新材料候选结果",
        f"- 状态：{status_text}",
        f"- 已生成候选：{len(result.generation.candidates)} 个",
        f"- 通过基础结构检查：{sum(item.is_valid is True for item in result.validations)} 个",
    ]
    if result.status != "ok":
        elements = " / ".join(result.constraints.allowed_elements) or "未能确定"
        return "\n".join(lines + [
            "",
            "#### 本轮未得到候选的原因",
            result.message,
            "这表示计算资源或模型依赖尚未就绪，并不代表当前材料体系的性能或可行性已经被否定。",
            "",
            "#### 已保留的任务条件",
            f"- 目标元素体系：{elements}",
            f"- 后续关注：{'、'.join(result.constraints.validation_targets) or '基础结构与热力学稳定性'}",
            "",
            "#### 下一步",
            "补齐对应 MatterGen 模型权重后，可在相同任务条件下重新执行；无需重新编写需求。",
        ])
    lines.extend(["", build_discovery_story(result)])
    if result.ranked_candidates:
        lines.append("\n### 本轮候选结果")
        lines.append("| 排名 | 候选 | 化学式 | 基础结构检查 | 形成能（相对元素） | 稳定性距离 E_hull（越低越好） |")
        lines.append("|---|---|---|---|---|---|")
        for item in result.ranked_candidates:
            validation = item.validation
            formation = f"{validation.formation_energy_per_atom:.4f}" if validation and validation.formation_energy_per_atom is not None else "待计算"
            hull = f"{validation.energy_above_hull:.4f}" if validation and validation.energy_above_hull is not None else "待计算"
            formula = item.candidate.formula_pretty or (validation.formula_pretty if validation else None) or "N/A"
            lines.append(f"| {item.rank} | {item.candidate.candidate_id} | {formula} | {'通过' if validation and validation.is_valid else '未通过/待定'} | {formation} | {hull} |")
    lines.extend(["", build_discovery_conclusion(result)])
    return "\n".join(lines)


def response_payload(result) -> Dict[str, Any]:
    return {"frontend": build_frontend_payload(result), "manifest": result.to_dict()}
