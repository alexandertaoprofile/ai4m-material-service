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
    r"玻璃基板|玻璃芯基板|玻璃核基板|封装玻璃|芯片玻璃|玻璃配方|低硼无碱|铝硼硅酸盐玻璃|"
    r"chip.?glass|glass.?core.?substrate|glass.?substrate|alumino.?borosilicate",
    re.IGNORECASE,
)
_SHORT_CF_PATTERN = re.compile(
    r"短碳纤维|短纤维|碳纤维增强|CF.?增强|复合耗材|"
    r"复合材料.*(?:刚度|模量|本构)|RVE|E11|各向异性|"
    # Parent orchestration can split the original 3D short-fibre request into
    # a follow-up carrying only these RVE input fields.  That combination is
    # still unambiguously this route, rather than a generic composite query.
    r"碳纤维(?:种类)?[^。；;]{0,80}(?:体分比|体积分数|有效长度|打印取向)|"
    r"(?:体分比|体积分数)[^。；;]{0,80}(?:有效长度|打印取向)",
    re.IGNORECASE,
)
_PEROVSKITE_PATTERN = re.compile(
    r"钙钛矿|perovskite|卤化物钙钛矿|Cs/FA/MA|Cs.*FA.*MA|碘溴|I/Br|"
    r"离子迁移|离子电导|电输运稳定|变温电导",
    re.IGNORECASE,
)
_COPPER_HOT_END_PATTERN = re.compile(
    r"GRCop[ -]?(?:42|84)|NARloy[ -]?Z|CuCrZr|Cu-?1Cr|铜基(?:合金)?|"
    r"再生冷却|燃烧室(?:内衬|壁)?|液氢|甲烷冷却|热流密度|铜合金", re.IGNORECASE,
)


def _is_negated_material_mention(text: str, start: int) -> bool:
    """Whether the material token at ``start`` is explicitly excluded.

    This intentionally reads the words *before* a token, so ``不锈钢`` is not
    confused with a negated "锈钢" material mention.  It accepts short Chinese
    clauses such as ``不做玻璃基板`` and ``不考虑使用镍基合金``.
    """
    prefix = text[max(0, start - 48):start]
    direct_negation = re.search(
        r"(?:不(?:做|选|采用|选用|使用|考虑|需要|选择|包含|含|含有|进入|要)|不是|不属于|不适用|"
        r"排除|禁止|避免|无|非)\s*(?:[、，,：:（）()\-—/\s]|使用|采用|作为){0,12}$",
        prefix,
        re.IGNORECASE,
    )
    if direct_negation:
        return True

    # A single request can negate a compound material name, e.g.
    # ``不考虑使用短碳纤维复合耗材``.  Regexes for a domain may match both
    # ``短碳纤维`` and the later ``复合耗材``; allow the negation to cover the
    # rest of that short clause, while stopping at a sentence boundary.
    return bool(re.search(
        r"(?:不(?:做|选|采用|选用|使用|考虑|需要|选择|包含|含|含有|进入|要)|不是|不属于|不适用|"
        r"排除|禁止|避免|无|非)[^。；;\n]{0,40}$",
        prefix,
        re.IGNORECASE,
    ))


def _has_active_pattern(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _is_negated_material_mention(text, match.start()) for match in pattern.finditer(text))


def _has_active_literal(text: str, literal: str) -> bool:
    start = text.casefold().find(literal.casefold())
    while start >= 0:
        if not _is_negated_material_mention(text, start):
            return True
        start = text.casefold().find(literal.casefold(), start + len(literal))
    return False


def is_copper_hot_end_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "copper_hot_end_local_composition_v1" or _has_active_pattern(_COPPER_HOT_END_PATTERN, text)


def is_copper_local_composition_intent(text: str, scope: dict[str, Any]) -> bool:
    """Keep named-grade evidence queries separate from bounded composition design."""
    if scope.get("model_domain") == "copper_hot_end_local_composition_v1":
        return True
    if not is_copper_hot_end_intent(text, scope):
        return False
    if any(key in scope for key in ("composition_wt_percent", "element_bounds_wt_percent", "processing_state")):
        return True
    return bool(re.search(r"配比|成分(?:设计|优化|筛选|生成)?|组分|wt\.?%|含量|局部(?:优化|配比)", text, re.IGNORECASE))


def is_reusable_rocket_stainless_intent(text: str, scope: dict[str, Any]) -> bool:
    """Recognize natural descriptions of reusable rocket stainless structures."""
    if scope.get("model_domain") == "reusable_rocket_stainless":
        return True
    lowered = text.casefold()
    explicit_terms = ("可回收火箭", "火箭贮箱", "火箭壳体", "低温不锈钢", "奥氏体不锈钢", "304l", "301ln", "cryoforming", "30x")
    if any(_has_active_literal(text, term) for term in explicit_terms):
        return True
    rocket = any(_has_active_literal(text, term) for term in ("火箭", "航天器", "航天飞行器"))
    stainless = any(_has_active_literal(text, term) for term in ("不锈钢", "stainless"))
    reusable_structure = any(_has_active_literal(text, term) for term in ("可回收", "回收", "贮箱", "壳体", "外壳", "承压壳", "表面壳"))
    return rocket and stainless and reusable_structure


def is_chip_glass_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "chip_glass_thermomechanical_family_v1" or _has_active_pattern(_CHIP_GLASS_PATTERN, text)

def is_short_cf_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "short_cf_thermomechanical_rve_v1" or _has_active_pattern(_SHORT_CF_PATTERN, text)

def is_perovskite_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "perovskite_transport_stability_v2" or _has_active_pattern(_PEROVSKITE_PATTERN, text)


def short_cf_matrix_from_text(text: str) -> str | None:
    """Infer only unambiguous built-in thermoplastic matrix aliases from prose."""
    lowered = text.casefold()
    aliases = (
        (("petg", "pet-g"), "PETG"),
        (("pla",), "PLA"),
        (("asa",), "ASA"),
        (("abs",), "ABS"),
        (("聚碳酸酯", "pc基体", "pc 基体"), "PC"),
    )
    for markers, matrix_name in aliases:
        if any(marker in lowered for marker in markers):
            return matrix_name
    return None

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
    """Identify a requested composite material, respecting explicit exclusions.

    A request such as ``不含树脂、纤维、填料或复合相`` describes a
    *monolithic* alloy boundary.  The former keyword-only check treated this
    sentence as a composite request and rejected the exact task it was meant
    to admit.  Keep the rule intentionally local: a negation in the short
    clause immediately before the matched material term is sufficient to
    exclude that term from composite routing.
    """
    scope_text = json.dumps(scope, ensure_ascii=False, default=str)
    combined = f"{text}\n{scope_text}"
    for match in _COMPOSITE_MATERIAL_PATTERN.finditer(combined):
        # Reuse the same clause-aware negation semantics as specialist
        # routing.  Thus “不包含碳纤维材料” and “不要树脂、纤维、填料” are
        # material exclusions, rather than an affirmative composite signal.
        if _is_negated_material_mention(combined, match.start()):
            continue
        return True
    return False


def is_ni_hot_end_intent(text: str, scope: dict[str, Any]) -> bool:
    """Recognize hot-section nickel-alloy tasks from material or service cues."""
    if scope.get("model_domain") == "ni_superalloy_hot_end":
        return True
    return _has_active_pattern(_NI_HOT_END_PATTERN, text) or bool(
        _has_active_pattern(_ENGINE_PATTERN, text) and _has_active_pattern(_HIGH_TEMPERATURE_PATTERN, text)
    )


def is_hea_exploration_intent(text: str, scope: dict[str, Any]) -> bool:
    """HEA requires an explicit multicomponent or exploration signal, never temperature alone."""
    if scope.get("model_domain") == "hea_mpea":
        return True
    lowered = text.casefold()
    explicit_system = any(_has_active_literal(text, token) for token in ("hea", "mpea", "高熵", "多主元"))
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
            # Conversation history is evidence of what has already happened,
            # not a new material request.  In particular, an earlier assistant
            # refusal may say "树脂/纤维/复合材料" and must never reverse a
            # later user statement such as "不含树脂、纤维、填料".  Retain only
            # user-authored message content when an envelope carries roles.
            role = str(item.get("role") or item.get("speaker") or "").strip().casefold()
            if role in {"assistant", "system", "tool", "function", "agent", "bot"}:
                return
            for key in ("idea", "content", "text", "query", "requirement", "summary", "message", "project_idea", "conversation_context", "upstream_result", "material_conclusion", "history", "messages", "conversation", "upstream_context", "previous_results"):
                if item.get(key) is not None:
                    visit(item[key])
        elif isinstance(item, list):
            # A role-labelled conversation may contain contradictory historic
            # user turns.  The current turn is the last user message; earlier
            # material systems must not be allowed to steer this request.
            role_messages = [
                child for child in item
                if isinstance(child, dict)
                and str(child.get("role") or child.get("speaker") or "").strip().casefold()
            ]
            if role_messages:
                latest_user = next((child for child in reversed(role_messages)
                                    if str(child.get("role") or child.get("speaker") or "").strip().casefold()
                                    in {"user", "human", "customer"}), None)
                if latest_user is not None:
                    visit(latest_user)
                return
            for child in item:
                visit(child)

    visit(value)
    return "\n\n".join(chunks)[:limit]


def upstream_requirement(payload: dict[str, Any]) -> tuple[str, list[str]]:
    # A direct user request always takes precedence over an accumulated
    # transcript or an upstream agent report.  This prevents stale rejection
    # language from becoming a routing signal on a follow-up turn.
    direct_keys = [key for key in ("idea", "content", "query", "project_idea") if payload.get(key) is not None]
    direct_text = context_text({key: payload[key] for key in direct_keys})
    if direct_text:
        return direct_text, direct_keys
    keys = [key for key in ("conversation_context", "upstream_result", "material_conclusion", "history", "messages", "conversation", "upstream_context", "previous_results") if payload.get(key) is not None]
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
    if is_copper_hot_end_intent(text, scope):
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
    short_cf_request = is_short_cf_intent(upstream_context, scope)
    perovskite_request = is_perovskite_intent(upstream_context, scope)
    if is_composite_material_request(upstream_context, scope) and not short_cf_request:
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    glass_request = is_chip_glass_intent(upstream_context, scope)
    if not short_cf_request and not glass_request and not perovskite_request and not is_alloy_request(upstream_context, scope):
        raise ValueError("本服务仅处理合金/高温合金的成分或配比优化；已有材料查询请使用成熟材料服务，非合金新材料生成请使用新材料服务")
    domain = scope.get("model_domain", "hea_mpea")
    if domain not in {"hea_mpea", "conventional_alloy", "refractory_calculated", "ni_superalloy_hot_end", "copper_hot_end_local_composition_v1", "reusable_rocket_stainless", "chip_glass_thermomechanical_family_v1", "short_cf_thermomechanical_rve_v1", "perovskite_transport_stability_v2"}:
        raise ValueError("unsupported model_domain")
    common = {"taskid": task_id(payload), "raw_requirement": upstream_context, "upstream_context": upstream_context, "upstream_context_keys": upstream_keys, "model_domain": domain, "objectives": scope.get("objectives", {}), "constraints": scope.get("constraints", {})}
    if domain == "ni_superalloy_hot_end":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "manufacturing_route": scope.get("manufacturing_route"), "heat_treatment": scope.get("heat_treatment"), "test_temperature_C": scope.get("test_temperature_C"), "applied_stress_MPa": scope.get("applied_stress_MPa"), "screening_thresholds": scope.get("screening_thresholds", {}), "casting_gradient_K_per_mm": scope.get("casting_gradient_K_per_mm"), "num_candidates": scope.get("num_candidates", 120), "random_seed": scope.get("random_seed", 20260901)}
    if domain == "copper_hot_end_local_composition_v1":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "composition_family": scope.get("composition_family", "GRCop_type_Cu_Cr_Nb"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "processing_state": scope.get("processing_state", "as_received"), "test_temperature_C": scope.get("test_temperature_C", 25), "ambient_process": scope.get("ambient_process", {}), "num_candidates": scope.get("num_candidates", 12), "random_seed": scope.get("random_seed", 20260911)}
    if domain == "reusable_rocket_stainless":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "test_temperature_K": scope.get("test_temperature_K"), "processing": scope.get("processing", {}), "component": scope.get("component"), "weld_state": scope.get("weld_state", "base_metal"), "thickness_mm": scope.get("thickness_mm"), "low_temperature_verification_K": scope.get("low_temperature_verification_K", [90, 111]), "verification_focus": scope.get("verification_focus", []), "num_candidates": scope.get("num_candidates", 40), "random_seed": scope.get("random_seed", 20260902)}
    if domain == "chip_glass_thermomechanical_family_v1":
        return {**common, "composition_basis": "mol_percent", "composition_mol_percent": scope.get("composition_mol_percent"), "oxide_bounds_mol_percent": scope.get("oxide_bounds_mol_percent", {}), "screening_thresholds": scope.get("screening_thresholds", {}), "num_candidates": scope.get("num_candidates", 80), "random_seed": scope.get("random_seed", 20260904), "application": scope.get("application", "芯片封装玻璃基板的热失配与挠曲初筛"), "service_options": scope.get("service_options", {})}
    if domain == "short_cf_thermomechanical_rve_v1":
        return {**common, "matrix_name": scope.get("matrix_name"), "matrix_properties": scope.get("matrix_properties", {}), "target_vf": scope.get("target_vf"), "fiber_length_mm": scope.get("fiber_length_mm"), "target_a11": scope.get("target_a11"), "num_candidates": scope.get("num_candidates", 40), "random_seed": scope.get("random_seed", 20260907), "screening_thresholds": scope.get("screening_thresholds", {})}
    if domain == "perovskite_transport_stability_v2":
        return {**common, "composition": scope.get("composition"), "fractions": scope.get("fractions", {}), "temperatures_K": scope.get("temperatures_K", [170, 220, 300, 330]), "num_candidates": scope.get("num_candidates", 5), "screening_thresholds": scope.get("screening_thresholds", {})}
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
    short_cf_intent = is_short_cf_intent(idea, supplied)
    perovskite_intent = is_perovskite_intent(idea, supplied)
    glass_intent = is_chip_glass_intent(idea, supplied)
    rocket_intent = is_reusable_rocket_stainless_intent(idea, supplied)
    copper_hot_end_intent = is_copper_hot_end_intent(idea, supplied)
    copper_local_intent = is_copper_local_composition_intent(idea, supplied)
    hot_end_intent = is_ni_hot_end_intent(idea, supplied)
    # A concrete requested specialist material is stronger evidence than a
    # generic material word used in an exclusion clause.  This keeps requests
    # such as “玻璃基板，不包含碳纤维” and “30X 不锈钢，排除树脂/纤维” on
    # their intended trained routes.
    specialist_intent = any((short_cf_intent, perovskite_intent, glass_intent,
                             rocket_intent, copper_hot_end_intent, hot_end_intent))
    if is_composite_material_request(idea, supplied) and not specialist_intent:
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    if not short_cf_intent and not glass_intent and not perovskite_intent and not is_alloy_request(idea, supplied):
        raise ValueError("本服务仅适用于合金或高温合金的成分优化，不适用于一般高温材料查询或非合金新材料生成")
    if short_cf_intent:
        inferred = {"model_domain":"short_cf_thermomechanical_rve_v1","matrix_name":"Bambu_PLA_Basic","target_vf":0.10,"fiber_length_mm":0.035,"target_a11":0.70,"num_candidates":40,"random_seed":20260907,"objectives":{"E11_MPa":{"goal":"maximize"},"anisotropy_ratio_E11_E22":{"goal":"maximize"}}}
        matrix_from_text = short_cf_matrix_from_text(idea)
        if matrix_from_text:
            inferred["matrix_name"] = matrix_from_text
        effective=dict(inferred); effective.update({key:value for key,value in supplied.items() if value not in (None,[],{},"")})
        provenance={key:("user" if key in supplied and supplied[key] not in (None,[],{},"") else "upstream_context" if key == "matrix_name" and matrix_from_text else "platform_default") for key in effective}
        return effective,{"parser":"rule_template_v0","raw_requirement":idea,"upstream_context_keys":upstream_keys,"template":"short_cf_thermomechanical_rve_screening","effective_model_input":effective,"field_provenance":provenance,"default_assumptions":[{"field":key,"value":inferred[key],"status":"platform_default"} for key in inferred if provenance[key]=="platform_default"],"questions_to_confirm":["可提供基体 E/ν/密度、实际体积分数、有效纤维长度和主方向取向，以替换默认 RVE 设计条件。"],"evidence_notice":"输出为固定 T300-like 短碳纤维、致密、完美界面 RVE 下的线弹性等效本构，不是强度或实物测试结果。"}
    if glass_intent:
        inferred = {"model_domain": "chip_glass_thermomechanical_family_v1", "composition_basis": "mol_percent", "num_candidates": 80, "random_seed": 20260904, "application": "芯片封装玻璃基板的热失配与挠曲初筛", "objectives": {"CTE_linear_0_to_300C": {"goal": "minimize"}, "young_modulus_GPa": {"goal": "maximize"}, "stress_optical_coefficient_nm_cm_per_MPa": {"goal": "minimize"}}, "screening_thresholds": {}}
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "chip_glass_thermomechanical_local_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可提供实际氧化物 mol% 边界、目标 CTE/E/SOC 门槛、玻璃厚度、层堆和温度循环，以替换默认探索条件。"], "evidence_notice": "候选只在低硼无碱玻璃家族的可追溯局部邻域内生成；残余应力和翘曲须结合层堆与热历史计算。"}
    if perovskite_intent:
        inferred = {"model_domain": "perovskite_transport_stability_v2", "temperatures_K": [170, 220, 300, 330], "num_candidates": 5, "objectives": {"ln_sigmaT_220K": {"goal": "minimize"}, "ln_sigmaT_300K": {"goal": "minimize"}}}
        effective = dict(inferred); effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "perovskite_transport_stability_grid_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可提供目标温度、偏压、湿度、光照和寿命定义；这些条件不在当前输运模型中。"], "evidence_notice": "模型输出 ln[sigma(T)T] 与有效 Ea 的输运代理；不输出器件 T80、PCE 或环境分解寿命。"}
    if rocket_intent:
        inferred = {"model_domain": "reusable_rocket_stainless", "element_bounds_wt_percent": {"Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0], "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08]}, "test_temperature_K": 293, "processing": {"material_state": "solution_annealed", "solution_treatment_temperature_K": 1323, "solution_treatment_time_s": 3600, "quench": "water", "product_form_code": 1, "melting_route_code": 1}, "component": "可回收火箭贮箱或承压壳体（母材）", "weld_state": "base_metal", "low_temperature_verification_K": [90, 111], "verification_focus": ["cryogenic_toughness", "weld", "fatigue", "LOX_compatibility"], "num_candidates": 40, "objectives": {"yield_strength": 1, "uts": 1, "elongation": 1}}
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "reusable_rocket_stainless_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可继续提供目标温度、板厚、焊接状态、成分 wt.% 边界和实际热处理，以替换本轮可见默认条件。"], "evidence_notice": "293–1273 K 输出为短时拉伸候选筛选；更低温度转为 301/304L 参考和验证规划。"}
    if copper_local_intent:
        inferred = {"model_domain":"copper_hot_end_local_composition_v1", "composition_family":"GRCop_type_Cu_Cr_Nb", "element_bounds_wt_percent":{"Cu":[84,94],"Cr":[4,9],"Nb":[4,7],"Zr":[0,1],"Ag":[0,2],"Al":[0,.2],"O":[0,.2],"Ni":[0,.2],"Fe":[0,.2],"Ti":[0,.5]}, "processing_state":"as_received", "test_temperature_C":25, "ambient_process":{"solution_temperature_K":1233,"solution_time_h":2,"cold_reduction_pct":50,"aged":True,"aging_temperature_K":723,"aging_time_h":2,"secondary_thermomechanical_process":False}, "num_candidates":12, "random_seed":20260911, "objectives":{"ultimate_tensile_strength_MPa":{"goal":"maximize"},"yield_0p2_MPa":{"goal":"maximize"}}}
        effective = dict(inferred); effective.update({key:value for key,value in supplied.items() if key != "ambient_process" and value not in (None,[],{},"")})
        if isinstance(supplied.get("ambient_process"), dict):
            effective["ambient_process"] = {**inferred["ambient_process"], **supplied["ambient_process"]}
        provenance = {key:("user" if key in supplied and supplied[key] not in (None,[],{},"") else "platform_default") for key in effective}
        return effective,{"parser":"rule_template_v0","raw_requirement":idea,"upstream_context_keys":upstream_keys,"template":"copper_hot_end_conditioned_local_composition_screening","effective_model_input":effective,"field_provenance":provenance,"default_assumptions":[{"field":key,"value":inferred[key],"status":"platform_default"} for key in inferred if provenance[key]=="platform_default"],"questions_to_confirm":["可补充目标温度、实际热处理和各元素 wt.% 边界，以替换 GRCop 型默认成分空间并收窄结果区间。"],"evidence_notice":"B 级仅用于成分、工艺状态和温度均在联合适用域内的短时强度直接预测；状态/温度外推会降为 C 级。室温 %IACS 为 C 级辅助预测；热导、硬度、密度和 CTE 是带区间的 D 级工程估算，不参与排序或硬筛选。疲劳与蠕变仍需载荷、温度和寿命定义后独立验证。"}
    if copper_hot_end_intent:
        raise ValueError("已有铜基牌号、状态与性质核验请使用 1105 成熟材料目录；1111 仅处理明确的铜基新配比/成分优化任务")
    hea_intent = is_hea_exploration_intent(idea, supplied)
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
