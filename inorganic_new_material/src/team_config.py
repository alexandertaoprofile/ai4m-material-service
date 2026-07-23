# -*- coding: utf-8 -*-
"""Runtime orchestration for the inorganic new-material discovery service.

This module intentionally contains only the active service path.  The former
MP/ALIGNN existing-material ``Coding`` flow is not a role in this service and
must not be imported during normal startup.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from alpha.actions import Action, UserRequirement
from alpha.logs import logger
from alpha.roles import Role
from alpha.schema import Message
from alpha.team import Team

from src.llm_utils import SeLLM, load_config
from src.material_workflow.llm_constraint_inference import (
    GenerationInputRequired,
    resolve_generation_request,
)
from src.material_workflow.llm_streaming import stream_llm_response
from src.material_workflow.payloads import build_payload
from src.material_workflow.presentation import (
    build_requirement_brief,
    emit_presentation_assets,
    stream_discovery_progress,
)
from src.material_workflow.upstream_api import result_summary, run_upstream_request
from src.service_paths import NEW_MATERIAL_RESULTS_ROOT


FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"


load_dotenv()
_today = datetime.datetime.now().strftime("%Y%m%d")
os.makedirs("logs", exist_ok=True)
logger.configure(handlers=[
    {"sink": sys.stdout, "level": "INFO"},
    {"sink": f"logs/{_today}.txt", "level": "INFO", "enqueue": True},
])
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:10240"


class InorganicNewMaterialDiscoveryAction(Action):
    """Run one MatterGen → pymatgen → MatterSim/MP discovery workflow."""

    name: str = "inorganic_new_material_discovery"
    desc: str = (
        "输入数据库外新无机晶体的设计需求：化学式、元素体系、材料类别、应用场景或上游材料结论，"
        "可选结构化 new_material 约束（allowed_elements、target_properties、validation_targets、max_candidates）。"
        "将其规范化为 MatterGen 条件，生成候选晶体，执行 pymatgen 准入与 MatterSim--MP 热力学初筛，"
        "输出候选结构、初筛证据、排序和 manifest。若无法从完整上游信息归纳可追溯的无机材料起点，"
        "输出用户友好的补充信息请求，不将其报告为计算失败。"
    )

    @staticmethod
    def _payload_from_instruction(instruction: Any, taskid: str, user_name: str, file_metadata: Any) -> dict[str, Any]:
        """Preserve an explicit JSON contract; otherwise retain conversational context."""
        if isinstance(instruction, dict):
            payload = dict(instruction)
        else:
            if isinstance(instruction, list):
                chunks = [str(getattr(item, "content", item)).strip() for item in instruction]
                chunks = [chunk for chunk in chunks if chunk]
                text = chunks[-1] if chunks else ""
                conversation_context = "\n".join(chunks[:-1])
            else:
                text = str(instruction or "")
                conversation_context = ""
            try:
                decoded = json.loads(text)
                payload = decoded if isinstance(decoded, dict) else {"idea": text}
            except (TypeError, json.JSONDecodeError):
                payload = {"idea": text}
            if conversation_context:
                payload.setdefault("conversation_context", conversation_context)
        payload.setdefault("taskid", str(taskid))
        payload.setdefault("user_name", str(user_name))
        payload.setdefault("file_metadata", file_metadata or [])
        return payload

    @staticmethod
    async def _stream_authoritative_markdown(llm, websocket, step_id: str, markdown: str) -> None:
        """Stream program-authored Markdown without allowing the display LLM to alter facts."""
        await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
        relay_prompt = (
            "你是无机新材料服务的 Markdown 流式转发器。下方内容由程序根据已保存的计算结果生成。"
            "请通过 token 流逐字输出标签内部的 Markdown，不得改写、删减、补充、翻译数值或输出标签本身。\n"
            "<AUTHORITATIVE_MARKDOWN>\n"
            f"{markdown}\n"
            "</AUTHORITATIVE_MARKDOWN>"
        )
        try:
            await stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(relay_prompt)],
                websocket=websocket,
                logger_obj=logger,
            )
        except Exception:
            await websocket.send_text(markdown.rstrip() + "\n")
        finally:
            await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")

    async def run(self, instruction: str, *args) -> str:
        websocket = args[0]
        user_name, taskid, file_metadata = args[1], args[2], args[3]
        payload = self._payload_from_instruction(instruction, taskid, user_name, file_metadata)
        config = load_config("config/config.yaml")
        llm = SeLLM(base_url=config["base_url_1"], api_key=config["api_key"])

        async def announce_missing_input_inference() -> None:
            logger.info("[CONSTRAINT_LLM] deterministic extraction empty; requesting constrained LLM inference taskid={}", taskid)
            await websocket.send_text(
                "正在解析上游任务并归纳无机晶体生成条件；若无法形成可追溯的材料起点，将提示需要补充的信息。\n"
            )
        payload, constraints = await resolve_generation_request(
            payload,
            on_missing_input_inference=announce_missing_input_inference,
        )

        await websocket.send_json(build_payload(
            {
                "id": FRONTEND_STEP_ID,
                "icon": "🧪",
                "title": "生成式无机新材料发现",
                "status": "in_progress",
                "description": "正在把需求转为生成条件，并依次生成候选、评估稳定性、比较同元素体系的已知稳定相。",
            },
            type_="progress",
            request_id=str(payload["taskid"]),
        ))
        await self._stream_authoritative_markdown(
            llm, websocket, FRONTEND_STEP_ID, build_requirement_brief(constraints)
        )

        progress_task = asyncio.create_task(
            stream_discovery_progress(
                websocket,
                NEW_MATERIAL_RESULTS_ROOT / constraints.taskid,
                constraints.taskid,
                step_id=FRONTEND_STEP_ID,
            )
        )
        try:
            result = await asyncio.to_thread(run_upstream_request, payload, NEW_MATERIAL_RESULTS_ROOT)
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        presentation = (result.artifacts or {}).get("presentation") or {}
        if presentation.get("assets"):
            await self._stream_authoritative_markdown(
                llm,
                websocket,
                FRONTEND_STEP_ID,
                "#### 已生成的可视化\n\n候选结构图、旋转视图、稳定性评分卡和三维结构模型已生成，先展示计算产物；随后给出结果解读。",
            )
            await emit_presentation_assets(websocket, result, step_id=FRONTEND_STEP_ID)
        await self._stream_authoritative_markdown(
            llm, websocket, FRONTEND_STEP_ID, result_summary(result)
        )
        return f"[[WORKFLOW_STATUS:{result.status}]]\n{result_summary(result)}"


class InorganicNewMaterialDiscoveryRole(Role):
    """The only active role registered by this service."""

    name: str = "inorganic_new_material"
    profile: str = (
        "生成式无机新材料发现服务：将数据库外新无机晶体的设计需求转为可执行的生成与热力学初筛任务。"
        "输入：化学式、元素体系、无机材料类别、应用场景或可追溯的上游材料结论之一；可选结构化 new_material 合同"
        "（allowed_elements、target_properties、validation_targets、max_candidates）。"
        "输出：候选 CIF/松弛结构路径、MatterSim--MP 近似形成能与高于凸包能、排序、阶段判断结论及完整 manifest；"
        "若无法从完整上游信息归纳无机材料起点，则向用户提示需补充的材料信息并等待补充，不宣称生成失败。"
        "执行链：约束规范化 → MatterGen 条件生成 → pymatgen 结构准入 → MatterSim 松弛 → MP 同元素体系竞争相查询与局部相图。"
        "边界：不用于已有材料查询、商品牌号、材料筛选/选型、FDM/FFF 丝材、商用耗材性质对比，"
        "也不处理合金或高温合金的元素配比、原子百分比与成分空间优化；这些分别应进入成熟材料或合金配比优化服务。"
        "结论只代表 MLFF--MP 热力学初筛；高温强度、蠕变、氧化、电导等目标性质必须由专项模型、DFT 或实验确认。"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._watch([UserRequirement])
        self.set_actions([InorganicNewMaterialDiscoveryAction])


class InorganicNewMaterialService:
    """Service-level Team orchestration kept outside the FastAPI transport layer."""

    @staticmethod
    def websocket_request(payload: dict) -> dict:
        """Normalize the historical WebSocket envelope without narrowing it."""
        if not isinstance(payload, dict):
            raise ValueError("initial WebSocket JSON must be an object")
        taskid = str(payload.get("taskid") or "").strip()
        if not taskid:
            raise ValueError("taskid is required")
        file_metadata = payload.get("file_metadata") or []
        if not isinstance(file_metadata, list):
            raise ValueError("file_metadata must be a list when provided")
        return {
            "taskid": taskid,
            "idea": str(payload.get("idea") or ""),
            "user_name": str(payload.get("user_name") or "-"),
            "file_metadata": file_metadata,
            "project_idea": json.dumps(payload, ensure_ascii=False),
        }

    @staticmethod
    def create_team() -> Team:
        team = Team()
        team.hire([InorganicNewMaterialDiscoveryRole()])
        return team

    async def run_round(self, websocket, team: Team, idea: str, n_round: int, user_name: str, taskid: str, file_metadata) -> None:
        """Run the historical Team envelope while preserving its frontend markers."""
        team.run_project(idea)
        await websocket.send_text("【XXX 开始: xxxx】")
        workflow_failed = False
        workflow_requires_input = False
        while n_round > 0:
            n_round -= 1
            for single_role in team.env.roles.values():
                if not single_role._observe():
                    continue
                single_role._no_think()
                if single_role.is_human:
                    await websocket.send_text(f"【{single_role._setting} 等待您 {single_role.rc.todo.desc}】")
                    if single_role.rc.todo.PROMPT_TEMPLATE is not None:
                        await websocket.send_text(single_role.rc.todo.PROMPT_TEMPLATE)
                    await websocket.send_text("[Pending]")
                    full_reply_content = await websocket.receive_text()
                    await websocket.send_text(f"【{single_role._setting} 已完成 {single_role.rc.todo.desc}】")
                else:
                    await websocket.send_text("[start]")
                    try:
                        full_reply_content = await single_role.rc.todo.run(
                            single_role.rc.history, websocket, user_name, taskid, file_metadata
                        )
                    except Exception as exc:
                        workflow_failed = True
                        if isinstance(exc, GenerationInputRequired):
                            workflow_requires_input = True
                            await websocket.send_text(f"### 需要补充生成信息\n\n{exc}")
                        else:
                            traceback.print_exc()
                            print(f"代码出错，请查看日志: {exc}")
                        if isinstance(exc, ValueError) and not isinstance(exc, GenerationInputRequired):
                            await websocket.send_text(
                                "### 需要补充生成条件\n\n"
                                f"{exc}\n\n"
                                "收到元素体系后，服务将继续执行候选结构生成、稳定性初筛和已知竞争相比较。"
                            )
                        elif not isinstance(exc, ValueError):
                            await websocket.send_text(
                                "### 新材料生成未能启动\n\n服务在初始化生成流程时遇到异常，已记录详细日志；请稍后重试。"
                            )
                        await websocket.send_text("[end]")
                        break

                    status_match = re.match(r"\[\[WORKFLOW_STATUS:(ok|failed|unavailable|timeout)\]\]\s*", str(full_reply_content or ""))
                    if status_match:
                        workflow_failed = status_match.group(1) != "ok"
                        full_reply_content = str(full_reply_content)[status_match.end():]
                    await websocket.send_text("[end]")

                full_reply_content = "" if full_reply_content is None else str(full_reply_content)
                message = Message(
                    content=full_reply_content,
                    role=single_role.profile,
                    cause_by=single_role.rc.todo,
                    sent_from=single_role,
                )
                single_role.rc.memory.add(message)
                single_role._set_state(state=-1)
                single_role.set_todo(None)
                single_role.publish_message(message)
                break
        if workflow_requires_input:
            await websocket.send_text("【XXX 等待补充: 已收到任务，等待补充材料生成信息后继续执行】")
        elif workflow_failed:
            await websocket.send_text("【XXX 未完成: 新材料生成未得到可用候选，请查看失败原因与下一步建议】")
        else:
            await websocket.send_text("【XXX 已完成: xxxx】")
