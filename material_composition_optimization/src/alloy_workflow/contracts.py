"""Pure request normalization for the HEA/MPEA alloy service.

No FastAPI, WebSocket, object-storage or runner dependency is allowed here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


_COMPOSITE_MATERIAL_PATTERN = re.compile(
    r"复合材料|复材|树脂|环氧|纤维|碳纤维|玻璃纤维|填料|增强相|聚合物|"
    r"金属基复合|陶瓷基复合|CFRP|GFRP|CF[/-]?(?:PEEK|PEKK|PA|PPS)|"
    r"PEEK|PEKK|PEI|PPS|CFRP|GFRP|epoxy|resin|fiber|composite",
    re.IGNORECASE,
)

# 路由遵循“材料体系和服役机制优先于泛化温度词”。发动机高温承力件的
# 蠕变/持久问题使用镍基热端数据；HEA/MPEA 用于明确的多主元成分空间探索，
# 重点比较强度、硬度和相组成倾向。
_NI_HOT_END_PATTERN = re.compile(
    r"镍基|高温镍|镍基高温|蠕变|持久(?:寿命|强度)?|单晶(?:叶片)?|定向凝固|"
    r"航空(?:航天)?高温合金|发动机热端|燃气轮机|涡轮(?:叶片)?|superalloy|"
    r"nickel.base|inconel|cmsx|ren[eé]|mar-m",
    re.IGNORECASE,
)
_ENGINE_PATTERN = re.compile(r"火箭发动机|航空发动机|发动机|燃气轮机|涡轮|热端", re.IGNORECASE)
_HIGH_TEMPERATURE_PATTERN = re.compile(
    r"高温|蠕变|持久|热端|(?:[6-9]\d{2}|[1-9]\d{3,})\s*(?:°\s*)?[cC]",
    re.IGNORECASE,
)
_HEA_EXPLORATION_PATTERN = re.compile(
    r"高熵|多主元|HEA|MPEA|at\.?%|原子百分比|成分空间|元素空间|探索(?:设计|筛选|优化)?|"
    r"硬度|相(?:组成)?风险|相稳定",
    re.IGNORECASE,
)
_CHIP_GLASS_PATTERN = re.compile(
    r"玻璃基板|封装玻璃|芯片玻璃|玻璃配方|低硼无碱|铝硼硅酸盐玻璃|"
    r"chip.?glass|glass.?substrate|alumino.?borosilicate",
    re.IGNORECASE,
)


def is_reusable_rocket_stainless_intent(text: str, scope: dict[str, Any]) -> bool:
    """Recognize natural descriptions of reusable rocket stainless structures."""
    if scope.get("model_domain") == "reusable_rocket_stainless":
        return True
    lowered = text.casefold()
    explicit_terms = ("可回收火箭", "火箭贮箱", "火箭壳体", "低温不锈钢", "奥氏体不锈钢", "304l", "301ln", "cryoforming", "30x")
    if any(term in lowered for term in explicit_terms):
        return True
    rocket = any(term in lowered for term in ("火箭", "航天器", "航天飞行器"))
    stainless = any(term in lowered for term in ("不锈钢", "stainless"))
    reusable_structure = any(term in lowered for term in ("可回收", "回收", "贮箱", "壳体", "外壳", "承压壳", "表面壳"))
    return rocket and stainless and reusable_structure


def is_chip_glass_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "chip_glass_thermomechanical_family_v1" or bool(_CHIP_GLASS_PATTERN.search(text))

# 明确识别为航空/发动机热端镍基合金、但用户尚未给出工况时的首轮筛选模板。
# 这些值是可见、可覆盖的平台默认工况，不是从用户文本中推断出的事实。
_HOT_END_PLATFORM_DEFAULTS: dict[str, Any] = {
    "element_bounds_wt_percent": {
        # 覆盖当前单晶来源合金（Nasair 100、CMSX、PWA、René）的实有元素；
        # 数值在其记录成分范围外保留了局部扰动余量，而不是使用 0–100 的无约束范围。
        "Ni": [30, 75], "Cr": [5, 12], "Co": [0, 12], "Re": [0, 4],
        "Al": [4, 7], "Ta": [2, 13], "W": [4, 12], "Ti": [0, 4],
        "Mo": [0, 2.5], "V": [0, 0.1], "C": [0, 0.1], "B": [0, 0.02],
        "Nb": [0, 1], "Hf": [0, 2],
    },
    "manufacturing_route": "single_crystal",
    "heat_treatment": "solution_stage_1_temp_C=1302; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=982; precipitation_stage_1_time_h=5; precipitation_stage_2_temp_C=871; precipitation_stage_2_time_h=20",
    "test_temperature_C": 950,
    "applied_stress_MPa": 250,
    "screening_thresholds": {"uts_min_MPa": 900, "proof_strength_min_MPa": 500, "rupture_life_min_h": 250},
}


def _hot_end_context_overrides(text: str) -> dict[str, Any]:
    """Read explicit thermal-service values from the upstream requirement.

    The upstream gateway frequently sends the design brief as prose rather than
    as an ``alloy_optimization`` object.  A temperature/load pair and a stated
    life target are sufficiently unambiguous to become visible run inputs.
    Explicit structured inputs are merged afterwards and always take priority.
    """
    overrides: dict[str, Any] = {}
    pair = re.search(
        r"(?<!\d)(?P<temperature>\d{2,4}(?:\.\d+)?)\s*(?:°\s*)?[cCＣ]\s*"
        r"(?:[/／,，;；]|在)\s*(?P<stress>\d{1,4}(?:\.\d+)?)\s*(?:MPa|mpa)",
        text,
    )
    if pair:
        overrides["test_temperature_C"] = float(pair.group("temperature"))
        overrides["applied_stress_MPa"] = float(pair.group("stress"))

    lifetime = re.search(
        r"(?:蠕变(?:断裂)?寿命|持久寿命|寿命)\s*(?:超过|大于|高于|不少于|至少|≥|>=)\s*"
        r"(?P<hours>\d+(?:\.\d+)?)\s*(?:小?时|h)\b",
        text,
        flags=re.IGNORECASE,
    )
    if lifetime:
        overrides["screening_thresholds"] = {
            **_HOT_END_PLATFORM_DEFAULTS["screening_thresholds"],
            "rupture_life_min_h": float(lifetime.group("hours")),
        }
    return overrides


def is_composite_material_request(text: str, scope: dict[str, Any]) -> bool:
    """Reject composite systems before an element-only alloy model is selected."""
    scope_text = json.dumps(scope, ensure_ascii=False, default=str)
    return bool(_COMPOSITE_MATERIAL_PATTERN.search(f"{text}\n{scope_text}"))


def is_ni_hot_end_intent(text: str, scope: dict[str, Any]) -> bool:
    """Recognize hot-section nickel-alloy tasks from material or service cues."""
    if scope.get("model_domain") == "ni_superalloy_hot_end":
        return True
    return bool(_NI_HOT_END_PATTERN.search(text)) or bool(
        _ENGINE_PATTERN.search(text) and _HIGH_TEMPERATURE_PATTERN.search(text)
    )


def is_hea_exploration_intent(text: str, scope: dict[str, Any]) -> bool:
    """HEA requires an explicit multicomponent or exploration signal, never temperature alone."""
    if scope.get("model_domain") == "hea_mpea":
        return True
    lowered = text.casefold()
    explicit_system = any(token in lowered for token in ("hea", "mpea", "高熵", "多主元"))
    composition_intent = any(token in lowered for token in ("配比", "成分", "元素比例", "原子百分比", "at.%", "优化", "筛选", "设计"))
    detected_elements = {
        symbol.casefold()
        for symbol in re.findall(
            r"(?<![A-Za-z])(?:Al|Co|Cr|Cu|Fe|Hf|Mn|Mo|Nb|Ni|Ta|Ti|V|W|Zr)(?![a-z])",
            text,
            flags=re.IGNORECASE,
        )
    }
    explicit_multielement_system = len(detected_elements) >= 3
    return explicit_system or bool(
        (_HEA_EXPLORATION_PATTERN.search(text) or explicit_multielement_system)
        and composition_intent
    )


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
    if is_composite_material_request(text, scope):
        return False
    if scope.get("composition") or scope.get("allowed_elements") or scope.get("element_bounds_at_pct") or scope.get("composition_wt_percent") or scope.get("element_bounds_wt_percent"):
        return True
    lowered = text.casefold()
    if is_reusable_rocket_stainless_intent(text, scope):
        return True
    if is_ni_hot_end_intent(text, scope):
        return True
    hea_system = is_hea_exploration_intent(text, scope)
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
    if is_composite_material_request(upstream_context, scope):
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    glass_request = is_chip_glass_intent(upstream_context, scope)
    if not glass_request and not is_alloy_request(upstream_context, scope):
        raise ValueError("本服务仅处理合金/高温合金的成分或配比优化；已有材料查询请使用成熟材料服务，非合金新材料生成请使用新材料服务")
    domain = scope.get("model_domain", "hea_mpea")
    if domain not in {"hea_mpea", "conventional_alloy", "refractory_calculated", "ni_superalloy_hot_end", "reusable_rocket_stainless", "chip_glass_thermomechanical_family_v1"}:
        raise ValueError("unsupported model_domain")
    common = {"taskid": task_id(payload), "raw_requirement": upstream_context, "upstream_context": upstream_context, "upstream_context_keys": upstream_keys, "model_domain": domain, "objectives": scope.get("objectives", {}), "constraints": scope.get("constraints", {})}
    if domain == "ni_superalloy_hot_end":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "manufacturing_route": scope.get("manufacturing_route"), "heat_treatment": scope.get("heat_treatment"), "test_temperature_C": scope.get("test_temperature_C"), "applied_stress_MPa": scope.get("applied_stress_MPa"), "screening_thresholds": scope.get("screening_thresholds", {}), "casting_gradient_K_per_mm": scope.get("casting_gradient_K_per_mm"), "num_candidates": scope.get("num_candidates", 120), "random_seed": scope.get("random_seed", 20260901)}
    if domain == "reusable_rocket_stainless":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "test_temperature_K": scope.get("test_temperature_K"), "processing": scope.get("processing", {}), "component": scope.get("component"), "weld_state": scope.get("weld_state", "base_metal"), "thickness_mm": scope.get("thickness_mm"), "low_temperature_verification_K": scope.get("low_temperature_verification_K", [90, 111]), "verification_focus": scope.get("verification_focus", []), "num_candidates": scope.get("num_candidates", 40), "random_seed": scope.get("random_seed", 20260902)}
    if domain == "chip_glass_thermomechanical_family_v1":
        return {**common, "composition_basis": "mol_percent", "composition_mol_percent": scope.get("composition_mol_percent"), "oxide_bounds_mol_percent": scope.get("oxide_bounds_mol_percent", {}), "screening_thresholds": scope.get("screening_thresholds", {}), "num_candidates": scope.get("num_candidates", 80), "random_seed": scope.get("random_seed", 20260904), "application": scope.get("application", "芯片封装玻璃基板的热失配与挠曲初筛"), "service_options": scope.get("service_options", {})}
    return {**common, "composition": scope.get("composition"), "allowed_elements": scope.get("allowed_elements", []), "element_bounds_at_pct": scope.get("element_bounds_at_pct", {}), "processing_method": scope.get("processing_method"), "test_temperature_C": scope.get("test_temperature_C", 25)}


def hot_end_missing_fields(scope: dict[str, Any]) -> list[dict[str, str]]:
    """Fields that must be explicit before a conditional proposal is run."""
    requirements = (
        ("element_bounds_wt_percent", "允许元素及各元素 wt.% 上下限"),
        ("manufacturing_route", "制造路线：cast、directionally_solidified 或 single_crystal"),
        ("heat_treatment", "热处理制度"),
        ("test_temperature_C", "目标温度（°C）"),
        ("applied_stress_MPa", "蠕变载荷（MPa）"),
    )
    return [{"field": key, "label": label} for key, label in requirements if scope.get(key) in (None, "", {}, [])]


def requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied = dict(payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {})
    idea, upstream_keys = upstream_requirement(payload)
    if is_composite_material_request(idea, supplied):
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    glass_intent = is_chip_glass_intent(idea, supplied)
    if not glass_intent and not is_alloy_request(idea, supplied):
        raise ValueError("本服务仅适用于合金或高温合金的成分优化，不适用于一般高温材料查询或非合金新材料生成")
    if glass_intent:
        inferred = {"model_domain": "chip_glass_thermomechanical_family_v1", "composition_basis": "mol_percent", "num_candidates": 80, "random_seed": 20260904, "application": "芯片封装玻璃基板的热失配与挠曲初筛", "objectives": {"CTE_linear_0_to_300C": {"goal": "minimize"}, "young_modulus_GPa": {"goal": "maximize"}, "stress_optical_coefficient_nm_cm_per_MPa": {"goal": "minimize"}}, "screening_thresholds": {}}
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "chip_glass_thermomechanical_local_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可提供实际氧化物 mol% 边界、目标 CTE/E/SOC 门槛、玻璃厚度、层堆和温度循环，以替换默认探索条件。"], "evidence_notice": "候选只在低硼无碱玻璃家族的可追溯局部邻域内生成；残余应力和翘曲须结合层堆与热历史计算。"}
    rocket_intent = is_reusable_rocket_stainless_intent(idea, supplied)
    if rocket_intent:
        inferred = {"model_domain": "reusable_rocket_stainless", "element_bounds_wt_percent": {"Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0], "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08]}, "test_temperature_K": 293, "processing": {"material_state": "solution_annealed", "solution_treatment_temperature_K": 1323, "solution_treatment_time_s": 3600, "quench": "water", "product_form_code": 1, "melting_route_code": 1}, "component": "可回收火箭贮箱或承压壳体（母材）", "weld_state": "base_metal", "low_temperature_verification_K": [90, 111], "verification_focus": ["cryogenic_toughness", "weld", "fatigue", "LOX_compatibility"], "num_candidates": 40, "objectives": {"yield_strength": 1, "uts": 1, "elongation": 1}}
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "reusable_rocket_stainless_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可继续提供目标温度、板厚、焊接状态、成分 wt.% 边界和实际热处理，以替换本轮可见默认条件。"], "evidence_notice": "293–1273 K 输出为短时拉伸候选筛选；更低温度转为 301/304L 参考和验证规划。"}
    hea_intent = is_hea_exploration_intent(idea, supplied)
    hot_end_intent = is_ni_hot_end_intent(idea, supplied)
    if hot_end_intent:
        inferred = {
            "model_domain": "ni_superalloy_hot_end", "num_candidates": 120,
            "screening_mode": "conservative_anchor_local",
            "objectives": {"ultimate_tensile_strength_MPa": {"goal": "maximize"}, "proof_strength_0p2_MPa": {"goal": "maximize"}, "rupture_life": {"goal": "maximize"}},
            **_HOT_END_PLATFORM_DEFAULTS,
        }
        context_overrides = _hot_end_context_overrides(idea)
        effective = dict(inferred)
        effective.update(context_overrides)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {
            key: (
                "user" if key in supplied and supplied[key] not in (None, [], {}, "")
                else "upstream_context" if key in context_overrides
                else "template_inference"
            )
            for key in effective
        }
        missing = hot_end_missing_fields(effective)
        defaults_used = [key for key in _HOT_END_PLATFORM_DEFAULTS if provenance[key] == "template_inference"]
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "hot_end_ni_superalloy_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in defaults_used], "questions_to_confirm": ([f"请补充：{item['label']}。" for item in missing] or (["已采用平台默认热端工况完成首轮筛选；可继续提供实际路线、热处理、温度、载荷或 wt.% 边界以重新计算。"] if defaults_used else ["当前输入完整，可开始候选筛选。"])), "missing_required_inputs": missing, "evidence_notice": "Screening is conditional comparison, not an engineering release conclusion."}
    high_temperature = bool(re.search(r"(?<!\d)(?:[6-9]\d{2}|[1-9]\d{3,})\s*(?:°\s*)?[cC](?![A-Za-z])", idea))
    if hea_intent:
        template = "aerospace_high_temperature_hea_exploration"
        inferred = {"model_domain": "hea_mpea", "allowed_elements": ["Ni", "Co", "Cr", "Al", "Ti"], "element_bounds_at_pct": {"Ni": [20, 40], "Co": [10, 30], "Cr": [10, 25], "Al": [5, 15], "Ti": [5, 20]}, "processing_method": "CAST", "test_temperature_C": 900, "screening_mode": "conservative_adaptive", "objectives": {"yield_strength_MPa": {"goal": "maximize"}, "phase_risk": {"goal": "minimize"}}}
        questions = ["请确认部件类型、服役温度与保温时间。", "请确认氧化环境、密度上限、制造路线和元素禁限。"]
    elif "高温" in idea or "high-temperature" in idea.casefold() or high_temperature:
        inferred = {
            "model_domain": "ni_superalloy_hot_end", "num_candidates": 120,
            "screening_mode": "conservative_anchor_local",
            "objectives": {"ultimate_tensile_strength_MPa": {"goal": "maximize"}, "proof_strength_0p2_MPa": {"goal": "maximize"}, "rupture_life": {"goal": "maximize"}},
            **_HOT_END_PLATFORM_DEFAULTS,
        }
        context_overrides = _hot_end_context_overrides(idea)
        effective = dict(inferred)
        effective.update(context_overrides)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {
            key: (
                "user" if key in supplied and supplied[key] not in (None, [], {}, "")
                else "upstream_context" if key in context_overrides
                else "platform_default"
            )
            for key in effective
        }
        defaults_used = [key for key in _HOT_END_PLATFORM_DEFAULTS if provenance[key] == "platform_default"]
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "hot_end_ni_superalloy_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in defaults_used], "questions_to_confirm": ["已采用平台默认热端工况完成首轮筛选；可继续提供实际路线、热处理、温度、载荷或 wt.% 边界以重新计算。"], "missing_required_inputs": [], "evidence_notice": "Screening is conditional comparison, not an engineering release conclusion。"}
    else:
        template = "generic_hea_exploration"
        inferred = {"model_domain": "hea_mpea", "allowed_elements": ["Co", "Cr", "Fe", "Mn", "Ni"], "element_bounds_at_pct": {"Co": [10, 30], "Cr": [10, 30], "Fe": [10, 30], "Mn": [10, 30], "Ni": [10, 30]}, "processing_method": "CAST", "test_temperature_C": 25, "screening_mode": "conservative_adaptive", "objectives": {"yield_strength_MPa": {"goal": "maximize"}, "phase_risk": {"goal": "minimize"}}}
        questions = ["请确认目标服役温度、允许元素体系、工艺和成本约束。"]
    effective = dict(inferred)
    effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
    provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "template_inference") for key in effective}
    return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": template, "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": value, "status": "requires_confirmation"} for key, value in inferred.items() if provenance[key] == "template_inference"], "questions_to_confirm": questions, "evidence_notice": "Template inference is exploratory only, not an engineering conclusion."}
