"""Safe, customer-visible fallback for requests outside trained expert domains."""
from __future__ import annotations

from typing import Any

from src.alloy_workflow.contracts import (
    is_composite_material_request,
    task_id,
    upstream_requirement,
)


def _family(text: str) -> tuple[str, list[dict[str, str]]]:
    """Return an exploratory formulation architecture, never a fake property model."""
    lowered = text.casefold()
    if is_composite_material_request(text, {}):
        return "复合材料/多相体系", [
            {"role": "连续相或基体", "symbol": "w_m", "range": "65–90 wt.%", "purpose": "先锁定树脂、金属或陶瓷基体及加工窗口"},
            {"role": "增强相或功能填料", "symbol": "w_r", "range": "5–30 wt.%", "purpose": "按强度、导热、导电或阻隔目标设置首轮梯度"},
            {"role": "界面/相容组分", "symbol": "w_i", "range": "0–5 wt.%", "purpose": "仅在明确相容性或润湿需求时纳入"},
            {"role": "加工助剂", "symbol": "w_a", "range": "0–5 wt.%", "purpose": "流变、分散或稳定需求；须与基体牌号一起验证"},
        ]
    if any(token in lowered for token in ("玻璃", "氧化物", "陶瓷", "cement", "水泥")):
        return "无机多组元体系", [
            {"role": "网络/主相", "symbol": "x_b", "range": "70–95 mol.%", "purpose": "确定主体网络或晶相"},
            {"role": "调节组分", "symbol": "x_m", "range": "5–25 mol.%", "purpose": "以目标热学/加工窗口设置梯度"},
            {"role": "微量功能组分", "symbol": "x_t", "range": "0–5 mol.%", "purpose": "逐个单因素引入并验证副作用"},
        ]
    return "单一金属/合金探索体系", [
        {"role": "基体元素", "symbol": "c_b", "range": "余量", "purpose": "按已知牌号或目标相结构确定"},
        {"role": "主合金化元素", "symbol": "c_m", "range": "5–30 wt.%（合计）", "purpose": "先围绕耐蚀、强度或高温稳定性建立局部窗口"},
        {"role": "微合金化元素", "symbol": "c_t", "range": "0–3 wt.%（合计）", "purpose": "逐项验证组织与工艺敏感性"},
        {"role": "杂质/残余元素", "symbol": "c_i", "range": "按标准或试验限值", "purpose": "不由本通用建议擅自放宽"},
    ]


def generic_fallback_plan(payload: dict[str, Any], reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    text, keys = upstream_requirement(payload)
    family, components = _family(text)
    effective = {
        "model_domain": "generic_composition_design_fallback_v1",
        "material_family": family,
        "formulation_components": components,
        "raw_requirement": text,
        "fallback_reason": reason,
    }
    plan = {
        "parser": "generic_llm_assisted_fallback_v1",
        "template": "generic_composition_design_fallback",
        "raw_requirement": text,
        "upstream_context_keys": keys,
        "effective_model_input": effective,
        "field_provenance": {"material_family": "rule_assisted", "formulation_components": "llm_assisted_exploration", "fallback_reason": "routing"},
        "default_assumptions": [{"field": "trained_expert", "value": "当前材料类型没有已完成训练并通过验证的专项模型", "status": "fallback_notice"}],
        "questions_to_confirm": ["确认基体/主相、目标性能与服役温度", "确认各组分可用范围、工艺路线与成本/法规边界", "补充至少一组可追溯组成—工艺—性质数据，以切换到可验证专项模型"],
        "evidence_notice": "以下为大模型/规则辅助的通用探索配比结构，不是已训练模型预测，不输出性能通过结论。",
    }
    return effective, plan


def generic_fallback_result(payload: dict[str, Any], reason: str, service_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    effective, plan = generic_fallback_plan(payload, reason)
    taskid = task_id(payload)
    stages = [
        {"label": "当前需求解析", "count": 1, "description": "保留当前用户提出的材料和应用信息。"},
        {"label": "通用配比结构", "count": 1, "description": "生成基体—功能组分—工艺变量的探索结构。"},
        {"label": "可验证试验方案", "count": 1, "description": "形成首轮梯度与需要记录的最少数据字段。"},
    ]
    result = {
        "taskid": taskid,
        "status": "completed_with_fallback",
        "service": service_name,
        "model_domain": "generic_composition_design_fallback_v1",
        "mode": "llm_assisted_generic_formulation",
        "model_version": "generic_llm_assisted_fallback_v1",
        "fallback_reason": reason,
        "requirement_interpretation": plan,
        "screening_conditions": {"material_family": effective["material_family"], "recommendation_scope": "通用探索配比；不作为性能预测或工程放行"},
        "sampling": {"generated": 1, "feasible": 1, "funnel_stages": stages},
        "initial_candidates": [{"candidate_id": "GEN-01", "material_family": effective["material_family"], "formulation_components": effective["formulation_components"], "candidate_kind": "llm_assisted_exploration_template"}],
        "all_candidates": [{"candidate_id": "GEN-01", "material_family": effective["material_family"], "formulation_components": effective["formulation_components"], "candidate_kind": "llm_assisted_exploration_template"}],
        "model_evidence": {"model_version": "无专项训练模型", "data_type": "大模型/规则辅助的通用配比结构", "validation": "当前材料体系未建立可用于性能预测的训练与独立验证集。"},
        "nonlinear_response_function": {"name": "not_available", "meaning": "当前没有该材料体系的已验证成分—性质预测函数。", "input": "待补充组成、工艺和服役条件", "output": "通用探索结构与试验数据模板；不输出性能数值。"},
        "next_actions": plan["questions_to_confirm"],
        "user_conclusion": f"当前请求已进入通用配比方案：识别为{effective['material_family']}，但该材料类型尚无已训练并通过验证的专项模型。结果给出大模型/规则辅助的首轮探索结构，仅供配方讨论与试验设计参考，不提供性能预测或工程放行结论。",
        "downstream_handoff_text": "交接通用配比变量、闭合约束、待补工艺字段和首轮试验记录模板；在形成可追溯数据集前不得将本结果用于性能门槛筛选。",
        "elapsed_seconds": 0.0,
    }
    return result, {"taskid": taskid, "raw_scope": effective, "model_domain": result["model_domain"]}
