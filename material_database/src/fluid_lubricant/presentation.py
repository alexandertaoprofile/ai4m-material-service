"""Report-style PNG assets generated from one deterministic query result."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/material_database_matplotlib")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.colors import to_rgb
from matplotlib.patches import Ellipse, Polygon, Rectangle

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
_CJK_FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"


def _funnel_label(step: str) -> str:
    """Mirror the report's Chinese labels without exposing query syntax."""
    import re
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
    return "获得可直接比较的电学与黏度数据" if step == "Exact transport evidence pairs" else step


def _font() -> FontProperties | None:
    return FontProperties(fname=str(_CJK_FONT)) if _CJK_FONT.is_file() else None


def _lighter(color: str, amount: float = .45) -> tuple[float, float, float]:
    """Blend a layer colour toward white for a soft 3D rim highlight."""
    red, green, blue = to_rgb(color)
    return tuple(value + (1 - value) * amount for value in (red, green, blue))


def _darker(color: str, amount: float = .18) -> tuple[float, float, float]:
    """Darken the underside of a layer to make its depth legible."""
    return tuple(value * (1 - amount) for value in to_rgb(color))


def render_assets(result: dict[str, Any], output_dir: Path, shortlist: dict[str, Any] | None = None) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = result["candidates"]
    assets: list[dict[str, str]] = []
    funnel = result["funnel"]
    font = _font()
    if funnel:
        fig, axis = plt.subplots(figsize=(10.8, max(5.2, len(funnel) * 1.18)))
        labels = [_funnel_label(item["step"]) for item in funnel]
        counts = [item["count"] for item in funnel]
        maximum = max(counts) or 1
        # Counts determine the layer scale, but a visual funnel also needs an
        # unambiguous progression: every lower layer must be visibly shorter
        # than the preceding one, including near-equal early counts.
        data_widths = [.32 + .60 * (count / maximum) ** .34 for count in counts]
        if len(counts) == 1:
            widths = data_widths
        else:
            staged_widths = [.94 - index * .58 / (len(counts) - 1) for index in range(len(counts))]
            widths = [min(data_width, staged_width) for data_width, staged_width in zip(data_widths, staged_widths)]
        palette = ("#ea9b7d", "#eaa5ad", "#d7b6d9", "#b7d98a", "#9ecce1", "#f0d780")
        layer_height, layer_gap, cap_height = .79, .35, .25
        for index, (label, count) in enumerate(zip(labels, counts)):
            top = widths[index]
            bottom = max(.20, top * .84)
            y_top = len(counts) - index * (layer_height + layer_gap)
            y_bottom = y_top - layer_height
            color = palette[index % len(palette)]
            # A tapered body plus an elliptical rim and shadowed lower edge
            # makes each stage read as a shallow conical funnel rather than a
            # flat trapezoid.  The white rim also separates adjacent stages.
            axis.add_patch(Ellipse((.5, y_bottom), width=bottom, height=cap_height, facecolor=_darker(color, .22), edgecolor="none", zorder=1))
            polygon = Polygon(
                [(.5 - top / 2, y_top), (.5 + top / 2, y_top), (.5 + bottom / 2, y_bottom), (.5 - bottom / 2, y_bottom)],
                closed=True, facecolor=color, edgecolor="none", zorder=2,
            )
            axis.add_patch(polygon)
            # A shaded right face gives the cone a slight oblique viewpoint.
            axis.add_patch(Polygon(
                [(.5 + top / 2, y_top), (.5 + bottom / 2, y_bottom), (.5 + bottom * .13, y_bottom), (.5 + top * .13, y_top)],
                closed=True, facecolor=_darker(color, .14), edgecolor="none", alpha=.22, zorder=3,
            ))
            axis.add_patch(Ellipse((.5, y_top), width=top, height=cap_height, facecolor=_lighter(color, .72), edgecolor="white", linewidth=1.4, zorder=4))
            axis.add_patch(Ellipse((.5, y_top - .012), width=top * .83, height=cap_height * .52, facecolor=_lighter(color, .20), edgecolor="none", zorder=5))
            font_size = 10 if top >= .48 else 8.5
            axis.text(.5, (y_top + y_bottom) / 2 - .035, label, ha="center", va="center", color="#263238", fontproperties=font, fontsize=font_size, fontweight="bold", zorder=6)
            # Keep the exact count outside the layer so labels stay legible.
            axis.annotate(
                f"{count:,}", xy=(.5 + bottom / 2, (y_top + y_bottom) / 2),
                xytext=(1.10, (y_top + y_bottom) / 2), ha="left", va="center",
                color="#425466", fontproperties=font, fontsize=12, fontweight="bold",
                arrowprops={"arrowstyle": "-", "color": "#94a3b8", "lw": 1.25},
            )
        bottom_y = len(counts) - (len(counts) - 1) * (layer_height + layer_gap) - layer_height
        axis.text(.5, bottom_y - .30, f"最终匹配：{counts[-1]:,} 条", ha="center", va="center", color="#175c54", fontproperties=font, fontsize=12, fontweight="bold")
        # Reserve independent header rows.  ``suptitle`` uses the glyph box
        # rather than its baseline for layout, so a larger gap is required to
        # avoid the subtitle overlapping after PNG scaling in the frontend.
        fig.suptitle("本轮筛选漏斗", x=.055, y=.982, ha="left", fontproperties=font, fontsize=17, fontweight="bold")
        fig.text(.055, .905, "每层代表本轮新增的一项筛选条件；右侧为通过该层后的证据配对数", color="#5b6472", fontproperties=font, fontsize=9.5)
        axis.set_xlim(-.02, 1.32); axis.set_ylim(bottom_y - .55, len(counts) + .46); axis.axis("off")
        fig.tight_layout(rect=(0, 0, 1, .86))
        path = output_dir / "evidence_funnel.png"; fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white"); plt.close(fig)
        assets.append({"name": "evidence_funnel", "type": "image/png", "description": "逐层展示本轮实际筛选条件及每层保留的证据配对数", "local_path": str(path)})
    points = result.get("plot_points") or []
    if points:
        fig, axis = plt.subplots(figsize=(8.8, 6.2))
        palette = {"evidence_complete_for_initial_screen": "#147d73", "flagged_for_review": "#d97706", "outside_requested_conditions": "#94a3b8"}
        labels = {
            "evidence_complete_for_initial_screen": "A 类：证据完整且数值匹配",
            "flagged_for_review": "B 类：数值匹配、待回查",
            "outside_requested_conditions": f"温度范围内其他证据（{result.get('plot_population_count', len(points)):,} 条）",
        }
        for status in ("outside_requested_conditions", "flagged_for_review", "evidence_complete_for_initial_screen"):
            subset = [item for item in points if item["status"] == status]
            if not subset:
                continue
            axis.scatter([item["dynamic_viscosity_mpa_s"] for item in subset], [item["conductivity_s_m"] for item in subset], s=16 if status == "outside_requested_conditions" else 31, alpha=.25 if status == "outside_requested_conditions" else .82, color=palette[status], label=labels[status], zorder=2 if status == "outside_requested_conditions" else 4)
        axis.set_xscale("log"); axis.set_yscale("log")
        axis.set_xlabel("动态黏度 / mPa·s（保守上界）", fontproperties=font)
        axis.set_ylabel("电导率 / S/m（保守下界）", fontproperties=font)
        axis.set_title("温度范围内：电导—黏度证据分布", loc="left", fontproperties=font, fontweight="bold")
        constraint_items = result["request"]["property_constraints"]
        viscosity_bounds = [item["value"] for item in constraint_items if item["name"] == "dynamic_viscosity"]
        viscosity_min = next((item["value"] for item in constraint_items if item["name"] == "dynamic_viscosity" and item["operator"] in {">=", ">"}), None)
        viscosity_max = next((item["value"] for item in constraint_items if item["name"] == "dynamic_viscosity" and item["operator"] in {"<=", "<"}), max(viscosity_bounds, default=150))
        conductivity_min = next((item["value"] for item in constraint_items if item["name"] == "conductivity" and item["operator"] in {">=", ">"}), None)
        conductivity_max = next((item["value"] for item in constraint_items if item["name"] == "conductivity" and item["operator"] in {"<=", "<"}), None)
        # ρ <= r creates σ >= 1/r; ρ >= r creates σ <= 1/r.  Do not collapse
        # duplicate resistivity constraints into one dict: a user may provide
        # a true interval (for example 1 < ρ < 10 Ω·m).
        for item in (item for item in constraint_items if item["name"] == "resistivity"):
            if item["operator"] in {"<=", "<"}:
                conductivity_min = 1 / item["value"]
            elif item["operator"] in {">=", ">"}:
                conductivity_max = 1 / item["value"]
        x_values = [item["dynamic_viscosity_mpa_s"] for item in points]
        y_values = [item["conductivity_s_m"] for item in points]
        x_low, x_high = min(x_values) * .8, max(x_values) * 1.3
        y_low, y_high = min(y_values) * .8, max(y_values) * 1.3
        axis.set_xlim(x_low, x_high); axis.set_ylim(y_low, y_high)
        if viscosity_min is not None:
            axis.axvline(viscosity_min, color="#dc2626", linestyle="--", linewidth=1.3, zorder=5)
        axis.axvline(viscosity_max, color="#dc2626", linestyle="--", linewidth=1.3, zorder=5)
        if conductivity_min is not None:
            axis.axhline(conductivity_min, color="#dc2626", linestyle="--", linewidth=1.3, zorder=5)
        if conductivity_max is not None:
            axis.axhline(conductivity_max, color="#dc2626", linestyle="--", linewidth=1.3, zorder=5)
        if conductivity_min is not None:
            box_left = viscosity_min if viscosity_min is not None else x_low
            box_width = viscosity_max - box_left
            box_top = min(y_high, conductivity_max) if conductivity_max is not None else y_high
            target = Rectangle(
                (box_left, conductivity_min), box_width, max(0, box_top - conductivity_min),
                facecolor="#16a34a", edgecolor="#16a34a", alpha=.16, linewidth=2.0,
                zorder=1, label="绿色区域：同时满足全部数值约束",
            )
            axis.add_patch(target)
        viscosity_text = f"硬约束：{viscosity_min:g}≤η≤{viscosity_max:g} mPa·s" if viscosity_min is not None else f"硬约束：η≤{viscosity_max:g} mPa·s"
        axis.text(.98, .04, f"{viscosity_text}\n红虚线为边界；绿色区域为同时满足区", transform=axis.transAxes, ha="right", va="bottom", color="#137166", fontproperties=font, fontsize=8.5)
        for label in [*axis.get_xticklabels(), *axis.get_yticklabels()]: label.set_fontproperties(font)
        axis.legend(prop=font, fontsize=8, loc="upper left"); axis.grid(True, which="both", alpha=.2)
        fig.tight_layout()
        path = output_dir / "transport_scatter.png"; fig.savefig(path, dpi=180, bbox_inches="tight"); plt.close(fig)
        assets.append({"name": "transport_scatter", "type": "image/png", "description": "本次结果的电导率—动态黏度散点图；颜色表示证据状态", "local_path": str(path)})
    return assets
