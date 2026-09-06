"""Human-readable streaming and chart assets for mature-material lookups."""
from __future__ import annotations

import json
import os
import re
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
    "hardness_rockwell_b": "洛氏硬度 B 标尺", "hardness_rockwell_c": "洛氏硬度 C 标尺",
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


def _table_cell(value: Any) -> str:
    """Keep source/test text inside its Markdown table cell."""
    return str(value).replace("|", "；")


def candidate_display_name(material: dict[str, Any]) -> str:
    """Give catalogue-only formulas a customer-readable Chinese identity."""
    name = str(material.get("display_name") or material.get("material_id") or "未命名材料").strip()
    grade = str(material.get("grade") or "").strip()
    family = str(material.get("family") or "").strip()
    family_key = family.casefold()
    family_labels = {
        "high-entropy/multi-principal-element alloy": "高熵/多主元合金",
        "metallic glass": "金属玻璃合金",
        "nickel-based superalloy": "镍基高温合金",
        "aluminum alloy": "铝合金",
        "titanium alloy": "钛合金",
        "nickel-chromium-iron alloy": "镍铬铁合金",
        "nickel-chromium-molybdenum-tungsten alloy": "镍铬钼钨合金",
        "high-chromium nickel alloy": "高铬镍基合金",
        "age-hardenable nickel-chromium-molybdenum-niobium alloy": "时效强化镍基合金",
        "nickel-chromium-iron-molybdenum high-temperature alloy": "镍基高温合金",
        "solid-solution-strengthened nickel-chromium-cobalt-molybdenum alloy": "固溶强化镍基合金",
    }
    family_label = family_labels.get(family_key)
    if not family_label and "nickel" in family_key:
        family_label = "镍基合金"
    if not family_label and "stainless" in family_key:
        family_label = "不锈钢"
    if re.search(r"[\u4e00-\u9fff]", name):
        base = name
    elif re.fullmatch(r"(?:[A-Z][a-z]?\d*(?:\.\d+)?\s*){2,}", name):
        # Formula notation identifies neither an alloy class nor a trade
        # material.  The customer label must therefore come from the
        # catalogue family field, with a neutral fallback when it is absent.
        base = f"{family_label + '候选' if family_label else '成分式材料候选'}（成分式：{name}）"
    elif family_label:
        proper_name = re.sub(r"^INCONEL\s+alloy\s+", "INCONEL ", name, flags=re.IGNORECASE)
        base = f"{family_label}（{proper_name}）"
    else:
        base = name
    grade_root = re.match(r"[A-Za-z0-9-]+", grade)
    grade_already_named = bool(grade_root and grade_root.group(0).casefold() in name.casefold())
    return f"{base}（牌号：{grade}）" if grade and not grade_already_named else base


def _condition_tail(condition: Any) -> str:
    """Keep customer-relevant test clauses without exposing import-field names."""
    text = str(condition or "")
    text = re.sub(r"\btest_temperature_deg_[cf]\s*=\s*(?:room|rt)\b", "测试温度：室温", text, flags=re.IGNORECASE)
    text = re.sub(r"\btest_temperature_deg_c\s*=\s*([-+]?\d+(?:\.\d+)?)", r"测试温度：\1 °C", text, flags=re.IGNORECASE)
    text = re.sub(r"\btest_temperature_deg_f\s*=\s*([-+]?\d+(?:\.\d+)?)", r"测试温度：\1 °F", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:^|;)\s*source column=[^;]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:^|;)\s*imported through [^;]+", "", text, flags=re.IGNORECASE)
    parts = [item.strip() for item in text.split(";") if item.strip()]
    return "；".join(parts)


def _customer_condition(condition: Any) -> str:
    """Summarise a source condition without turning table footnotes into UI logs."""
    raw = _condition_tail(condition)
    if not raw or raw.casefold() in {"unspecified", "not_recorded"}:
        return "产品状态/测试条件未注明"

    # A table-level footnote can describe several product states.  Unless the
    # source row identifies its letter, showing every footnote as this row's
    # condition would be misleading as well as unreadable.
    footnotes = re.findall(r"\$\^\{?\s*([a-z])\s*\}?", raw, flags=re.IGNORECASE)
    if len(set(footnotes)) > 1:
        return "来源表包含多种热处理/测试脚注；当前数据行未标明对应脚注，需按原表复核"

    lowered = raw.casefold()
    labels: list[str] = []
    for pattern, label in (
        (r"hot[- ]rolled round", "热轧圆棒"),
        (r"cold[- ]rolled sheet", "冷轧板材"),
        (r"sheet and plate", "板材/板件"),
        (r"annealed and aged", "退火及时效态"),
        (r"solution[- ]annealed", "固溶退火态"),
        (r"room temperature|\brt\b", "室温测试"),
    ):
        if re.search(pattern, lowered, flags=re.IGNORECASE) and label not in labels:
            labels.append(label)
    if re.search(r"annealed", lowered, flags=re.IGNORECASE) and not any(
        label in labels for label in ("退火及时效态", "固溶退火态")
    ):
        labels.append("退火态")
    if labels:
        return "；".join(labels)
    if re.search(r"\btable\s+\d+\b", raw, flags=re.IGNORECASE):
        return "来源表条件已记录；详见证据出处"
    return raw if len(raw) <= 120 else "来源已记录测试/产品条件；详见证据出处"


def _temperature_range_text(span: Any) -> str:
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return "温度范围未注明"
    try:
        lower_k, upper_k = float(span[0]), float(span[1])
    except (TypeError, ValueError):
        return "温度范围未注明"
    # Keep cryogenic source coverage in kelvin.  "-269 °C" is technically
    # correct but obscures the original 4–300 K validity interval.
    if lower_k < 273.15 or upper_k < 273.15:
        return f"{lower_k:g}–{upper_k:g} K"
    return f"{lower_k - 273.15:g}–{upper_k - 273.15:g} °C"


def _temperature_text(value: Any) -> str:
    try:
        return f"{float(value) - 273.15:g} °C"
    except (TypeError, ValueError):
        return "温度未注明"


def _curve_value_text(item: dict[str, Any]) -> str:
    endpoints = item.get("temperature_endpoints")
    if isinstance(endpoints, list) and len(endpoints) == 2:
        use_kelvin = any(
            isinstance(endpoint, dict) and isinstance(endpoint.get("temperature_K"), (int, float))
            and endpoint["temperature_K"] < 273.15
            for endpoint in endpoints
        )
        formatted = []
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            value = endpoint.get("value")
            if not isinstance(value, (int, float)):
                continue
            temperature = endpoint.get("temperature_K")
            temperature_text = f"{float(temperature):g} K" if use_kelvin else _temperature_text(temperature)
            formatted.append(f"{temperature_text}：{format_value(value, item.get('unit'))}")
        if len(formatted) == 2:
            return "；".join(formatted)
    values = item.get("value_range")
    if isinstance(values, (list, tuple)) and len(values) == 2:
        lower, upper = values
        return (
            format_value(lower, item.get("unit"))
            if lower == upper else f"{format_value(lower, item.get('unit'))}–{format_value(upper, item.get('unit'))}"
        )
    return "当前目录未收录曲线数值范围"


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
        condition = _customer_condition(item.get("condition"))
        if len(span) == 2:
            condition = f"测量温区 {_temperature_range_text(span)}；{condition}"
        rows.append((identity, property_label(item.get("property")), _curve_value_text(item), condition))
    # D-level values are deliberately not mixed into available_properties.
    # Still show them in the normal identity card so a lookup without explicit
    # simulation goals remains useful and honest about its evidence level.
    for estimate in candidate.get("engineering_estimates", []):
        rows.append((
            identity,
            f"{property_label(estimate.get('property'))}（工程估算）",
            _estimate_value_text(estimate),
            f"D：模型/工程估算，不能用于通过判断；不参与筛选/排序；{estimate.get('condition') or '适用状态待核验'}；{estimate.get('basis') or '依据待补'}",
        ))
    return rows or [(identity, "已入库性质", "暂未收录可展示的数值", "-")]


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:96] or "property_comparison"


def _font() -> FontProperties | None:
    return FontProperties(fname=str(_CJK_FONT)) if _CJK_FONT.is_file() else None


def _style_candidate_distribution(axis: Any, fig: Any, title: str, subtitle: str, font: FontProperties | None) -> None:
    """Use the same airy report-card language as the evidence funnel."""
    fig.patch.set_facecolor("white")
    axis.set_facecolor("#F7FBFF")
    axis.spines[["top", "right", "left", "bottom"]].set_visible(False)
    axis.grid(axis="x", color="#D8E7F3", linewidth=.9, alpha=.85)
    axis.set_axisbelow(True)
    axis.tick_params(axis="y", length=0, pad=10, colors="#243B53")
    axis.tick_params(axis="x", length=0, pad=7, colors="#627D98")
    fig.suptitle(title, x=.075, y=.975, ha="left", fontproperties=font, fontsize=16, fontweight="bold", color="#17324D")
    fig.text(.075, .907, subtitle, color="#627D98", fontproperties=font, fontsize=9.5)


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
        fig, axis = plt.subplots(figsize=(10.2, max(4.2, 0.66 * len(values) + 2.45)))
        colors = ["#4A9BD3", *["#A9D1EE"] * (len(values) - 1)]
        bars = axis.barh(labels, numbers, color=colors, height=.58, edgecolor="none")
        axis.invert_yaxis()
        axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
        _style_candidate_distribution(
            axis, fig, f"候选材料{property_label(property_name)}分布",
            "按当前方向性目标排序；颜色深浅表示排序位置，不代表工程通过。", font,
        )
        for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
            label.set_fontproperties(font)
        axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=7, fontsize=9, color="#243B53")
        axis.set_xlim(0, max(numbers) * 1.22)
        fig.tight_layout(rect=(0, 0, 1, .86))
        path = output_dir / f"{_safe_name(property_name)}_preference_comparison.png"
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
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
    fig, axis = plt.subplots(figsize=(10.2, max(4.2, 0.66 * len(values) + 2.45)))
    bars = axis.barh(labels, numbers, color=["#69B9AF" if status == "pass" else "#E7A078" for status in statuses], height=.58, edgecolor="none")
    axis.invert_yaxis()
    axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
    _style_candidate_distribution(
        axis, fig, f"候选材料{property_label(property_name)}分布",
        "绿色为满足本轮边界，橙色为未满足；虚线为当前筛选条件。", font,
    )
    for bound in [item for item in requested if item.get("property") == property_name]:
        axis.axvline(float(bound["value"]), color="#c53030", linestyle="--", linewidth=1)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=7, fontsize=9, color="#243B53")
    axis.set_xlim(0, max(numbers) * 1.22)
    fig.tight_layout(rect=(0, 0, 1, .86))
    path = output_dir / f"{_safe_name(property_name)}_comparison.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
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
    fig, axis = plt.subplots(figsize=(10.2, max(4.2, 0.66 * len(values) + 2.45)))
    bars = axis.barh(labels, numbers, color=["#4A9BD3", *["#A9D1EE"] * (len(values) - 1)], height=.58, edgecolor="none")
    axis.invert_yaxis()
    axis.set_xlabel(f"{property_label(property_name)} ({unit})".strip(), fontproperties=font)
    _style_candidate_distribution(axis, fig, f"候选材料{property_label(property_name)}分布", "展示当前目录中同条件可直接比较的已入库数值。", font)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.bar_label(bars, labels=[format_value(number, unit) for number in numbers], padding=7, fontsize=9, color="#243B53")
    axis.set_xlim(0, max(numbers) * 1.22)
    fig.tight_layout(rect=(0, 0, 1, .86))
    path = output_dir / f"default_{_safe_name(property_name)}_comparison.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
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
    names = "、".join(constraints.get("material_queries") or [])
    family_labels = {
        "__3d_printing_consumables__": "3D 打印耗材",
        "__additive_manufacturing_materials__": "增材制造材料",
    }
    families = "、".join(
        family_labels.get(str(value), str(value))
        for value in (constraints.get("material_families") or [])
    )
    temperature = constraints.get("service_temperature_K")
    temperature_text = f"{temperature - 273.15:g} °C" if isinstance(temperature, (int, float)) else ""
    context = constraints.get("selection_context") or {}
    context_rows = [
        ("目标部位", context.get("component")),
        ("已知工况", context.get("operating_conditions")),
        ("应用场景", context.get("application")),
        ("服役环境", context.get("environment")),
        ("制造与结构上下文", context.get("manufacturing")),
        ("当前项目阶段", context.get("project_progress")),
    ]
    lines = [
        "## 1. 需求与已知工况", "",
        "我先根据你已经描述的内容整理如下，后续会在这里逐步补全。", "",
    ]
    known_rows: list[tuple[str, str]] = []
    if names:
        known_rows.append(("指定材料/牌号", names))
    if families:
        known_rows.append(("材料体系范围", families))
    if temperature_text:
        known_rows.append(("服役温度", temperature_text))
    for label, value in context_rows:
        if not value:
            continue
        if label == "制造与结构上下文" and "stl" in str(value).lower():
            value = "已提供 STL 几何文件，可用于理解零件边界；制造工艺尚待补充"
        known_rows.append((label, str(value)))
    property_constraints = constraints.get("property_constraints") or []
    if property_constraints:
        thresholds = "；".join(
            f"{property_label(item.get('property'))} {item.get('operator')} {format_value(item.get('value'), item.get('unit'))}"
            for item in property_constraints
        )
        known_rows.append(("性能门槛", thresholds))
    preferences = constraints.get("preference_goals") or []
    if preferences:
        grouped_preferences: dict[str, set[str]] = {}
        for item in preferences:
            grouped_preferences.setdefault(str(item.get("property") or ""), set()).add(str(item.get("direction") or ""))
        goals = "；".join(
            f"{property_label(name)}{'方向待确认' if directions == {'maximize', 'minimize'} else ('越高越好' if 'maximize' in directions else '越低越好')}"
            for name, directions in grouped_preferences.items()
        )
        known_rows.append(("性能关注点", goals))
    if known_rows:
        lines += ["| 信息维度 | 已掌握内容 |", "|---|---|"]
        lines += [f"| {label} | {value} |" for label, value in known_rows]
    else:
        lines += ["你暂时不需要整理成材料参数；告诉我零件大致用途或使用环境，就可以从目录中开始为你比较。"]

    follow_up: list[tuple[str, str]] = []
    if not context.get("component"):
        follow_up.append(("零件角色", "目标部位及其连接/受力方式，例如承力臂、关节壳体或连接件。"))
    if not context.get("operating_conditions") or not temperature_text:
        follow_up.append(("工况边界", "载荷、循环情况，以及连续/峰值服役温度。"))
    if not context.get("environment"):
        follow_up.append(("服役环境", "介质、湿度、腐蚀、磨损或清洁要求。"))
    if not context.get("manufacturing"):
        follow_up.append(("制造方案", "目标工艺、热处理和表面处理要求。"))
    if follow_up:
        lines += ["", "### 为形成部件级判断，建议补充", "", "| 补充主题 | 建议说明 |", "|---|---|"]
        lines += [f"| {label} | {detail} |" for label, detail in follow_up]
    return "\n".join(lines)


def analysis_markdown(result: dict[str, Any]) -> str:
    """Keep the first existing content section focused on scenario and method.

    This is presentation only: it does not turn a stated context into an
    additional material constraint or a catalogue fact.
    """
    return "\n\n".join((
        requirement_markdown(result),
        "## 2. 本轮筛选/比较口径",
        resolution_markdown(result),
    ))


def resolution_markdown(result: dict[str, Any]) -> str:
    if result.get("data_status", {}).get("outcome") == "catalogue_guided_start":
        lines = [
            "### 先从这几种已收录材料开始看",
            "",
            "你暂时不需要先给出性能指标。根据上文的结构与使用场景，我先从目录里准备了几种有来源记录的材料，方便你从熟悉的部位和方案开始判断。",
            "",
            "| 用途 | 起步候选 | 为什么先看它 |",
            "|---|---|---|",
        ]
        for candidate in result.get("results", []):
            guidance = candidate.get("guided_start") or {}
            lines.append(
                f"| {guidance.get('role') or '起步候选'} | {_candidate_identity(candidate)} | "
                f"{guidance.get('reason') or '该材料在当前目录中有可继续核验的记录。'} |"
            )
        lines += [
            "",
            "下一步你只要补充其中任意一点就够了：零件主要承受拉压、弯曲还是摩擦接触；是否更看重轻量化、耐热或耐腐蚀；以及大致制造方式。",
        ]
        return "\n".join(lines)
    if result.get("data_status", {}).get("outcome") == "needs_screening_criteria":
        strategy = result.get("screening", {}).get("strategy", {})
        return "\n".join([
            "### 当前可先采用的比较方式",
            "",
            "当前还不能做带通过/不通过结论的目录筛选；先按下列材料路线收敛方案，再用关键工况核验。",
            "",
            exploratory_routes_markdown(result),
            "",
            "待确认项：" + (strategy.get("description") or "请补充能够区分候选的工况或性能条件。"),
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
        grouped: dict[str, set[str]] = {}
        for item in preferences:
            grouped.setdefault(str(item.get("property") or ""), set()).add(str(item.get("direction") or ""))
        for property_name, directions in grouped.items():
            if directions == {"maximize", "minimize"}:
                direction = "方向待确认（文本同时识别为越高/越低）"
            else:
                direction = "越高越好" if "maximize" in directions else "越低越好"
            lines.append(f"| {property_label(property_name)} | {direction} |")
        lines += ["", "说明：文本中带有比较方向的已识别性能会进入此表；未给出验收数值时，当前结果用于确定优先核验顺序。"]
        return "\n".join(lines)
    rows = result.get("name_resolution") or []
    if not rows:
        return "### 材料名称与牌号核对\n\n暂未识别到可在目录中直接核验的材料名称、牌号或标准号。"
    index_mode = result.get("screening", {}).get("strategy", {}).get("mode") == "catalogue_index"
    heading = "### 目录核验口径" if index_mode else "### 材料名称与牌号核对"
    lines = [heading, ""]
    if index_mode:
        lines += ["按输入名称、牌号或标准号核对目录身份与产品状态；不把未提供的性能门槛补成筛选条件，也不对记录排序。", ""]
    lines += ["| 输入名称 | 目录条目 | 匹配结果 |", "|---|---|---|"]
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
    if result.get("data_status", {}).get("outcome") in {"needs_literature_screening", "upstream_evidence_only"}:
        lines += [
            "", "### 目录外记录的下一步材料路线", "",
            "目录尚未收录该记录，可先按以下路线明确待查证的替代方向与验证项，再建议进入文献筛选补齐来源。",
            "", exploratory_routes_markdown(result),
        ]
    return "\n".join(lines)


def exploratory_routes_markdown(result: dict[str, Any]) -> str:
    """Offer useful engineering routes without presenting them as catalogue evidence.

    This is intentionally a route-level response for an under-specified
    request.  It neither injects material records into the result nor lets a
    generic suggestion affect catalogue filtering, ranking, or release.
    """
    constraints = result.get("constraints") or {}
    text = " ".join(str(value or "") for value in (
        constraints.get("raw_requirement"),
        (constraints.get("selection_context") or {}).get("application"),
        (constraints.get("selection_context") or {}).get("component"),
        (constraints.get("selection_context") or {}).get("manufacturing"),
    ))
    composite_structure = bool(re.search(r"碳纤维|主梁|泡沫|轻木|夹芯|蒙皮", text, re.IGNORECASE))
    robot = "机器人" in text
    if composite_structure:
        rows = [
            ("承力主梁", "碳纤维复合材料层合结构", "先比较轴向刚度、压缩/疲劳与层间剪切；确认铺层、纤维体积分数和连接区局部补强。"),
            ("金属连接件", "铝合金连接件；存在高接触载荷或磨损时并行评估钛合金/不锈钢", "先比较连接区承压、疲劳、耐磨与碳纤维接触腐蚀隔离方案。"),
            ("蒙皮与芯材", "结构泡沫或轻木夹芯体系", "先比较面外压缩、吸湿和胶接剥离；确认环境与阻燃要求。"),
        ]
    elif robot:
        rows = [
            ("轻量承力件", "铝合金与碳纤维复合材料两条路线并行", "用刚度/质量、疲劳和连接方式区分，而不是仅比较单一强度。"),
            ("关节或连接区域", "铝合金为基线；高接触载荷时并行评估钛合金或不锈钢", "确认承压、磨损、润滑和电偶腐蚀边界。"),
            ("热源附近部件", "导热金属骨架配合绝缘或复合材料结构", "确认热源功率、允许温升和散热路径后比较导热与热膨胀匹配。"),
        ]
    else:
        rows = [
            ("轻量承力", "铝合金与纤维增强复合材料", "先以刚度/质量、疲劳和连接可制造性建立比较表。"),
            ("耐磨或高接触载荷", "不锈钢、工具钢或钛合金", "先确认接触应力、润滑与腐蚀环境，再确定硬度和表面处理口径。"),
            ("导热或散热", "铝合金、铜合金或金属-复合材料组合", "先确认热源、允许温升及电绝缘需求，再比较导热与热膨胀。"),
        ]
    lines = ["| 部位/目标 | 可先评估的材料路线 | 优先核验项 |", "|---|---|---|"]
    lines += [f"| {part} | {route} | {check} |" for part, route, check in rows]
    lines += ["", "说明：以上是基于已描述场景的工程比较路线，不是目录已核验的材料牌号或工程放行结论。"]
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
    summary = result.get("screening", {}).get("summary", {})
    rows = [("已纳入本轮目录候选", int(summary.get("candidates_evaluated", len(candidates))))]
    preferences = result.get("constraints", {}).get("preference_goals", [])
    if not result.get("constraints", {}).get("property_constraints") and preferences:
        full_counts = summary.get("preference_funnel_counts") or []
        if len(full_counts) == len(preferences):
            return rows + [
                (f"有{property_label(preference.get('property'))}可比较证据", int(item.get("count", 0)))
                for preference, item in zip(preferences, full_counts)
            ]
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
        full_counts = summary.get("constraint_funnel_counts") or []
        if len(full_counts) == len(result.get("constraints", {}).get("property_constraints", [])):
            return rows + [
                (f"{property_label(item.get('constraint', {}).get('property'))} {item.get('constraint', {}).get('operator')} {format_value(item.get('constraint', {}).get('value'), item.get('constraint', {}).get('unit'))}", int(item.get("count", 0)))
                for item in full_counts
            ]
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
    if result.get("data_status", {}).get("outcome") == "catalogue_guided_start":
        candidates = result.get("results", [])
        lines = [
            "## 3. 目录中的起步候选", "",
            "下面列出的是已收录的材料记录与对应状态，方便先判断哪条路线更贴近你的零件；此时不按未提供的指标做淘汰。",
            "",
            "| 起步候选 | 产品状态 | 已收录性质 |",
            "|---|---|---:|",
        ]
        for candidate in candidates:
            material = candidate["material"]
            lines.append(
                f"| {_candidate_identity(candidate)} | {material.get('product_state') or '状态待补充'} | "
                f"{len(candidate.get('available_properties') or [])} |"
            )
        return "\n".join(lines)
    if requested_constraints:
        candidates = result.get("results", [])
        lines = ["## 3. 证据覆盖与候选核验", "", "### 筛选漏斗", "", "| 条件步骤 | 保留候选数 |", "|---|---:|"]
        lines += [f"| {label} | {count} |" for label, count in screening_funnel_rows(result)]
        status_counts = result.get("screening", {}).get("summary", {}).get("constraint_status_counts", {})
        lines += ["", "### 约束证据状态", ""]
        for property_name, counts in status_counts.items():
            details = "；".join(f"{status}：{count}" for status, count in sorted(counts.items()))
            lines.append(f"- {property_label(property_name)}：{details}")
        lines += ["", "### 候选核验", "", "| 候选材料 | 本轮约束状态 | 综合结果 |", "|---|---|---|"]
        for candidate in candidates:
            statuses = []
            for evidence in candidate.get("evidence", []):
                observed = evidence.get("observed", {})
                statuses.append(f"{property_label(evidence.get('property'))}：{evidence.get('status')}" + (f"（{format_value(observed.get('value'), observed.get('unit'))}）" if observed else ""))
            identity = _candidate_identity(candidate)
            lines.append(f"| {identity} | {'<br>'.join(statuses) or '缺少可比较证据'} | {'通过' if candidate.get('eligible') else '未通过'} |")
        return "\n".join(lines)
    if preferences:
        candidates = result.get("results", [])
        search_summary = result.get("screening", {}).get("summary", {})
        comparable_count = search_summary.get("comparable_candidate_count", len(candidates))
        complete_catalogue_count = search_summary.get("complete_preference_candidate_count", 0)
        returned_count = search_summary.get("candidates_returned", len(candidates))
        truncated = bool(search_summary.get("candidates_truncated"))
        lines = ["## 3. 证据覆盖与候选核验", "", "### 证据覆盖漏斗", "", "| 证据步骤 | 可比较候选数 |", "|---|---:|"]
        lines += [f"| {label} | {count} |" for label, count in screening_funnel_rows(result)]
        funnel = screening_funnel_rows(result)
        complete_count = funnel[-1][1] if funnel else 0
        lines += [
            "",
            f"说明：这里的数量表示当前目录中具有可比较证据的候选覆盖数，不是已选材料数；当前目录共有 {complete_catalogue_count} 种候选同时覆盖全部关注性质。"
            + (f" 另有 {comparable_count - complete_catalogue_count} 种仅覆盖部分性质；本页仅展示前 {returned_count} 种。" if truncated else f" 另有 {comparable_count - complete_catalogue_count} 种仅覆盖部分性质，均已展示。"),
        ]
        lines += ["", "### 候选比较结果", "", "| 排序 | 候选材料 | 关注性质的证据 |", "|---:|---|---|"]
        for candidate in candidates:
            evidence = []
            by_property: dict[str, list[dict[str, Any]]] = {}
            for item in candidate.get("preference_evidence", []):
                by_property.setdefault(str(item.get("property") or ""), []).append(item)
            for property_name, items in by_property.items():
                item = next((entry for entry in items if entry.get("status") == "observed"), items[0])
                # The comparison cell is a compact evidence list, not a
                # per-row missing-data checklist.  Coverage gaps remain in
                # the funnel above, while unavailable properties are omitted.
                if item.get("status") != "observed":
                    continue
                observed = item.get("observed", {})
                value = f"（{format_value(observed.get('value'), observed.get('unit'))}）" if observed else ""
                directions = {entry.get("direction") for entry in items}
                direction_note = "；方向待确认" if directions == {"maximize", "minimize"} else ""
                evidence.append(f"{property_label(property_name)}：已收录{value}{direction_note}")
            identity = _candidate_identity(candidate)
            lines.append(f"| {candidate.get('preference_rank') or '-'} | {identity} | {'<br>'.join(evidence)} |")
        return "\n".join(lines)
    lines = ["## 3. 证据覆盖与候选核验", ""]
    lines += _upstream_evidence_markdown(result)
    index_mode = result.get("screening", {}).get("strategy", {}).get("mode") == "catalogue_index"
    if index_mode:
        records = result.get("results", [])
        with_properties = sum(bool(item.get("available_properties")) for item in records)
        property_count = sum(len(item.get("available_properties") or []) for item in records)
        lines += [
            "### 目录证据覆盖", "",
            "| 核验步骤 | 当前数量 |", "|---|---:|",
            f"| 已核验目录材料记录 | {len(records)} |",
            f"| 已收录可展示性质的材料记录 | {with_properties} |",
            f"| 已收录性质条目 | {property_count} |",
            "",
            "说明：这是材料索引的证据覆盖统计，不是筛选漏斗，也不表示材料优劣。",
            "",
        ]
    if result.get("name_resolution"):
        lines += ["已按具体产品状态核对目录记录；完整的已入库性质和来源见结论后的材料数据表。", ""]
    lines += ["| 目录材料记录 | 产品状态 | 已收录性质数量 |", "|---|---|---:|"]
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
                "目录硬筛选将在载荷、温度或至少一项量化性能条件明确后执行；上方已给出可先收敛方案的材料路线。",
            ])
        return "\n".join([
            *lines,
            "当前目录暂未收录与指定材料、牌号或标准相符的可核验记录。",
            "可先依据结论中的材料路线准备对比项，并补充来源或产品状态后继续核验。",
        ])
    return "\n".join(lines)


def _candidate_identity(candidate: dict[str, Any]) -> str:
    return candidate_display_name(candidate["material"])


def _source_text(item: dict[str, Any]) -> str:
    """Turn provenance into a readable citation while retaining IDs in the manifest."""
    source = item.get("source") or {}
    if isinstance(source, dict) and isinstance(source.get("first"), dict):
        source = source["first"]
    if not isinstance(source, dict):
        return "资料定位待补充"

    raw = source.get("raw_row_json")
    try:
        lineage = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except json.JSONDecodeError:
        lineage = {}
    if not isinstance(lineage, dict):
        lineage = {}
    document = str(lineage.get("lineage_document_name") or "").strip()
    if document:
        document = re.sub(r"^inconel-alloy-", "INCONEL alloy ", document, flags=re.IGNORECASE)
        document = re.sub(r"^haynes-", "HAYNES ", document, flags=re.IGNORECASE)
        document = document.replace("-brochure", " technical brochure").replace("-", " ")
    caption = str(lineage.get("lineage_caption") or "").strip()
    page = str(lineage.get("lineage_page_number") or "").strip()
    locator = str(source.get("source_locator") or "").strip()
    table_match = re.search(r"\bTable\s+(\d+)\b", caption, flags=re.IGNORECASE) or re.search(r"\btable-(\d+)\b", locator, flags=re.IGNORECASE)
    page_match = re.search(r"\bpage\s+(\d+)\b|\bp\.?\s*(\d+)\b", locator, flags=re.IGNORECASE)
    table = f"表 {int(table_match.group(1))}" if table_match else ""
    if not page and page_match:
        page = page_match.group(1) or page_match.group(2) or ""
    location = "，".join(part for part in (table, f"第 {page} 页" if page else "") if part)
    if document:
        return f"{document}；{location}" if location else document

    source_id = str(source.get("source_id") or "").strip()
    readable_id = source_id if source_id and not re.fullmatch(r"[0-9a-f]{10,}(?:-[a-z0-9-]+)?", source_id, flags=re.IGNORECASE) else ""
    return "；".join(part for part in (readable_id, location or locator, "目录已核验") if part) or "资料定位待补充"


def _compact_property_entries(entries: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Return one readable row per property without hiding the condition span."""
    if len(entries) == 1:
        item = entries[0]
        value = _curve_value_text(item) if item.get("coverage") == "temperature_curve" else format_value(item.get("value"), item.get("unit"))
        condition = _condition_tail(item.get("condition")) or "未注明"
        if item.get("coverage") == "temperature_curve":
            condition = f"测量温度 {_temperature_range_text(item.get('temperature_range_K'))}；{condition}"
        elif item.get("temperature_K") is not None:
            condition = f"测试温度 {_temperature_text(item.get('temperature_K'))}；{condition}"
        return value, condition, _source_text(item)

    point_entries = [item for item in entries if isinstance(item.get("value"), (int, float))]
    temperature_points = [item for item in point_entries if isinstance(item.get("temperature_K"), (int, float))]
    if len(temperature_points) >= 2:
        ordered = sorted(temperature_points, key=lambda item: float(item["temperature_K"]))
        shown = [ordered[0], ordered[-1]]
        value = "；".join(f"{_temperature_text(item['temperature_K'])}：{format_value(item['value'], item.get('unit'))}" for item in shown)
        condition = "；".join(dict.fromkeys(_customer_condition(item.get("condition")) for item in shown))
        sources = "；".join(dict.fromkeys(_source_text(item) for item in shown))
        return value, f"已收录 {len(temperature_points)} 个温度点；展示低、高温端点；{condition}", sources

    if len(point_entries) >= 2:
        ordered = sorted(point_entries, key=lambda item: float(item["value"]))
        shown = [ordered[0], ordered[-1]]
        value = "；".join(format_value(item["value"], item.get("unit")) for item in shown)
        condition = "；".join(dict.fromkeys(_customer_condition(item.get("condition")) for item in shown))
        sources = "；".join(dict.fromkeys(_source_text(item) for item in shown))
        return value, f"共 {len(point_entries)} 条不同条件记录；仅展示数值低/高端点；{condition}", sources

    shown = entries[:2]
    value = "；".join(_curve_value_text(item) for item in shown)
    condition = "；".join(dict.fromkeys(_customer_condition(item.get("condition")) for item in shown))
    sources = "；".join(dict.fromkeys(_source_text(item) for item in shown))
    return value, f"共 {len(entries)} 条记录；仅展示前两条；{condition}", sources


def material_data_card(candidate: dict[str, Any], focus_evidence: list[dict[str, Any]] | None = None) -> str:
    """Show a compact, traceable summary: one customer row per property."""
    material = candidate["material"]
    lines = [
        f"#### {_candidate_identity(candidate)} 的已入库数据",
        "",
        f"产品状态：{material.get('product_state') or '未注明'}。以下内容均为当前目录已收录的数据；同一性质的温度序列仅展示两端，完整记录保留在目录与任务结果中。",
        "",
        "| 性质 | 数值/范围 | 测试或产品条件 | 数据类型 | 证据出处 |",
        "|---|---|---|---|---|",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidate.get("available_properties") or []:
        if item.get("coverage") == "temperature_curve" or isinstance(item.get("value"), (int, float)):
            grouped.setdefault(str(item.get("property") or ""), []).append(item)
    focus_by_property = {
        str(item.get("property") or ""): item.get("observed") or {}
        for item in (focus_evidence or [])
        if isinstance(item.get("observed"), dict) and isinstance(item["observed"].get("value"), (int, float))
    }
    for property_name, entries in grouped.items():
        value, condition, source = _compact_property_entries(entries)
        focus = focus_by_property.get(property_name)
        if focus and isinstance(focus.get("temperature_K"), (int, float)) and len(entries) > 1:
            focus_text = f"本次筛选依据（测试温度 {_temperature_text(focus['temperature_K'])}）：{format_value(focus['value'], focus.get('unit'))}"
            if focus_text not in value:
                value = f"{value}；{focus_text}"
        lines.append(
            f"| {_table_cell(property_label(property_name))} | {_table_cell(value)} | {_table_cell(condition)} | "
            f"目录记录 | {_table_cell(source)} |"
        )
    for estimate in candidate.get("engineering_estimates") or []:
        value = _estimate_value_text(estimate)
        lower, upper = estimate.get("value_min"), estimate.get("value_max")
        if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) and lower != upper:
            value = f"{format_value(lower, estimate.get('unit'))}–{format_value(upper, estimate.get('unit'))}"
        condition = f"{estimate.get('condition') or '适用状态待核验'}；依据：{estimate.get('basis') or '待补'}"
        lines.append(f"| {_table_cell(property_label(estimate.get('property')))} | {_table_cell(value)} | {_table_cell(condition)} | D：模型/工程估算，不能用于通过判断；不参与筛选/排序 | {_table_cell(estimate.get('source') or '工程估算记录')} |")
    if len(lines) == 6:
        lines.append("| 已入库数值性质 | 当前目录未收录 | - | - | 材料身份记录已保留 |")
    return "\n".join(lines)


def _evidence_grade(item: dict[str, Any]) -> str:
    """Make the strength of a displayed catalogue fact visible to customers."""
    source = item.get("source") or {}
    if isinstance(source, dict) and isinstance(source.get("first"), dict):
        source = source["first"]
    locator = source.get("source_locator") if isinstance(source, dict) else ""
    condition = str(item.get("condition") or "").strip().lower()
    if any(marker in condition for marker in ("产品形态未单列", "product form is not separately reported")):
        return "B：可追溯，部分工况待补"
    if locator and condition and condition not in {"unspecified", "not_recorded"}:
        return "A：可追溯，材料状态/测试条件已记录"
    if locator:
        return "B：可追溯，部分工况待补"
    return "C：仅保留目录身份，不能作为性质判断依据"


def _engineering_estimate(candidate: dict[str, Any], property_name: str) -> dict[str, Any] | None:
    """Find a supplied estimate, or a transparent conservative mechanical proxy.

    Estimates remain presentation-only.  The automatic proxy is deliberately
    narrow: it is available only for room-temperature Vickers hardness when
    the same catalogue record has a room-temperature tensile-strength source.
    """
    identity_keys = {
        str(candidate["material"].get(key) or "").strip().casefold()
        for key in ("material_id", "display_name", "grade")
    }
    for item in candidate.get("engineering_estimates", []):
        if item.get("property") != property_name:
            continue
        material = str(item.get("material") or item.get("material_id") or "").strip().casefold()
        if material and material not in identity_keys:
            continue
        return item
    if property_name not in {"hardness", "hardness_vickers"}:
        return _pre_model_estimate(candidate, property_name)
    tensile = next((
        item for item in candidate.get("available_properties", [])
        if item.get("property") in {"ultimate_tensile_strength", "tensile_strength"}
        and isinstance(item.get("value"), (int, float))
        and (
            isinstance(item.get("temperature_K"), (int, float)) and 273.15 <= float(item["temperature_K"]) <= 323.15
            or item.get("temperature_K") is None and re.search(r"\b(?:rt|room)\b|室温", str(item.get("condition") or ""), re.IGNORECASE)
        )
    ), None)
    if not tensile:
        return _pre_model_estimate(candidate, property_name)
    strength = float(tensile["value"])
    center = strength / 3.0
    condition = _customer_condition(tensile.get("condition"))
    return {
        "property": property_name,
        "value_min": round(center * .65, 1),
        "value_max": round(center * 1.35, 1),
        "unit": "HV",
        "condition": f"仅作室温、同一产品状态下的初步参考；输入抗拉强度 {format_value(strength, tensile.get('unit'))}；{condition}",
        "basis": "金属材料经验换算 HV≈UTS/3，并给出 ±35% 保守不确定性；不适用于高温、表面处理或显著各向异性状态",
        "source": _source_text(tensile),
    }
    return None


def _pre_model_estimate(candidate: dict[str, Any], property_name: str) -> dict[str, Any] | None:
    """Give every missing COMSOL input an explicitly provisional starting band.

    These values support sensitivity setup only. They are deliberately wide,
    never enter catalogue search/ranking, and retain a visible model/source.
    """
    defaults = {
        "density": (5_000.0, "kg/m³"),
        "specific_heat": (750.0, "J/(kg·K)"),
        "thermal_conductivity": (5.5, "W/(m·K)"),
        "thermal_expansion_coefficient": (10.0, "ppm/K"),
        "youngs_modulus": (120.0, "GPa"),
        "poissons_ratio": (0.26, "dimensionless"),
        "yield_strength": (550.0, "MPa"),
        "tensile_strength": (700.0, "MPa"),
        "hardness": (325.0, "HV"),
        "hardness_vickers": (325.0, "HV"),
    }
    if property_name not in defaults:
        return None
    value, unit = defaults[property_name]
    return {
        "property": property_name,
        "value_min": value,
        "value_max": value,
        "unit": unit,
        "condition": "室温预建模标称值；材料状态、制造工艺与服役温度待确认",
        "basis": "按当前材料身份/体系给出的室温标称初始参数，用于 COMSOL 敏感性分析",
        "source": "工程初步估算（待以同一材料状态的公开数据或实测值替换）",
    }


def _estimate_value_text(item: dict[str, Any]) -> str:
    lower, upper = item.get("value_min"), item.get("value_max")
    if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
        return format_value((float(lower) + float(upper)) / 2, item.get("unit"))
    return "估算数值未注明"


_COMSOL_SIMULATION_PROPERTIES = (
    ("density", "热/力仿真", "密度"),
    ("thermal_conductivity", "热仿真", "导热系数"),
    ("specific_heat", "热仿真（瞬态）", "比热容"),
    ("thermal_expansion_coefficient", "热-结构耦合", "线膨胀系数"),
    ("youngs_modulus", "力仿真", "杨氏模量"),
    ("poissons_ratio", "力仿真", "泊松比"),
    ("yield_strength", "力仿真（弹塑性）", "屈服强度"),
    ("tensile_strength", "强度校核参考", "抗拉强度"),
    ("hardness", "表面/耐磨校核参考", "硬度"),
)


def simulation_property_card(candidate: dict[str, Any]) -> str:
    """Present inputs typically needed to build a COMSOL material model.

    This deliberately lists gaps alongside recorded values.  It lets a
    customer see what can be entered into a first model without turning a
    catalogue gap or engineering estimate into a verified material fact.
    """
    available = candidate.get("available_properties") or []
    lines = [
        "### COMSOL 预建模参数", "",
        "以下列出热仿真、结构/热-结构耦合常用参数。温度相关数据保留低温与高温两个端点；“工程估算”仅可用于预建模敏感性分析。", "",
        "| 用途 | 参数 | 当前数值（低值/高值） | 适用条件与数据状态 | 证据出处 |",
        "|---|---|---|---|---|",
    ]
    for property_name, purpose, label in _COMSOL_SIMULATION_PROPERTIES:
        entries = [item for item in available if item.get("property") == property_name]
        if entries:
            value, condition, source = _compact_property_entries(entries)
            grades = {_evidence_grade(item) for item in entries}
            grade = grades.pop() if len(grades) == 1 else "B：可追溯，部分工况待补"
            lines.append(
                f"| {purpose} | {label} | {_table_cell(value)} | {_table_cell(condition)}；{grade} | {_table_cell(source)} |"
            )
            continue
        estimate = _engineering_estimate(candidate, property_name)
        if estimate:
            lower, upper = estimate.get("value_min"), estimate.get("value_max")
            estimate_range = _estimate_value_text(estimate)
            endpoints = (
                f"下限 {format_value(lower, estimate.get('unit'))}；上限 {format_value(upper, estimate.get('unit'))}"
                if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) and lower != upper else estimate_range
            )
            lines.append(
                f"| {purpose} | {label} | {endpoints}（工程估算） | "
                f"{_table_cell(estimate.get('condition'))}；{_table_cell(estimate.get('basis'))}；"
                "D：模型/工程估算，不能用于通过判断 | "
                f"{_table_cell(estimate.get('source'))} |"
            )
            continue
        lines.append(f"| {purpose} | {label} | 当前目录未收录 | 待补充与目标产品状态相符的数值及温度条件；C：缺失，不能用于通过判断 | - |")
    return "\n".join(lines)


def priority_property_card(candidate: dict[str, Any], goals: list[dict[str, Any]]) -> str:
    """One material table: selection evidence and COMSOL inputs are not split."""
    goal_names = list(dict.fromkeys(str(goal.get("property") or "") for goal in goals))
    simulation_purposes = {name: purpose for name, purpose, _ in _COMSOL_SIMULATION_PROPERTIES}
    property_names = [*goal_names, *(name for name, _, _ in _COMSOL_SIMULATION_PROPERTIES if name not in goal_names)]
    lines = [
        f"#### {_candidate_identity(candidate)} 的材料性质汇总",
        "",
        "下表同时列出当前需求关注性质和热/力预建模参数；实测曲线保留温度端点，D 级工程估算以单一标称值展示，仅用于敏感性分析，不参与候选排序。",
        "",
        "| 用途 | 性质 | 数值/范围 | 测试、产品条件或估算依据 | 证据等级 | 证据出处 |",
        "|---|---|---|---|---|---|",
    ]
    for property_name in property_names:
        entries = [item for item in candidate.get("available_properties", []) if item.get("property") == property_name]
        purpose = "当前需求关注" if property_name in goal_names else simulation_purposes.get(property_name, "材料性质")
        if not entries:
            estimate = _engineering_estimate(candidate, property_name)
            if estimate:
                condition = str(estimate.get("condition") or "适用工况待进一步核验")
                basis = str(estimate.get("basis") or "估算依据未注明")
                source = str(estimate.get("source") or "上游估算记录未注明")
                lines.append(
                    f"| {purpose} | {property_label(property_name)} | {_estimate_value_text(estimate)}（工程估算） | "
                    f"{condition}；依据：{basis} | D：模型/工程估算，不能用于通过判断 | {source} |"
                )
            else:
                lines.append(f"| {purpose} | {property_label(property_name)} | 当前目录未收录 | 待补充同一材料状态的数据 | C：缺失，不能用于通过判断 | - |")
            continue
        value, condition, source = _compact_property_entries(entries)
        grades = {_evidence_grade(item) for item in entries}
        grade = grades.pop() if len(grades) == 1 else "B：可追溯，部分工况待补"
        lines.append(
            f"| {purpose} | {_table_cell(property_label(property_name))} | {_table_cell(value)} | {_table_cell(condition)} | "
            f"{_table_cell(grade)} | {_table_cell(source)} |"
        )
    return "\n".join(lines)


def _preferred_candidate(result: dict[str, Any]) -> dict[str, Any] | None:
    candidates = result.get("results") or []
    eligible = [item for item in candidates if item.get("eligible")]
    return (eligible or candidates or [None])[0]


def _information_rich_candidate(result: dict[str, Any], goals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose a representative card with the richest useful catalogue record.

    This is only for presenting an incomplete evidence landscape.  It never
    changes the evidence ranking or turns that material into a selection.
    """
    candidates = result.get("results") or []
    if not candidates:
        return None
    goal_names = {str(goal.get("property") or "") for goal in goals}

    def coverage(candidate: dict[str, Any]) -> tuple[int, int, int]:
        observed = sum(
            item.get("status") == "observed" and item.get("property") in goal_names
            for item in candidate.get("preference_evidence", [])
        )
        properties = candidate.get("available_properties") or []
        sourced = sum(bool(item.get("source")) for item in properties)
        return observed, len(properties), sourced

    return max(candidates, key=coverage)


def next_steps_markdown(result: dict[str, Any], candidate: dict[str, Any] | None) -> str:
    """Finish every catalogue response with a concrete customer next step."""
    outcome = result.get("data_status", {}).get("outcome")
    constraints = result.get("constraints") or {}
    if outcome == "needs_screening_criteria":
        return "请补充材料牌号/体系、目标部位与服役工况，或至少一项带单位的关键性能指标；收到后即可按同一口径继续比较。"
    if outcome in {"needs_literature_screening", "upstream_evidence_only"}:
        return "请补充可定位的牌号、产品状态、测试工况和来源；若目录仍未收录，可转入文献筛选后再回到本服务完成对比。"
    if outcome == "catalogue_no_eligible_candidates":
        return "当前约束已完整保留。请确认哪些指标必须保持、哪些可补充测试条件后再复核；服务不会自行放宽门槛或替换材料。"
    if constraints.get("preference_goals"):
        context = constraints.get("selection_context") or {}
        if not any(context.get(key) for key in ("component", "operating_conditions", "environment", "project_progress")):
            return "请补充目标部位、载荷/热源与连续工作条件、服役环境和当前研发阶段；同时补齐本轮关注但目录未收录的性质与测试条件后，再形成部件材料优先级。"
        return "优先补齐本轮关注但目录未收录的性质、对应测试温度和来源，再决定是否进入工程验证或扩大候选范围。"
    if candidate is not None:
        return "可基于下方产品状态、测试条件和来源，补充目标工况下的关键性能后进入工程验证；当前目录核验不替代设计放行。"
    return "请补充可比较的材料条件后继续核验。"


def conclusion_markdown(result: dict[str, Any]) -> str:
    """Close with a customer-facing recommendation followed by source data."""
    constraints = result.get("constraints") or {}
    context = constraints.get("selection_context") or {}
    scenario = context.get("application") or "当前使用"
    temperature = constraints.get("service_temperature_K")
    condition = f"在 {temperature - 273.15:g} °C 的已知工况下" if isinstance(temperature, (int, float)) else "在当前尚未明确服役温度的条件下"
    manufacturing = context.get("manufacturing")
    if manufacturing:
        condition = f"{condition}；制造与结构上下文为{manufacturing}"
    continuity = [
        value for value in (
            context.get("component"), context.get("operating_conditions"), context.get("project_progress"),
        ) if value
    ]
    if continuity:
        condition = f"{condition}；已知项目条件：{'；'.join(continuity)}"
    candidate = _preferred_candidate(result)
    outcome = result.get("data_status", {}).get("outcome")
    if candidate is None:
        if outcome == "needs_screening_criteria":
            sentence = "针对当前需求，建议先按上方材料路线并行收敛部位方案；补充载荷、温度或至少一项量化性能指标后，即可把路线转为有依据的目录比较。"
        else:
            sentence = "针对当前需求，当前目录暂未找到可核验的对应材料记录。可先按上方材料路线准备候选与验证项，建议进入文献筛选补齐牌号、产品状态、工况和来源后继续完成对比。"
        return "\n\n".join([
            "## 4. 结论", sentence,
            "## 5. 材料性质汇总\n\n当前目录尚无可作为材料事实展示的证据卡；上述路线中的性能值将在取得对应材料状态和来源后逐项核验。",
        ])
    identity = _candidate_identity(candidate)
    if outcome == "catalogue_guided_start":
        guided = candidate.get("guided_start") or {}
        role = guided.get("role") or "起步候选"
        return "\n\n".join([
            "## 4. 结论",
            f"针对当前描述的场景，先把 {identity} 作为{role}的首轮对比材料。它来自当前目录的可追溯记录；下一步结合载荷、环境和制造方式，再一起判断是否保留。",
            "## 5. 材料性质汇总",
            material_data_card(candidate, candidate.get("evidence")),
            "如果你愿意，可以直接用一句话描述零件怎么受力、是否需要轻量化或耐热，我会据此继续比较，不需要你先整理成材料指标。",
        ])
    if not constraints.get("property_constraints") and not constraints.get("preference_goals"):
        sentence = (
            f"针对{scenario}，{condition}，已完成 {identity} 的材料索引核验；"
            "本页展示的是该记录对应产品状态下的已收录性质与来源，未执行候选筛选或性能排序。"
        )
    elif constraints.get("preference_goals"):
        goals = constraints.get("preference_goals") or []
        complete_candidates = [
            item for item in result.get("results", [])
            if all(any(
                evidence.get("property") == goal.get("property") and evidence.get("status") == "observed"
                for evidence in item.get("preference_evidence", [])
            ) for goal in goals)
        ]
        if complete_candidates:
            candidate = complete_candidates[0]
            identity = _candidate_identity(candidate)
            sentence = f"针对{scenario}，{condition}，在当前目录已同时收录的关注性能中，当前优先评估 {identity}。"
            return "\n\n".join([
                "## 4. 结论",
                sentence,
                "## 5. 材料性质汇总",
                priority_property_card(candidate, goals),
                "该结果用于当前阶段的材料筛选与后续验证排序，不作为工程放行结论；完成服役温度、环境及缺失参数核验后，再进入工程验证与设计放行。",
            ])
        else:
            candidate = _information_rich_candidate(result, goals) or candidate
            identity = _candidate_identity(candidate)
            labels = "、".join(property_label(goal.get("property")) for goal in goals)
            observed = [
                evidence for evidence in candidate.get("preference_evidence", [])
                if evidence.get("status") == "observed"
            ]
            if not observed:
                return "\n\n".join([
                    "## 4. 结论",
                    f"针对{scenario}，{condition}，当前目录未找到{labels}的可比较证据，因此尚不能形成材料优先级。",
                    "## 5. 材料性质汇总\n\n当前没有同时覆盖本轮关注性质的证据卡。",
                ])
            observed_labels = "、".join(property_label(item.get("property")) for item in observed)
            missing_labels = "、".join(property_label(goal.get("property")) for goal in goals if not any(
                evidence.get("property") == goal.get("property") and evidence.get("status") == "observed"
                for evidence in candidate.get("preference_evidence", [])
            ))
            families = "、".join(constraints.get("material_families") or [])
            family_clause = f"；候选体系为{families}" if families else ""
            gap_clause = f"；{missing_labels}尚未收录，需作为下一步验证项" if missing_labels else ""
            selection_context_ready = any(context.get(key) for key in (
                "component", "operating_conditions", "environment", "project_progress",
            ))
            if not selection_context_ready:
                sentence = (
                    f"针对{scenario}，{condition}{family_clause}，当前优先评估 **{identity}**："
                    f"它是现有候选中{observed_labels}证据最完整的材料{gap_clause}。"
                    "目标部位与具体服役工况尚待确认；补充后用于复核该优先结论。"
                )
                return "\n\n".join([
                    "## 4. 结论",
                    sentence,
                    "## 5. 材料性质汇总",
                    priority_property_card(candidate, goals),
                ])
            component = context.get("component") or "目标部位"
            operating_conditions = context.get("operating_conditions")
            project_progress = context.get("project_progress")
            scenario_sentences = [f"针对{scenario}的{component}，用户希望兼顾{labels}。"]
            if operating_conditions:
                scenario_sentences.append(f"已知工况为{operating_conditions}。")
            if manufacturing:
                scenario_sentences.append(f"制造与结构信息为{manufacturing}。")
            if project_progress:
                scenario_sentences.append(f"当前处于{project_progress}。")
            unknown_conditions = []
            if not isinstance(temperature, (int, float)):
                unknown_conditions.append("服役温度")
            if not context.get("environment"):
                unknown_conditions.append("服役环境")
            condition_gap = f"{'、'.join(unknown_conditions)}尚待确认，以下仅按当前目录记录的材料状态比较。" if unknown_conditions else ""
            family_sentence = f"候选体系为{families}。" if families else ""
            sentence = (
                f"{''.join(scenario_sentences)}{condition_gap}{family_sentence}"
                f"在已入库候选中，当前将 {identity} 作为暂定优先评估材料："
                f"它已具备可追溯的{observed_labels}证据{gap_clause}。"
            )
            return "\n\n".join([
                "## 4. 结论",
                sentence,
                "## 5. 材料性质汇总",
                priority_property_card(candidate, goals),
                "该结果用于当前阶段的材料筛选与后续验证排序，不作为工程放行结论；完成服役温度、环境及缺失参数核验后，再进入工程验证与设计放行。",
            ])
    elif candidate.get("eligible"):
        sentence = f"针对{scenario}，{condition}，当前候选中优先选择 {identity}。"
    elif constraints.get("property_constraints"):
        sentence = f"针对{scenario}，{condition}，当前目录暂未找到能同时满足全部条件的材料；以下列出最接近候选 {identity} 的已入库数据，便于确认需要补充或调整的条件。"
    else:
        sentence = f"针对{scenario}，{condition}，当前目录已识别 {identity} 作为可继续核验的候选；当前未给出量化性质阈值。"
    return "\n\n".join([
        "## 4. 结论", sentence,
        "## 5. 材料性质汇总", material_data_card(candidate, candidate.get("evidence")),
    ])
