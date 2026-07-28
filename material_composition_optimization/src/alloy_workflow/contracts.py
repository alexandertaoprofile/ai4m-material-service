"""Pure request normalization for the HEA/MPEA alloy service.

No FastAPI, WebSocket, object-storage or runner dependency is allowed here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


def task_id(payload: dict[str, Any]) -> str:
    external_taskid = str(payload.get("taskid") or f"alloy-{datetime.now(timezone.utc):%Y%m%d%H%M%S}").strip()
    if not external_taskid or len(external_taskid) > 512:
        raise ValueError("invalid taskid")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", external_taskid):
        return external_taskid
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", external_taskid).strip("_.-")[:72]
    digest = hashlib.sha256(external_taskid.encode("utf-8")).hexdigest()[:16]
    return f"{readable or 'alloy'}-{digest}"


def context_text(value: Any, limit: int = 12000) -> str:
    chunks: list[str] = []

    def visit(item: Any) -> None:
        if len("\n".join(chunks)) >= limit:
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            try:
                visit(json.loads(text))
            except (TypeError, json.JSONDecodeError):
                chunks.append(text)
        elif isinstance(item, dict):
            for key in ("idea", "content", "text", "query", "requirement", "summary", "message", "project_idea", "conversation_context", "upstream_result", "material_conclusion", "history", "messages", "conversation", "upstream_context", "previous_results"):
                if item.get(key) is not None:
                    visit(item[key])
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return "\n\n".join(chunks)[:limit]


def upstream_requirement(payload: dict[str, Any]) -> tuple[str, list[str]]:
    keys = [key for key in ("idea", "content", "query", "project_idea", "conversation_context", "upstream_result", "material_conclusion", "history", "messages", "conversation", "upstream_context", "previous_results") if payload.get(key) is not None]
    return context_text({key: payload[key] for key in keys}), keys


def is_alloy_request(text: str, scope: dict[str, Any]) -> bool:
    if scope.get("composition") or scope.get("allowed_elements") or scope.get("element_bounds_at_pct"):
        return True
    lowered = text.casefold()
    hea_system = any(token in lowered for token in ("hea", "mpea", "高熵", "多主元"))
    composition_intent = any(token in lowered for token in ("配比", "成分", "元素比例", "原子百分比", "at.%", "优化"))
    high_temp_alloy = ("高温合金" in lowered or "high-temperature alloy" in lowered) and composition_intent
    # 上游摘要常省略 HEA/MPEA 名称，却保留了 Ni-Co-Cr-Al-Ti 这类元素体系。
    # 至少三个受支持的金属元素，再加明确配比/成分意图，才视为合金服务请求；
    # 单个元素或普通“高温材料”描述不会因此被错误接入。
    alloy_elements = {
        "al", "co", "cr", "cu", "fe", "hf", "mn", "mo", "nb", "ni", "ta", "ti", "v", "w", "zr",
    }
    detected_elements = {
        symbol.casefold()
        for symbol in re.findall(r"(?<![A-Za-z])(?:Al|Co|Cr|Cu|Fe|Hf|Mn|Mo|Nb|Ni|Ta|Ti|V|W|Zr)(?![a-z])", text, flags=re.IGNORECASE)
    }
    explicit_element_system = len(detected_elements & alloy_elements) >= 3
    return (hea_system and composition_intent) or high_temp_alloy or (explicit_element_system and composition_intent)


def contract(payload: dict[str, Any]) -> dict[str, Any]:
    scope = payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {}
    if not isinstance(scope, dict):
        raise ValueError("alloy_optimization must be an object")
    upstream_context, upstream_keys = upstream_requirement(payload)
    if not is_alloy_request(upstream_context, scope):
        raise ValueError("本服务仅处理合金/高温合金的成分或配比优化；已有材料查询请使用成熟材料服务，非合金新材料生成请使用新材料服务")
    domain = scope.get("model_domain", "hea_mpea")
    if domain not in {"hea_mpea", "conventional_alloy", "refractory_calculated"}:
        raise ValueError("unsupported model_domain")
    return {"taskid": task_id(payload), "raw_requirement": upstream_context, "upstream_context": upstream_context, "upstream_context_keys": upstream_keys, "model_domain": domain, "composition": scope.get("composition"), "allowed_elements": scope.get("allowed_elements", []), "element_bounds_at_pct": scope.get("element_bounds_at_pct", {}), "processing_method": scope.get("processing_method"), "test_temperature_C": scope.get("test_temperature_C", 25), "objectives": scope.get("objectives", {}), "constraints": scope.get("constraints", {})}


def requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied = dict(payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {})
    idea, upstream_keys = upstream_requirement(payload)
    if not is_alloy_request(idea, supplied):
        raise ValueError("本服务仅适用于合金或高温合金的成分优化，不适用于一般高温材料查询或非合金新材料生成")
    text = idea.lower()
    # 上游摘要可能只保留“900°C”而不再出现“高温”字样；该温度级别与明确
    # 合金请求一起使用时，采用高温 HEA 模板，不把温度数字单独作为路由条件。
    high_temperature = bool(re.search(r"(?<!\d)(?:[6-9]\d{2}|[1-9]\d{3,})\s*(?:°\s*)?[cC](?![A-Za-z])", idea))
    engine = any(token in text for token in ("航空", "发动机", "aero", "engine", "turbine", "热端", "高温")) or high_temperature
    if engine:
        template = "aerospace_high_temperature_hea_exploration"
        inferred = {"model_domain": "hea_mpea", "allowed_elements": ["Ni", "Co", "Cr", "Al", "Ti"], "element_bounds_at_pct": {"Ni": [20, 40], "Co": [10, 30], "Cr": [10, 25], "Al": [5, 15], "Ti": [5, 20]}, "processing_method": "CAST", "test_temperature_C": 900, "screening_mode": "conservative_adaptive", "objectives": {"yield_strength_MPa": {"goal": "maximize"}, "phase_risk": {"goal": "minimize"}}}
        questions = ["请确认部件类型、服役温度与保温时间。", "请确认氧化环境、密度上限、制造路线和元素禁限。"]
    else:
        template = "generic_hea_exploration"
        inferred = {"model_domain": "hea_mpea", "allowed_elements": ["Co", "Cr", "Fe", "Mn", "Ni"], "element_bounds_at_pct": {"Co": [10, 30], "Cr": [10, 30], "Fe": [10, 30], "Mn": [10, 30], "Ni": [10, 30]}, "processing_method": "CAST", "test_temperature_C": 25, "screening_mode": "conservative_adaptive", "objectives": {"yield_strength_MPa": {"goal": "maximize"}, "phase_risk": {"goal": "minimize"}}}
        questions = ["请确认目标服役温度、允许元素体系、工艺和成本约束。"]
    effective = dict(inferred)
    effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
    provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "template_inference") for key in effective}
    return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": template, "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": value, "status": "requires_confirmation"} for key, value in inferred.items() if provenance[key] == "template_inference"], "questions_to_confirm": questions, "evidence_notice": "Template inference is exploratory only, not an engineering conclusion."}
