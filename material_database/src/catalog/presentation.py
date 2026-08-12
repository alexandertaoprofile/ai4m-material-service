"""Human-readable streaming and chart assets for mature-material lookups."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from src.catalog.property_vocabulary import vocabulary_labels

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


_CJK_FONT = Path(os.getenv(
    "MATURE_MATERIAL_CJK_FONT_PATH",
    str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"),
))


PROPERTY_LABELS = {
    "density": "密度", "specific_heat": "比热容", "thermal_conductivity": "导热系数",
    "interfacial_bond_strength": "界面结合力",
    "thermal_diffusivity": "热扩散率", "yield_strength": "屈服强度", "hardness": "硬度",
    "melting_range_low": "熔点下限", "melting_range_high": "熔点上限",
    "youngs_modulus": "杨氏模量", "shear_modulus": "剪切模量", "poissons_ratio": "泊松比",
    "beta_transus": "β 相转变温度", "electrical_resistivity_IG": "电阻率（IG）",
    "electrical_resistivity_corrected": "修正电阻率", "liquidus_temperature": "液相线温度",
    "solidus_temperature": "固相线温度", "magnetic_permeability": "相对磁导率",
    "specific_enthalpy": "比焓",
    "tensile_strength": "拉伸强度", "heat_deflection_temperature": "热变形温度",
    "ultimate_tensile_strength": "抗拉强度", "hardness_vickers": "维氏硬度",
    "elongation": "延伸率", "plastic_elongation": "塑性延伸率", "grain_size": "晶粒尺寸",
    "density_calculated": "计算密度",
}
PROPERTY_LABELS.update(vocabulary_labels())

# When a query has no explicit threshold, prefer a property that every returned
# candidate actually records.  This makes the default chart a material
# comparison rather than a catalogue-completeness visualisation.
_DEFAULT_COMPARISON_PRIORITY = (
    "density",
    "heat_deflection_temperature",
    "tensile_strength",
    "youngs_modulus",
    "yield_strength",
    "thermal_conductivity",
    "hardness",
)


def property_label(name: str | None) -> str:
    return PROPERTY_LABELS.get(str(name or ""), str(name or "未命名性质"))


def format_value(value: Any, unit: str | None = None) -> str:
    if isinstance(value, (int, float)):
        text = f"{value:,.5g}"
    else:
        text = str(value or "-")
    return f"{text} {unit}".strip()


def _condition_tail(condition: Any) -> str:
    """Keep the property-specific test clause without repeating product state."""
    parts = [item.strip() for item in str(condition or "").split(";") if item.strip()]
    return parts[-1] if parts else ""


def _recorded_property_text(evidence: list[dict[str, Any]]) -> str:
    """Format traceable point data into customer-readable values and ranges."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        if item.get("coverage") != "temperature_curve" and isinstance(item.get("value"), (int, float)):
            grouped.setdefault(str(item.get("property") or ""), []).append(item)
    entries: list[str] = []
    for property_name, points in grouped.items():
        units = {str(item.get("unit") or "") for item in points}
        values = [float(item["value"]) for item in points]
        unit = next(iter(units)) if len(units) == 1 else ""
        if len(points) == 1:
            point = points[0]
            condition = _condition_tail(point.get("condition"))
            suffix = f"（{condition}）" if condition else ""
            entries.append(f"{property_label(property_name)}：{format_value(point['value'], point.get('unit'))}{suffix}")
            continue
        range_text = format_value(min(values), unit) if min(values) == max(values) else f"{format_value(min(values), unit)}–{format_value(max(values), unit)}"
        details = []
        for point in points:
            condition = _condition_tail(point.get("condition"))
            detail = format_value(point["value"], point.get("unit"))
            details.append(f"{condition}：{detail}" if condition else detail)
        entries.append(f"{property_label(property_name)}：{range_text}（{'；'.join(details)}）")
    return "<br>".join(entries) or "暂未收录可展示的性质"


def _property_table_rows(candidate: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Return narrow, one-property-per-row customer table data."""
    material = candidate["material"]
    identity = material.get("display_name") or material.get("material_id") or "-"
    if material.get("grade"):
        identity += f"（{material['grade']}）"
    state = material.get("product_state") or "-"
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidate.get("available_properties", []):
        if item.get("coverage") != "temperature_curve" and isinstance(item.get("value"), (int, float)):
            grouped.setdefault(str(item.get("property") or ""), []).append(item)
    rows: list[tuple[str, str, str, str]] = []
    for property_name, points in grouped.items():
        units = {str(item.get("unit") or "") for item in points}
        values = [float(item["value"]) for item in points]
        unit = next(iter(units)) if len(units) == 1 else ""
        if len(points) == 1:
            value_text = format_value(points[0]["value"], points[0].get("unit"))
            condition = _condition_tail(points[0].get("condition")) or "未注明"
        else:
            value_text = format_value(min(values), unit) if min(values) == max(values) else f"{format_value(min(values), unit)}–{format_value(max(values), unit)}"
            details = []
            for point in points:
                condition = _condition_tail(point.get("condition")) or "未注明"
                details.append(f"{condition}（{format_value(point['value'], point.get('unit'))}）")
            condition = "；".join(details)
        rows.append((identity, property_label(property_name), value_text, condition))
    for item in candidate.get("available_properties", []):
        if item.get("coverage") != "temperature_curve":
            continue
        span = item.get("temperature_range_K") or []
        if len(span) == 2:
            value_text = f"{format_value(span[0], 'K')}–{format_value(span[1], 'K')} 温度曲线"
        else:
            value_text = "温度曲线"
        rows.append((identity, property_label(item.get("property")), value_text, _condition_tail(item.get("condition")) or "未注明"))
    return rows or [(identity, "已入库性质", "暂未收录可展示的数值", "-")]


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:96] or "property_comparison"


def _font() -> FontProperties | None:
    return FontProperties(fname=str(_CJK_FONT)) if _CJK_FONT.is_file() else None


def render_property_comparison(result: dict[str, Any], output_dir: Path) -> Path | None:
    """Render a requested property, or a useful default physical-property chart."""
    requested = result.get("constraints", {}).get("property_constraints", [])
    preferences = result.get("constraints", {}).get("preference_goals", [])
    if not requested and preferences:
        property_name = preferences[0].get("property")
        values: list[tuple[str, float]] = []
        unit = ""
        for candidate in result.get("results", []):
            for evidence in candidate.get("preference_evidence", []):
                observed = evidence.get("observed", {})
                if evidence.get("property") == property_name and evidence.get("status") == "observed" and isinstance(observed.get("value"), (int, float)):
                    values.append((candidate["material"].get("display_name") or candidate["material"].get("material_id"), observed["value"]))
                    unit = unit or observed.get("unit") or ""
                    break
        if not values:
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        labels, numbers = zip(*values)
        font = _font()
        fig, axis = plt.subplots(figsize=(8, max(3.4, 0.65 * len(values) + 1.8)))
        bars = axis.barh(labels, numbers, color="#2b6cb0")
        axis.invert_yaxis()
        axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
        axis.set_title(f"候选材料{property_label(property_name)}分布（方向排序）", fontproperties=font)
        for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
            label.set_fontproperties(font)
        axis.grid(axis="x", alpha=0.22)
        axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=4, fontsize=9)
        fig.tight_layout()
        path = output_dir / f"{_safe_name(property_name)}_preference_comparison.png"
        fig.savefig(path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return path
    if not requested:
        return render_default_property_comparison(result, output_dir)
    property_name = requested[0].get("property")
    values: list[tuple[str, float]] = []
    statuses: list[str] = []
    unit = requested[0].get("unit") or ""
    for candidate in result.get("results", []):
        for evidence in candidate.get("evidence", []):
            if evidence.get("property") != property_name:
                continue
            observed = evidence.get("observed", {})
            if evidence.get("status") in {"pass", "fail"} and isinstance(observed.get("value"), (int, float)):
                values.append((candidate["material"].get("display_name") or candidate["material"].get("material_id"), observed["value"]))
                statuses.append(str(evidence.get("status")))
            if not unit:
                unit = observed.get("unit") or ""
            break
    if not values:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    labels, numbers = zip(*values)
    font = _font()
    fig, axis = plt.subplots(figsize=(8, max(3.4, 0.65 * len(values) + 1.8)))
    bars = axis.barh(labels, numbers, color=["#2f855a" if status == "pass" else "#c05621" for status in statuses])
    axis.invert_yaxis()
    axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
    axis.set_title(f"候选材料{property_label(property_name)}分布与筛选边界", fontproperties=font)
    for bound in [item for item in requested if item.get("property") == property_name]:
        axis.axvline(float(bound["value"]), color="#c53030", linestyle="--", linewidth=1)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.grid(axis="x", alpha=0.22)
    axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=4, fontsize=9)
    fig.tight_layout()
    path = output_dir / f"{_safe_name(property_name)}_comparison.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def _point_property(candidate: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
    """Return the first recorded numeric point for one semantic property."""
    for item in candidate.get("available_properties", []):
        if (
            item.get("property") in names
            and item.get("coverage") != "temperature_curve"
            and isinstance(item.get("value"), (int, float))
        ):
            return item
    return None


def default_comparison_property(result: dict[str, Any]) -> str | None:
    """Choose one numeric, same-unit property shared by all candidates."""
    candidates = result.get("results") or []
    if not candidates:
        return None
    common: dict[str, set[str]] | None = None
    for candidate in candidates:
        current: dict[str, set[str]] = {}
        for item in candidate.get("available_properties", []):
            property_name = str(item.get("property") or "")
            unit = str(item.get("unit") or "")
            if (
                property_name
                and unit
                and item.get("coverage") != "temperature_curve"
                and isinstance(item.get("value"), (int, float))
            ):
                current.setdefault(property_name, set()).add(unit)
        if common is None:
            common = current
        else:
            common = {
                property_name: common[property_name] & units
                for property_name, units in current.items()
                if property_name in common and common[property_name] & units
            }
        if not common:
            return None
    available = common or {}
    return next(
        (property_name for property_name in _DEFAULT_COMPARISON_PRIORITY if property_name in available),
        next(iter(available), None),
    )


def _default_property_values(result: dict[str, Any], property_name: str) -> list[tuple[str, float, str]]:
    """Get one recorded value per candidate for an unconstrained comparison."""
    values: list[tuple[str, float, str]] = []
    for candidate in result.get("results", []):
        point = _point_property(candidate, (property_name,))
        if not point:
            return []
        values.append((
            candidate["material"].get("display_name") or candidate["material"].get("material_id"),
            float(point["value"]),
            str(point.get("unit") or ""),
        ))
    return values if len({unit for _, _, unit in values}) == 1 else []


def render_default_numeric_comparison(result: dict[str, Any], output_dir: Path) -> tuple[Path, str] | None:
    """Render a common recorded property if no melting interval is available."""
    property_name = default_comparison_property(result)
    if not property_name:
        return None
    values = _default_property_values(result, property_name)
    if not values:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    labels, numbers, units = zip(*values)
    unit = units[0]
    font = _font()
    fig, axis = plt.subplots(figsize=(8, max(3.4, 0.65 * len(values) + 1.8)))
    bars = axis.barh(labels, numbers, color="#2267b5")
    axis.invert_yaxis()
    axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
    axis.set_title(f"候选材料{property_label(property_name)}对比", fontproperties=font)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.grid(axis="x", alpha=0.22)
    axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=4, fontsize=9)
    fig.tight_layout()
    path = output_dir / f"default_{_safe_name(property_name)}_comparison.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path, property_name


def _melting_intervals(result: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Normalize solidus/liquidus and melting-range records into comparable intervals."""
    intervals: list[tuple[str, float, float]] = []
    for candidate in result.get("results", []):
        lower = _point_property(candidate, ("solidus_temperature", "melting_range_low"))
        upper = _point_property(candidate, ("liquidus_temperature", "melting_range_high"))
        if not lower or not upper or lower.get("unit") != "K" or upper.get("unit") != "K":
            continue
        label = candidate["material"].get("display_name") or candidate["material"].get("material_id")
        intervals.append((label, float(lower["value"]), float(upper["value"])))
    return intervals


def render_default_property_comparison(result: dict[str, Any], output_dir: Path) -> Path | None:
    """Show a shared material property when no user threshold selected one.

    A count of catalogue fields is an internal completeness check, not a material
    decision signal.  Prefer the recorded melting interval for alloy candidates;
    A single complete interval is still useful: it shows the available melting
    window for that candidate without claiming a comparison.
    """
    intervals = _melting_intervals(result)
    if not intervals:
        default_chart = render_default_numeric_comparison(result, output_dir)
        return default_chart[0] if default_chart else None
    output_dir.mkdir(parents=True, exist_ok=True)
    labels, lower_values, upper_values = zip(*intervals)
    font = _font()
    positions = list(range(len(intervals)))
    figure_height = max(3.3, 0.82 * len(intervals) + 1.9)
    fig, axis = plt.subplots(figsize=(8.6, figure_height), facecolor="white")
    axis.set_facecolor("white")
    interval_widths = [upper - lower for lower, upper in zip(lower_values, upper_values)]
    bars = axis.barh(
        positions,
        interval_widths,
        left=lower_values,
        height=0.38,
        color="#2a6fbb",
        edgecolor="#174a82",
        linewidth=0.8,
        alpha=0.92,
    )
    all_values = [*lower_values, *upper_values]
    padding = max(20.0, (max(all_values) - min(all_values)) * 0.28)
    axis.set_xlim(min(all_values) - padding, max(all_values) + padding)
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.set_xlabel("温度 (K)", fontproperties=font)
    title = "候选合金熔化温度区间" if len(intervals) == 1 else "候选合金熔化温度区间对比"
    axis.set_title(title, fontproperties=font)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.grid(axis="x", alpha=0.18, linewidth=0.8)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(axis="y", length=0, pad=10)
    for position, lower, upper, width, bar in zip(positions, lower_values, upper_values, interval_widths, bars):
        axis.scatter([lower, upper], [position, position], s=32, color=["#174a82", "#d8791f"], zorder=3)
        axis.annotate(
            format_value(lower, "K"), (lower, position), xytext=(0, -18),
            textcoords="offset points", ha="center", va="top", fontsize=9.5,
        )
        axis.annotate(
            format_value(upper, "K"), (upper, position), xytext=(0, -18),
            textcoords="offset points", ha="center", va="top", fontsize=9.5,
        )
        axis.annotate(
            f"区间 {format_value(width, 'K')}", (lower + width / 2, position),
            xytext=(0, 15), textcoords="offset points", ha="center", va="bottom",
            color="#174a82", fontsize=9.5, fontproperties=font,
        )
    fig.tight_layout()
    path = output_dir / "melting_temperature_interval_comparison.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def requirement_markdown(result: dict[str, Any]) -> str:
    constraints = result["constraints"]
    names = "、".join(constraints.get("material_queries") or []) or "从需求文本提取"
    families = "、".join(constraints.get("material_families") or []) or "不限"
    temperature = constraints.get("service_temperature_K")
    temperature_text = f"{temperature - 273.15:g} °C" if isinstance(temperature, (int, float)) else "未指定"
    context = constraints.get("selection_context") or {}
    context_rows = [
        ("应用场景", context.get("application")),
        ("服役环境", context.get("environment")),
        ("制造与结构上下文", context.get("manufacturing")),
    ]
    lines = ["### 需求理解", "", "我会按你给出的场景、工况和指标，在已入库的材料数据中核对可比较证据。", ""]
    lines += ["| 项目 | 本轮输入 |", "|---|---|", f"| 材料名称/别名 | {names} |", f"| 材料族 | {families} |", f"| 服役温度 | {temperature_text} |", f"| 性质条件 | {len(constraints.get('property_constraints') or [])} 项 |"]
    lines += [f"| {label} | {value} |" for label, value in context_rows if value]
    for item in constraints.get("property_constraints") or []:
        lines.append(f"| └ {property_label(item.get('property'))} | {item.get('operator')} {format_value(item.get('value'), item.get('unit'))} |")
    return "\n".join(lines)


def analysis_markdown(result: dict[str, Any]) -> str:
    """Keep the first existing content section focused on scenario and method.

    This is presentation only: it does not turn a stated context into an
    additional material constraint or a catalogue fact.
    """
    return "\n\n".join((requirement_markdown(result), resolution_markdown(result)))


def resolution_markdown(result: dict[str, Any]) -> str:
    if result.get("data_status", {}).get("outcome") == "needs_screening_criteria":
        strategy = result.get("screening", {}).get("strategy", {})
        return "\n".join([
            "### 还需要确认的信息",
            "",
            strategy.get("description") or "已进入通用成熟材料初筛，但尚没有足以比较目录证据的条件。",
        ])
    reference = result.get("constraints", {}).get("catalog_reference") or {}
    if reference:
        return "\n".join([
            "### 候选商品合金",
            "",
            f"针对 {reference.get('target') or '当前合金需求'}，目录中找到以下可用于后续对比的商品合金。",
        ])
    requested_constraints = result.get("constraints", {}).get("property_constraints") or []
    preferences = result.get("constraints", {}).get("preference_goals") or []
    if requested_constraints:
        lines = ["### 已确认的筛选条件", "", "| 性质 | 约束 |", "|---|---|"]
        for item in requested_constraints:
            lines.append(f"| {property_label(item.get('property'))} | {item.get('operator')} {format_value(item.get('value'), item.get('unit'))} |")
        strategy = result.get("screening", {}).get("strategy", {})
        lines += ["", f"筛选方式：**{strategy.get('description') or '按明确约束筛选已入库证据。'}**"]
        return "\n".join(lines)
    if preferences:
        lines = ["### 你关注的性能", "", "| 性质 | 方向 |", "|---|---|"]
        for item in preferences:
            direction = "越高越好" if item.get("direction") == "maximize" else "越低越好"
            lines.append(f"| {property_label(item.get('property'))} | {direction} |")
        lines += ["", "说明：这是定性目标的证据排序，不是数值阈值筛选；不会据此宣称候选已经工程通过。"]
        return "\n".join(lines)
    rows = result.get("name_resolution") or []
    if not rows:
        return "### 材料名称核对\n\n暂未识别到可在目录中直接核验的材料名称、牌号或标准号。"
    lines = ["### 材料名称核对", "", "| 输入名称 | 目录条目 | 匹配结果 |", "|---|---|---|"]
    has_exact_match = False
    for row in rows:
        matched = "、".join(dict.fromkeys(
            item.get("display_name") or item.get("material_id", "")
            for item in (row.get("resolved_materials") or [])
        )) or "无"
        status = {"matched": "精确匹配", "ambiguous": "需确认牌号或状态", "unmatched": "目录中未找到"}.get(row.get("status"), row.get("status"))
        if row.get("status") == "matched":
            has_exact_match = True
        lines.append(f"| {row.get('input')} | {matched} | {status} |")
    if has_exact_match:
        lines += ["", "说明：下列性质仅对应所列产品状态和测试条件，不外推至同名的其他品牌、加工方式或材料状态。"]
    return "\n".join(lines)


def _upstream_evidence_markdown(result: dict[str, Any]) -> list[str]:
    evidence = result.get("constraints", {}).get("upstream_evidence") or []
    if not evidence:
        return []
    lines = ["#### 上游提供的材料信息（待目录核验）", "", "| 材料/牌号 | 性质或信息 | 数值/描述 | 工况 | 来源 |", "|---|---|---|---|---|"]
    for item in evidence:
        material = item.get("material") or item.get("name") or item.get("grade") or "未注明"
        property_name = item.get("property") or item.get("field") or "材料信息"
        value = item.get("value") if item.get("value") not in (None, "") else item.get("description") or "未注明"
        unit = item.get("unit") or ""
        condition = item.get("condition") or item.get("test_condition") or "未注明"
        source = item.get("source") or "上游未注明"
        lines.append(f"| {material} | {property_name} | {value} {unit}".rstrip() + f" | {condition} | {source} |")
    lines += ["", "说明：本表仅整理上游提供的信息；除非下方存在目录匹配记录，否则不视为本服务已核验的数据库事实。", ""]
    return lines


def screening_funnel_rows(result: dict[str, Any]) -> list[tuple[str, int]]:
    """Return the same ordered, evidence-strict funnel for text and charts."""
    candidates = list(result.get("results", []))
    rows = [("已纳入本轮目录候选", len(candidates))]
    preferences = result.get("constraints", {}).get("preference_goals", [])
    if not result.get("constraints", {}).get("property_constraints") and preferences:
        for preference in preferences:
            property_name = preference.get("property")
            candidates = [
                candidate for candidate in candidates
                if any(item.get("property") == property_name and item.get("status") == "observed"
                       for item in candidate.get("preference_evidence", []))
            ]
            rows.append((f"有{property_label(property_name)}可比较证据", len(candidates)))
        return rows
    for constraint in result.get("constraints", {}).get("property_constraints", []):
        def passes(candidate: dict[str, Any]) -> bool:
            for evidence in candidate.get("evidence", []):
                requested = evidence.get("requested", {})
                if (evidence.get("property") == constraint.get("property") and requested.get("operator") == constraint.get("operator")
                        and requested.get("value") == constraint.get("value") and requested.get("unit") == constraint.get("unit")):
                    return evidence.get("status") == "pass"
            return False
        candidates = [candidate for candidate in candidates if passes(candidate)]
        rows.append((f"{property_label(constraint.get('property'))} {constraint.get('operator')} {format_value(constraint.get('value'), constraint.get('unit'))}", len(candidates)))
    return rows


def comparison_markdown(result: dict[str, Any]) -> str:
    requested_constraints = result.get("constraints", {}).get("property_constraints") or []
    preferences = result.get("constraints", {}).get("preference_goals") or []
    if requested_constraints:
        candidates = result.get("results", [])
        lines = ["### 2. 筛选漏斗与证据", "", "#### 筛选漏斗", "", "| 条件步骤 | 保留候选数 |", "|---|---:|"]
        lines += [f"| {label} | {count} |" for label, count in screening_funnel_rows(result)]
        status_counts = result.get("screening", {}).get("summary", {}).get("constraint_status_counts", {})
        lines += ["", "#### 约束证据状态", ""]
        for property_name, counts in status_counts.items():
            details = "；".join(f"{status}：{count}" for status, count in sorted(counts.items()))
            lines.append(f"- {property_label(property_name)}：{details}")
        lines += ["", "#### 候选核验", "", "| 候选材料 | 本轮约束状态 | 综合结果 |", "|---|---|---|"]
        for candidate in candidates:
            statuses = []
            for evidence in candidate.get("evidence", []):
                observed = evidence.get("observed", {})
                statuses.append(f"{property_label(evidence.get('property'))}：{evidence.get('status')}" + (f"（{format_value(observed.get('value'), observed.get('unit'))}）" if observed else ""))
            identity = candidate["material"].get("display_name") or candidate["material"].get("material_id")
            lines.append(f"| {identity} | {'<br>'.join(statuses) or '缺少可比较证据'} | {'通过' if candidate.get('eligible') else '未通过'} |")
        return "\n".join(lines)
    if preferences:
        candidates = result.get("results", [])
        lines = ["### 2. 排序证据与覆盖", "", "#### 证据覆盖漏斗", "", "| 证据步骤 | 可比较候选数 |", "|---|---:|"]
        lines += [f"| {label} | {count} |" for label, count in screening_funnel_rows(result)]
        lines += ["", "#### 候选排序核验", "", "| 排序 | 候选材料 | 方向性证据 |", "|---:|---|---|"]
        for candidate in candidates:
            evidence = []
            for item in candidate.get("preference_evidence", []):
                observed = item.get("observed", {})
                value = f"（{format_value(observed.get('value'), observed.get('unit'))}）" if observed else ""
                status = "已观察" if item.get("status") == "observed" else "缺失"
                direction = "↑" if item.get("direction") == "maximize" else "↓"
                evidence.append(f"{property_label(item.get('property'))}{direction}：{status}{value}")
            identity = candidate["material"].get("display_name") or candidate["material"].get("material_id")
            lines.append(f"| {candidate.get('preference_rank') or '-'} | {identity} | {'<br>'.join(evidence) or '缺少可比较证据'} |")
        gaps = result.get("preference_data_gaps") or []
        if gaps:
            lines += ["", "#### 暂待补充数据的材料", ""]
            for item in gaps[:5]:
                missing = "、".join(property_label(name) for name in item.get("missing_properties") if name) or "关注性质"
                lines.append(f"- {item.get('display_name') or item.get('material_id')}：目录已识别该材料，但暂未收录本次关注的{missing}数据，因此未参与排序。")
            if len(gaps) > 5:
                lines.append(f"- 另有 {len(gaps) - 5} 种目录材料也缺少上述关注性质，未在此逐项展开。")
        return "\n".join(lines)
    lines = ["### 候选材料核验", ""]
    lines += _upstream_evidence_markdown(result)
    if result.get("name_resolution"):
        lines += ["已按具体产品状态核对目录记录；完整的已入库性质和来源见结论后的材料数据表。", ""]
    lines += ["| 候选材料 | 产品状态 | 已收录性质数量 |", "|---|---|---:|"]
    for candidate in result.get("results", []):
        material = candidate["material"]
        identity = material.get("display_name") or material.get("material_id") or "未命名材料"
        if material.get("grade"):
            identity += f"（{material['grade']}）"
        state = material.get("product_state") or "未注明"
        lines.append(f"| {identity} | {state} | {len(candidate.get('available_properties') or [])} |")
    if not result.get("results", []):
        if result.get("data_status", {}).get("outcome") == "needs_screening_criteria":
            return "\n".join([
                *lines,
                "本轮尚未执行候选比较：系统不会在缺少目标工况和性能窗口时猜测或推荐材料。",
            ])
        return "\n".join([
            *lines,
            "本轮目录中未找到与指定材料、牌号或标准相符的已入库记录。",
            "为避免误导，系统不会展示或推断其他材料作为替代候选。",
        ])
    return "\n".join(lines)


def _candidate_identity(candidate: dict[str, Any]) -> str:
    material = candidate["material"]
    identity = material.get("display_name") or material.get("material_id") or "该材料"
    return f"{identity}（{material['grade']}）" if material.get("grade") else identity


def _source_text(item: dict[str, Any]) -> str:
    source = item.get("source") or {}
    if isinstance(source, dict) and isinstance(source.get("first"), dict):
        source = source["first"]
    if not isinstance(source, dict):
        return "目录来源未注明"
    source_id = source.get("source_id") or "目录来源未注明"
    locator = source.get("source_locator") or ""
    return f"{source_id}；{locator}" if locator else str(source_id)


_ENGINEERING_REFERENCE_VALUES: dict[str, list[tuple[str, str, str]]] = {
    # These ranges are deliberately kept out of the catalogue CSVs.  They are
    # customer-requested engineering references for a common, well-defined
    # temper, not source-verified facts used by the screening engine.
    "al 6061-t6": [
        ("density", "约 2,700 kg/m³", "室温；6061-T6 常见轧制材"),
        ("yield_strength", "约 240–280 MPa", "室温；6061-T6 常见轧制材"),
        ("ultimate_tensile_strength", "约 260–320 MPa", "室温；6061-T6 常见轧制材"),
        ("elastic_modulus", "约 68–70 GPa", "室温；6061-T6 常见轧制材"),
        ("thermal_conductivity", "约 150–180 W/(m·K)", "室温；6061-T6 常见轧制材"),
        ("hardness", "约 85–100 HB", "室温；6061-T6 常见轧制材"),
    ],
}


def _engineering_reference_rows(candidate: dict[str, Any], recorded: set[str]) -> list[tuple[str, str, str]]:
    name = str(candidate["material"].get("display_name") or "").strip().lower()
    return [item for item in _ENGINEERING_REFERENCE_VALUES.get(name, []) if item[0] not in recorded]


def material_data_card(candidate: dict[str, Any]) -> str:
    """Show stored facts after the conclusion, including their source locator."""
    material = candidate["material"]
    lines = [
        f"#### {_candidate_identity(candidate)} 的已入库数据",
        "",
        f"产品状态：{material.get('product_state') or '未注明'}。以下内容均为当前目录已收录的数据；不同状态或温度下的数值分别列出。",
        "",
        "| 性质 | 数值/范围 | 测试或产品条件 | 数据类型 | 来源 |",
        "|---|---|---|---|---|",
    ]
    properties = candidate.get("available_properties") or []
    recorded: set[str] = set()
    for item in properties:
        if item.get("coverage") == "temperature_curve":
            span = item.get("temperature_range_K") or []
            value = (
                f"{format_value(span[0], 'K')}–{format_value(span[1], 'K')} 温度曲线"
                if len(span) == 2 else "温度曲线"
            )
        elif isinstance(item.get("value"), (int, float)):
            value = format_value(item["value"], item.get("unit"))
        else:
            continue
        recorded.add(str(item.get("property") or ""))
        condition = _condition_tail(item.get("condition")) or "未注明"
        lines.append(f"| {property_label(item.get('property'))} | {value} | {condition} | 目录记录 | {_source_text(item)} |")
    for property_name, value, condition in _engineering_reference_rows(candidate, recorded):
        lines.append(
            f"| {property_label(property_name)} | {value} | {condition} | 工程估算 | "
            "工程参考区间；非目录记录，不参与筛选或排序 |"
        )
    if len(lines) == 6:
        lines.append("| 已入库数值性质 | 当前目录未收录 | - | - | 材料身份记录已保留 |")
    return "\n".join(lines)


def _preferred_candidate(result: dict[str, Any]) -> dict[str, Any] | None:
    candidates = result.get("results") or []
    eligible = [item for item in candidates if item.get("eligible")]
    return (eligible or candidates or [None])[0]


def conclusion_markdown(result: dict[str, Any]) -> str:
    """Close with a customer-facing recommendation followed by source data."""
    constraints = result.get("constraints") or {}
    context = constraints.get("selection_context") or {}
    scenario = context.get("application") or "当前使用"
    temperature = constraints.get("service_temperature_K")
    condition = f"在 {temperature - 273.15:g} °C 的已知工况下" if isinstance(temperature, (int, float)) else "在当前尚未明确服役温度的条件下"
    candidate = _preferred_candidate(result)
    outcome = result.get("data_status", {}).get("outcome")
    if candidate is None:
        if outcome == "needs_screening_criteria":
            return "### 结论\n\n针对当前需求，先补充材料牌号/体系、服役工况或至少一项关键性能指标后，才能形成有依据的材料选择。"
        return "### 结论\n\n针对当前需求，目录暂未找到可核验的对应材料数据。建议进入文献筛选，补齐牌号、工况和来源后，再继续完成材料对比。"
    identity = _candidate_identity(candidate)
    if not constraints.get("property_constraints") and not constraints.get("preference_goals"):
        sentence = f"针对{scenario}，{condition}，当前目录核验到 {identity}；当前未给出量化性质阈值，以下数据可作为后续选材比较的依据。"
    elif candidate.get("eligible"):
        sentence = f"针对{scenario}，{condition}，当前候选中优先选择 {identity}。"
    elif constraints.get("preference_goals"):
        sentence = f"针对{scenario}，{condition}，当前目录中优先继续评估 {identity}。"
    elif constraints.get("property_constraints"):
        sentence = f"针对{scenario}，{condition}，当前目录暂未找到能同时满足全部条件的材料；以下列出最接近候选 {identity} 的已入库数据，便于确认需要补充或调整的条件。"
    else:
        sentence = f"针对{scenario}，{condition}，当前目录已识别 {identity} 作为可继续核验的候选；当前未给出量化性质阈值。"
    return "\n\n".join(["### 结论", sentence, material_data_card(candidate)])
