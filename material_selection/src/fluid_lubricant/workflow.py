"""1105-internal workflow for conductive-lubricant evidence screening.

This module deliberately has no transport code.  ``MaterialMature`` dispatches
to it after receiving the existing mature-material payload, and ``main.py``
continues to emit the unchanged frontend event contract.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.fluid_lubricant.presentation import render_assets as render_screen_assets
from src.fluid_lubricant.query import run_query
from src.fluid_lubricant.shortlist import build_report_shortlist
from src.screening_language import BOUND_OPERATOR as _BOUND_OPERATOR, RANGE_JOINER as _RANGE_JOINER, bound_constraints, range_constraints


WORKFLOW_KIND = "conductive_lubricant_initial_screen"


def _decision_summary(result: dict[str, Any]) -> str:
    """Use the same plain-language decision frame as the catalogue workflow."""
    screening = result.get("screening") or {}
    summary = screening.get("summary") or {}
    matched = int(summary.get("matched_before_limit") or 0)
    outcome = result.get("data_status", {}).get("outcome") or ""
    if not screening:
        finding = "尚未形成可执行的电学、黏度和温度条件，因此没有进行证据配对。"
        use = "帮助下一轮把需求补成可比较的初筛条件。"
    elif outcome == "fluid_evidence_landscape":
        finding = f"整理出 {matched} 条可比较证据，并按方向性目标展示，未把它们当作性能通过结论。"
        use = "建立后续数值窗口和补测计划的证据地图。"
    elif matched:
        finding = f"找到 {matched} 条同时落入当前电学与黏度窗口的证据配对；其配方完整性和应用适用性仍按分层保留。"
        use = "选择导电功能基准体系，并安排润滑性能与耐温验证。"
    else:
        finding = "在当前数值窗口内没有找到完整匹配的证据配对。"
        use = "定位需要补充的条件或证据，而不是直接替换为其他配方。"
    return "\n".join([
        "### 适用范围与后续验证",
        "| 项目 | 当前情况 |",
        "|---|---|",
        "| 针对的场景 | 同时具有导电与润滑意图的液体介质初筛。 |",
        f"| 当前证据 | {finding} |",
        f"| 可支持的下一步 | {use} |",
        "| 仍需验证 | 抗磨、承载、老化、兼容性和均相稳定性，再决定工程验证路线。 |",
    ])
# The upstream planner sometimes calls the target a "导电润滑材料库" instead
# of repeating "导电润滑油".  It is still the same fluid workflow when an
# electrical property is present, so keep the material-library wording in the
# application-intent recognizer too.
# Upstream execution summaries often condense the original wording to
# ``导电润滑需求``.  The electrical-intent check is still required alongside
# this pattern, so accepting the bare application term does not turn a generic
# material request into a fluid-screening request.
_LUBRICATION = re.compile(r"润滑(?:油|剂|介质|液|脂|材料(?:库)?|需求)?|lubrican(?:t|ts)|lubricating", re.IGNORECASE)
_ELECTRICAL = re.compile(r"导电|电导率|电阻率|低电阻|抗静电|conductiv|resistiv", re.IGNORECASE)
# Do not treat a planner's historical phrase such as “默认初筛口径” as a
# current user instruction.  Defaults are permitted only when this turn gives
# an imperative instruction to use them.
_DEFAULT_INSTRUCTION = re.compile(r"(?:按|采用|使用|其余按|剩下按)默认|自己(?:来|默认)|你(?:来|自己|帮我).*默认", re.IGNORECASE)
_TEMPERATURE_C = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d+)?)\s*(?:°\s*C|℃|摄氏度|度)(?!\s*K)", re.IGNORECASE)

DEFAULT_INITIAL_SCREEN_REQUEST = {
    "conditions": {"temperature_k": {"min": 293.15, "max": 303.15}},
    "property_constraints": [
        {"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"},
        {"name": "dynamic_viscosity", "operator": ">=", "value": 130, "unit": "mPa*s"},
        {"name": "dynamic_viscosity", "operator": "<=", "value": 150, "unit": "mPa*s"},
    ],
    "evidence_policy": {"composition": "include_flagged", "manual_review": "include_flagged"},
    "limit": 50,
}

_RANGE_PREFIX = r"(?:在|为|约|范围(?:为)?|介于)?"
_VISCOSITY_UNIT = r"(?:mPa\s*[·・*.]?\s*s|毫帕[·・]?秒)"
_RESISTIVITY_UNIT = r"(?:Ω|ohm)\s*[·・*.]?\s*m"
_CONDUCTIVITY_UNIT = r"S\s*/\s*m"

_VISCOSITY_RANGE = re.compile(
    # Besides direct wording such as ``旋转黏度 130-150 mPa·s``, the
    # gateway's execution summary commonly says ``黏度范围（130-150
    # mPa·s）``.  The latter is still an explicit numeric instruction, not a
    # request to reinstate a default profile with unrelated viscosity limits.
    rf"(?:旋转|动力|动态)?[黏粘]度\s*{_RANGE_PREFIX}\s*[（(\[]?\s*(\d+(?:\.\d+)?)\s*(?:{_VISCOSITY_UNIT}\s*)?{_RANGE_JOINER}\s*(\d+(?:\.\d+)?)\s*{_VISCOSITY_UNIT}(?:\s*之间)?",
    re.IGNORECASE,
)
_VISCOSITY_BOUND = re.compile(
    rf"(?:(?:旋转|动力|动态)?[黏粘]度\s*)?(?P<operator>{_BOUND_OPERATOR})\s*(?P<value>\d+(?:\.\d+)?)\s*{_VISCOSITY_UNIT}",
    re.IGNORECASE,
)
_RESISTIVITY_BOUND = re.compile(
    # The second half of a two-sided constraint often omits the repeated
    # property name: "电阻率小于10 Ω·m，但是大于1 Ω·m".  Ω·m is specific
    # enough to recognise that continuation without confusing it with another
    # numeric property.
    rf"(?:电阻率\s*)?(?P<operator>{_BOUND_OPERATOR})\s*(?P<value>\d+(?:\.\d+)?)\s*{_RESISTIVITY_UNIT}",
    re.IGNORECASE,
)
_RESISTIVITY_RANGE = re.compile(
    rf"电阻率\s*{_RANGE_PREFIX}\s*[（(\[]?\s*(\d+(?:\.\d+)?)\s*(?:{_RESISTIVITY_UNIT}\s*)?{_RANGE_JOINER}\s*(\d+(?:\.\d+)?)\s*{_RESISTIVITY_UNIT}(?:\s*之间)?",
    re.IGNORECASE,
)
_CONDUCTIVITY_BOUND = re.compile(
    rf"(?:电导率\s*)?(?P<operator>{_BOUND_OPERATOR})\s*(?P<value>\d+(?:\.\d+)?)\s*{_CONDUCTIVITY_UNIT}",
    re.IGNORECASE,
)
_CONDUCTIVITY_RANGE = re.compile(
    rf"电导率\s*{_RANGE_PREFIX}\s*[（(\[]?\s*(\d+(?:\.\d+)?)\s*(?:{_CONDUCTIVITY_UNIT}\s*)?{_RANGE_JOINER}\s*(\d+(?:\.\d+)?)\s*{_CONDUCTIVITY_UNIT}(?:\s*之间)?",
    re.IGNORECASE,
)


def _directional_preferences_from_text(text: str) -> list[dict[str, str]]:
    """Extract ranking directions without inventing a numeric threshold."""
    value = text or ""
    patterns = {
        "conductivity": (r"电导率.{0,12}越(?:高|大)越好", "maximize"),
        "resistivity": (r"电阻率.{0,12}越(?:低|小)越好", "minimize"),
        "dynamic_viscosity": (r"(?:动态|动力|旋转)?[黏粘]度.{0,12}越(?:低|小)越好", "minimize"),
    }
    return [
        {"name": name, "direction": direction}
        for name, (pattern, direction) in patterns.items()
        if re.search(pattern, value, re.IGNORECASE)
    ]


def _human_funnel_step(step: str) -> str:
    """Turn internal query steps into stable, user-facing Chinese labels."""
    temperature = re.fullmatch(r"Temperature\s*(>=|<=)\s*([0-9.]+)\s*K", step)
    if temperature:
        operator, value = temperature.groups()
        return f"测试温度{'不低于' if operator == '>=' else '不高于'} {float(value) - 273.15:g} °C"
    property_step = re.fullmatch(r"(conductivity|resistivity|dynamic_viscosity)\s*(>=|<=)\s*([0-9.]+)\s*(\S+)", step)
    if property_step:
        name, operator, value, unit = property_step.groups()
        labels = {"conductivity": "电导率", "resistivity": "电阻率", "dynamic_viscosity": "动态黏度"}
        units = {"ohm*m": "Ω·m", "mPa*s": "mPa·s"}
        return f"{labels[name]}{'不低于' if operator == '>=' else '不高于'} {float(value):g} {units.get(unit, unit)}"
    if step == "Exact transport evidence pairs":
        return "获得可直接比较的电学与黏度数据"
    return step


def _constraints_from_bounds(matches: list[re.Match[str]], *, name: str, unit: str) -> list[dict[str, Any]]:
    """Keep every stated lower/upper bound for one measurable property."""
    return bound_constraints(matches, key="name", name=name, unit=unit)


def _constraints_from_range(match: re.Match[str], *, name: str, unit: str) -> list[dict[str, Any]]:
    return range_constraints(key="name", name=name, unit=unit, lower=float(match.group(1)), upper=float(match.group(2)))


def _request_from_text(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build only the simple, explicit constraints stated by the user.

    Explicit user conditions always override the default profile.  In
    particular, 130–150 mPa·s is a strict interval, not a hint that can be
    silently changed to a one-sided viscosity limit.
    """
    request = json.loads(json.dumps(DEFAULT_INITIAL_SCREEN_REQUEST))
    interpretation: dict[str, Any] = {}
    match = _VISCOSITY_RANGE.search(text or "")
    if match:
        lower, upper = sorted((float(match.group(1)), float(match.group(2))))
        request["property_constraints"] = [
            *[item for item in request["property_constraints"] if item["name"] != "dynamic_viscosity"],
            {"name": "dynamic_viscosity", "operator": ">=", "value": lower, "unit": "mPa*s"},
            {"name": "dynamic_viscosity", "operator": "<=", "value": upper, "unit": "mPa*s"},
        ]
        interpretation.update({
            "viscosity_min_mpa_s": lower,
            "viscosity_max_mpa_s": upper,
            "viscosity_user_expression": f"{lower:g}–{upper:g} mPa·s",
        })
    else:
        viscosity_bounds = list(_VISCOSITY_BOUND.finditer(text or ""))
        if viscosity_bounds:
            viscosity_constraints = _constraints_from_bounds(viscosity_bounds, name="dynamic_viscosity", unit="mPa*s")
            request["property_constraints"] = [
                *[item for item in request["property_constraints"] if item["name"] != "dynamic_viscosity"],
                *viscosity_constraints,
            ]
            interpretation["viscosity_user_expression"] = "；".join(
                f"动态黏度 {'≥' if item['operator'] == '>=' else '≤'} {item['value']:g} mPa·s"
                for item in viscosity_constraints
            )
    conductivity_range = _CONDUCTIVITY_RANGE.search(text or "")
    conductivity_bounds = list(_CONDUCTIVITY_BOUND.finditer(text or ""))
    if conductivity_range or conductivity_bounds:
        conductivity_constraints = (
            _constraints_from_range(conductivity_range, name="conductivity", unit="S/m")
            if conductivity_range else _constraints_from_bounds(conductivity_bounds, name="conductivity", unit="S/m")
        )
        request["property_constraints"] = [
            *conductivity_constraints,
            *[item for item in request["property_constraints"] if item["name"] != "conductivity"],
        ]
        interpretation["electrical_user_expression"] = "；".join(
            f"电导率 {'≥' if item['operator'] == '>=' else '≤'} {item['value']:g} S/m"
            for item in conductivity_constraints
        )
    resistivity_range = _RESISTIVITY_RANGE.search(text or "")
    resistivity_bounds = list(_RESISTIVITY_BOUND.finditer(text or ""))
    if resistivity_range or resistivity_bounds:
        resistivity_constraints = (
            _constraints_from_range(resistivity_range, name="resistivity", unit="ohm*m")
            if resistivity_range else _constraints_from_bounds(resistivity_bounds, name="resistivity", unit="ohm*m")
        )
        request["property_constraints"] = [
            *resistivity_constraints,
            *[item for item in request["property_constraints"] if item["name"] not in {"conductivity", "resistivity"}],
        ]
        interpretation["electrical_user_expression"] = "；".join(
            f"电阻率 {'≥' if item['operator'] == '>=' else '≤'} {item['value']:g} Ω·m"
            for item in resistivity_constraints
        )
    return request, interpretation


def is_conductive_lubricant_request(text: str) -> bool:
    """Route only when both application and electrical intent are explicit."""
    return bool(_LUBRICATION.search(text or "") and _ELECTRICAL.search(text or ""))


def user_allows_default(text: str) -> bool:
    return bool(_DEFAULT_INSTRUCTION.search(text or ""))


def has_explicit_screening_conditions(text: str) -> bool:
    """Whether this turn supplies numeric fluid-screening constraints.

    This allows a follow-up carrying only numbers (without repeating
    “导电润滑油”) to execute, instead of incorrectly falling back to a generic
    default prompt because a client omitted prior conversational state.
    """
    value = text or ""
    return bool(
        _VISCOSITY_RANGE.search(value)
        or _VISCOSITY_BOUND.search(value)
        or _RESISTIVITY_BOUND.search(value)
        or _RESISTIVITY_RANGE.search(value)
        or _CONDUCTIVITY_BOUND.search(value)
        or _CONDUCTIVITY_RANGE.search(value)
    )


def _is_default_initial_request(request: dict[str, Any]) -> bool:
    """Whether an upstream structured request is a current or legacy fallback."""
    constraints = request.get("property_constraints") or []
    expected_profiles = (
        DEFAULT_INITIAL_SCREEN_REQUEST["property_constraints"],
        # Some gateways may hold a cached copy of the former <=130 default.
        # Treat it as fallback too; it is never evidence of a user choice.
        [
            {"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"},
            {"name": "dynamic_viscosity", "operator": "<=", "value": 130, "unit": "mPa*s"},
        ],
    )
    return any(
        isinstance(constraints, list)
        and len(constraints) == len(expected)
        and all(
            isinstance(item, dict)
            and item.get("name") == default["name"]
            and item.get("operator") == default["operator"]
            and float(item.get("value")) == float(default["value"])
            for item, default in zip(constraints, expected)
        )
        for expected in expected_profiles
    )


def operating_temperature_c(text: str) -> float | None:
    """Capture an application temperature without confusing it with test data."""
    match = _TEMPERATURE_C.search(text or "")
    return float(match.group(1)) if match else None


class FluidLubricantWorkflow:
    """Produce traceable screening manifests without changing the WS protocol."""

    def __init__(self, *, database: Path, results_root: Path, service_name: str) -> None:
        self.database = database
        self.results_root = results_root
        self.service_name = service_name

    @staticmethod
    def contract(
        payload: dict[str, Any], *, taskid: str, raw_requirement: str, scope: dict[str, Any],
        apply_default_profile: bool = False,
    ) -> dict[str, Any]:
        # The language model/upstream may fill this constrained object, but no
        # free-form SQL or inferred threshold is accepted here.
        request = scope.get("fluid_initial_screen", scope.get("fluid_screening"))
        if request is not None and not isinstance(request, dict):
            raise ValueError("mature_material.fluid_initial_screen must be an object")
        request = dict(request or {})
        default_profile_applied = False
        inferred_interpretation: dict[str, Any] = {}
        text_has_viscosity = bool(_VISCOSITY_RANGE.search(raw_requirement) or _VISCOSITY_BOUND.search(raw_requirement))
        text_has_electrical = bool(
            _RESISTIVITY_BOUND.search(raw_requirement)
            or _RESISTIVITY_RANGE.search(raw_requirement)
            or _CONDUCTIVITY_BOUND.search(raw_requirement)
            or _CONDUCTIVITY_RANGE.search(raw_requirement)
        )
        text_preferences = _directional_preferences_from_text(raw_requirement)
        # Do not silently execute an upstream-provided default profile when
        # the raw request merely claims that the user supplied "precise"
        # conditions but contains none of their values.  This is the exact
        # failure mode that turned a missing 130–150 range into a stale,
        # one-sided default.
        if request and not (text_has_viscosity or text_has_electrical or user_allows_default(raw_requirement)) and _is_default_initial_request(request):
            request = {}
        # The upstream extractor is advisory.  A numeric condition written by
        # the user in this turn is authoritative, even when upstream supplied
        # an incompatible fluid_initial_screen object.
        if request and (text_has_viscosity or text_has_electrical):
            text_request, inferred_interpretation = _request_from_text(raw_requirement)
            text_constraints = text_request["property_constraints"]
            retained = list(request.get("property_constraints") or [])
            if text_has_viscosity:
                retained = [item for item in retained if item.get("name") != "dynamic_viscosity"]
                retained += [item for item in text_constraints if item["name"] == "dynamic_viscosity"]
            if text_has_electrical:
                retained = [item for item in retained if item.get("name") not in {"conductivity", "resistivity"}]
                retained += [item for item in text_constraints if item["name"] in {"conductivity", "resistivity"}]
            request["property_constraints"] = retained
        if text_preferences:
            request.setdefault("property_constraints", [])
            request["preference_goals"] = text_preferences
        if not request and text_preferences:
            request = {
                "property_constraints": [],
                "preference_goals": text_preferences,
                "evidence_policy": {"composition": "include_flagged", "manual_review": "include_flagged"},
                "limit": 50,
            }
        if not request and apply_default_profile:
            request, inferred_interpretation = _request_from_text(raw_requirement)
            default_profile_applied = True
        if request:
            request["task_id"] = taskid
        return {
            "taskid": taskid,
            "workflow_kind": WORKFLOW_KIND,
            "raw_requirement": raw_requirement,
            "screening_request": request,
            "database": str(scope.get("fluid_database") or ""),
            "default_profile_applied": default_profile_applied,
            "screening_interpretation": inferred_interpretation,
            "application_operating_temperature_c": operating_temperature_c(raw_requirement),
        }

    async def run(self, constraints: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        request = constraints["screening_request"]
        if not request:
            return {
                "taskid": constraints["taskid"],
                "status": "completed",
                "service": self.service_name,
                "created_at": created_at,
                "workflow_kind": WORKFLOW_KIND,
                "constraints": constraints,
                "results": [],
                "screening": None,
                "data_status": {
                    "outcome": "needs_screening_criteria",
                    "message": "已识别到导电润滑需求，但本次转交的任务描述没有包含电阻率/电导率、黏度或温度的具体数值，因此尚未执行筛选。为避免用默认值替代您的要求，请重新传入完整条件。",
                    "scope": "请提供具体的电阻率或电导率范围、黏度范围和目标温度；服务仅在收到可核验数值后筛选已入库的液体性质证据。",
                },
            }
        database = self.database
        requested_database = str(constraints.get("database") or "").strip()
        if requested_database:
            # The online workflow never lets a request choose an arbitrary
            # filesystem path.  Dataset changes are deployed through settings.
            raise ValueError("fluid_database is deployment configuration and cannot be supplied by a request")
        screening = run_query(database, request)
        shortlist = build_report_shortlist(database, screening)
        preference_only = bool(request.get("preference_goals")) and not request.get("property_constraints")
        no_numeric_match = bool(request.get("property_constraints")) and screening["summary"]["matched_before_limit"] == 0
        return {
            "taskid": constraints["taskid"],
            "status": "completed",
            "service": self.service_name,
            "created_at": created_at,
            "workflow_kind": WORKFLOW_KIND,
            "constraints": constraints,
            # Public results are the actual numeric matches for this query.
            # Report-curated A/B directions are supplementary context and must
            # never displace or masquerade as the query result.
            "results": shortlist["matched_evidence"],
            "screening": screening,
            "shortlist": shortlist,
            "data_status": {
                "outcome": "fluid_evidence_landscape" if preference_only else ("fluid_no_matching_evidence" if no_numeric_match else "fluid_initial_screen_completed"),
                "message": (
                    "已按本轮方向偏好排序返回导电液体证据地图；没有数值硬阈值，因此排序不等同于性能通过、工程推荐或长期润滑验证。"
                    if preference_only else (
                        "已完整执行本轮温度、电学、黏度及证据质量约束，但没有证据配对同时通过；"
                        "筛选漏斗和每一步归零位置已保留，未擅自放宽窗口或推荐替代配方。"
                        if no_numeric_match else "已按本轮明确条件返回导电液体候选的数值初筛证据；这不等同于导电润滑油推荐或长期润滑、相容性、机理验证通过。"
                    )
                ),
                "scope": "本轮只比较实验电学与黏度证据及配方质量标记；未验证油膜、承载/抗磨、氧化寿命、腐蚀、密封兼容或135 °C长期均相稳定性。",
            },
        }

    @staticmethod
    def sections(result: dict[str, Any]) -> tuple[str, str, str]:
        status = result["data_status"]
        if not result.get("screening"):
            return (
                "## 1. 需求与已知工况\n\n" + status["message"] + "\n\n## 2. 本轮筛选/比较口径\n\n当前还没有可执行的温度、电学和黏度比较条件。",
                "## 3. 证据覆盖与候选核验\n\n本轮尚未进行证据配对，因此没有候选核验结果。",
                "## 4. 结论\n\n请先补齐可比较条件，才能形成导电液体的证据结论。\n\n## 5. 材料性质汇总\n\n当前没有可展示的匹配体系。\n\n" + _decision_summary(result),
            )
        screening = result["screening"]
        request = screening["request"]
        rows = ["## 1. 需求与已知工况", "", "你当前提出的是同时具有导电与润滑意图的液体介质需求；本轮先核对电学与黏度证据，不将其直接称为可用的导电润滑油。", "", "## 2. 本轮筛选/比较口径", "", "### 已确认的筛选条件", ""]
        if result["constraints"].get("default_profile_applied"):
            rows.append("- 本轮按已提供的数值和默认初筛口径执行；默认值不视为用户最终验收标准。")
        interpretation = result["constraints"].get("screening_interpretation") or {}
        if interpretation.get("viscosity_min_mpa_s") is not None:
            rows.append(f"- 黏度口径：用户明确给出 {interpretation['viscosity_user_expression']}；按严格区间 {interpretation['viscosity_min_mpa_s']:g} ≤ η ≤ {interpretation['viscosity_max_mpa_s']:g} mPa·s 筛选，已覆盖默认值。")
        elif interpretation.get("viscosity_user_expression"):
            rows.append(f"- 黏度口径：用户明确给出 {interpretation['viscosity_user_expression']}；已覆盖默认值。")
        if interpretation.get("electrical_user_expression"):
            rows.append(f"- 电学口径：用户明确给出 {interpretation['electrical_user_expression']}；已覆盖默认电导率条件。")
        operating = result["constraints"].get("application_operating_temperature_c")
        if operating is not None:
            rows.append(f"- 记录的应用耐温要求：{operating:g} °C；当前数据仅用于室温传输性质初筛，不能替代该温度下的长期稳定性验证。")
        temperature = request.get("temperature_min_k"), request.get("temperature_max_k")
        if temperature != (None, None):
            rows.append(f"- 温度范围：{temperature[0] if temperature[0] is not None else '-'}–{temperature[1] if temperature[1] is not None else '-'} K")
        property_names = {
            "conductivity": "电导率",
            "resistivity": "电阻率",
            "dynamic_viscosity": "动态黏度",
        }
        unit_labels = {"ohm*m": "Ω·m", "mPa*s": "mPa·s"}
        for item in request["property_constraints"]:
            rows.append(f"- {property_names.get(item['name'], item['name'])} {item['operator']} {item['value']:g} {unit_labels.get(item['unit'], item['unit'])}")
        for goal in request.get("preference_goals", []):
            direction = "越高越好" if goal["direction"] == "maximize" else "越低越好"
            rows.append(f"- 排序偏好：{property_names.get(goal['name'], goal['name'])}{direction}（不构成数值硬阈值）")
        rows += ["", "### 筛选漏斗", "", "| 条件步骤 | 证据配对数 |", "|---|---:|"]
        rows += [f"| {_human_funnel_step(item['step'])} | {item['count']:,} |" for item in screening["funnel"]]
        rows += ["", "### 证据状态", ""]
        for key, value in screening["summary"]["status_counts"].items():
            if key == "flagged_for_review":
                rows.append(f"- 数值通过、但配方或来源仍需回查：{value:,} 条证据配对。它们不是 {value:,} 个可直接复配的候选。")
            else:
                rows.append(f"- {key}：{value:,}")
        shortlist = result.get("shortlist") or {}
        matched_all = shortlist.get("matched_evidence", [])
        # Keep the report readable by default.  The full evidence set remains
        # in the manifest/result for download and audit.
        matched = matched_all[:5]
        b_candidates = shortlist.get("b_candidates", [])
        candidate_heading = "## 3. 证据覆盖与候选核验"
        candidate_intro = (
            "本轮没有数值硬阈值；以下仅按明确方向偏好排序，不能视为性能通过、产品推荐或润滑适用性结论。"
            if result["data_status"]["outcome"] == "fluid_evidence_landscape" else
            "这些是数值匹配证据，不是可直接用于风机轴承的产品推荐。工程分类仅根据已记录组分作保守分流，不能替代润滑性能验证。"
        )
        candidate_rows = [
            candidate_heading, "", candidate_intro, "",
            "### 证据完整性分层", "",
            "- **A 类（证据完整）**：所有用户硬条件均有可比实验值，配方组成完整且无待人工复核标记。",
            "- **B 类（数值匹配、待回查）**：所有硬条件在数值上匹配，但配方组成、来源字段或应用适用性仍需核验。",
            "- **C 类（补测线索）**：与应用方向相关，但缺少至少一项硬条件所需的可比证据；只用于安排补测，不作为通过结果。",
            "",
            f"本轮共匹配 **{screening['summary']['matched_before_limit']:,} 条**证据配对；默认展示前 **{len(matched):,} 条**，完整结果保留在任务结果中可供下载和审计。去重键为“来源、组分、比例、温度、压力”。",
            "", "| 证据 | 配方体系（中文概述） | 工程分类 | 配比字段 | 条件与性质 | 来源 |", "|---|---|---|---|---|---|",
        ]
        for item in matched:
            composition = item["composition"]
            fractions = ", ".join(
                f"组分{index}={composition[key]:.4g}" for index, key in enumerate(("component_1_fraction", "component_2_fraction", "component_3_fraction"), start=1)
                if isinstance(composition.get(key), (int, float))
            ) or "未完整报告"
            p = item["properties"]
            detail = f"{item['conditions']['temperature_k']:g} K；ρ≤{p['resistivity_ohm_m']['max']:.3g} Ω·m；η={p['dynamic_viscosity_mpa_s']['max']:.3g} mPa·s"
            candidate_rows.append(f"| {item['evidence_id']} | {item['composition_display']} | {item['candidate_class']}：{item['engineering_note']} | {fractions} | {detail} | {item['evidence']['source_id']} |")
        if not matched:
            candidate_rows += ["| - | 本轮没有数值匹配的配方—工况证据 | - | - | - | - |"]
        if b_candidates:
            candidate_rows += [
                "", "### C 类：润滑基础油补测线索（不计入上述数值匹配）", "",
                "以下 4 项只具有 40 °C 运动黏度，缺少本轮所需电阻率/电导率和动态黏度；因此不在散点图中，也不能替代上述匹配结果。", "",
                "| 配方体系（中文说明） | 已报告比例 | 已有数据 | 建议补测 | 来源 |", "|---|---|---|---|---|",
            ]
            for item in b_candidates:
                p = item["properties"]
                candidate_rows.append(f"| {item['composition_display']} | {item['reported_fraction']} | {item['conditions']['temperature_k'] - 273.15:.0f} °C；ν={p['kinematic_viscosity_mm2_s']:.3g} mm²/s | 室温电阻率/电导率、动态/旋转黏度 | {item['evidence']['source_id']} |")
        gaps = (shortlist.get("data_gaps") or [])
        conclusion = ["## 4. 结论", "", status["message"]]
        best = shortlist.get("primary_research_reference")
        if best:
            bp = best["properties"]
            composition = best["composition"]
            source_records = "；".join(filter(None, (
                best["evidence"].get("conductivity_record_ids"),
                best["evidence"].get("viscosity_record_ids"),
            ))) or "详见任务结果中的原始记录"
            fractions = ", ".join(
                f"组分{index}={composition[key]:.4g}" for index, key in enumerate(("component_1_fraction", "component_2_fraction", "component_3_fraction"), start=1)
                if isinstance(composition.get(key), (int, float))
            ) or "原始记录未完整报告"
            conclusion += [
                "", "## 5. 材料性质汇总", "",
                f"在当前匹配记录中，建议优先将 **{best['evidence_id']}** 作为导电功能基准体系开展下一轮验证；它不是可直接用于风机轴承的产品推荐。",
                "", "| 项目 | 当前已知信息 |",
                "|---|---|",
                f"| 体系 | {best['composition_display']} |",
                "| 定位 | 导电功能液体基准体系，可用于指导后续油溶性离子添加剂设计。 |",
                f"| 组分 | {best.get('composition_original') or '详见原始来源'} |",
                f"| 已报告比例 | {fractions}（比例基准：{composition.get('composition_basis') or '需回查原始来源'}） |",
                f"| 测试条件与数值 | {best['conditions']['temperature_k'] - 273.15:.0f} °C；电阻率 {bp['resistivity_ohm_m']['max']:.3g} Ω·m；动态黏度 {bp['dynamic_viscosity_mpa_s']['max']:.3g} mPa·s。 |",
                f"| 数据状态 | {best['evidence_tier']}。 |",
                f"| 来源 | {best['evidence']['source_id']}；电学与黏度原始记录：{source_records}。 |",
                "| 为什么优先 | 在当前结果中，它不含水和明显低沸点共溶剂，属于以离子组分为主的体系，并同时落入本轮电阻率与黏度窗口。 |",
                "| 尚待验证 | 抗磨与承载、油膜形成、135 °C 老化后电阻率与黏度、金属/密封兼容性、腐蚀、均相稳定性及过滤性。 |",
                "| 下一步 | 将其离子传导机制转化为 POE、TMP 酯或 PAG 基础油中的油溶性功能组分，再进行台架验证。 |",
            ]
        if not best:
            conclusion += ["", "## 5. 材料性质汇总", "", "当前没有可作为优先评估依据的完整匹配体系。"]
        conclusion += ["", "### 服役温度与长期适用性边界", "", "| 核查项 | 当前状态 | 结论 |", "|---|---|---|"]
        conclusion += [f"| {item['item']} | {item['status']} | {item['meaning']} |" for item in gaps]
        conclusion += ["", status["scope"], "", _decision_summary(result)]
        return "\n".join(rows), "\n".join(candidate_rows), "\n".join(conclusion)

    def render_assets(self, result: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
        screening = result.get("screening")
        if not screening:
            return []
        assets = render_screen_assets(screening, output_dir, shortlist=result.get("shortlist"))
        return [{
            "name": item["name"], "title": item["description"], "description": item["description"],
            "local_path": item["local_path"], "url": "", "type": "MaterialsPNG",
        } for item in assets]
