# src/MNS_CaseHub/cases/material_discovery_demo/handler.py
# -*- coding: utf-8 -*-

import json
from typing import Any, Dict, List, Optional

from .pipeline import run_material_discovery


async def run_material_discovery_over_ws(
    websocket,
    *,
    idea: str,
    taskid: str,
    user_name: str,
    file_metadata: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    给 team_config 里的 todo 调用：
    - 运行 pipeline
    - websocket 发一份 JSON（前端可以直接渲染）
    - 返回一个简短字符串作为该角色的最终 content
    """
    file_metadata = file_metadata or []

    # 你这里可以定义前端约定的 message type
    await websocket.send_text("[material_discovery:start]")

    result = run_material_discovery(
        idea=idea,
        taskid=taskid,
        user_name=user_name,
        file_metadata=file_metadata,
        max_candidates=5,
        prefer_stable=True,
        whitelist_path=None,
        use_mpapi_python=False,  # 如果你确认 mp-api env 里依赖齐全，再改 True
    )

    # 给前端一份结构化 JSON（建议前端直接吃）
    await websocket.send_text(json.dumps({
        "type": "material_discovery_result",
        "payload": result,
    }, ensure_ascii=False))

    await websocket.send_text("[material_discovery:end]")

    if not result.get("ok"):
        return f"材料初筛失败：{result.get('error','unknown error')}"

    # 给 LLM 的文本返回尽量短，前端主要看 payload
    mpid = None
    try:
        mpid = result["manifest"]["query"]["primary_material_id"]
    except Exception:
        pass
    return f"材料初筛已完成。primary={mpid or 'N/A'}，已生成结构 GLB 与候选表格。"
