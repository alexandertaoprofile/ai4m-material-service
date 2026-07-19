"""Upstream-agent entry point for HEA/MPEA alloy composition optimization.

The FastAPI service owns the HTTP and WebSocket routes.  This module exists
for the shared Alpha team loader, so that an upstream orchestrator receives
the same alloy workflow rather than the copied inorganic-generation workflow.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from alpha.actions import Action, UserRequirement
from alpha.roles import Role

from main import RESULTS, _proposal, _taskid, emit_public_asset_events, prepare_public_assets
from src.alloy_workflow.presentation import emit_result_content

FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "耗材选型和计算优化"
FRONTEND_TEAM_TYPE = "Robot_Materials"


def _payload_from_instruction(
    instruction: Any, taskid: str, user_name: str, file_metadata: Any,
) -> dict[str, Any]:
    """Accept the common upstream text/dict/list input without inventing data."""
    if isinstance(instruction, dict):
        payload = dict(instruction)
    else:
        if isinstance(instruction, list):
            text = ""
            # Alpha passes Message objects in history.  Prefer the newest
            # user content rather than ``str(Message)`` or a prior agent reply.
            for item in reversed(instruction):
                if isinstance(item, dict):
                    role, content = item.get("role"), item.get("content")
                else:
                    role, content = getattr(item, "role", None), getattr(item, "content", None)
                if content and (role in (None, "user") or str(role).lower().endswith("user")):
                    text = str(content)
                    break
            if not text and instruction:
                text = str(getattr(instruction[-1], "content", instruction[-1]))
        else:
            text = str(instruction or "")
        try:
            decoded = json.loads(text)
            payload = decoded if isinstance(decoded, dict) else {"idea": text}
        except json.JSONDecodeError:
            payload = {"idea": text}
    payload.setdefault("taskid", str(taskid))
    payload.setdefault("user_name", str(user_name))
    payload.setdefault("file_metadata", file_metadata or [])
    return payload


class Coding(Action):
    """Convert an alloy request into screened HEA candidates and presentation."""

    name: str = "Coding"
    desc: str = (
        "面向 HEA/MPEA 合金的成分空间构建、代理模型批量初筛、适用域与不确定性评估；"
        "输出用户可读结论和供后续数学优化解析的结构化交接包。"
    )

    async def run(self, instruction: Any, *args: Any) -> str:
        websocket, user_name, taskid, file_metadata = args[:4]
        payload = _payload_from_instruction(instruction, str(taskid), str(user_name), file_metadata)
        request_id = _taskid(payload)
        await websocket.send_json({
            "version": "1.0.0", "agent": "alloy_composition_optimization",
            "request_id": request_id, "type": "progress",
            "data": {
                "id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID,
                "title": FRONTEND_STEP_TITLE, "teamType": FRONTEND_TEAM_TYPE,
                "status": "in_progress", "description": "正在将需求映射为可确认的 HEA 探索条件。",
            },
        })
        result = await asyncio.to_thread(_proposal, payload)
        result["_summary_path"] = RESULTS / request_id / "presentation" / "summary.md"
        public_urls, asset_docs, _asset_titles, visual_assets = await prepare_public_assets(websocket, request_id, result)
        await emit_result_content(websocket, result, step_id=FRONTEND_STEP_ID, visual_assets=visual_assets)
        result.pop("_summary_path", None)
        await emit_public_asset_events(websocket, result, public_urls, asset_docs)
        await websocket.send_json({
            "version": "1.0.0", "agent": "alloy_composition_optimization",
            "request_id": request_id, "type": "result", "data": result,
        })
        return str(result.get("user_conclusion", "合金候选初筛完成。"))


class XIMUAlpha_AlloyCompositionOptimization(Role):
    """HEA/MPEA 合金配比优化专属 Agent。"""

    name: str = "合金配比优化和候选初筛"
    profile: str = (
        "子流程：合金配比优化和候选初筛。当母服务需要面向 HEA/MPEA 合金做成分空间构建、"
        "候选排序、性能预测、不确定性与适用域评价、工艺条件建议和数学优化交接时，调用本服务。"
        "适用于航空发动机高温合金、轻量化结构合金、耐热耐蚀合金和多主元合金等场景。"
        "典型关键词：合金配比、高熵合金、HEA、MPEA、候选筛选、强度预测、相组成、适用域、数学优化。"
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._watch([UserRequirement])
        self.set_actions([Coding])


# Compatibility for generic Alpha launchers that import this historical name.
XIMUAlpha_MNS = XIMUAlpha_AlloyCompositionOptimization

# Descriptive alias for direct programmatic imports.
AlloyCompositionScreening = Coding
