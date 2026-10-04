"""Pure request normalization for the HEA/MPEA alloy service.

No FastAPI, WebSocket, object-storage or runner dependency is allowed here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from src.alloy_workflow.identity import SUPPORTED_MODEL_DOMAINS


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
    r"玻璃基板|玻璃芯基板|玻璃核基板|封装玻璃|芯片玻璃|低硼无碱|铝硼硅酸盐玻璃|"
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
_AEROSPACE_ALUMINUM_PATTERN = re.compile(
    r"航空铝|航空航天铝|铝锂|Al[ -]?(?:Li|Cu|Zn|Mg)|铝合金|AA(?:2|5|6|7)\d{3}|"
    # A bare 2xxx/7xxx alloy designation is allowed (e.g. 2024, 7075-T6),
    # but a four-digit physical value must not be treated as a grade.  In
    # particular, density expressions such as ``7800 kg/m³`` previously
    # matched the 7xxx branch and incorrectly routed steel to aluminum.
    r"(?<![\d.])(?:2[0-9]{3}|7[0-9]{3})(?![\d.]|\s*(?:kg|g|MPa|GPa|Pa|K|N|%|m(?:m|³|3))|[-/.]\d)",
    re.IGNORECASE,
)


def _is_negated_material_mention(text: str, start: int) -> bool:
    """Whether the material token at ``start`` is explicitly excluded.

    This intentionally reads the words *before* a token, so ``不锈钢`` is not
    confused with a negated "锈钢" material mention.  It accepts short Chinese
    clauses such as ``不做玻璃基板`` and ``不考虑使用镍基合金``.
    """
    prefix = text[max(0, start - 48):start]
    # Material qualifiers describe composition/structure, not exclusion.
    prefix = re.sub(r"无碱|无铅|无硼|非晶|非等原子|非晶态", "", prefix)
    prefix = prefix.replace("不用", "不使用")
    switches = list(re.finditer(
        r"改(?:用|做|为|成)|而是(?:用|选|做)?|[，,；;。]\s*(?:针对|选用|采用|选择|使用)",
        prefix,
    ))
    if switches:
        prefix = prefix[switches[-1].end():]
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
    return bool(re.search(r"配方|配比|成分(?:设计|优化|筛选|生成)?|组分|wt\.?%|含量|局部(?:优化|配比)", text, re.IGNORECASE))


def is_reusable_rocket_stainless_intent(text: str, scope: dict[str, Any]) -> bool:
    """Recognize natural descriptions of reusable rocket stainless structures."""
    if scope.get("model_domain") == "reusable_rocket_stainless":
        return True
    # Confirmed product-language alias for the 30X rocket-stainless workflow.
    # This does not reinterpret scrap/recycled-feedstock requests as 30X.
    if _has_active_pattern(re.compile(r"可回收(?:的|型|式|用)?\s*不锈钢", re.IGNORECASE), text):
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
    unsupported = re.compile(r"连续(?:碳|玻璃)?纤维|长(?:碳|玻璃)?纤维|热固(?:性)?|环氧|epoxy|continuous.?fib[er]+", re.IGNORECASE)
    if _has_active_pattern(unsupported, text):
        return False
    return scope.get("model_domain") == "short_cf_thermomechanical_rve_v1" or _has_active_pattern(_SHORT_CF_PATTERN, text)

def is_perovskite_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "perovskite_transport_stability_v2" or _has_active_pattern(_PEROVSKITE_PATTERN, text)

def is_aerospace_aluminum_intent(text: str, scope: dict[str, Any]) -> bool:
    return scope.get("model_domain") == "aerospace_aluminum_composition_v1" or _has_active_pattern(_AEROSPACE_ALUMINUM_PATTERN, text)


def short_cf_matrix_from_text(text: str) -> str | None:
    """Infer only unambiguous built-in thermoplastic matrix aliases from prose."""
    lowered = text.casefold()
    aliases = (
        (("petg", "pet-g"), "PETG"),
        (("pla",), "PLA"),
        (("asa",), "ASA"),
        (("abs",), "ABS"),
        (("聚碳酸酯", "pc"), "PC"),
    )
    found = []
    for markers, matrix_name in aliases:
        if any(_has_active_pattern(re.compile(r"(?<![A-Za-z])" + re.escape(marker) + r"(?![A-Za-z])", re.I), text)
               for marker in markers):
            found.append(matrix_name)
    if len(found) > 1:
        raise MaterialContextError("存在多个复合材料基体，请确认本次采用哪一种，或分别优化。", text, [])
    return found[0] if found else None

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
    "screening_thresholds": {},
}


def _explicit_strength_thresholds(text: str) -> dict[str, float]:
    """Directional goals are not numerical gates; retain only stated minima."""
    thresholds: dict[str, float] = {}
    for key, aliases in (
        ("uts_min_MPa", r"抗拉强度|极限拉伸强度|\bUTS\b"),
        ("proof_strength_min_MPa", r"(?:0[.]2\s*%\s*)?屈服强度|\bYS\b|proof\s+strength"),
    ):
        match = re.search(
            rf"(?:{aliases})\s*(?:不低于|不少于|至少|超过|大于|高于|≥|>=|>)\s*"
            r"(?P<value>\d+(?:[.]\d+)?)\s*MPa\b", text, re.IGNORECASE,
        )
        if match:
            thresholds[key] = float(match.group("value"))
    return thresholds


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

    thresholds = _explicit_strength_thresholds(text)
    lifetime = re.search(
        r"(?:蠕变(?:断裂)?寿命|持久寿命|寿命)\s*(?:超过|大于|高于|不少于|至少|≥|>=)\s*"
        r"(?P<hours>\d+(?:\.\d+)?)\s*(?:小?时|h)\b",
        text,
        flags=re.IGNORECASE,
    )
    if lifetime:
        thresholds["rupture_life_min_h"] = float(lifetime.group("hours"))
    if thresholds:
        overrides["screening_thresholds"] = thresholds
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


_ORCHESTRATOR_SECTION_PATTERN = re.compile(
    r"(?m)^\s*【(?P<name>用户原始目标|历史执行上下文|最新用户补充(?:，高优先级)?|本轮必须执行的任务)】\s*$"
)
_ROLE_TURN_PATTERN = re.compile(
    r"(?ms)^\s*用户\s*[:：]\s*(?P<content>.*?)(?=^\s*(?:用户|助手|系统|工具)\s*[:：]|\Z)"
)
_EMPTY_SUPPLEMENT_VALUES = frozenset({"", "无", "无。", "暂无", "未提供", "none", "null", "n/a"})


def _latest_user_turn(text: str) -> str:
    turns = [match.group("content").strip() for match in _ROLE_TURN_PATTERN.finditer(text)]
    return next((turn for turn in reversed(turns) if turn), "")


def _authoritative_request_text(text: str) -> str:
    """Remove replayed assistant output from flat orchestrator envelopes.

    The parent service can resend an entire prior transcript inside the top-level
    ``idea`` string.  Specialist names in that assistant output are evidence of
    what was displayed, not a new material request, so only the latest user turn
    and the explicit current-task section may participate in routing.
    """
    stripped = text.strip()
    matches = list(_ORCHESTRATOR_SECTION_PATTERN.finditer(stripped))
    if not matches:
        if "=== 对话历史 ===" in stripped:
            return _latest_user_turn(stripped) or stripped
        return stripped

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
        sections[match.group("name")] = stripped[match.end():end].strip()

    original = sections.get("用户原始目标", "")
    latest = sections.get("最新用户补充，高优先级", sections.get("最新用户补充", "")).strip()
    current_task = sections.get("本轮必须执行的任务", "").strip()
    original_user = _latest_user_turn(original) if "=== 对话历史 ===" in original else original
    priority_user = latest if latest.casefold() not in _EMPTY_SUPPLEMENT_VALUES else original_user

    selected: list[str] = []
    for item in (priority_user, current_task):
        cleaned = item.strip()
        if cleaned and cleaned not in selected:
            selected.append(cleaned)
    return "\n\n".join(selected) or stripped


def _is_context_only_followup(text: str) -> bool:
    compact = re.sub(r"[\s，,。.!！?？、;；:：]+", "", text).casefold()
    return compact in {
        "继续", "继续做", "继续进行", "继续执行", "接着做", "同上",
        "按之前的继续", "按原方案继续", "continue", "goon",
    }


class MaterialContextError(ValueError):
    """A material reference cannot be resolved; this is not an untrained domain."""

    def __init__(self, message: str, request: str, keys: list[str]) -> None:
        super().__init__(message)
        self.request = request
        self.keys = keys


_FORMULATION_INTENT = re.compile(
    r"配方|配比|组分(?:设计|优化)|成分(?:设计|优化)|调整(?:组分|比例)|"
    r"(?:formulation|composition)\s*(?:design|optim)", re.IGNORECASE,
)
_MATERIAL_REFERENCE = re.compile(
    r"(?:上述|上面|前述|前面|上一步|上轮|上一轮|刚才|之前|已选|选定|推荐)"
    r"[^。；;\n]{0,20}(?:材料|体系|配方|方案|组分|资料)|"
    r"(?:该|此|这个|这种)(?:材料|体系|配方)|\b(?:this|that|above)\s+material\b",
    re.IGNORECASE,
)
_MATERIAL_IDENTITY = re.compile(
    r"高熵|多主元|\b(?:HEA|MPEA)\b|镍基|铜基|铜合金|不锈钢|合金钢|"
    r"高强钢|马氏体时效钢|钛合金|钛基|镁合金|镁基|高温合金|铝合金|铝锂|"
    r"玻璃|陶瓷|水泥|聚合物|塑料|树脂|纤维|复材|复合材料|钙钛矿|氧化物|电解质|electrolyte|"
    r"(?<![A-Za-z])(?:PLA|PETG|ABS|ASA|PC|PP|PE|PA6|PA66|PEEK|PEKK|PPS)(?![A-Za-z])|"
    r"GRCop|NARloy|CuCrZr|Ti[-–]6Al|Ti[-–]Nb|18Ni|CMSX|Inconel|"
    r"perovskite|stainless|superalloy|polymer|plastic|resin|composite|ceramic|glass",
    re.IGNORECASE,
)
_UPSTREAM_MATERIAL_KEYS = (
    "material_conclusion", "upstream_result", "upstream_context", "previous_results",
    "simulation_result", "simulation_context", "literature_result", "literature_context",
    "conversation_context", "history", "messages", "conversation", "project_idea",
)
_SELECTION_KEYS = ("selected_material", "confirmed_material", "chosen_material", "priority_material")
_IDENTITY_KEYS = ("material_family", "material_name", "material_system", "simulation_material", "grade", "name", "composition_formula")
_HISTORY_KEYS = frozenset({"conversation_context", "history", "messages", "conversation"})


def _explicit_selection_records(value: Any, depth: int = 0) -> list[tuple[str, dict[str, Any]]]:
    """Accept explicit upstream selection, never a general assistant capability list."""
    if depth > 12:
        return []
    if isinstance(value, dict):
        if str(value.get("role") or value.get("speaker") or "").casefold() == "system":
            return []
        for key in _SELECTION_KEYS:
            if value.get(key) not in (None, "", [], {}):
                return _material_records(value[key], depth=depth + 1)
        # Structured subject facts from an upstream tool are usable evidence.
        # Its arbitrary prose and capability lists are not routing instructions.
        subject = {key: value[key] for key in _IDENTITY_KEYS if value.get(key)}
        if subject:
            subject.update({key: val for key, val in value.items()
                            if key in _CONTEXT_SCOPE_FIELDS and key not in {"objectives", "screening_thresholds"}})
            return _material_records(subject, depth=depth + 1)
        for key in ("content", "text", "summary", "result", "data", "output", "material", "simulation", "study_subject"):
            records = _explicit_selection_records(value.get(key), depth + 1)
            if records:
                return records
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            matches = list(re.finditer(r"(?:已(?:经)?选定|已选择|最终选择|优先选择|仿真材料|研究对象|材料体系)\s*[:：]\s*(?P<material>[^。；;\n]+)", value))
            if not matches:
                matches = list(re.finditer(r"(?:已(?:经)?选定|已选择|最终选择|优先选择)\s*(?P<material>[^。；;\n]+)", value))
            if matches:
                return [record for match in matches for record in _material_records(match.group("material"), depth=depth + 1)]
        else:
            if isinstance(decoded, (dict, list)):
                return _explicit_selection_records(decoded, depth + 1)
    return []


def _has_material_identity(text: str) -> bool:
    from src.alloy_workflow.requirement_semantics import canonical_text
    text = canonical_text(text)
    return _has_active_pattern(_MATERIAL_IDENTITY, text) or _has_active_pattern(_AEROSPACE_ALUMINUM_PATTERN, text) or bool(
        re.search(r"(?:[A-Z][a-z]?(?:[-–]|\d+(?:\.\d+)?)){2,}[A-Z][a-z]?", text)
    )


def _usable_material_context(text: str) -> bool:
    return _has_material_identity(text) or bool(
        _has_active_pattern(_ENGINE_PATTERN, text) and _has_active_pattern(_HIGH_TEMPERATURE_PATTERN, text)
    ) or any(domain in text for domain in SUPPORTED_MODEL_DOMAINS)


def _material_records(value: Any, *, history: bool = False, depth: int = 0) -> list[tuple[str, dict[str, Any]]]:
    """Read a selected record, not capability lists or arbitrary assistant prose."""
    if depth > 12:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            text = _authoritative_request_text(value)
            return [(text, {})] if _usable_material_context(text) else []
        return _material_records(decoded, history=history, depth=depth + 1)
    if isinstance(value, list):
        role_labelled = any(isinstance(item, dict) and (item.get("role") or item.get("speaker")) for item in value)
        if history or role_labelled:
            for item in reversed(value):
                if isinstance(item, dict):
                    role = str(item.get("role") or item.get("speaker") or "user").casefold()
                    if role not in {"user", "human", "customer"}:
                        selected_records = _explicit_selection_records(item, depth + 1)
                        if selected_records:
                            return selected_records
                        continue
                records = _material_records(item, history=True, depth=depth + 1)
                if records:
                    return records
            return []
        selected = [item for item in value if isinstance(item, dict) and (
            item.get("selected") is True or item.get("selection_status") in {"selected", "confirmed"}
        )]
        return [record for item in (selected or value) for record in _material_records(item, depth=depth + 1)]
    if not isinstance(value, dict):
        return []
    if str(value.get("role") or value.get("speaker") or "").casefold() == "system":
        return []
    for key in _SELECTION_KEYS:
        if value.get(key) not in (None, "", [], {}):
            return _material_records(value[key], depth=depth + 1)
    role = str(value.get("role") or value.get("speaker") or "").casefold()
    if role in {"assistant", "system", "tool", "function", "agent", "bot"}:
        return _explicit_selection_records(value, depth + 1)
    parts = [str(value[key]) for key in _IDENTITY_KEYS if isinstance(value.get(key), str) and value[key].strip()]
    if value.get("model_domain") in SUPPORTED_MODEL_DOMAINS:
        parts.append(str(value["model_domain"]))
    for key in ("idea", "content", "text", "query", "requirement", "summary", "message", "user_conclusion"):
        if isinstance(value.get(key), str):
            parts.append(_authoritative_request_text(value[key]))
    text = "\n".join(parts)
    if _usable_material_context(text):
        return [(text, value)]
    for key in ("result", "data", "output", "material", "simulation", "study_subject", "material_conclusion", "effective_model_input", "alloy_optimization", "constraints", "candidates", "materials"):
        if value.get(key) not in (None, "", [], {}):
            records = _material_records(value[key], depth=depth + 1)
            if records:
                return records
    return []


def _ambiguous_material_text(text: str) -> bool:
    # A base-metal qualifier plus HEA names one system, not two candidates.
    text = re.sub(r"(?:镍基|铜基|钛基)(?=[^，、/与和。；\n]{0,8}(?:高熵|多主元))", "", text)
    families = (
        r"高熵|多主元|\bHEA\b|\bMPEA\b", r"镍基|CMSX|Inconel",
        r"铜基|铜合金|GRCop|NARloy|CuCrZr", r"不锈钢|304L|301LN|30X",
        r"航空铝|铝合金|铝锂|Al[- ]?Li", r"玻璃基板|芯片玻璃|低硼无碱|玻璃芯基板",
        r"短碳纤维|复合耗材", r"钙钛矿|perovskite", r"钛合金|钛基|Ti[-–]6Al",
        r"马氏体时效钢|18Ni|高强钢", r"镁合金|镁基",
    )
    return sum(_has_active_pattern(re.compile(pattern, re.IGNORECASE), text) for pattern in families) > 1


def _referenced_material(payload: dict[str, Any], request: str, direct_key: str, raw_text: str) -> tuple[str, list[str]]:
    sources = [(key, payload[key]) for key in _UPSTREAM_MATERIAL_KEYS if key != "project_idea" and payload.get(key) is not None]
    # A parent may encode context inside idea rather than separate JSON keys.
    matches = list(_ORCHESTRATOR_SECTION_PATTERN.finditer(raw_text))
    for index, match in enumerate(matches):
        if match.group("name") not in {"用户原始目标", "历史执行上下文"}:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        section = raw_text[match.end():end].strip()
        if match.group("name") == "历史执行上下文":
            selected = _explicit_selection_records(section)
            if not selected:
                continue
            section = [{"material_name": text, **record} for text, record in selected]
        sources.append((direct_key, section))
    if "=== 对话历史 ===" in raw_text and not matches:
        turns = [match.group("content").strip() for match in _ROLE_TURN_PATTERN.finditer(raw_text)]
        sources.append((direct_key, turns[:-1]))
    if payload.get("project_idea") is not None:
        sources.append(("project_idea", payload["project_idea"]))
    def selected_values(value, depth=0):
        if depth>8 or not isinstance(value,dict) or value.get('role')=='system':return []
        direct=[(key,value[key]) for key in _SELECTION_KEYS if value.get(key)]
        if direct:return [item for _, item in direct]
        return [item for key in ('result','data','output') for item in selected_values(value.get(key),depth+1)]
    selected_sources=[(key,{'selected_material':value}) for key,source in sources
                      if key not in _HISTORY_KEYS for value in selected_values(source)]
    if len(selected_sources)==1:
        sources=selected_sources
    # Conflicting current upstream evidence needs a choice; history is a lower
    # priority fallback and must not override an explicitly selected object.
    from src.alloy_workflow.requirement_semantics import canonical_text, material_families
    current_families = set()
    current_keys = []
    for key, source in sources:
        if key in _HISTORY_KEYS or key in {"project_idea", direct_key}:
            continue
        for fact, _ in _material_records(source):
            current_families.update(material_families(canonical_text(fact)))
            current_keys.append(key)
    if len(current_families) > 1:
        raise MaterialContextError("上游文献、仿真或选材结果对应不同材料，请明确本次优化对象。", request,
                                   list(dict.fromkeys([direct_key] + current_keys)))
    for key, source in sources:
        records = _material_records(source, history=key in _HISTORY_KEYS or (key == direct_key and isinstance(source, list)))
        unique = {re.sub(r"\s+", "", text).casefold(): text for text, _ in records}
        if not unique:
            continue
        keys = list(dict.fromkeys([direct_key, key]))
        if len(unique) != 1 or _ambiguous_material_text(next(iter(unique.values()))):
            raise MaterialContextError("上游包含多个材料候选，请明确本次要优化哪个材料，或说明需要分别优化哪些材料。", request, keys)
        context = next(iter(unique.values()))
        if _is_context_only_followup(request):
            return context[:12000], keys
        return (request + "\n\n已明确的材料上下文：\n" + context)[:12000], keys
    raise MaterialContextError("请提供“上述材料”对应的材料名称或上一步材料选择结果，以便确定配方变量和适用模型。", request, [direct_key])


def upstream_requirement(payload: dict[str, Any]) -> tuple[str, list[str]]:
    # A current-turn request always takes precedence over ``project_idea`` and
    # accumulated history.  Process one current field at a time rather than
    # concatenating stale project material with the new request.
    for key in ("idea", "content", "query"):
        if payload.get(key) is None:
            continue
        raw_text = context_text(payload[key], limit=120000)
        direct_text = _authoritative_request_text(raw_text)[:12000]
        if direct_text:
            scope = payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {}
            explicit_scope = isinstance(scope, dict) and any(scope.get(field) not in (None, "", [], {}) for field in (
                "model_domain", "composition", "composition_wt_percent", "composition_mol_percent", "allowed_elements", "matrix_name",
            ))
            needs_context = _is_context_only_followup(direct_text) or (
                not _has_material_identity(direct_text)
                and (_MATERIAL_REFERENCE.search(direct_text) or (_FORMULATION_INTENT.search(direct_text) and not _usable_material_context(direct_text)))
            )
            if needs_context and not explicit_scope:
                return _referenced_material(payload, direct_text, key, raw_text)
            return direct_text, [key]

    for key in _UPSTREAM_MATERIAL_KEYS:
        if payload.get(key) is None:
            continue
        records = _material_records(payload[key], history=key in _HISTORY_KEYS)
        if records:
            if len(records) != 1 or _ambiguous_material_text(records[0][0]):
                raise MaterialContextError("上游包含多个材料候选，请明确本次要优化哪个材料。", "", [key])
            return records[0][0][:12000], [key]
    keys = [key for key in _UPSTREAM_MATERIAL_KEYS if payload.get(key) is not None]
    history_text = context_text({key: payload[key] for key in keys}, limit=120000)
    return _authoritative_request_text(history_text)[:12000], keys


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
    composition_intent = any(token in lowered for token in ("配方", "配比", "组分", "成分", "元素比例", "原子百分比", "at.%", "优化"))
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
    aluminum_request = is_aerospace_aluminum_intent(upstream_context, scope)
    if is_composite_material_request(upstream_context, scope) and not short_cf_request:
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    glass_request = is_chip_glass_intent(upstream_context, scope)
    specialist_request = any((
        short_cf_request, glass_request, perovskite_request, aluminum_request,
        is_reusable_rocket_stainless_intent(upstream_context, scope),
        is_copper_hot_end_intent(upstream_context, scope),
        is_ni_hot_end_intent(upstream_context, scope),
    ))
    if not specialist_request and not is_alloy_request(upstream_context, scope):
        raise ValueError("本服务仅处理合金/高温合金的成分或配比优化；已有材料查询请使用成熟材料服务，非合金新材料生成请使用新材料服务")
    domain = str(scope.get("model_domain", "hea_mpea")).strip()
    if domain not in SUPPORTED_MODEL_DOMAINS:
        raise ValueError(
            f"model_domain={domain!r} 尚未接入已验证专项模型；"
            "将返回材料家族特定的通用配比与试验设计框架"
        )
    common = {"taskid": task_id(payload), "raw_requirement": upstream_context, "upstream_context": upstream_context, "upstream_context_keys": upstream_keys, "model_domain": domain, "objectives": scope.get("objectives", {}), "constraints": scope.get("constraints", {})}
    if domain == "ni_superalloy_hot_end":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "manufacturing_route": scope.get("manufacturing_route"), "heat_treatment": scope.get("heat_treatment"), "test_temperature_C": scope.get("test_temperature_C"), "applied_stress_MPa": scope.get("applied_stress_MPa"), "screening_thresholds": scope.get("screening_thresholds", {}), "casting_gradient_K_per_mm": scope.get("casting_gradient_K_per_mm"), "num_candidates": scope.get("num_candidates", 120), "random_seed": scope.get("random_seed", 20260901)}
    if domain == "copper_hot_end_local_composition_v1":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "composition_family": scope.get("composition_family", "GRCop_type_Cu_Cr_Nb"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "processing_state": scope.get("processing_state", "as_received"), "test_temperature_C": scope.get("test_temperature_C", 25), "ambient_process": scope.get("ambient_process", {}), "screening_thresholds": scope.get("screening_thresholds", {}), "num_candidates": scope.get("num_candidates", 12), "random_seed": scope.get("random_seed", 20260911)}
    if domain == "reusable_rocket_stainless":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "test_temperature_K": scope.get("test_temperature_K"), "processing": scope.get("processing", {}), "component": scope.get("component"), "weld_state": scope.get("weld_state", "base_metal"), "thickness_mm": scope.get("thickness_mm"), "low_temperature_verification_K": scope.get("low_temperature_verification_K", [90, 111]), "verification_focus": scope.get("verification_focus", []), "num_candidates": scope.get("num_candidates", 40), "random_seed": scope.get("random_seed", 20260902)}
    if domain == "chip_glass_thermomechanical_family_v1":
        return {**common, "composition_basis": "mol_percent", "composition_mol_percent": scope.get("composition_mol_percent"), "oxide_bounds_mol_percent": scope.get("oxide_bounds_mol_percent", {}), "screening_thresholds": scope.get("screening_thresholds", {}), "num_candidates": scope.get("num_candidates", 80), "random_seed": scope.get("random_seed", 20260904), "application": scope.get("application", "芯片封装玻璃基板的热失配与挠曲初筛"), "service_options": scope.get("service_options", {})}
    if domain == "short_cf_thermomechanical_rve_v1":
        return {**common, "matrix_name": scope.get("matrix_name"), "matrix_properties": scope.get("matrix_properties", {}), "target_vf": scope.get("target_vf"), "fiber_length_mm": scope.get("fiber_length_mm"), "target_a11": scope.get("target_a11"), "num_candidates": scope.get("num_candidates", 40), "random_seed": scope.get("random_seed", 20260907), "screening_thresholds": scope.get("screening_thresholds", {})}
    if domain == "perovskite_transport_stability_v2":
        return {**common, "composition": scope.get("composition"), "fractions": scope.get("fractions", {}), "temperatures_K": scope.get("temperatures_K", [170, 220, 300, 330]), "num_candidates": scope.get("num_candidates", 5), "screening_thresholds": scope.get("screening_thresholds", {})}
    if domain == "aerospace_aluminum_composition_v1":
        return {**common, "composition_wt_percent": scope.get("composition_wt_percent"), "element_bounds_wt_percent": scope.get("element_bounds_wt_percent", {}), "processing_category": scope.get("processing_category", "Solutionised + Artificially peak aged"), "num_candidates": scope.get("num_candidates", 12), "random_seed": scope.get("random_seed", 20260914)}
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


def _requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied = dict(payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {})
    requested_domain = str(supplied.get("model_domain") or "").strip()
    if requested_domain and requested_domain not in SUPPORTED_MODEL_DOMAINS:
        raise ValueError(
            f"model_domain={requested_domain!r} 尚未接入已验证专项模型；"
            "将返回材料家族特定的通用配比与试验设计框架"
        )
    idea, upstream_keys = upstream_requirement(payload)
    explicit_hea = any(_has_active_literal(idea, token) for token in ("高熵", "多主元", "HEA", "MPEA"))
    if not explicit_hea and not supplied.get('model_domain') and _has_active_pattern(re.compile(r"钛合金|钛基|Ti[-–]6Al|Ti[-–]Nb", re.IGNORECASE), idea):
        raise ValueError("钛合金专项配比模型尚未接入；按钛合金家族提供配方变量与试验设计，不套用 HEA/MPEA 模型")
    short_cf_intent = is_short_cf_intent(idea, supplied)
    perovskite_intent = is_perovskite_intent(idea, supplied)
    aluminum_intent = is_aerospace_aluminum_intent(idea, supplied)
    glass_intent = is_chip_glass_intent(idea, supplied)
    rocket_intent = is_reusable_rocket_stainless_intent(idea, supplied)
    copper_hot_end_intent = is_copper_hot_end_intent(idea, supplied)
    copper_local_intent = is_copper_local_composition_intent(idea, supplied)
    hot_end_intent = is_ni_hot_end_intent(idea, supplied) and (not explicit_hea or supplied.get("model_domain") == "ni_superalloy_hot_end")
    # An identity already resolved with conflict checks has priority over
    # cross-domain property words (e.g. creep in a stainless-steel request).
    selected_domain = supplied.get("model_domain")
    if selected_domain in SUPPORTED_MODEL_DOMAINS:
        short_cf_intent = selected_domain == "short_cf_thermomechanical_rve_v1"
        perovskite_intent = selected_domain == "perovskite_transport_stability_v2"
        aluminum_intent = selected_domain == "aerospace_aluminum_composition_v1"
        glass_intent = selected_domain == "chip_glass_thermomechanical_family_v1"
        rocket_intent = selected_domain == "reusable_rocket_stainless"
        copper_hot_end_intent = copper_local_intent = selected_domain == "copper_hot_end_local_composition_v1"
        hot_end_intent = selected_domain == "ni_superalloy_hot_end"
    # A concrete requested specialist material is stronger evidence than a
    # generic material word used in an exclusion clause.  This keeps requests
    # such as “玻璃基板，不包含碳纤维” and “30X 不锈钢，排除树脂/纤维” on
    # their intended trained routes.
    specialist_intent = any((short_cf_intent, perovskite_intent, aluminum_intent, glass_intent,
                             rocket_intent, copper_hot_end_intent, hot_end_intent))
    if is_composite_material_request(idea, supplied) and not specialist_intent:
        raise ValueError("本服务仅适用于单一金属合金的元素配比优化；包含树脂、纤维、填料或其他复合相的材料应使用复合材料专项流程")
    if not specialist_intent and selected_domain != "hea_mpea" and not is_alloy_request(idea, supplied):
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
        inferred["application"] = "芯片封装玻璃基板的配方与热机械性能优化"
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "chip_glass_thermomechanical_local_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可提供实际氧化物 mol% 边界、目标 CTE/E/SOC 门槛、玻璃厚度、层堆和温度循环，以替换默认探索条件。"], "evidence_notice": "候选只在低硼无碱玻璃家族的可追溯局部邻域内生成；残余应力和翘曲须结合层堆与热历史计算。"}
    if perovskite_intent:
        inferred = {"model_domain": "perovskite_transport_stability_v2", "temperatures_K": [170, 220, 300, 330], "num_candidates": 5, "objectives": {"ln_sigmaT_220K": {"goal": "minimize"}, "ln_sigmaT_300K": {"goal": "minimize"}}}
        effective = dict(inferred); effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "perovskite_transport_stability_grid_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可提供目标温度、偏压、湿度、光照和寿命定义；这些条件不在当前输运模型中。"], "evidence_notice": "模型输出 ln[sigma(T)T] 与有效 Ea 的输运代理；不输出器件 T80、PCE 或环境分解寿命。"}
    if aluminum_intent:
        inferred = {"model_domain":"aerospace_aluminum_composition_v1", "processing_category":"Solutionised + Artificially peak aged", "num_candidates":12, "random_seed":20260914, "element_bounds_wt_percent":{}, "objectives":{"uts_MPa":{"goal":"maximize"},"yield_strength_MPa":{"goal":"maximize"},"elongation_pct":{"goal":"maximize"}}}
        effective = dict(inferred); effective.update({key:value for key,value in supplied.items() if value not in (None, [], {}, "")})
        provenance={key:("user" if key in supplied and supplied[key] not in (None,[],{},"") else "platform_default") for key in effective}
        return effective,{"parser":"rule_template_v0","raw_requirement":idea,"upstream_context_keys":upstream_keys,"template":"aerospace_aluminum_wt_percent_screening","effective_model_input":effective,"field_provenance":provenance,"default_assumptions":[{"field":key,"value":inferred[key],"status":"platform_default"} for key in inferred if provenance[key]=="platform_default"],"questions_to_confirm":["可提供目标牌号族、各元素 wt.% 边界和已知工艺类别，以收窄局部候选。"],"evidence_notice":"模型输出名义室温 UTS、YS 和延伸率；未包含具体时效/固溶温度与时间，不能作为热处理优化模型。Al-Li 子域仅作局部参考。"}
    if rocket_intent:
        inferred = {"model_domain": "reusable_rocket_stainless", "element_bounds_wt_percent": {"Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0], "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08]}, "test_temperature_K": 293, "processing": {"material_state": "solution_annealed", "solution_treatment_temperature_K": 1323, "solution_treatment_time_s": 3600, "quench": "water", "product_form_code": 1, "melting_route_code": 1}, "component": "可回收火箭贮箱或承压壳体（母材）", "weld_state": "base_metal", "low_temperature_verification_K": [90, 111], "verification_focus": ["cryogenic_toughness", "weld", "fatigue", "LOX_compatibility"], "num_candidates": 40, "objectives": {"yield_strength": 1, "uts": 1, "elongation": 1}}
        effective = dict(inferred)
        effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
        provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "platform_default") for key in effective}
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "reusable_rocket_stainless_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in inferred if provenance[key] == "platform_default"], "questions_to_confirm": ["可继续提供目标温度、板厚、焊接状态、成分 wt.% 边界和实际热处理，以替换本轮可见默认条件。"], "evidence_notice": "293–1273 K 输出为短时拉伸候选筛选；更低温度转为 301/304L 参考和验证规划。"}
    if copper_local_intent:
        inferred = {"model_domain":"copper_hot_end_local_composition_v1", "composition_family":"GRCop_type_Cu_Cr_Nb", "element_bounds_wt_percent":{"Cu":[84,94],"Cr":[4,9],"Nb":[4,7],"Zr":[0,1],"Ag":[0,2],"Al":[0,.2],"O":[0,.2],"Ni":[0,.2],"Fe":[0,.2],"Ti":[0,.5]}, "processing_state":"as_received", "test_temperature_C":25, "ambient_process":{"solution_temperature_K":1233,"solution_time_h":2,"cold_reduction_pct":50,"aged":True,"aging_temperature_K":723,"aging_time_h":2,"secondary_thermomechanical_process":False}, "num_candidates":12, "random_seed":20260911, "objectives":{"ultimate_tensile_strength_MPa":{"goal":"maximize"},"yield_0p2_MPa":{"goal":"maximize"}}}
        text_thresholds = _explicit_strength_thresholds(idea)
        inferred["screening_thresholds"] = {}
        effective = dict(inferred); effective.update({key:value for key,value in supplied.items() if key != "ambient_process" and value not in (None,[],{},"")})
        effective["screening_thresholds"] = {**text_thresholds, **(supplied.get("screening_thresholds") or {})}
        if isinstance(supplied.get("ambient_process"), dict):
            effective["ambient_process"] = {**inferred["ambient_process"], **supplied["ambient_process"]}
        provenance = {key:("user" if key in supplied and supplied[key] not in (None,[],{},"") else "upstream_context" if key == "screening_thresholds" and text_thresholds else "platform_default") for key in effective}
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
        effective["screening_thresholds"] = {**context_overrides.get("screening_thresholds", {}), **(supplied.get("screening_thresholds") or {})}
        provenance = {
            key: (
                "user" if key in supplied and supplied[key] not in (None, [], {}, "")
                else "upstream_context" if key in context_overrides
                else "template_inference"
            )
            for key in effective
        }
        missing = hot_end_missing_fields(effective)
        defaults_used = [key for key in _HOT_END_PLATFORM_DEFAULTS if provenance[key] == "template_inference" and inferred[key] not in (None, [], {}, "")]
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
        effective["screening_thresholds"] = {**context_overrides.get("screening_thresholds", {}), **(supplied.get("screening_thresholds") or {})}
        provenance = {
            key: (
                "user" if key in supplied and supplied[key] not in (None, [], {}, "")
                else "upstream_context" if key in context_overrides
                else "platform_default"
            )
            for key in effective
        }
        defaults_used = [key for key in _HOT_END_PLATFORM_DEFAULTS if provenance[key] == "platform_default" and inferred[key] not in (None, [], {}, "")]
        return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": "hot_end_ni_superalloy_screening", "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": inferred[key], "status": "platform_default"} for key in defaults_used], "questions_to_confirm": ["已采用平台默认热端工况完成首轮筛选；可继续提供实际路线、热处理、温度、载荷或 wt.% 边界以重新计算。"], "missing_required_inputs": [], "evidence_notice": "Screening is conditional comparison, not an engineering release conclusion。"}
    else:
        template = "generic_hea_exploration"
        inferred = {"model_domain": "hea_mpea", "allowed_elements": ["Co", "Cr", "Fe", "Mn", "Ni"], "element_bounds_at_pct": {"Co": [10, 30], "Cr": [10, 30], "Fe": [10, 30], "Mn": [10, 30], "Ni": [10, 30]}, "processing_method": "CAST", "test_temperature_C": 25, "screening_mode": "conservative_adaptive", "objectives": {"yield_strength_MPa": {"goal": "maximize"}, "phase_risk": {"goal": "minimize"}}}
        questions = ["请确认目标服役温度、允许元素体系、工艺和成本约束。"]
    effective = dict(inferred)
    effective.update({key: value for key, value in supplied.items() if value not in (None, [], {}, "")})
    provenance = {key: ("user" if key in supplied and supplied[key] not in (None, [], {}, "") else "template_inference") for key in effective}
    return effective, {"parser": "rule_template_v0", "raw_requirement": idea, "upstream_context_keys": upstream_keys, "template": template, "effective_model_input": effective, "field_provenance": provenance, "default_assumptions": [{"field": key, "value": value, "status": "requires_confirmation"} for key, value in inferred.items() if provenance[key] == "template_inference"], "questions_to_confirm": questions, "evidence_notice": "Template inference is exploratory only, not an engineering conclusion."}


_CONTEXT_SCOPE_FIELDS = frozenset({
    "model_domain", "composition", "composition_wt_percent", "composition_mol_percent", "composition_basis",
    "allowed_elements", "element_bounds_at_pct", "element_bounds_wt_percent", "oxide_bounds_mol_percent",
    "processing_method", "processing_state", "processing_category", "manufacturing_route", "heat_treatment",
    "test_temperature_C", "test_temperature_K", "temperatures_K", "applied_stress_MPa", "processing",
    "ambient_process", "composition_family", "matrix_name", "matrix_properties", "target_vf", "fiber_length_mm",
    "target_a11", "candidate_windows", "search_space", "fractions", "screening_thresholds", "objectives", "component", "weld_state", "thickness_mm",
    "low_temperature_verification_K", "verification_focus", "application", "service_options", "num_candidates",
})


def requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return _resolved_requirement_plan(payload)
    except MaterialContextError as exc:
        from src.alloy_workflow.fallback import generic_fallback_plan
        return generic_fallback_plan(payload, exc)


def _resolved_requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the material first; inherit only existing, explicitly named inputs."""
    text, keys = upstream_requirement(payload)
    explicit = dict(payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {})
    inherited: dict[str, Any] = {}
    # If a current request was used on its own, historic model inputs stay out.
    source_keys = [key for key in keys if key not in {"idea", "content", "query"}]
    for key in source_keys:
        records = _material_records(payload.get(key), history=key in _HISTORY_KEYS)
        if len(records) != 1:
            continue
        record = records[0][1]
        for scope in (record, record.get("screening_conditions"), record.get("simulation_conditions"), record.get("effective_model_input"), record.get("constraints"), record.get("alloy_optimization")):
            if isinstance(scope, dict):
                inherited.update({field: value for field, value in scope.items() if field in _CONTEXT_SCOPE_FIELDS and value not in (None, "", [], {})})
    from src.alloy_workflow.requirement_semantics import canonical_text, checked_domain, parse_numeric_requirements, merge_requirements
    canonical = canonical_text(text)
    decisions = []
    domain = checked_domain(canonical, {**inherited, **explicit}, decisions)
    if source_keys and domain == "short_cf_thermomechanical_rve_v1" and not (inherited.get("matrix_name") or explicit.get("matrix_name")):
        matrix = short_cf_matrix_from_text(canonical)
        if matrix:
            inherited["matrix_name"] = matrix
    # Values quoted in a paper/simulation remain evidence. Only the current
    # request supplies new numerical goals; structured adopted conditions can
    # still be inherited above.
    current = next((_authoritative_request_text(context_text(payload[key])) for key in ("idea", "content", "query") if payload.get(key)), "")
    try:
        parsed, evidence = parse_numeric_requirements(current, domain, {**inherited, **explicit}, decisions)
    except MaterialContextError as exc:
        exc.request = current
        exc.decisions = decisions
        raise
    try:
        supplied = merge_requirements(inherited, parsed, explicit, text, decisions)
    except MaterialContextError as exc:
        if not domain:raise
        supplied={**inherited,**explicit,**parsed}
        supplied['_calculation_blockers']=[str(exc)]
    if domain:
        supplied["model_domain"] = domain
    normalized = dict(payload)
    normalized["idea"] = canonical_text(current) if domain and source_keys else canonical
    normalized["alloy_optimization"] = supplied
    effective, plan = _requirement_plan(normalized)
    # Partial bounds update only named components. Keep the default bounds of
    # other components so a Cr-only request does not delete required Ni input.
    bounds_fields=('element_bounds_wt_percent','element_bounds_at_pct','oxide_bounds_mol_percent')
    partial={key:supplied[key] for key in bounds_fields if isinstance(supplied.get(key),dict) and supplied[key]}
    if partial:
        base_payload=dict(normalized)
        base_payload['alloy_optimization']={key:value for key,value in supplied.items() if key not in partial}
        base,_=_requirement_plan(base_payload)
        for key,value in partial.items():
            defaults=base.get(key) or {}
            effective[key]={**defaults,**value}
            if defaults.keys()-value.keys():
                decisions.append({'field':key,'parameter':'组分边界采用方式','adopted':'指定组分采用本次范围，其余组分保留当前模型的默认范围',
                    'reason':'本次仅调整部分组分；完整上下限在配比范围表中列出。'})
    from src.alloy_workflow.request_capabilities import separate_requirements
    for key in effective.get('_disabled_objectives',[]):
        effective.get('objectives',{}).pop(key,None)
    separate_requirements(effective,decisions)
    if plan.get('missing_required_inputs'):
        effective.setdefault('_calculation_blockers',[]).append('当前专项计算缺少必要条件：'+
            '、'.join(str(v.get('label',v)) if isinstance(v,dict) else str(v) for v in plan['missing_required_inputs']))
    plan["raw_requirement"] = text
    plan["upstream_context_keys"] = keys
    plan["parameter_evidence"] = evidence
    plan["execution_notes"] = decisions
    plan["original_structured_parameters"] = explicit
    for key in parsed:
        plan["field_provenance"][key] = "text_extraction"
    if domain and "model_domain" not in explicit and "model_domain" not in inherited:
        plan["field_provenance"]["model_domain"] = "material_identity"
    for key in inherited.keys() - explicit.keys() - parsed.keys():
        if key in plan["field_provenance"]:
            plan["field_provenance"][key] = "upstream_context"
    for decision in decisions:
        if decision['field'] in plan['field_provenance']:
            plan['field_provenance'][decision['field']] = 'adopted_assumption'
    return effective, plan
