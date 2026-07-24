"""Human-readable streaming and chart assets for mature-material lookups."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


_CJK_FONT = Path(os.getenv(
    "MATURE_MATERIAL_CJK_FONT_PATH",
    str(Path(__file__).resolve().parents[3] / "inorganic_new_material" / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"),
))


PROPERTY_LABELS = {
    "density": "密度", "specific_heat": "比热容", "thermal_conductivity": "导热系数",
    "thermal_diffusivity": "热扩散率", "yield_strength": "屈服强度", "hardness": "硬度",
    "melting_range_low": "熔点下限", "melting_range_high": "熔点上限",
    "youngs_modulus": "杨氏模量", "shear_modulus": "剪切模量", "poissons_ratio": "泊松比",
    "beta_transus": "β 相转变温度", "electrical_resistivity_IG": "电阻率（IG）",
    "electrical_resistivity_corrected": "修正电阻率", "liquidus_temperature": "液相线温度",
    "solidus_temperature": "固相线温度", "magnetic_permeability": "相对磁导率",
    "specific_enthalpy": "比焓",
    "tensile_strength": "拉伸强度", "heat_deflection_temperature": "热变形温度",
}


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
    """Render one comparable requested property or catalogue-coverage chart."""
    requested = result.get("constraints", {}).get("property_constraints", [])
    if not requested:
        return render_catalog_coverage(result, output_dir)
    property_name = requested[0].get("property")
    values: list[tuple[str, float]] = []
    unit = requested[0].get("unit") or ""
    for candidate in result.get("results", []):
        for evidence in candidate.get("evidence", []):
            if evidence.get("property") != property_name:
                continue
            observed = evidence.get("observed", {})
            if evidence.get("status") in {"pass", "fail"} and isinstance(observed.get("value"), (int, float)):
                values.append((candidate["material"].get("display_name") or candidate["material"].get("material_id"), observed["value"]))
            if not unit:
                unit = observed.get("unit") or ""
            break
    if not values:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    labels, numbers = zip(*values)
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
    path = output_dir / f"{_safe_name(property_name)}_comparison.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def render_catalog_coverage(result: dict[str, Any], output_dir: Path) -> Path | None:
    """Show evidence coverage when the user has not selected a property to compare."""
    values = []
    for candidate in result.get("results", []):
        properties = {item.get("property") for item in candidate.get("available_properties", []) if item.get("property")}
        values.append((candidate["material"].get("display_name") or candidate["material"].get("material_id"), len(properties)))
    if not values:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    labels, counts = zip(*values)
    font = _font()
    fig, axis = plt.subplots(figsize=(8, max(3.4, 0.65 * len(values) + 1.8)))
    bars = axis.barh(labels, counts, color="#4a8f6d")
    axis.invert_yaxis()
    axis.set_xlabel("已入库且可追溯的性质种类数", fontproperties=font)
    axis.set_title("候选材料的数据覆盖度", fontproperties=font)
    for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        label.set_fontproperties(font)
    axis.grid(axis="x", alpha=0.22)
    axis.bar_label(bars, padding=4, fontsize=9)
    fig.tight_layout()
    path = output_dir / "catalog_property_coverage.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def requirement_markdown(result: dict[str, Any]) -> str:
    constraints = result["constraints"]
    names = "、".join(constraints.get("material_queries") or []) or "从需求文本提取"
    families = "、".join(constraints.get("material_families") or []) or "不限"
    temperature = constraints.get("service_temperature_K")
    temperature_text = f"{temperature - 273.15:g} °C" if isinstance(temperature, (int, float)) else "未指定"
    lines = ["### 1. 检索需求", ""]
    lines += ["| 项目 | 本轮输入 |", "|---|---|", f"| 材料名称/别名 | {names} |", f"| 材料族 | {families} |", f"| 服役温度 | {temperature_text} |", f"| 性质条件 | {len(constraints.get('property_constraints') or [])} 项 |"]
    for item in constraints.get("property_constraints") or []:
        lines.append(f"| └ {property_label(item.get('property'))} | {item.get('operator')} {format_value(item.get('value'), item.get('unit'))} |")
    return "\n".join(lines)


def resolution_markdown(result: dict[str, Any]) -> str:
    rows = result.get("name_resolution") or []
    if not rows:
        return "### 1. 材料名称核对\n\n未识别到可在目录中直接核验的材料名称、牌号或标准号。"
    lines = ["### 1. 材料名称匹配", "", "| 输入名称 | 目录条目 | 匹配结果 |", "|---|---|---|"]
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


def comparison_markdown(result: dict[str, Any]) -> str:
    lines = ["### 2. 目录核验与性质信息", ""]
    lines += _upstream_evidence_markdown(result)
    if result.get("name_resolution"):
        lines += ["以下数据按具体产品状态分组展示，便于核对数值与测试条件。", ""]
    for candidate in result.get("results", []):
        material = candidate["material"]
        identity = material.get("display_name") or material.get("material_id") or "未命名材料"
        if material.get("grade"):
            identity += f"（{material['grade']}）"
        state = material.get("product_state") or "未注明"
        lines += [f"#### {identity}", "", f"状态：{state}", "", "| 性质 | 已入库数值/范围 | 测试条件 |", "|---|---|---|"]
        for _identity, property_name, value_text, condition in _property_table_rows(candidate):
            lines.append(f"| {property_name} | {value_text} | {condition} |")
        lines.append("")
    if not result.get("results", []):
        return "\n".join([
            *lines,
            "本轮目录中未找到与指定材料、牌号或标准相符的已入库记录。",
            "为避免误导，系统不会展示或推断其他材料作为替代候选。",
        ])
    return "\n".join(lines)


def conclusion_markdown(result: dict[str, Any]) -> str:
    candidates = result.get("results", [])
    eligible = sum(bool(item.get("eligible")) for item in candidates)
    catalog_message = result.get("data_status", {}).get("message", "")
    has_property_constraints = bool(result.get("constraints", {}).get("property_constraints"))
    if not candidates:
        has_upstream_evidence = bool(result.get("constraints", {}).get("upstream_evidence"))
        next_step = (
            "上游提供的信息已原样整理，但尚无本目录可核验记录。建议先进入文献筛选，"
            "补充可追溯的材料名称/牌号、性质、测试工况与来源；随后可再次提交本服务核验。"
            if has_upstream_evidence else
            "建议进入文献筛选，收集目标材料的名称或牌号、性质数据、测试工况及来源；"
            "获得这些信息后，可再次提交本服务进行统一整理与目录核验。"
        )
        return "\n".join([
            "### 3. 本轮建议", "",
            catalog_message,
            next_step,
        ])
    if not has_property_constraints:
        return "\n".join([
            "### 3. 本轮结论", "",
            f"本轮在结构化目录中匹配到 **{len(candidates)}** 种候选。",
            "本轮未给出量化性质阈值，因此系统未声明任何候选已通过性能筛选。",
            catalog_message,
        ])
    return "\n".join([
        "### 3. 本轮结论", "",
        f"本轮在结构化目录中比较了 **{len(candidates)}** 种候选，**{eligible}** 种满足当前可比较的性质条件。",
        catalog_message,
    ])
