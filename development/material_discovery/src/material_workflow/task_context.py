"""Traceable application context, kept separate from executable chemistry.

Only current/user statements and explicitly supplied upstream context are read.
Assistant examples, tool output and system instructions are not task evidence.
This module never supplies formulas, element masks or numerical screening limits.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping


_HISTORY = ("conversation_context", "history", "messages", "previous_messages", "conversation")
_CONTEXT = ("context", "upstream_context", "upstream_result", "material_conclusion", "project_idea")
_SCENE_KEYS = ("application_scenario", "application", "scenario", "component")
_TEXT_KEYS = ("idea", "content", "text", "query", "requirement", "summary", "message", "user_conclusion")
_ROLES = re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[-*>]\s*)?(用户|我|助手|系统|工具|user|human|assistant|system|tool)(?:\s*[：:]\s*|\s*[·•][^\n]*\n)", re.I)
_SECTIONS = re.compile(r"(?:\[|【|===?\s*)(用户原始目标|历史执行上下文|对话历史)(?:\]|】|\s*===?)\s*[：:]?\s*")
_SWITCH = re.compile(r"换(?:个|一个|成)|改(?:做|为|成)|另(?:一个|一项)|不再|新的任务|重新开始")
_NEGATED = re.compile(r"(?:不是|并非|不用于|不针对|不考虑|不关注|不做|无需|不需要)")
_SCENE_SIGNAL = re.compile(
    r"应用(?:场景|需求|方向)|使用场景|研发背景|需求背景|用于|应用于|面向|针对|服务于|"
    r"芯片|封装|电池|电解质|电极|火箭|贮箱|壳体|发动机|燃烧室|航空|航天|"
    r"传感器|探测器|器件|光电|压电|铁电|催化|吸附|储氢|热电|半导体|绝缘层|隔热层|"
    r"implant|battery|electrolyte|sensor|device|packaging", re.I)
_TECHNICAL_ONLY = re.compile(
    r"明确同意|开放化学空间|周期(?:无机)?晶体|无机周期晶体|晶体结构|晶体探索|"
    r"结构(?:预测|探索|生成)|化学式|元素(?:体系|集合|组合|白名单)|低凸包能|"
    r"稳定性|带隙|模量|\b(?:CSP|DNG|OMatG|IRL)\b", re.I)
_LABEL = re.compile(r"(?:用于|应用于|面向|针对|服务于|作为|为)([^，。；：:\n]{2,100})")
_LABEL_END = re.compile(r"寻找|找一种|筛选|开发|研发|设计|探索|选型|挑选|选择|生成|比较|你做|做一个|做一种")
_GOALS = {
    "band_gap": r"带隙|band.?gap|电子绝缘|电绝缘|绝缘层|绝缘材料",
    "bulk_modulus": r"体积模量|bulk.modulus",
    "shear_modulus": r"剪切模量|shear.modulus",
    "electron_effective_mass": r"电子有效质量",
    "hole_effective_mass": r"空穴有效质量",
    "dielectric_constant_x": r"介电常数|介电响应",
}
_FOLLOW_UP = {
    "离子电导率与迁移势垒": r"离子(?:电导|导电|输运)|迁移势垒|ion(?:ic)?.conduct",
    "压电响应": r"压电|piezo",
    "铁电极化与翻转": r"铁电|ferroelectric",
    "非线性光学响应": r"非线性光学",
    "热膨胀及热失配": r"热膨胀|热失配|CTE",
    "目标温度下的热稳定性": r"热稳定|耐高温|高温稳定|耐热",
    "屈服、疲劳及工况力学": r"屈服|疲劳|蠕变|承压|强度",
    "热导率": r"导热|热导率|thermal.conduct",
    "绝缘击穿与封装适配": r"击穿|封装",
    "目标工况下的构件响应": r"火箭|贮箱|壳体|外壳",
}


def _clean(text, limit=1200):
    text = re.sub(r"<<<[^>]*>>>|<[^>]*>", " ", str(text))
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|[-*>]\s*)", "", text)
    text = text.replace("```", "").replace("$$", "")
    text = re.sub(r"\s+", " ", text).strip(" 。；;\t")
    return text[:limit]


def _records(value, source, depth=0):
    if depth > 6 or value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            return _records(decoded, source, depth + 1)
        turns = list(_ROLES.finditer(value))
        if turns:
            rows = []
            for index, match in list(enumerate(turns))[-40:]:
                if match.group(1).lower() not in {"用户", "我", "user", "human"}:
                    continue
                end = turns[index + 1].start() if index + 1 < len(turns) else len(value)
                rows.append({"text": value[match.end():end][:32000], "source": f"{source}.user[{index}]"})
            return rows
        return [{"text": value[:32000], "source": source}]
    if isinstance(value, list):
        return [row for index, item in list(enumerate(value))[-40:]
                for row in _records(item, f"{source}[{index}]", depth + 1)]
    if not isinstance(value, Mapping):
        return []
    role = str(value.get("role") or value.get("speaker") or "user").casefold()
    if role not in {"user", "human", "customer", "用户"}:
        return []
    rows = []
    for key in (*_SCENE_KEYS, *_TEXT_KEYS):
        if isinstance(value.get(key), str):
            rows.extend(_records(value[key], f"{source}.{key}", depth + 1))
    for key in (*_HISTORY, *_CONTEXT, "result", "data", "task_context", "selected_material", "confirmed_material"):
        if value.get(key) is not None:
            rows.extend(_records(value[key], f"{source}.{key}", depth + 1))
    return rows


def _scene(record, explicit=False):
    # A negated former scene is not a candidate for inheritance.  Clauses after
    # a contrast/change marker can still name the new positive application.
    text = record["text"]
    if _NEGATED.search(text):
        text = re.split(r"而是|改做|改为|改成|但现在|现在要|现在做", text)[-1]
    statements = [part.strip() for part in re.split(r"[。；\n]", text) if part.strip()]
    selected = []
    for statement in statements:
        if _NEGATED.search(statement):
            continue
        if not explicit and not _SCENE_SIGNAL.search(statement):
            if selected and any(re.search(pattern, statement, re.I) for pattern in (*_GOALS.values(), *_FOLLOW_UP.values())):
                selected.append(_clean(statement, 500))
            continue
        stripped = _TECHNICAL_ONLY.sub("", statement)
        # An execution-only sentence such as "针对 LiAlO2 做结构预测" is not
        # an application.  Chemical tokens must not make it look like one.
        stripped = re.sub(r"(?:[A-Z][a-z]?\d*)+|[\d\s,，、.%:/—–-]+", "", stripped)
        stripped = re.sub(r"针对|面向|希望|进行|开展|探索|生成|比较|候选|做|低|高|关注|并|和|的|与|在|按|用户|已确认|明确同意|本次", "", stripped)
        if not explicit and len(stripped) < 4:
            continue
        selected.append(_clean(statement, 500))
    if not selected:
        return None
    statement = "；".join(selected[:2])
    label = ""
    if explicit:
        label = statement[:100]
    else:
        match = _LABEL.search(selected[0])
        if match:
            label = _LABEL_END.split(match.group(1), 1)[0].strip(" 的，。； ")
            label = re.sub(r"(?:的)?(?:部分|用途|应用场景)$", "", label).strip(" 的，。； ")
    return {"statement": statement, "label": label, "source": record["source"]}


def build_task_context(payload, source, raw, current):
    """Choose the current scene first, otherwise the latest relevant user scene.

    The returned background is for presentation and traceability only; execution
    continues to use the unmodified current-turn/confirmed chemical contract.
    """
    containers = [("payload", payload)]
    for key in ("new_material", "mattergen", "generation_constraints", "constraints"):
        if isinstance(payload.get(key), Mapping):
            containers.insert(0, (key, payload[key]))
            break
    chosen = _scene({"text": current, "source": "current_request"})
    for container_name, container in containers:
        if chosen:
            break
        brief = container.get("task_context")
        fields = [(key, container.get(key)) for key in _SCENE_KEYS]
        if isinstance(brief, Mapping):
            fields.extend((f"task_context.{key}", brief.get(key)) for key in _SCENE_KEYS)
        for name, value in fields:
            if isinstance(value, str) and value.strip():
                chosen = _scene({"text": value, "source": f"{container_name}.{name}"}, explicit=True)
                if chosen:
                    break
        if chosen:
            break
    current_scene = chosen is not None
    if not chosen and not _SWITCH.search(current):
        records = []
        # Original-goal sections precede role-labelled history; history is newer.
        sections = list(_SECTIONS.finditer(raw))
        for index, match in enumerate(sections):
            if match.group(1) == "历史执行上下文":
                continue
            end = sections[index + 1].start() if index + 1 < len(sections) else len(raw)
            section = raw[match.end():end]
            from .constraints import _CURRENT_TASK_MARKER
            task = _CURRENT_TASK_MARKER.search(section)
            section = section[:task.start()] if task else section
            records.extend(_records(section, f"idea.{match.group(1)}"))
        if not sections and _ROLES.search(raw):
            records.extend(_records(raw, "idea"))
        for key in (*_CONTEXT, *_HISTORY):
            for container_name, container in reversed(containers):
                if container.get(key) is not None:
                    records.extend(_records(container[key], f"{container_name}.{key}"))
        for record in reversed(records):
            if _clean(record["text"]) == _clean(current):
                continue
            chosen = _scene(record)
            if chosen:
                break
            if _SWITCH.search(record["text"]):
                break  # A newer explicit task reset supersedes older applications.
    scenario_text = chosen["statement"] if chosen else ""
    # Background-only interests are visibly distinguished from execution goals.
    goal_text = current + "\n" + scenario_text
    performance_goals = [key for key, pattern in _GOALS.items() if re.search(pattern, goal_text, re.I)]
    follow_up = [label for label, pattern in _FOLLOW_UP.items() if re.search(pattern, goal_text, re.I)]
    return {
        "scenario": scenario_text,
        "scenario_label": chosen["label"] if chosen else "",
        "scenario_source": chosen["source"] if chosen else "",
        "scenario_origin": "current" if current_scene else "upstream" if chosen else "unspecified",
        "current_request": _clean(current, 4000),
        "performance_goals": performance_goals,
        "follow_up_validation": follow_up,
        "context_role": "应用背景；不自动转为化学式、元素或数值筛选条件",
    }
