"""成熟材料目录服务的编排层。

``main.py`` 负责 HTTP/WebSocket 传输与前端事件；本模块负责从需求规范化、
目录检索到 manifest 和展示资产准备的完整服务流程，不依赖历史 Alpha 链路。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.catalog.presentation import (
    analysis_markdown,
    comparison_markdown,
    conclusion_markdown,
    default_comparison_property,
    property_label,
    render_property_comparison,
    screening_funnel_rows,
    resolution_markdown,
)
from src.catalog.query import MatureMaterialCatalog, parse_preference_goals, parse_property_constraints
from src.catalog.property_vocabulary import PROPERTY_VOCABULARY
from src.fluid_lubricant.workflow import (
    WORKFLOW_KIND as FLUID_SCREENING_WORKFLOW,
    FluidLubricantWorkflow,
    has_explicit_screening_conditions,
    is_conductive_lubricant_request,
    user_allows_default,
)
from src.fluid_lubricant.presentation import render_assets as render_fluid_style_assets
from src.service_identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE
from src.screening_language import RANGE_JOINER, range_constraints


CATALOG_SCREENING_WORKFLOW = "mature_material_catalogue_initial_screen"
_OPEN_SELECTION_REQUEST = re.compile(r"挑选|选(?:一款|材|型|择)|筛选|推荐", re.IGNORECASE)
_CATALOGUE_MATERIAL_REQUEST = re.compile(
    r"(?:成熟|商品)?(?:金属|固体)?材料|金属材料|合金|不锈钢|高温合金|复合材料",
    re.IGNORECASE,
)
_DIRECTIONAL_PROPERTIES = {
    "抗拉强度": "ultimate_tensile_strength", "极限抗拉强度": "ultimate_tensile_strength",
    "屈服强度": "yield_strength", "导热": "thermal_conductivity", "导热率": "thermal_conductivity", "导热系数": "thermal_conductivity",
    "硬度": "hardness", "延伸率": "elongation", "密度": "density",
}
_TEXT_PROPERTY_CONSTRAINTS = tuple(
    (alias, property_name, unit_pattern, canonical_unit)
    for property_name, _label, aliases, unit_pattern, canonical_unit in PROPERTY_VOCABULARY
    if unit_pattern
    for alias in aliases
)
_TEXT_OPERATORS = (
    (r"不低于|不少于|至少|不小于|≥|>=", ">="),
    (r"不高于|不超过|至多|不大于|≤|<=", "<="),
    (r"大于|高于|>", ">"),
    (r"小于|低于|<", "<"),
)


def catalogue_screening_strategy(*, material_queries: list[str], material_families: list[str], standards: list[str],
                                 property_constraints: list[dict[str, Any]], service_temperature_K: float | None,
                                 selection_context: dict[str, str], preference_goals: list[dict[str, str]]) -> dict[str, Any]:
    """Choose an evidence-screening depth from stated, not inferred, inputs."""
    dimensions = {
        "material_anchor": bool(material_queries or standards),
        "material_family": bool(material_families),
        "service_temperature": service_temperature_K is not None,
        "property_targets": bool(property_constraints or preference_goals),
        "application": bool(selection_context.get("application")),
        "environment": bool(selection_context.get("environment")),
        "manufacturing": bool(selection_context.get("manufacturing")),
    }
    property_target_count = len({item.get("property") for item in property_constraints + preference_goals if item.get("property")})
    count = sum(value for key, value in dimensions.items() if key != "property_targets") + property_target_count
    # A named grade is also a valid request in its own right.  Treat it as a
    # catalogue index/verification request until the user states a property
    # target; do not make the customer add artificial screening conditions just
    # to see the record, its product state, and its evidence.
    if (material_queries or standards) and not property_constraints and not preference_goals:
        mode = "catalogue_index"
        description = "按材料名称、牌号或标准核验目录身份、产品状态、已收录性质及其来源；未给出性能条件，因此本轮不筛选或排序。"
    # Directional wording (for example “高散热、硬度”) is useful for
    # evidence ordering, but is never a hard multi-condition pass/fail
    # screen.  Keep its strategy truthful even when the request also states
    # application or manufacturing context.
    elif preference_goals and not property_constraints:
        mode = "evidence_landscape"
        description = "按明确的方向性目标排序并展示证据覆盖，不将定性目标伪造成性能阈值或通过结论。"
    elif count == 0:
        mode = "criteria_collection"
        description = "未提供可比较条件，只收集筛选条件。"
    elif count == 1:
        mode = "evidence_landscape"
        description = "按单一明确条件浏览已入库证据，不对候选作综合优先级判断。"
    elif count == 2:
        mode = "cross_filter"
        description = "按两个明确维度交叉过滤，展示符合与缺失证据。"
    else:
        mode = "strict_evidence_screen"
        description = "按三个及以上明确维度执行严格证据筛选。"
    return {
        "mode": mode,
        "provided_dimension_count": count,
        "property_target_count": property_target_count,
        "dimensions": dimensions,
        "description": description,
        "inference_policy": "Never assume a material family, operating condition, or performance threshold that the user did not provide.",
    }


def _directional_goals_from_text(text: str) -> list[dict[str, str]]:
    goals = []
    for label, property_name in _DIRECTIONAL_PROPERTIES.items():
        if re.search(re.escape(label) + r".{0,12}越(?:高|大|强)越好", text, re.IGNORECASE):
            goals.append({"property": property_name, "direction": "maximize"})
        if re.search(re.escape(label) + r".{0,12}越(?:低|小)越好", text, re.IGNORECASE):
            goals.append({"property": property_name, "direction": "minimize"})
    # Engineering requests often use compact modifiers rather than the fully
    # spelled-out “越高越好”.  These are ranking intentions only.  In
    # particular, “高散热、硬度” is conventional shorthand for both high
    # thermal-conductivity and high-hardness targets.
    if re.search(r"(?:高散热|高导热|散热(?:性|能力)?好)", text, re.IGNORECASE):
        goals.append({"property": "thermal_conductivity", "direction": "maximize"})
    if re.search(r"(?:高硬度|高散热\s*[、，,及和与]\s*硬度)", text, re.IGNORECASE):
        goals.append({"property": "hardness", "direction": "maximize"})
    if re.search(r"(?:高强度|强度高|高抗拉|高屈服)", text, re.IGNORECASE):
        goals.append({"property": "ultimate_tensile_strength", "direction": "maximize"})
    # “低密度/轻量化” is the equally common compact counterpart of
    # “密度越低越好”.  It is a ranking intent, never an inferred threshold.
    if re.search(r"(?:低密度|轻量化|轻质(?:化)?|质量轻)", text, re.IGNORECASE):
        goals.append({"property": "density", "direction": "minimize"})
    deduplicated = dict.fromkeys((goal["property"], goal["direction"]) for goal in goals)
    ordered = [
        {"property": property_name, "direction": direction}
        for property_name, direction in deduplicated
    ]
    markers = {
        "density": ("密度", "轻量", "轻质", "质量轻"),
        "ultimate_tensile_strength": ("抗拉", "拉伸", "强度", "高强"),
        "yield_strength": ("屈服",),
        "thermal_conductivity": ("散热", "导热"),
        "hardness": ("硬度",),
        "elongation": ("延伸",),
    }
    return sorted(
        ordered,
        key=lambda goal: min((text.find(marker) for marker in markers.get(goal["property"], ()) if text.find(marker) >= 0), default=len(text)),
    )


def _property_is_mentioned(text: str, property_name: str) -> bool:
    """Do not carry a stale structured preference into a newer user turn."""
    aliases = [label for label, mapped in _DIRECTIONAL_PROPERTIES.items() if mapped == property_name]
    return any(label in text for label in aliases)


def _selection_context_from_text(text: str) -> dict[str, str]:
    """Recover stated context without upgrading it to a numeric constraint."""
    context: dict[str, str] = {}
    if re.search(r"机器人", text, re.IGNORECASE):
        context["application"] = "机器人零部件"
    if re.search(r"(?<![A-Za-z0-9])STL(?![A-Za-z0-9])", text, re.IGNORECASE):
        context["manufacturing"] = "参考 STL 几何文件；具体制造工艺待确认"
    return context
    return goals


def _property_constraints_from_text(text: str) -> list[dict[str, Any]]:
    """Extract explicit, unit-bearing numeric thresholds from a user turn."""
    # Planner summaries may retain LaTex wrappers around units/operators.
    # Remove formatting only; never turn an unqualified number into a limit.
    text = re.sub(r"\\(?:text|mathrm)\s*\{([^}]*)\}", r"\1", text or "")
    text = text.replace(r"\geq", "≥").replace(r"\ge", "≥").replace(r"\leq", "≤").replace(r"\le", "≤")
    constraints: list[dict[str, Any]] = []
    for label, property_name, unit_pattern, canonical_unit in _TEXT_PROPERTY_CONSTRAINTS:
        range_pattern = (
            re.escape(label) + r"\s*(?:为|是|需达到|要求)?\s*"
            + r"(?P<lower>\d+(?:\.\d+)?)\s*" + RANGE_JOINER + r"\s*"
            + r"(?P<upper>\d+(?:\.\d+)?)\s*"
            + r"(?P<unit>" + unit_pattern + r")"
        )
        for match in re.finditer(range_pattern, text, re.IGNORECASE):
            lower, upper = float(match.group("lower")), float(match.group("upper"))
            if lower > upper:
                continue
            unit = canonical_unit or match.group("unit")
            for constraint in range_constraints(key="property", name=property_name, unit=unit, lower=lower, upper=upper):
                if constraint not in constraints:
                    constraints.append(constraint)
        for operator_pattern, operator in _TEXT_OPERATORS:
            pattern = (
                re.escape(label) + r"\s*(?:为|是|需达到|要求)?\s*"
                + r"(?:" + operator_pattern + r")\s*"
                + r"(?P<value>\d+(?:\.\d+)?)\s*"
                + r"(?P<unit>" + unit_pattern + r")"
            )
            for match in re.finditer(pattern, text, re.IGNORECASE):
                unit = canonical_unit or match.group("unit")
                constraint = {"property": property_name, "operator": operator, "value": float(match.group("value")), "unit": unit}
                if constraint not in constraints:
                    constraints.append(constraint)
    positions = {
        property_name: min((text.find(label) for label, candidate_property, _unit_pattern, _canonical_unit in _TEXT_PROPERTY_CONSTRAINTS
                            if candidate_property == property_name and text.find(label) >= 0), default=len(text))
        for property_name in {item["property"] for item in constraints}
    }
    constraints.sort(key=lambda item: positions[item["property"]])
    return constraints


class MatureMaterialCatalogQuery:
    """描述确定性的已有成熟材料目录查询动作。"""

    name: str = ACTION_NAME
    desc: str = ACTION_DESCRIPTION

    async def run(self, instruction: str, *args, **kwargs) -> str:
        return (
            "已有材料查询由 mature_material 的 /start 或 /mature-material/query 执行。"
            "请提供材料名称、厂家/牌号、标准号，或包含性质、工况和来源的 upstream_evidence；"
            "若尚无材料证据，建议先进入文献筛选。"
        )


class MaterialMature:
    """编排一次可追溯的成熟材料目录查询。"""

    name: str = ROLE_NAME
    profile: str = ROLE_PROFILE

    _EXECUTION_MARKER = re.compile(
        r"(?:接下来(?:需要)?进行执行的任务|接下来执行的任务|当前(?:需要)?执行任务|执行任务)\s*[：:]\s*",
        flags=re.IGNORECASE,
    )
    _CURRENT_QUESTION_MARKER = re.compile(r"(?:^|\n)===\s*当前问题\s*===\s*(.+)$", flags=re.DOTALL)
    _NON_MATERIAL_TOKENS = frozenset({
        "XIMUALPHA", "LLM", "RAG", "PDF", "CIF", "MP", "MPA", "HFE", "HTCC", "CTE", "IPC",
    })
    _MATERIAL_ACRONYMS = frozenset({"ABS", "ASA", "PA", "PEEK", "PEI", "PETG", "PLA", "PPS", "PTFE", "PVC"})
    _PRINT_CONSUMABLE_SCOPE = "__3d_printing_consumables__"
    _ADDITIVE_SCOPE = "__additive_manufacturing_materials__"
    # Correct only an unambiguous, high-frequency one-letter transposition.
    # The canonical query is still shown in the report; arbitrary fuzzy
    # matching would be unsafe for material grades and standards.
    _COMMON_MATERIAL_TYPO_CORRECTIONS = {"CEEK": "PEEK"}

    def __init__(
        self,
        *,
        catalog_root: Path | str = "data/processed",
        raw_data_root: Path | str = ".",
        results_root: Path | str = "results/mature_material",
        service_name: str = "mature-material",
        **metadata: Any,
    ) -> None:
        self.catalog_root = Path(catalog_root)
        self.raw_data_root = Path(raw_data_root)
        self.results_root = Path(results_root)
        self.service_name = service_name
        self.metadata = metadata
        self.actions = [MatureMaterialCatalogQuery]

    def _fluid_workflow(self) -> FluidLubricantWorkflow:
        return FluidLubricantWorkflow(
            database=self.catalog_root / "fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite",
            results_root=self.results_root,
            service_name=self.service_name,
        )

    @staticmethod
    def _taskid(payload: dict[str, Any]) -> str:
        taskid = str(payload.get("taskid") or f"mature-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
        if not taskid.strip() or len(taskid) > 512:
            raise ValueError("taskid must be a non-empty string no longer than 512 characters")
        return taskid

    @staticmethod
    def task_storage_key(taskid: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", taskid):
            return taskid
        return "opaque-" + hashlib.sha256(taskid.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value] if isinstance(value, list) else []

    @classmethod
    def _context_text(cls, value: Any, *, limit: int = 12000) -> str:
        fragments: list[str] = []

        def visit(item: Any) -> None:
            if len("\n".join(fragments)) >= limit:
                return
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    return
                try:
                    decoded = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    fragments.append(text)
                else:
                    visit(decoded)
            elif isinstance(item, dict):
                for key in ("idea", "content", "text", "query", "summary", "message", "requirement"):
                    if item.get(key) is not None:
                        visit(item[key])
                for key in ("messages", "history", "conversation", "upstream_context", "previous_results"):
                    if item.get(key) is not None:
                        visit(item[key])
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return "\n\n".join(fragments)[:limit]

    @classmethod
    def _upstream_context(cls, payload: dict[str, Any]) -> tuple[str, list[str]]:
        keys = [
            key for key in ("idea", "content", "query", "prompt", "user_input", "current_user_message", "latest_user_message", "history", "messages", "conversation", "upstream_context", "previous_results")
            if payload.get(key) is not None
        ]
        return cls._context_text({key: payload[key] for key in keys}), keys

    @staticmethod
    def _direct_user_requirement(payload: dict[str, Any]) -> str:
        """Use a latest user turn when present, otherwise accept an upstream summary.

        Gateways in this deployment have used several envelopes for a follow-up
        turn (``data.messages``, ``input.history`` and ``sender`` rather than
        ``role``).  Compressed upstream summaries remain supported, provided
        they retain the actual numerical constraints rather than replacing
        them with a phrase such as "precise viscosity criteria".
        """
        direct_keys = (
            "current_user_message", "latest_user_message", "user_input",
            "user_message", "follow_up_message", "latest_message", "prompt",
            "requirement", "question", "query",
        )
        text_keys = ("content", "text", "message", "value", "body", "query", "question")
        user_roles = {"user", "human", "用户", "终端用户", "client"}
        containers = ("messages", "history", "conversation", "turns", "chat_history")

        def text(value: Any) -> str:
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, Mapping):
                for key in text_keys:
                    candidate = text(value.get(key))
                    if candidate:
                        return candidate
            return ""

        def latest_user_turn(value: Any) -> str:
            if isinstance(value, list):
                for item in reversed(value):
                    candidate = latest_user_turn(item)
                    if candidate:
                        return candidate
                return ""
            if not isinstance(value, Mapping):
                return ""
            role = str(value.get("role") or value.get("sender") or value.get("author") or value.get("type") or "").strip().lower()
            if role in user_roles:
                return text(value)
            # Walk nested gateway envelopes in reverse conversational order.
            for key in containers:
                candidate = latest_user_turn(value.get(key))
                if candidate:
                    return candidate
            for key in ("data", "input", "payload", "request", "context"):
                candidate = latest_user_turn(value.get(key))
                if candidate:
                    return candidate
            return ""

        for key in direct_keys:
            candidate = text(payload.get(key))
            if candidate:
                return candidate
        # Some gateways flatten the complete conversation into ``idea`` and
        # delimit the actual final user turn with this marker.  Treat that
        # trailing turn as direct input before applying the long-history guard
        # below; otherwise an unrelated old-material guard also discards the
        # real material name in the final question.
        for key in ("idea", "content", "query"):
            candidate = text(payload.get(key))
            match = MaterialMature._CURRENT_QUESTION_MARKER.search(candidate)
            if match:
                return match.group(1).strip()
        return latest_user_turn(payload)

    @classmethod
    def _material_extraction_text(cls, text: str) -> str:
        matches = list(cls._EXECUTION_MARKER.finditer(text or ""))
        return (text or "")[matches[-1].end():].strip() if matches else (text or "").strip()

    @classmethod
    def _formula_like_terms(cls, text: str) -> list[str]:
        terms = re.findall(
            r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])",
            cls._material_extraction_text(text),
        )
        return list(dict.fromkeys(
            term for term in terms
            if len(term) >= 3
            and term.upper() not in cls._NON_MATERIAL_TOKENS
            and (term.upper() in cls._MATERIAL_ACRONYMS or any(char.isdigit() for char in term) or any(char.islower() for char in term))
        ))

    def contract(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 阶段 1：规范化母服务/前端输入，只保留可核验的材料名称、牌号、标准、
        # 性质条件与上游证据；上游证据不能直接提升为目录事实。
        taskid = self._taskid(payload)
        scope = payload.get("mature_material") or payload.get("constraints") or {}
        if not isinstance(scope, dict):
            raise ValueError("mature_material must be an object")
        temperature_c = scope.get("service_temperature_C", scope.get("temperature_C"))
        try:
            default_temperature_K = float(temperature_c) + 273.15 if temperature_c is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("temperature_C must be numeric") from exc
        properties = scope.get("property_constraints", scope.get("property_filters", {}))
        upstream_context, upstream_keys = self._upstream_context(payload)
        direct_requirement = self._direct_user_requirement(payload)
        raw_requirement = str(direct_requirement or scope.get("query") or payload.get("idea") or upstream_context)
        # Planner summaries can quote a prior default profile before their
        # final execution clause.  For fluid screening, only that last clause
        # is allowed to supply current numeric constraints; otherwise old
        # defaults become a false user instruction.
        fluid_requirement = self._material_extraction_text(raw_requirement)
        previous = self.load_task(taskid)
        previous_is_fluid = bool(previous and previous.get("workflow_kind") == FLUID_SCREENING_WORKFLOW)
        explicit_fluid_request = is_conductive_lubricant_request(fluid_requirement)
        explicit_fluid_numbers = has_explicit_screening_conditions(fluid_requirement) and "油" in fluid_requirement
        # A structured fluid request is also an explicit routing signal, but a
        # latest request for a solid/metal catalogue must override a stale
        # nested scope supplied by the gateway.
        explicit_catalogue_request = bool(_CATALOGUE_MATERIAL_REQUEST.search(fluid_requirement))
        structured_fluid_request = bool(
            isinstance(scope.get("fluid_initial_screen", scope.get("fluid_screening")), dict)
            and not explicit_catalogue_request
        )
        # A previous fluid task is context, not a routing lock.  Continue it
        # only when this turn supplies fluid-screening numbers or explicitly
        # asks to use defaults.  A new request such as “推荐成熟金属材料” must
        # be allowed to return to the catalogue workflow even with the same
        # taskid and stale upstream history.
        fluid_followup = previous_is_fluid and (
            has_explicit_screening_conditions(fluid_requirement)
            or user_allows_default(fluid_requirement)
        )
        if explicit_fluid_request or explicit_fluid_numbers or structured_fluid_request or fluid_followup:
            previously_requested_criteria = bool(
                previous
                and previous.get("workflow_kind") == FLUID_SCREENING_WORKFLOW
                and previous.get("data_status", {}).get("outcome") == "needs_screening_criteria"
            )
            return self._fluid_workflow().contract(
                payload,
                taskid=taskid,
                raw_requirement=fluid_requirement,
                scope=scope,
                apply_default_profile=(
                    user_allows_default(fluid_requirement)
                    or previously_requested_criteria
                    or has_explicit_screening_conditions(fluid_requirement)
                ),
            )
        extraction_text = self._material_extraction_text(raw_requirement)
        # A long upstream summary may contain obsolete PLA/ASA mentions from a
        # different branch.  Alias recovery is allowed only for the latest
        # execution clause, or for a short direct request with no history.
        has_execution_marker = bool(self._EXECUTION_MARKER.search(raw_requirement))
        alias_extraction_text = extraction_text if has_execution_marker or len(raw_requirement) <= 1200 else ""
        upstream_evidence = scope.get("upstream_evidence", payload.get("upstream_evidence", []))
        if isinstance(upstream_evidence, dict):
            upstream_evidence = [upstream_evidence]
        if not isinstance(upstream_evidence, list) or not all(isinstance(item, dict) for item in upstream_evidence):
            raise ValueError("upstream_evidence must be an object or a list of objects")
        engineering_estimates = scope.get("engineering_estimates", payload.get("engineering_estimates", []))
        if isinstance(engineering_estimates, dict):
            engineering_estimates = [engineering_estimates]
        if not isinstance(engineering_estimates, list) or not all(isinstance(item, dict) for item in engineering_estimates):
            raise ValueError("engineering_estimates must be an object or a list of objects")
        for estimate in engineering_estimates:
            if not str(estimate.get("property") or "").strip():
                raise ValueError("every engineering estimate needs property")
            if not str(estimate.get("source") or "").strip() or not str(estimate.get("basis") or "").strip():
                raise ValueError("every engineering estimate needs source and basis")
        material_queries = self._as_list(scope.get("material_queries", scope.get("materials", scope.get("names", []))))
        for typo, canonical in self._COMMON_MATERIAL_TYPO_CORRECTIONS.items():
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(typo) + r"(?![A-Za-z0-9])", alias_extraction_text, re.IGNORECASE):
                if canonical not in material_queries:
                    material_queries.append(canonical)
        for acronym in self._MATERIAL_ACRONYMS:
            # Do not truncate a composite grade such as PPS-CF into PPS.
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(acronym) + r"(?![A-Za-z0-9-])", alias_extraction_text, re.IGNORECASE):
                if acronym not in material_queries:
                    material_queries.append(acronym)
        if not material_queries:
            for item in upstream_evidence:
                for key in ("material", "name", "grade", "standard"):
                    value = str(item.get(key) or "").strip()
                    if value and value not in material_queries:
                        material_queries.append(value)
        material_families = self._as_list(scope.get("material_families", scope.get("families", [])))
        # These are catalogue scopes, not inferred material grades.  They are
        # intentionally explicit so a request for printing consumables does
        # not silently turn into an all-metal catalogue search.
        printable_text = extraction_text.casefold()
        if not material_families and re.search(r"(?:3d\s*打印|打印耗材|打印线材|fdm|fff|sls|sla|光固化|耗材)", printable_text, re.IGNORECASE):
            material_families.append(
                self._PRINT_CONSUMABLE_SCOPE
                if re.search(r"(?:耗材|线材|fdm|fff|sls|sla|光固化)", printable_text, re.IGNORECASE)
                else self._ADDITIVE_SCOPE
            )
        selection_context_raw = scope.get("selection_context", {})
        if selection_context_raw is None:
            selection_context_raw = {}
        if not isinstance(selection_context_raw, dict):
            raise ValueError("mature_material.selection_context must be an object")
        selection_context = {
            key: str(selection_context_raw.get(key) or scope.get(key) or "").strip()
            for key in (
                "application", "component", "operating_conditions",
                "environment", "manufacturing", "project_progress",
            )
        }
        for key, value in _selection_context_from_text(extraction_text).items():
            selection_context.setdefault(key, value)
            if not selection_context[key]:
                selection_context[key] = value
        parsed_property_constraints = [item.__dict__ for item in parse_property_constraints(properties, default_temperature_K)]
        for constraint in _property_constraints_from_text(extraction_text):
            if constraint not in parsed_property_constraints:
                parsed_property_constraints.append(constraint)
        explicit_preferences = scope.get("preference_goals", [])
        preference_goals = [item.__dict__ for item in parse_preference_goals(explicit_preferences)]
        if direct_requirement:
            preference_goals = [
                goal for goal in preference_goals
                if _property_is_mentioned(extraction_text, str(goal.get("property") or ""))
            ]
        for goal in _directional_goals_from_text(raw_requirement):
            if goal not in preference_goals:
                preference_goals.append(goal)
        standards = self._as_list(scope.get("standards", []))
        strategy = catalogue_screening_strategy(
            material_queries=material_queries,
            material_families=material_families,
            standards=standards,
            property_constraints=parsed_property_constraints,
            service_temperature_K=default_temperature_K,
            selection_context=selection_context,
            preference_goals=preference_goals,
        )
        # Keep the first response compact for the UI.  Search still calculates
        # and reports the full candidate counts before this display limit;
        # callers can request up to 50 results explicitly.
        requested_limit = scope.get("top_k", 10)
        top_k = max(1, min(int(requested_limit), 50))
        return {
            "taskid": taskid,
            "workflow_kind": CATALOG_SCREENING_WORKFLOW,
            "raw_requirement": raw_requirement,
            "upstream_context": upstream_context,
            "upstream_context_keys": upstream_keys,
            "material_queries": material_queries,
            "material_families": material_families,
            "standards": standards,
            "property_constraints": parsed_property_constraints,
            "preference_goals": preference_goals,
            "service_temperature_K": default_temperature_K,
            "top_k": top_k,
            "source_preference": str(scope.get("source_preference", "all")),
            "alias_extraction_text": alias_extraction_text,
            # 上游证据只原样保留和展示；没有目录匹配时绝不写成已核验事实。
            "upstream_evidence": upstream_evidence,
            # This optional input is deliberately separate from upstream facts
            # and catalogue evidence.  It may be generated by an upstream LLM,
            # but is never used by catalogue filtering or ranking.
            "engineering_estimates": engineering_estimates,
            "selection_context": selection_context,
            "screening_strategy": strategy,
            # Keep the generic path in the same inspectable workflow shape as
            # conductive-liquid screening.  This is a catalogue evidence
            # request, not a recommendation profile or inferred substitute.
            "screening_request": {
                "material_queries": material_queries,
                "material_families": material_families,
                "standards": standards,
                "property_constraints": parsed_property_constraints,
                "preference_goals": preference_goals,
                "service_temperature_K": default_temperature_K,
                "selection_context": selection_context,
                "limit": top_k,
            },
        }

    async def run(self, constraints: dict[str, Any]) -> dict[str, Any]:
        """阶段 2：执行目录检索与可比性判断，不处理任何传输协议。"""
        if constraints.get("workflow_kind") == FLUID_SCREENING_WORKFLOW:
            return await self._fluid_workflow().run(constraints)
        catalog = MatureMaterialCatalog(self.catalog_root)
        if not catalog.ready:
            return {
                "taskid": constraints["taskid"],
                "status": "accepted_pending_catalog_ingestion",
                "service": self.service_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workflow_kind": CATALOG_SCREENING_WORKFLOW,
                "constraints": constraints,
                "results": [],
                "screening": None,
                "data_status": {
                    "catalog_ready": False,
                    "raw_data_root_available": self.raw_data_root.exists(),
                    "message": "Structured catalogue is unavailable; raw PDFs were not queried.",
                },
            }
        parsed_constraints = parse_property_constraints(constraints["property_constraints"], constraints["service_temperature_K"])
        names = constraints["material_queries"] or catalog.aliases_mentioned_in(constraints.get("alias_extraction_text", ""))
        if not names:
            names = self._formula_like_terms(constraints.get("alias_extraction_text", ""))
        preferences = parse_preference_goals(constraints.get("preference_goals", []))
        if names or constraints["material_families"] or constraints["standards"] or parsed_constraints or preferences:
            search = catalog.search(
                names=names,
                families=constraints["material_families"],
                standards=constraints["standards"],
                constraints=parsed_constraints,
                preferences=preferences,
                top_k=constraints["top_k"],
            )
        else:
            search = {"name_resolution": [], "candidates": []}
        eligible = sum(item["eligible"] for item in search["candidates"])
        # Estimates are presentation-only annotations supplied by an upstream
        # engineering/LLM step.  Attaching them after ``catalog.search`` makes
        # it impossible for them to affect filtering or preference ranking.
        for candidate in search["candidates"]:
            # Catalogue D-level records are presentation-only, just like
            # request-scoped estimates.  Preserve both; catalog.search has
            # already completed, so neither can affect eligibility/ranking.
            candidate["engineering_estimates"] = [
                *(candidate.get("engineering_estimates") or []),
                *constraints["engineering_estimates"],
            ]
        constraint_status_counts: dict[str, dict[str, int]] = {}
        for candidate in search["candidates"]:
            for evidence in candidate.get("evidence", []):
                property_name = str(evidence.get("property") or "")
                status = str(evidence.get("status") or "unknown")
                if property_name:
                    constraint_status_counts.setdefault(property_name, {}).setdefault(status, 0)
                    constraint_status_counts[property_name][status] += 1
        has_upstream_evidence = bool(constraints["upstream_evidence"])
        strategy = constraints["screening_strategy"]
        # Free-text aliases are resolved only once the structured catalogue is
        # open.  Promote an unambiguous recovered name to index mode here so a
        # customer writing “铝合金6061” gets the same index experience as an
        # explicit API caller providing ``material_queries``.
        if names and not parsed_constraints and not preferences and strategy["mode"] == "criteria_collection":
            strategy = catalogue_screening_strategy(
                material_queries=names,
                material_families=constraints["material_families"],
                standards=constraints["standards"],
                property_constraints=[],
                service_temperature_K=constraints["service_temperature_K"],
                selection_context=constraints["selection_context"],
                preference_goals=[],
            )
        searchable_criteria = bool(names or constraints["material_families"] or constraints["standards"] or parsed_constraints or preferences)
        if (strategy["mode"] == "criteria_collection" or not searchable_criteria) and not has_upstream_evidence:
            message = (
                "当前已提供的条件不足以执行目录证据比较；请补充材料体系/牌号、"
                "可比较的性能阈值或其他可检索条件。服务不会假设高温、高强或任何候选材料体系。"
            )
            outcome = "needs_screening_criteria"
        elif not search["candidates"]:
            message = "目录中暂未找到与本轮指定材料、牌号或标准相匹配的已入库记录；未展示或推断替代候选材料。"
            outcome = "upstream_evidence_only" if has_upstream_evidence else "needs_literature_screening"
        elif preferences and not parsed_constraints:
            total_comparable = int(search.get("comparable_candidate_count", len(search["candidates"])))
            total_complete = int(search.get("complete_preference_candidate_count", total_comparable))
            returned = len(search["candidates"])
            truncation = f"；当前展示前 {returned} 种" if search.get("truncated") else "；已完整展示"
            message = f"已按本轮 {len(preferences)} 项方向偏好识别 {total_comparable} 种至少覆盖一项性质的目录证据，其中 {total_complete} 种同时覆盖全部关注性质{truncation}；偏好仅用于排序，不构成性能通过或工程推荐。"
            outcome = "catalogue_evidence_landscape"
        elif parsed_constraints and eligible == 0:
            message = (
                f"已按本轮 {len(parsed_constraints)} 项明确约束评估 {len(search['candidates'])} 种目录候选，"
                "但没有候选同时满足全部可比较条件；已保留每项约束的通过、不通过或缺失证据，未推断放宽条件或替代材料。"
            )
            outcome = "catalogue_no_eligible_candidates"
        elif strategy["mode"] == "catalogue_index":
            message = f"已完成 {len(search['candidates'])} 种已指定材料/牌号的目录核验；本轮为材料索引，不执行候选筛选或排序。"
            # Keep the existing completed-match outcome for API compatibility;
            # the inspectable strategy is the discriminator for index mode.
            outcome = "catalog_matched"
        elif not parsed_constraints:
            message = f"已在结构化材料目录中匹配到 {len(search['candidates'])} 种候选；本轮未提供量化性质阈值。"
            outcome = "catalog_matched"
        else:
            message = f"已在结构化材料目录中评估 {len(search['candidates'])} 种候选，其中 {eligible} 种满足当前可比较的性质条件。"
            outcome = "catalog_matched"
        return {
            "taskid": constraints["taskid"],
            "status": "completed",
            "service": self.service_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workflow_kind": CATALOG_SCREENING_WORKFLOW,
            "constraints": {
                **constraints,
                # Persist the effective resolved anchor as well as the raw
                # requirement, so the report's first section does not say
                # “未指定” after a successful free-text catalogue lookup.
                "material_queries": names,
                "screening_strategy": strategy,
            },
            "results": search["candidates"],
            "preference_data_gaps": search.get("preference_data_gaps", []),
            "name_resolution": search["name_resolution"],
            "screening": {
                "request": {**constraints["screening_request"], "material_queries": names},
                "strategy": strategy,
                "summary": {
                    "candidates_evaluated": int(search.get("candidate_count_before_limit", len(search["candidates"]))),
                    "candidates_returned": len(search["candidates"]),
                    "candidates_truncated": bool(search.get("truncated")),
                    "comparable_candidate_count": int(search.get("comparable_candidate_count", len(search["candidates"]))),
                    "complete_preference_candidate_count": int(search.get("complete_preference_candidate_count", len(search["candidates"]))),
                    "preference_funnel_counts": search.get("preference_funnel_counts", []),
                    "constraint_funnel_counts": search.get("constraint_funnel_counts", []),
                    "eligible_candidates": eligible,
                    "matched_name_count": sum(item["status"] == "matched" for item in search["name_resolution"]),
                    "constraint_status_counts": constraint_status_counts,
                },
                "evidence_policy": "Only structured catalogue evidence is evaluated; missing data and incompatible conditions do not pass.",
                "next_action": "await_user_criteria" if outcome == "needs_screening_criteria" else "return_catalogue_evidence",
            },
            "data_status": {
                "catalog_ready": True,
                "raw_data_root_available": self.raw_data_root.exists(),
                "outcome": outcome,
                "message": message,
                "scope": "仅查询已清洗的结构化目录数据；Markdown 解析数据将按来源和表格逐步入库。",
            },
        }

    def presentation_sections(self, result: dict[str, Any]) -> tuple[str, str, str]:
        """Return content for the two existing WS sections and conclusion.

        The caller keeps the fixed frontend marker and event protocol; only
        factual content varies by internal workflow.
        """
        if result.get("workflow_kind") == FLUID_SCREENING_WORKFLOW:
            return self._fluid_workflow().sections(result)
        return analysis_markdown(result), comparison_markdown(result), conclusion_markdown(result)

    def summary(self, result: dict[str, Any]) -> str:
        # 阶段 3：仅由已保存的结果生成可读结论，不补充目录外事实。
        return "\n\n".join(self.presentation_sections(result))

    def save(self, manifest: dict[str, Any]) -> None:
        # 阶段 4：将可追溯结果落盘，供任务查询接口和展示层复用。
        path = self.results_root / self.task_storage_key(manifest["taskid"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_task(self, taskid: str) -> dict[str, Any] | None:
        path = self.results_root / self.task_storage_key(taskid) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def asset_path(self, taskid: str, asset_name: str) -> Path:
        if Path(asset_name).name != asset_name:
            raise ValueError("invalid asset path")
        return self.results_root / self.task_storage_key(taskid) / "presentation" / asset_name

    def render_assets(self, result: dict[str, Any]) -> list[dict[str, str]]:
        # 阶段 5：生成目录事实的对比图；WebSocket/MinIO 发布仍属于 main.py 的适配层。
        presentation_dir = self.results_root / self.task_storage_key(result["taskid"]) / "presentation"
        if result.get("workflow_kind") == FLUID_SCREENING_WORKFLOW:
            return self._fluid_workflow().render_assets(result, presentation_dir)
        has_property_constraint = bool(result.get("constraints", {}).get("property_constraints"))
        has_preference = bool(result.get("constraints", {}).get("preference_goals"))
        funnel_assets = (
            render_fluid_style_assets(
                {"funnel": [{"step": label, "count": count} for label, count in screening_funnel_rows(result)], "candidates": [], "plot_points": []},
                presentation_dir,
            ) if has_property_constraint or has_preference else []
        )
        chart = render_property_comparison(result, presentation_dir)
        if not chart and not funnel_assets:
            return []
        assets = []
        if funnel_assets:
            funnel = funnel_assets[0]
            assets.append({
                "name": "evidence_funnel",
                "title": "材料筛选漏斗" if has_property_constraint else "材料证据覆盖漏斗",
                "description": (
                    "展示每一项明确约束后的保留候选数；归零表示该条件下没有可通过的已入库证据。"
                    if has_property_constraint else
                    "展示方向性排序目标在当前目录候选中的可比较证据覆盖；它不是性能通过漏斗。"
                ),
                "local_path": funnel["local_path"], "url": "", "type": "MaterialsPNG",
            })
        if not chart:
            return assets
        is_single_candidate = len(result.get("results") or []) == 1
        default_property = default_comparison_property(result)
        has_default_numeric_comparison = not has_property_constraint and default_property is not None and "melting_temperature" not in chart.name
        if has_property_constraint:
            title = "候选材料筛选分布"
            description = "绿色表示该性质通过，橙色表示不通过；红色虚线为本轮筛选边界，仅比较有相同单位和可比温度证据的候选。"
        elif has_preference:
            preferred_property = (result.get("constraints", {}).get("preference_goals") or [{}])[0].get("property")
            title = f"候选材料{property_label(preferred_property)}方向排序"
            description = "蓝色柱状图展示本轮方向性目标的已入库证据；不代表候选已经通过工程筛选。"
        elif has_default_numeric_comparison:
            title = f"候选材料{property_label(default_property)}对比"
            description = "柱状图比较本轮候选共有的已入库数值性质。"
        else:
            title = "候选合金熔化温度区间" if is_single_candidate else "候选合金熔化温度区间对比"
            description = "展示该候选已入库的熔化温度上下限。" if is_single_candidate else "展示候选已入库的熔化温度上下限，便于横向比较。"
        assets.append({
            "name": "property_comparison" if has_property_constraint else ("preference_property_comparison" if has_preference else (f"default_{default_property}_comparison" if has_default_numeric_comparison else ("melting_temperature_interval" if is_single_candidate else "melting_temperature_comparison"))),
            "title": title,
            "description": description,
            "local_path": str(chart),
            "url": "",
            "type": "MaterialsPNG",
        })
        return assets
