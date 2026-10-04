"""Single, opt-in requirement review over the existing /start WebSocket (v1)."""
from __future__ import annotations

import os
import re
import uuid
from typing import Any


class ReviewStopped(Exception):
    """User explicitly stopped this generation request."""


def review_enabled() -> bool:
    return (os.getenv("REQUIREMENT_CONFIRMATION_ENABLED") == "1"
            and os.getenv("REQUIREMENT_REVIEW_PROTOCOL") == "structured")


def parse_review_answer(answer: str) -> tuple[str, Any]:
    """Classify an explicit user choice without inferring consent to open DNG."""
    value = answer.strip()
    match = re.fullmatch(r"(?:化学式|formula)\s*[：:]\s*(.+)", value, re.I | re.S)
    if match:
        from .omatg_contract import formula_counts
        formula = match.group(1).strip()
        formula_counts(formula)
        return "formula", formula
    match = re.fullmatch(r"(?:元素|元素范围|elements?)\s*[：:]\s*(.+)", value, re.I | re.S)
    if match:
        value = match.group(1).strip()
    else:
        # A bare formula is unambiguous; accept it as a backward-compatible answer.
        from .omatg_contract import formula_counts
        try:
            counts = formula_counts(value)
        except ValueError:
            pass
        else:
            if len(counts) > 1 or any(count > 1 for count in counts.values()):
                return "formula", value
    if re.match(r"^(?:需求|材料大类|开放探索|open\s+exploration)\s*[：:]", value, re.I):
        need = re.split(r"[：:]", value, maxsplit=1)[1].strip()
        if not need:
            raise ValueError("开放探索需要填写材料方向或希望筛选的性质。")
        return "open", need
    from .constraints import _elements_from_context
    elements = _elements_from_context("用户补充：" + value)
    if not elements:
        raise ValueError("请按“化学式：BaTiO3”“元素：Fe Cr Ni”或“开放探索：描述需求”填写。")
    if len(elements) > 8:
        raise ValueError("当前最多支持 8 种允许元素。")
    return "elements", elements


def merge_review_answer(payload: dict[str, Any], answer: str) -> dict[str, Any]:
    kind, parsed = parse_review_answer(answer)
    original = str(payload.get("idea") or payload.get("instruction") or "")
    merged = {**payload, "idea": f"{original}\n用户补充：{answer}"}
    source = dict(payload.get("new_material") or {})
    source["raw_requirement"] = f"{source.get('raw_requirement') or original}\n用户补充：{answer}"
    if kind == "formula":
        source.update(target_formula=parsed, generation_mode="csp", allow_open_chemical_space=False)
        source.pop("allowed_elements", None)
        source.pop("elements", None)
    elif kind == "elements":
        source.update(allowed_elements=parsed, generation_mode="dng", allow_open_chemical_space=False)
        source.pop("target_formula", None)
    else:
        source.update(generation_mode="dng", allow_open_chemical_space=True)
        source.pop("target_formula", None)
        source.pop("allowed_elements", None)
        source.pop("elements", None)
    merged["new_material"] = source
    return merged


async def request_element_clarification(websocket: Any, taskid: str, reason: str,
                                        stageid: str = "FILAMENT_SELECTION_OPTIMIZATION") -> str | None:
    """Ask once; retry invalid replies on the same request. Disconnect propagates."""
    request_id = uuid.uuid4().hex
    question_id = "element_system"
    await websocket.send_json({
        "type": "requirement_review_request", "schema_version": 1,
        "taskid": taskid, "request_id": request_id, "stageid": stageid,
        "data": {
            "title": "选择新材料结构生成的起点",
            "fields": [],
            "questions": [{"id": question_id, "question": (
                "请选择一种方式填写：化学式：BaTiO3（按固定组成生成结构）；"
                "元素：Fe Cr Ni（只在这些元素中探索组成和结构）；"
                "需求：描述材料类别、用途或目标性质（明确同意开放元素空间，生成后筛选）；"
                "也可写“开放探索：...”或“材料大类：...”。"
                "也可直接填写化学式或元素。仅写笼统需求不会自动开放化学空间。"),
                           "answer": "", "required": True}],
            "baseline_markdown": "",
            "allow_comment": False, "auto_continue": False,
        },
    })
    while True:
        reply = await websocket.receive_json()
        data = reply.get("data") if isinstance(reply, dict) else None
        data = data if isinstance(data, dict) else {}
        action = reply.get("action") if isinstance(reply, dict) else None
        error = None
        answer = None
        if not isinstance(reply, dict) or reply.get("type") != "checkpoint.reply" or reply.get("schema_version") != 1:
            error = "需要 v1 checkpoint.reply 消息。"
        elif reply.get("taskid") != taskid or reply.get("request_id") != request_id:
            error = "taskid 或 request_id 不匹配。"
        elif action not in ("apply", "continue", "stop"):
            error = "action 必须是 apply、continue 或 stop。"
        elif action == "apply":
            edits = data.get("edits")
            answers = data.get("answers")
            if not isinstance(edits, list) or edits or not isinstance(answers, list):
                error = "请填写化学式、元素范围或明确选择开放探索。"
            elif len(answers) != 1 or not isinstance(answers[0], dict) or answers[0].get("id") != question_id:
                error = "请回答元素问题 element_system。"
            elif not isinstance(answers[0].get("answer"), str) or not answers[0]["answer"].strip():
                error = "补充内容不能为空。"
            elif len(answers[0]["answer"]) > 2000:
                error = "补充内容过长。"
            else:
                answer = answers[0]["answer"].strip()
                try:
                    parse_review_answer(answer)
                except ValueError as exc:
                    error = str(exc)
        if error:
            await websocket.send_json({"type": "requirement_review_error", "schema_version": 1,
                                       "taskid": taskid, "request_id": request_id, "message": error})
            continue
        await websocket.send_json({"type": "requirement_review_accepted", "schema_version": 1,
                                   "taskid": taskid, "request_id": request_id,
                                   "action": action, "will_reanalyze": action == "apply"})
        if action == "stop":
            raise ReviewStopped()
        return answer if action == "apply" else None
