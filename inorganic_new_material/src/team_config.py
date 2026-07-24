# -*- coding: utf-8 -*-
"""无机新材料发现服务的运行编排层。

``main.py`` 负责 HTTP/WebSocket 传输；本模块按阶段编排约束提取、生成、
结构准入、热力学初筛和展示。旧 MP/ALIGNN 成熟材料查询动作不属于本服务，
正常启动时不得导入。
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
from src.service_identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE


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
    """执行一次 MatterGen → pymatgen → MatterSim/MP 无机晶体发现流程。"""

    name: str = ACTION_NAME
    desc: str = ACTION_DESCRIPTION

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
        # 阶段 1：保留母服务传来的完整上下文、任务和文件元数据，形成服务 payload。
        websocket = args[0]
        user_name, taskid, file_metadata = args[1], args[2], args[3]
        payload = self._payload_from_instruction(instruction, taskid, user_name, file_metadata)
        config = load_config("config/config.yaml")
        llm = SeLLM(base_url=config["base_url_1"], api_key=config["api_key"])

        # 阶段 2：先确定性解析生成条件；缺失时仅请求受约束的 LLM 补全，随后仍须校验。
        async def announce_missing_input_inference() -> None:
            logger.info("[CONSTRAINT_LLM] deterministic extraction empty; requesting constrained LLM inference taskid={}", taskid)
            await websocket.send_text(
                "正在解析上游任务并归纳无机晶体生成条件；若无法形成可追溯的材料起点，将提示需要补充的信息。\n"
            )
        payload, constraints = await resolve_generation_request(
            payload,
            on_missing_input_inference=announce_missing_input_inference,
        )

        # 阶段 3：发送既有 progress/正文边界，说明已确认或仍待补充的生成条件。
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

        # 阶段 4：在独立线程运行领域流水线，并并行转发已存在的阶段进度。
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

        # 阶段 5：基于已保存的计算结果发送展示资产和权威结论；不由展示 LLM 改写事实。
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
    """本服务注册的唯一活动角色。"""

    name: str = ROLE_NAME
    profile: str = ROLE_PROFILE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._watch([UserRequirement])
        self.set_actions([InorganicNewMaterialDiscoveryAction])


class InorganicNewMaterialService:
    """位于 FastAPI 传输层之外的服务级 Team 编排。"""

    @staticmethod
    def websocket_request(payload: dict) -> dict:
        """阶段 0：兼容既有 WebSocket 信封，不缩窄或丢弃上游上下文。"""
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
        # 阶段 0b：仅装配当前无机发现角色，禁止注册已退役的已有材料查询角色。
        team = Team()
        team.hire([InorganicNewMaterialDiscoveryRole()])
        return team

    async def run_round(self, websocket, team: Team, idea: str, n_round: int, user_name: str, taskid: str, file_metadata) -> None:
        """阶段 6：执行历史 Team 信封，并保持既有前端标记与结束语义。"""
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
