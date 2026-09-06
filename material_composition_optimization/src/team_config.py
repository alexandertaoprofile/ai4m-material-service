"""材料配方设计与性能筛选的上游 Agent 入口。

``main.py`` 负责 HTTP/WebSocket 传输与前端事件协议；本模块只负责让 Alpha
母服务以与直连入口相同的顺序调用合金工作流，不再携带无机晶体生成逻辑。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from alpha.actions import Action, UserRequirement
from alpha.roles import Role

from src.alloy_workflow.contracts import requirement_plan as _requirement_plan, task_id as _taskid
from src.alloy_workflow.identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE
from src.alloy_workflow.presentation import emit_result_content, hot_end_input_guide_block, planned_alloy_method_block, stream_authoritative_markdown
from src.alloy_workflow.protocol import prepare_public_assets
from src.alloy_workflow.runtime import RUNTIME

# 与 main.py 共享同一运行时装配，禁止由编排层反向 import 传输入口。
RESULTS = RUNTIME.results_root
_proposal = RUNTIME.propose

FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "材料配方设计与性能筛选"


async def execute_alloy_optimization(websocket: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """按直连 WebSocket 的既有顺序执行上游角色调用。

    阶段 2 生成候选：规范化高熵/多主元合金需求、调用独立计算执行器、落盘清单并生成
    当前任务的 PNG/Markdown 资产。

    阶段 3 展示结果：优先发布 PNG，失败时回退本地任务 URL；随后按既有
    前端协议推送含 Markdown 图片的正文。调用方在本函数返回后再发最终 result。
    """
    # 阶段 1：将母服务的角色消息包装成与直连 WebSocket 相同、可校验的任务范围。
    request_id = _taskid(payload)

    # 阶段 2：高熵/多主元合金用例只执行成分空间和代理模型计算，返回标准结果及本地任务资产；
    # 它不处理 WebSocket 协议，也不依赖 Alpha Role。
    result = await asyncio.to_thread(_proposal, payload)

    # 阶段 3a：尽可能将当前任务资产发布为前端可访问地址。
    result["_summary_path"] = RESULTS / request_id / "presentation" / "summary.md"
    public_urls, asset_docs, _asset_titles, visual_assets = await prepare_public_assets(websocket, request_id, result, RESULTS)

    # 阶段 3b：在内容区块内发送 Markdown 图片；GLB 不属于本服务资产类型。
    await emit_result_content(websocket, result, step_id=FRONTEND_STEP_ID, visual_assets=visual_assets)
    result.pop("_summary_path", None)
    return result


def _payload_from_instruction(
    instruction: Any, taskid: str, user_name: str, file_metadata: Any,
) -> dict[str, Any]:
    """兼容母服务常见的文本、字典和历史消息列表输入，不臆造缺失数据。"""
    if isinstance(instruction, dict):
        payload = dict(instruction)
    else:
        if isinstance(instruction, list):
            text = ""
            user_history: list[str] = []
            # Alpha 历史中可能是 Message 对象；优先取最新用户内容，而不是
            # ``str(Message)``。保留本轮此前的用户消息以继承已明确的部件和
            # 服役机制；助手提出的备选材料不被写入用户约束。
            for item in reversed(instruction):
                if isinstance(item, dict):
                    role, content = item.get("role"), item.get("content")
                else:
                    role, content = getattr(item, "role", None), getattr(item, "content", None)
                if content and (role in (None, "user") or str(role).lower().endswith("user")):
                    user_history.append(str(content))
            if user_history:
                text = user_history[0]
            if not text and instruction:
                text = str(getattr(instruction[-1], "content", instruction[-1]))
            try:
                decoded = json.loads(text)
                payload = decoded if isinstance(decoded, dict) else {"idea": text}
            except json.JSONDecodeError:
                payload = {"idea": text}
            if len(user_history) > 1:
                previous_user_context = list(reversed(user_history[1:]))
                existing_context = payload.get("conversation_context")
                payload["conversation_context"] = (
                    previous_user_context + ([existing_context] if existing_context else [])
                )
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
    """将已接入材料域的需求转换为候选配方及其展示结果。"""

    name: str = ACTION_NAME
    desc: str = ACTION_DESCRIPTION

    async def run(self, instruction: Any, *args: Any) -> str:
        # 阶段 0：适配 Alpha/母服务输入；先保留 task、用户和文件元数据，再进入服务流程。
        websocket, user_name, taskid, file_metadata = args[:4]
        payload = _payload_from_instruction(instruction, str(taskid), str(user_name), file_metadata)
        request_id = _taskid(payload)

        effective, plan = _requirement_plan(payload)
        progress_description = ("正在整理芯片玻璃基板的氧化物配方与热机械筛选条件。" if effective.get("model_domain") == "chip_glass_thermomechanical_family_v1" else "正在整理高温镍基合金的工况与成分设计条件。" if effective.get("model_domain") == "ni_superalloy_hot_end" else "正在整理可回收火箭不锈钢的温度、工艺与配方边界。" if effective.get("model_domain") == "reusable_rocket_stainless" else "正在将需求映射为高熵/多主元合金的探索条件。")
        await websocket.send_json({
            "version": "1.0.0", "agent": "alloy_composition_optimization",
            "request_id": request_id, "type": "progress",
            "data": {
                "id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID,
                "title": FRONTEND_STEP_TITLE,
                "status": "in_progress", "description": progress_description,
            },
        })
        await stream_authoritative_markdown(websocket, planned_alloy_method_block(payload), step_id=FRONTEND_STEP_ID)

        if plan.get("requires_domain_confirmation"):
            waiting = {"taskid": request_id, "status": "waiting_for_input", "service": "alloy-composition-optimization", "model_domain": "routing_confirmation", "requirement_interpretation": plan, "user_conclusion": "请确认采用高温镍基合金还是 HEA/MPEA 路线后开始配方筛选。"}
            await websocket.send_json({"version": "1.0.0", "agent": "alloy_composition_optimization", "request_id": request_id, "type": "progress", "data": {"id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "status": "completed", "description": "已识别高温合金任务，等待确认材料体系。"}})
            await websocket.send_json({"version": "1.0.0", "agent": "alloy_composition_optimization", "request_id": request_id, "type": "result", "data": waiting})
            return str(waiting["user_conclusion"])

        if effective.get("model_domain") == "ni_superalloy_hot_end" and plan.get("missing_required_inputs"):
            waiting = {"taskid": request_id, "status": "waiting_for_input", "service": "alloy-composition-optimization", "model_domain": "ni_superalloy_hot_end", "requirement_interpretation": plan, "user_conclusion": "已识别为高温镍基合金成分设计任务；补齐路线、热处理、温度、载荷和 wt.% 边界后即可开始条件筛选。"}
            await websocket.send_json({"version": "1.0.0", "agent": "alloy_composition_optimization", "request_id": request_id, "type": "progress", "data": {"id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "status": "completed", "description": "已识别高温镍基合金任务，等待补齐条件后开始计算。"}})
            await stream_authoritative_markdown(websocket, hot_end_input_guide_block(plan), step_id=FRONTEND_STEP_ID)
            await websocket.send_json({"version": "1.0.0", "agent": "alloy_composition_optimization", "request_id": request_id, "type": "result", "data": waiting})
            return str(waiting["user_conclusion"])

        # 阶段 2—3：执行计算、准备展示资产并流式输出正文。
        result = await execute_alloy_optimization(websocket, payload)

        # 阶段 4：仅发送一次最终结构化交接 result 事件。
        await websocket.send_json({
            "version": "1.0.0", "agent": "alloy_composition_optimization",
            "request_id": request_id, "type": "result", "data": result,
        })
        return str(result.get("user_conclusion", "材料候选初筛完成。"))


class AlloyCompositionOptimizationRole(Role):
    """已接入材料域的配方设计与性能筛选 Agent。"""

    name: str = ROLE_NAME
    profile: str = ROLE_PROFILE

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._watch([UserRequirement])
        self.set_actions([Coding])
