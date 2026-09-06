"""合金服务的共享运行时装配。

该模块让 HTTP 入口和 Alpha 角色入口使用同一套“用例 → 图表/清单资产”的执行
路径。它不处理 WebSocket，也不发送前端事件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from dotenv import load_dotenv
from matplotlib.font_manager import FontProperties
from matplotlib.ft2font import FT2Font
from matplotlib.colors import to_rgb
from matplotlib.patches import Ellipse, FancyBboxPatch, Polygon, Rectangle

from src.alloy_workflow.application import AlloyOptimizationApplication
from src.alloy_workflow.runner import HEASurrogateRunner
from src.alloy_workflow.microstructure_tendency import build_microstructure_tendency

# ``runtime`` 可能由 src/team_config.py 先于 main.py 导入；因此在装配前自行
# 加载当前服务目录的 .env，不能依赖 main.py 的导入副作用。
load_dotenv()


class AlloyRuntime:
    """装配 HEA 用例、隔离 runner 与任务展示资产。"""

    def __init__(self) -> None:
        self.results_root = Path(os.getenv("ALLOY_RESULTS_ROOT", "results/alloy_composition_optimization"))
        surrogate_root = Path(os.getenv("HEA_SURROGATE_ROOT", "/data/se42/hea_surrogate"))
        environment_prefix = Path(os.getenv("HEA_SURROGATE_ENV_PREFIX", "/data/mamba/envs/mattergen-py310"))
        self.chart_font_path = Path(os.getenv(
            "ALLOY_CJK_FONT_PATH",
            str(Path(__file__).resolve().parents[2] / "assets/fonts/NotoSansCJKsc-Regular.otf"),
        ))
        self.runner = HEASurrogateRunner(
            results_root=self.results_root,
            surrogate_root=surrogate_root,
            environment_prefix=environment_prefix,
            executable=os.getenv("MICROMAMBA_EXECUTABLE", "micromamba"),
        )
        self.application = AlloyOptimizationApplication(self.runner, "alloy-composition-optimization")

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行候选提议并生成任务级展示资产。"""
        result, constraints = self.application.propose_space(payload)
        candidates = result.get("_presentation_candidates", result.get("initial_candidates", []))
        if candidates and result.get("model_domain") == "hea_mpea":
            result["microstructure_tendency"] = build_microstructure_tendency(candidates[0])
        assets = self._render(result)
        result.pop("_presentation_candidates", None)
        taskid = constraints["taskid"]
        result["presentation"] = {
            "summary_markdown": f"/alloy/tasks/{taskid}/assets/summary.md",
            "assets": [
                {"name": name, "url": f"/alloy/tasks/{taskid}/assets/{path.name}", "type": "MaterialsPNG"}
                for name, path in assets.items() if path.suffix == ".png"
            ],
        }
        self.save(result)
        return result

    def save(self, manifest: dict[str, Any]) -> None:
        """保存可由 REST 查询的任务清单。"""
        task_dir = self.results_root / manifest["taskid"]
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _chart_font(self) -> FontProperties:
        if not self.chart_font_path.is_file():
            raise RuntimeError(f"Chinese chart font is unavailable: {self.chart_font_path}")
        chart_text = "候选筛选路径相风险通过性能与不确定性通过最终可比候选保留率候选数量强度硬度最优候选训练数据范围内边界附近范围外强度门槛硬度门槛预测屈服强度MPa元素含量at最优候选精确配方与可继续探索区间P5P50P95元素成分预测组织倾向示意非真实显微图像固溶体基体潜在第二相金属间化合物风险标记晶界混相风险数据适用域置信度模型初筛探索性单相主导较高低中等候选合金截面规则化材料图形用于表达实际相形貌尺度空间位置SSIM0123456789NiCoCrAlTiNbMoTaW—；.%（）"
        font_file = FT2Font(str(self.chart_font_path))
        missing = sorted({char for char in chart_text if not char.isspace() and not font_file.get_char_index(ord(char))})
        if missing:
            raise RuntimeError(f"Chinese chart font is missing glyphs: {''.join(missing)}")
        plt.rcParams["axes.unicode_minus"] = False
        return FontProperties(fname=str(self.chart_font_path))

    @staticmethod
    def _apply_chart_font(ax: Any, font: FontProperties) -> None:
        for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            label.set_fontproperties(font)

    def _render(self, result: dict[str, Any]) -> dict[str, Path]:
        if result.get("model_domain") == "ni_superalloy_hot_end":
            return self._render_hot_end(result)
        if result.get("model_domain") == "reusable_rocket_stainless":
            return self._render_rocket_stainless(result)
        if result.get("model_domain") == "chip_glass_thermomechanical_family_v1":
            return self._render_chip_glass(result)
        task_dir = self.results_root / result["taskid"] / "presentation"
        task_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        assets: dict[str, Path] = {}
        candidates = result.get("_presentation_candidates", result.get("initial_candidates", []))
        sampling = result.get("sampling", {})
        font = self._chart_font()

        diagnostics = sampling.get("diagnostics") or {}
        generated = int(sampling.get("generated", 0))
        stages = [
            ("生成候选", generated, "#9DC3E6"),
            ("相风险通过", int(diagnostics.get("low_phase_risk", generated)), "#6FA8DC"),
            ("性能与不确定性通过", int(diagnostics.get("property_qualified_before_domain", sampling.get("feasible", 0))), "#4F81BD"),
            ("最终可比候选", int(sampling.get("feasible", 0)), "#1F4E79"),
        ]
        fig, ax = plt.subplots(figsize=(10.8, 6.1), facecolor="#FFFFFF")
        counts = [item[1] for item in stages]
        maximum = max(counts) or 1
        # Same visual grammar as material_database's evidence funnel: count
        # determines width, while layer order remains legible when counts tie.
        data_widths = [.32 + .60 * (count / maximum) ** .34 for count in counts]
        staged_widths = [.94 - index * .58 / (len(counts) - 1) for index in range(len(counts))]
        widths = [min(data_width, staged_width) for data_width, staged_width in zip(data_widths, staged_widths)]
        palette = ("#9ecce1", "#7fb5d5", "#5d93c0", "#315f8f")
        layer_height, layer_gap, cap_height = .82, .32, .24
        for index, ((label, count, _), top) in enumerate(zip(stages, widths)):
            bottom = max(.20, top * .84)
            y_top = len(stages) - index * (layer_height + layer_gap)
            y_bottom = y_top - layer_height
            color = palette[index]
            dark = tuple(value * .78 for value in to_rgb(color))
            light = tuple(min(1, value + (1 - value) * .55) for value in to_rgb(color))
            ax.add_patch(Ellipse((.5, y_bottom), width=bottom, height=cap_height, facecolor=dark, edgecolor="none", zorder=1))
            ax.add_patch(Polygon([(.5-top/2,y_top),(.5+top/2,y_top),(.5+bottom/2,y_bottom),(.5-bottom/2,y_bottom)], closed=True, facecolor=color, edgecolor="none", zorder=2))
            ax.add_patch(Polygon([(.5+top/2,y_top),(.5+bottom/2,y_bottom),(.5+bottom*.13,y_bottom),(.5+top*.13,y_top)], closed=True, facecolor=dark, edgecolor="none", alpha=.22, zorder=3))
            ax.add_patch(Ellipse((.5, y_top), width=top, height=cap_height, facecolor=light, edgecolor="white", linewidth=1.35, zorder=4))
            ax.add_patch(Ellipse((.5, y_top-.012), width=top*.83, height=cap_height*.52, facecolor=tuple(min(1, value + (1 - value) * .2) for value in to_rgb(color)), edgecolor="none", zorder=5))
            ax.text(.5, (y_top+y_bottom)/2-.035, label, ha="center", va="center", color="#203B55", fontproperties=font, fontsize=10 if top >= .48 else 8.5, fontweight="bold", zorder=6)
            retention = count / generated if generated else 0
            ax.annotate(f"{count:,}（保留 {retention:.1%}）", xy=(.5+bottom/2, (y_top+y_bottom)/2), xytext=(1.08, (y_top+y_bottom)/2), ha="left", va="center", color="#425466", fontproperties=font, fontsize=11, fontweight="bold", arrowprops={"arrowstyle":"-", "color":"#94a3b8", "lw":1.15})
        bottom_y = len(stages) - (len(stages)-1)*(layer_height+layer_gap) - layer_height
        ax.text(.5, bottom_y-.31, f"最终可比候选：{counts[-1]:,} 个", ha="center", va="center", color="#1F4E79", fontproperties=font, fontsize=12, fontweight="bold")
        fig.suptitle("候选筛选漏斗", x=.055, y=.982, ha="left", fontproperties=font, fontsize=17, fontweight="bold")
        fig.text(.055, .905, "逐层展示生成、相风险、性能与不确定性、最终可比候选的实际保留数量", color="#5b6472", fontproperties=font, fontsize=9.5)
        ax.set_xlim(-.02, 1.38); ax.set_ylim(bottom_y-.55, len(stages)+.46); ax.axis("off")
        fig.tight_layout(rect=(0, 0, 1, .86))
        path = task_dir / "screening_funnel.png"
        fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig)
        assets["screening_funnel"] = path

        if candidates:
            fig, ax = plt.subplots(figsize=(8.2, 5.2), facecolor="#FFFFFF")
            domain_style = {"inside": ("#1F77B4", "训练数据范围内"), "boundary": ("#F28E2B", "训练数据边界附近"), "outside": ("#B07AA1", "训练数据范围外")}
            shown_domains = set()
            for index, item in enumerate(candidates):
                level = item.get("applicability_domain", {}).get("level", "outside")
                color, label = domain_style.get(level, domain_style["outside"])
                first_of_domain = level not in shown_domains
                shown_domains.add(level)
                ax.errorbar(item["yield_strength_MPa"]["mean"], item["hardness_HV"]["mean"],
                            xerr=item["yield_strength_MPa"].get("std", 0), fmt="o", color=color,
                            ecolor=color, elinewidth=.8, capsize=2, alpha=.33, zorder=2)
                ax.scatter(item["yield_strength_MPa"]["mean"], item["hardness_HV"]["mean"],
                           color=color, s=48, alpha=.82, edgecolor="white", linewidth=.6, label=label if first_of_domain else None, zorder=3)
            top = candidates[0]
            ax.scatter(top["yield_strength_MPa"]["mean"], top["hardness_HV"]["mean"], marker="*", s=260,
                       color="#D62728", edgecolor="white", linewidth=1.1, label="最优候选", zorder=5)
            ax.annotate("最优候选 01", (top["yield_strength_MPa"]["mean"], top["hardness_HV"]["mean"]),
                        xytext=(9, 10), textcoords="offset points", fontproperties=font, fontsize=10, color="#8C1D18")
            objectives = (result.get("screening_criteria") or {}).get("objectives") or {}
            if objectives.get("yield_strength_MPa", {}).get("min") is not None:
                ax.axvline(float(objectives["yield_strength_MPa"]["min"]), color="#7F7F7F", linestyle="--", linewidth=1, label="强度门槛")
            if objectives.get("hardness_HV", {}).get("min") is not None:
                ax.axhline(float(objectives["hardness_HV"]["min"]), color="#7F7F7F", linestyle=":", linewidth=1, label="硬度门槛")
            ax.legend(prop=font, frameon=True, edgecolor="#D9D9D9", loc="best")
            ax.set_xlabel("预测屈服强度（MPa）", fontproperties=font)
            ax.set_ylabel("预测硬度（HV）", fontproperties=font)
            ax.set_title("强度—硬度分布与最优候选", fontproperties=font, pad=12, fontsize=15)
            self._apply_chart_font(ax, font)
            ax.grid(alpha=.16); ax.set_axisbelow(True)
            ax.spines[["top", "right"]].set_visible(False)
            path = task_dir / "strength_hardness_tradeoff.png"
            fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig)
            assets["strength_hardness_tradeoff"] = path

            ranges = result.get("derived_candidate_percentiles_at_pct", {})
            names = list(ranges)
            fig, (ax_top, ax) = plt.subplots(2, 1, figsize=(8.2, 6.5), facecolor="#FFFFFF", gridspec_kw={"height_ratios": [1, 1.35]})
            low = [ranges[name]["p05"] for name in names]
            mid = [ranges[name]["p50"] for name in names]
            high = [ranges[name]["p95"] for name in names]
            palette = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1"]
            composition = candidates[0].get("composition_at_pct", {})
            left = 0.0
            for index, (element, amount) in enumerate(composition.items()):
                value = float(amount)
                ax_top.barh(["最优候选 01"], [value], left=left, color=palette[index % len(palette)], height=.55, label=element)
                if value >= 4:
                    ax_top.text(left + value / 2, 0, f"{element}\n{value:.1f}%", ha="center", va="center", color="white", fontsize=9, fontproperties=font)
                left += value
            ax_top.set_xlim(0, 100); ax_top.set_xlabel("元素原子百分比（at.%）", fontproperties=font)
            ax_top.set_title("最优候选精确配方与可继续探索区间", fontproperties=font, pad=12, fontsize=15)
            ax_top.legend(prop=font, ncol=min(len(composition), 6), loc="upper center", bbox_to_anchor=(.5, -0.22), frameon=False)
            ax_top.spines[["top", "right", "left"]].set_visible(False); ax_top.tick_params(axis="y", length=0)
            self._apply_chart_font(ax_top, font)
            ax.errorbar(names, mid, yerr=[np.subtract(mid, low), np.subtract(high, mid)], fmt="o", capsize=7, color="#4E79A7", linewidth=2, markersize=7)
            ax.set_ylabel("元素含量（at.%；P5—P50—P95）", fontproperties=font)
            ax.set_xlabel("元素", fontproperties=font)
            self._apply_chart_font(ax, font)
            ax.grid(axis="y", alpha=.16); ax.set_axisbelow(True)
            ax.spines[["top", "right"]].set_visible(False)
            path = task_dir / "composition_percentiles.png"
            fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig)
            assets["composition_percentiles"] = path

            tendency = result.get("microstructure_tendency") or {}
            if tendency:
                fig, (ax_micro, ax_info) = plt.subplots(1, 2, figsize=(12.4, 6.3), facecolor="#F8FAFC", gridspec_kw={"width_ratios": [1.10, .90]})
                ax_micro.set_xlim(0, 10); ax_micro.set_ylim(0, 7); ax_micro.set_aspect("equal"); ax_micro.axis("off")
                ax_micro.add_patch(FancyBboxPatch((.10, .10), 9.80, 6.80, boxstyle="round,pad=.02,rounding_size=.12", facecolor="#FFFFFF", edgecolor="#D7E2EC", linewidth=1.0, zorder=0))
                # The dark rim and bevel deliberately read as a polished alloy
                # coupon.  The interior remains an abstract probability map.
                ax_micro.add_patch(FancyBboxPatch((.36, .64), 9.28, 5.46, boxstyle="round,pad=.015,rounding_size=.08", facecolor="#3E5E7C", edgecolor="#25435F", linewidth=1.05, zorder=1))
                ax_micro.add_patch(FancyBboxPatch((.43, .72), 9.14, 5.30, boxstyle="round,pad=.015,rounding_size=.06", facecolor="#E8F0F6", edgecolor="#9AB0C4", linewidth=.8, zorder=2))
                x = np.linspace(0, 1, 520)
                y = np.linspace(0, 1, 280)
                xx, yy = np.meshgrid(x, y)
                sheen = .74 + .12 * np.exp(-((xx-.22)/.22)**2) + .06 * np.exp(-((xx-.78)/.13)**2) - .05 * yy
                metallic = np.dstack((sheen*.88, sheen*.96, np.minimum(1, sheen*1.05)))
                ax_micro.imshow(metallic, extent=(.46, 9.54, .75, 5.99), origin="lower", zorder=2.2, alpha=.54, aspect="auto")
                # Fixed tessellation: visual grammar only, not a grain-size or morphology prediction.
                grains = [
                    [(.43,.72),(2.70,.72),(2.30,2.27),(.43,2.66)], [(2.70,.72),(5.10,.72),(4.82,2.15),(2.30,2.27)],
                    [(5.10,.72),(7.52,.72),(7.27,2.43),(4.82,2.15)], [(7.52,.72),(9.57,.72),(9.57,2.72),(7.27,2.43)],
                    [(.43,2.66),(2.30,2.27),(3.05,3.90),(.43,4.39)], [(2.30,2.27),(4.82,2.15),(5.14,3.95),(3.05,3.90)],
                    [(4.82,2.15),(7.27,2.43),(7.03,4.12),(5.14,3.95)], [(7.27,2.43),(9.57,2.72),(9.57,4.62),(7.03,4.12)],
                    [(.43,4.39),(3.05,3.90),(2.51,6.02),(.43,6.02)], [(3.05,3.90),(5.14,3.95),(5.82,6.02),(2.51,6.02)],
                    [(5.14,3.95),(7.03,4.12),(8.00,6.02),(5.82,6.02)], [(7.03,4.12),(9.57,4.62),(9.57,6.02),(8.00,6.02)],
                ]
                shades = ["#DCE8F2", "#C9DBE9", "#D4E3EE", "#BED4E5", "#D8E6F0", "#C4D9E9"]
                for index, vertices in enumerate(grains):
                    ax_micro.add_patch(Polygon(vertices, closed=True, facecolor=shades[index % len(shades)], edgecolor="#5E7E9E", linewidth=1.0, zorder=3, alpha=.87))
                marker_positions = [(1.28,1.48),(3.48,1.44),(6.08,1.46),(8.54,1.70),(1.22,3.32),(3.74,3.13),(6.12,3.35),(8.60,3.71),(1.54,5.18),(3.56,5.01),(5.88,5.31),(7.65,5.11),(8.87,5.61),(4.55,5.66)]
                count = int(tendency.get("visual_marker_count", 0))
                if count:
                    points = marker_positions[:count]
                    ax_micro.scatter([item[0] for item in points], [item[1] for item in points], s=145, color="#E9A38E", alpha=.25, edgecolor="none", zorder=4)
                    ax_micro.scatter([item[0] for item in points], [item[1] for item in points], s=54, color="#C95F48", edgecolor="white", linewidth=1.2, zorder=5)
                composition = (candidates[0].get("composition_at_pct") or {}) if candidates else {}
                alloy_label = " · ".join(composition.keys()) or "HEA / MPEA"
                ax_micro.text(.45, 6.54, "候选合金截面 · 相组成倾向", fontproperties=font, fontsize=16, weight="bold", color="#193B5A")
                ax_micro.text(.45, 6.25, "规则化材料示意 · 非真实显微图像", fontproperties=font, fontsize=8.8, color="#6A7E91")
                ax_micro.add_patch(FancyBboxPatch((7.22,6.20), 2.30,.34, boxstyle="round,pad=.02,rounding_size=.10", facecolor="#E7EEF4", edgecolor="#C6D5E1", linewidth=.7, zorder=6))
                ax_micro.text(8.37, 6.29, alloy_label, ha="center", va="center", fontproperties=font, fontsize=7.8, color="#41617C", zorder=7)
                ax_micro.plot([.58, 1.10], [.38, .38], color="#5E7E9E", linewidth=1.4, zorder=5)
                ax_micro.text(1.22, .31, "晶界示意", fontproperties=font, fontsize=8.5, color="#536B82")
                ax_micro.scatter([3.02], [.38], s=50, color="#CF705A", edgecolor="white", linewidth=1.0, zorder=5)
                ax_micro.text(3.18, .31, "第二相/金属间化合物风险标记", fontproperties=font, fontsize=8.5, color="#536B82")
                ax_micro.text(.45, .08, "图形元素用于表达相风险层级，不对应实际相形貌、尺度或空间位置", fontproperties=font, fontsize=7.7, color="#7A8D9E")
                ax_info.axis("off")
                phase = tendency.get("phase_probabilities") or {}
                ax_info.add_patch(FancyBboxPatch((.02,.05), .95,.90, boxstyle="round,pad=.014,rounding_size=.025", facecolor="#FFFFFF", edgecolor="#D7E2EC", linewidth=1.0, transform=ax_info.transAxes))
                ax_info.text(.08, .88, "相组成预测解读", fontproperties=font, fontsize=16, weight="bold", color="#193B5A", transform=ax_info.transAxes)
                ax_info.text(.08, .835, "模型相分类输出的可视化解读", fontproperties=font, fontsize=8.8, color="#718497", transform=ax_info.transAxes)
                info = [("主体组织", tendency.get("title", "-")), ("混相风险", tendency.get("mixed_phase_risk", "-")), ("IM 风险", tendency.get("intermetallic_risk", "-")), ("数据适用域", tendency.get("applicability_domain", "-")), ("结果定位", tendency.get("confidence", "模型初筛"))]
                y = .735
                for label, value in info:
                    ax_info.text(.09, y, label, fontproperties=font, fontsize=9.5, color="#6A7E91", transform=ax_info.transAxes, va="center")
                    ax_info.text(.47, y, value, fontproperties=font, fontsize=11.5, color="#1E4365", transform=ax_info.transAxes, va="center", weight="bold")
                    ax_info.plot([.08,.91], [y-.055,y-.055], color="#E2E9EF", linewidth=.8, transform=ax_info.transAxes)
                    y -= .105
                ax_info.text(.09, .235, "相分类概率", fontproperties=font, fontsize=10.5, weight="bold", color="#365A79", transform=ax_info.transAxes)
                probability_rows = [("SS", float(phase.get("SS", 0)), "#4D84AE"), ("IM", float(phase.get("IM", 0)), "#CF705A"), ("SS+IM", float(phase.get("SS+IM", 0)), "#D7A94A")]
                y = .180
                for label, value, color in probability_rows:
                    ax_info.text(.09, y, label, fontproperties=font, fontsize=9.5, color="#526C83", transform=ax_info.transAxes, va="center")
                    ax_info.add_patch(FancyBboxPatch((.24,y-.014), .46,.028, boxstyle="round,pad=.002,rounding_size=.01", facecolor="#E6EDF3", edgecolor="none", transform=ax_info.transAxes))
                    ax_info.add_patch(FancyBboxPatch((.24,y-.014), max(.006,.46*value),.028, boxstyle="round,pad=.002,rounding_size=.01", facecolor=color, edgecolor="none", transform=ax_info.transAxes))
                    ax_info.text(.74, y, f"{value:.1%}", fontproperties=font, fontsize=9.5, color="#1E4365", transform=ax_info.transAxes, va="center", ha="left")
                    y -= .053
                path = task_dir / "microstructure_tendency.png"
                fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig)
                assets["microstructure_tendency"] = path

        from src.alloy_workflow.presentation import final_conclusion_block
        summary = task_dir / "summary.md"
        summary.write_text("\n".join(["### 合金配比探索结果", "", final_conclusion_block(result)]), encoding="utf-8")
        assets["summary_markdown"] = summary
        return assets

    def _render_rocket_stainless(self, result: dict[str, Any]) -> dict[str, Path]:
        """Render the same funnel → trade-off → composition visual sequence as 1111 hot-end."""
        task_dir = self.results_root / result["taskid"] / "presentation"
        task_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        from src.alloy_workflow.presentation import rocket_stainless_summary_block
        assets: dict[str, Path] = {}
        candidates = result.get("initial_candidates") or []
        if result.get("mode") != "cryogenic_reference" and candidates:
            font = self._chart_font()
            all_candidates = result.get("all_candidates") or candidates
            sampling = result.get("sampling") or {}
            stages = [
                (str(item.get("label") or "筛选阶段"), int(item.get("count", 0)))
                for item in (sampling.get("funnel_stages") or [])
                if isinstance(item, dict)
            ] or [
                ("满足成分边界的候选", len(all_candidates)),
                ("训练成分邻域内", sum(1 for item in all_candidates if (item.get("applicability_domain") or {}).get("level") == "inside")),
                ("强度—延性综合优先", min(5, sum(1 for item in all_candidates if (item.get("applicability_domain") or {}).get("level") == "inside"))),
            ]
            counts = [count for _, count in stages]; maximum = max(counts) or 1
            widths = [max(.30, .30 + .64 * (count / maximum) ** .34) for count in counts]
            fig, ax = plt.subplots(figsize=(10.8, 7.2), facecolor="#FFFFFF")
            for index, ((label, count), top_width) in enumerate(zip(stages, widths)):
                bottom_width = max(.20, top_width * .80); y_top = len(stages) - index * 1.24; y_bottom = y_top - .86
                color = ("#9ECCE1", "#78ADD0", "#5D93C0", "#467BAA", "#315F8F")[min(index, 4)]; dark = tuple(value * .78 for value in to_rgb(color)); light = tuple(min(1, value + (1 - value) * .55) for value in to_rgb(color))
                ax.add_patch(Ellipse((.5, y_bottom), width=bottom_width, height=.24, facecolor=dark, edgecolor="none", zorder=1))
                ax.add_patch(Polygon([(.5-top_width/2,y_top),(.5+top_width/2,y_top),(.5+bottom_width/2,y_bottom),(.5-bottom_width/2,y_bottom)], closed=True, facecolor=color, edgecolor="none", zorder=2))
                ax.add_patch(Ellipse((.5,y_top), width=top_width, height=.24, facecolor=light, edgecolor="white", linewidth=1.3, zorder=3))
                ax.text(.5, (y_top+y_bottom)/2, label, ha="center", va="center", color="#203B55", fontproperties=font, fontsize=10, fontweight="bold", zorder=4)
                retention = count / counts[0] if counts[0] else 0
                ax.annotate(f"{count:,}（保留 {retention:.1%}）", xy=(.5+bottom_width/2,(y_top+y_bottom)/2), xytext=(1.08,(y_top+y_bottom)/2), ha="left", va="center", color="#425466", fontproperties=font, fontsize=11, fontweight="bold", arrowprops={"arrowstyle":"-","color":"#94A3B8","lw":1.15})
            fig.suptitle("可回收火箭不锈钢：候选筛选漏斗", x=.055, y=.98, ha="left", fontproperties=font, fontsize=17, fontweight="bold")
            fig.text(.055,.895,"按成分边界、Fe 平衡、成分适用域与强度—延性综合排序逐层保留",color="#5B6472",fontproperties=font,fontsize=9.5)
            bottom_y = len(stages) - (len(stages) - 1) * 1.24 - .86
            ax.set_xlim(-.02,1.38); ax.set_ylim(bottom_y-.35,len(stages)+.36); ax.axis("off"); fig.tight_layout(rect=(0,0,1,.84))
            path = task_dir / "rocket_screening_funnel.png"; fig.savefig(path,dpi=220,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); assets["rocket_screening_funnel"] = path

            fig, ax = plt.subplots(figsize=(8.6, 5.6), facecolor="#FFFFFF")
            styles = {"inside": ("#1F77B4", "训练邻域内"), "boundary": ("#F28E2B", "数据边界附近")}
            used: set[str] = set()
            for candidate in candidates:
                tensile = candidate["short_time_tensile"]; level = candidate.get("applicability_domain", {}).get("level", "boundary")
                color, label = styles.get(level, styles["boundary"])
                ax.scatter(tensile["elongation_pct"]["mean"], tensile["yield_0p2_MPa"]["mean"], s=52, color=color, alpha=.75, edgecolor="white", linewidth=.55, label=label if level not in used else None)
                used.add(level)
            top = candidates[0]; tensile = top["short_time_tensile"]
            ax.scatter(tensile["elongation_pct"]["mean"], tensile["yield_0p2_MPa"]["mean"], marker="*", s=250, color="#D62728", edgecolor="white", linewidth=1, label="优先候选", zorder=4)
            ax.set_xlabel("预测延伸率（%）", fontproperties=font); ax.set_ylabel("预测 0.2% 屈服强度（MPa）", fontproperties=font)
            ax.set_title("强度—延性取舍", fontproperties=font, fontsize=15, pad=12); ax.legend(prop=font); ax.grid(alpha=.16); ax.set_axisbelow(True); ax.spines[["top", "right"]].set_visible(False); self._apply_chart_font(ax, font)
            path = task_dir / "rocket_strength_ductility_tradeoff.png"; fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig); assets["rocket_strength_ductility_tradeoff"] = path

            top_comp = top.get("composition_wt_percent") or {}; labels = list(top_comp); amounts = [float(top_comp[k]) for k in labels]
            fig, ax = plt.subplots(figsize=(8.6, 4.8), facecolor="#FFFFFF")
            colors = plt.cm.tab20(np.linspace(0, 1, len(labels))); left = 0.0
            for label, amount, color in zip(labels, amounts, colors):
                ax.barh(["优先候选"], [amount], left=left, label=label, color=color)
                if amount >= 2: ax.text(left + amount / 2, 0, f"{label}\n{amount:.1f}", ha="center", va="center", fontsize=8)
                left += amount
            ax.set_xlim(0, 100); ax.set_xlabel("质量百分比（wt.%）", fontproperties=font); ax.set_title("优先候选成分（Fe 为平衡元素）", fontproperties=font, fontsize=15, pad=12); ax.legend(prop=font, ncol=min(7, len(labels)), loc="upper center", bbox_to_anchor=(.5, -0.18), frameon=False); ax.spines[["top", "right", "left"]].set_visible(False); ax.tick_params(axis="y", length=0); self._apply_chart_font(ax, font)
            path = task_dir / "rocket_composition_comparison.png"; fig.tight_layout(); fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig); assets["rocket_composition_comparison"] = path
        summary = task_dir / "summary.md"; summary.write_text(rocket_stainless_summary_block(result), encoding="utf-8"); assets["summary_markdown"] = summary
        return assets

    def _render_chip_glass(self, result: dict[str, Any]) -> dict[str, Path]:
        """Render glass assets through the same task-local/published 1111 path."""
        task_dir = self.results_root / result["taskid"] / "presentation"
        task_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        font = self._chart_font(); assets: dict[str, Path] = {}
        stages = [(str(item.get("label") or "筛选阶段"), int(item.get("count", 0))) for item in (result.get("sampling", {}).get("funnel_stages") or [])]
        if stages:
            # Keep the same five-layer cadence as the hot-end nickel funnel.
            # The inserted applicability layer is not a second filtering claim:
            # it makes explicit that all locally perturbed candidates remain in
            # the admitted glass-family / oxide-bound domain at this stage.
            if len(stages) == 4:
                anchor_label, anchor_count = stages[0]
                perturb_label, perturb_count = stages[1]
                property_label, property_count = stages[2]
                shortlist_label, shortlist_count = stages[3]
                stages = [
                    (anchor_label, anchor_count),
                    (perturb_label, perturb_count),
                    ("同家族适用域与氧化物边界", perturb_count),
                    (property_label, property_count),
                    (shortlist_label, shortlist_count),
                ]
            # Match the hot-end nickel chart geometry, palette, cap treatment
            # and callout placement one-to-one; only labels and counts differ.
            fig, ax = plt.subplots(figsize=(10.8, 7.2), facecolor="#FFFFFF")
            counts = [count for _, count in stages]; maximum = max(counts) or 1
            widths = [max(.30, .30 + .64 * (count / maximum) ** .34) for count in counts]
            palette = ("#8FC6E4", "#69A8D0", "#4D8DBB", "#356F9E", "#234F7D")
            layer_height, layer_gap, cap_height = .90, .38, .24
            for index, ((label, count), top_width) in enumerate(zip(stages, widths)):
                bottom_width = max(.20, top_width * .78)
                y_top = len(stages) - index * (layer_height + layer_gap); y_bottom = y_top - layer_height
                color = palette[min(index, len(palette) - 1)]
                dark = tuple(value * .78 for value in to_rgb(color)); light = tuple(min(1, value + (1 - value) * .55) for value in to_rgb(color))
                ax.add_patch(Ellipse((.5, y_bottom), width=bottom_width, height=cap_height, facecolor=dark, edgecolor="none", zorder=1))
                ax.add_patch(Polygon([(.5-top_width/2, y_top), (.5+top_width/2, y_top), (.5+bottom_width/2, y_bottom), (.5-bottom_width/2, y_bottom)], closed=True, facecolor=color, edgecolor="none", zorder=2))
                ax.add_patch(Ellipse((.5, y_top), width=top_width, height=cap_height, facecolor=light, edgecolor="white", linewidth=1.35, zorder=3))
                ax.text(.5, (y_top+y_bottom)/2, label, ha="center", va="center", color="#203B55", fontproperties=font, fontsize=10, fontweight="bold", zorder=4)
                ax.annotate(f"{count:,}（保留 {count / counts[0]:.1%}）", xy=(.5+bottom_width/2, (y_top+y_bottom)/2), xytext=(1.08, (y_top+y_bottom)/2), ha="left", va="center", color="#425466", fontproperties=font, fontsize=11, fontweight="bold", arrowprops={"arrowstyle": "-", "color": "#94A3B8", "lw": 1.15})
            bottom_y = len(stages) - (len(stages)-1) * (layer_height+layer_gap) - layer_height
            fig.suptitle("芯片玻璃基板：候选筛选漏斗", x=.055, y=.98, ha="left", fontproperties=font, fontsize=17, fontweight="bold")
            fig.text(.055, .895, "来源锚点、局部氧化物扰动、性质门槛与优先短名单逐层保留", color="#5B6472", fontproperties=font, fontsize=9.5)
            ax.set_xlim(-.02, 1.38); ax.set_ylim(bottom_y-.35, len(stages)+.36); ax.axis("off")
            path = task_dir / "glass_screening_funnel.png"; fig.tight_layout(rect=(0, 0, 1, .86)); fig.savefig(path, dpi=220, bbox_inches="tight"); plt.close(fig); assets["glass_screening_funnel"] = path
        candidates = result.get("all_candidates") or result.get("initial_candidates") or []
        if candidates:
            fig, ax = plt.subplots(figsize=(9.6, 5.8), facecolor="#FFFFFF")
            cte = [item["predicted_properties"]["CTE_linear_0_to_300C"]["prediction_ppm_per_K"] for item in candidates]
            modulus = [item["predicted_properties"]["young_modulus_GPa"]["prediction"] for item in candidates]
            score = [item.get("selection_score", 0) for item in candidates]
            points = ax.scatter(cte, modulus, c=score, cmap="Blues", s=45, edgecolor="white", linewidth=.5)
            top = (result.get("initial_candidates") or candidates)[0]
            top_cte = top["predicted_properties"]["CTE_linear_0_to_300C"]["prediction_ppm_per_K"]; top_e = top["predicted_properties"]["young_modulus_GPa"]["prediction"]
            ax.scatter([top_cte], [top_e], marker="*", s=240, color="#E07A38", edgecolor="white", zorder=4, label="当前优先候选")
            ax.set_xlabel("CTE（0–300°C，ppm/K）", fontproperties=font); ax.set_ylabel("杨氏模量 E（GPa）", fontproperties=font)
            ax.set_title("同家族候选的 CTE—刚度取舍", fontproperties=font, fontsize=15, fontweight="bold")
            legend = ax.legend(); [label.set_fontproperties(font) for label in legend.get_texts()]
            colorbar = fig.colorbar(points, ax=ax); colorbar.set_label("综合筛选分数", fontproperties=font)
            self._apply_chart_font(ax, font); ax.grid(alpha=.16); ax.set_axisbelow(True); ax.spines[["top", "right"]].set_visible(False)
            path = task_dir / "glass_cte_modulus_tradeoff.png"; fig.tight_layout(); fig.savefig(path, dpi=220, bbox_inches="tight"); plt.close(fig); assets["glass_cte_modulus_tradeoff"] = path
            anchor = top.get("source_anchor") or {}; anchor_comp = anchor.get("composition_mol_percent") or {}
            candidate_comp = top.get("composition_mol_percent") or {}
            elements = [name for name in candidate_comp if candidate_comp.get(name, 0) > 0 or anchor_comp.get(name, 0) > 0]
            fig, ax = plt.subplots(figsize=(11.2, 5.8), facecolor="#FFFFFF")
            loc = np.arange(len(elements)); width = .38
            ax.bar(loc-width/2, [anchor_comp.get(name, 0) for name in elements], width, color="#A7C4D8", label="来源锚点")
            ax.bar(loc+width/2, [candidate_comp.get(name, 0) for name in elements], width, color="#1F5B89", label="优先候选")
            ax.set_xticks(loc, elements, rotation=35, ha="right"); ax.set_ylabel("氧化物含量（mol%）", fontproperties=font)
            ax.set_title("优先候选相对来源锚点的氧化物配方调整", fontproperties=font, fontsize=15, fontweight="bold")
            legend = ax.legend(); [label.set_fontproperties(font) for label in legend.get_texts()]
            self._apply_chart_font(ax, font); ax.grid(axis="y", alpha=.16); ax.set_axisbelow(True); ax.spines[["top", "right"]].set_visible(False)
            path = task_dir / "glass_composition_traceability.png"; fig.tight_layout(); fig.savefig(path, dpi=220, bbox_inches="tight"); plt.close(fig); assets["glass_composition_traceability"] = path
        from src.alloy_workflow.presentation import glass_summary_block
        summary = task_dir / "summary.md"; summary.write_text(glass_summary_block(result), encoding="utf-8"); assets["summary_markdown"] = summary
        return assets

    def _render_hot_end(self, result: dict[str, Any]) -> dict[str, Path]:
        """Compact evidence-first visuals for the Ni hot-end route."""
        task_dir = self.results_root / result["taskid"] / "presentation"
        task_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        font = self._chart_font()
        all_candidates = result.get("all_candidates") or result.get("initial_candidates") or []
        shortlisted = result.get("initial_candidates") or []
        nearest = result.get("nearest_candidates") or []
        candidates = all_candidates
        sampling = result.get("sampling") or {}
        assets: dict[str, Path] = {}
        stages = [
            (str(item.get("label") or "筛选阶段"), int(item.get("count", 0)))
            for item in (sampling.get("funnel_stages") or [])
            if isinstance(item, dict)
        ] or [("满足成分约束的候选", int(sampling.get("generated", 0))), ("综合优先短名单", len(result.get("initial_candidates", [])))]
        counts = [count for _, count in stages]
        maximum = max(counts) or 1
        widths = [max(.30, .30 + .64 * (count / maximum) ** .34) for count in counts]
        palette = ("#8FC6E4", "#69A8D0", "#4D8DBB", "#356F9E", "#234F7D")
        fig, ax = plt.subplots(figsize=(10.8, 7.2), facecolor="#FFFFFF")
        layer_height, layer_gap, cap_height = .90, .38, .24
        for index, ((label, count), top_width) in enumerate(zip(stages, widths)):
            bottom_width = max(.20, top_width * .78)
            y_top = len(stages) - index * (layer_height + layer_gap)
            y_bottom = y_top - layer_height
            color = palette[min(index, len(palette) - 1)]
            dark = tuple(value * .78 for value in to_rgb(color))
            light = tuple(min(1, value + (1 - value) * .55) for value in to_rgb(color))
            ax.add_patch(Ellipse((.5, y_bottom), width=bottom_width, height=cap_height, facecolor=dark, edgecolor="none", zorder=1))
            ax.add_patch(Polygon([(.5-top_width/2, y_top), (.5+top_width/2, y_top), (.5+bottom_width/2, y_bottom), (.5-bottom_width/2, y_bottom)], closed=True, facecolor=color, edgecolor="none", zorder=2))
            ax.add_patch(Ellipse((.5, y_top), width=top_width, height=cap_height, facecolor=light, edgecolor="white", linewidth=1.35, zorder=3))
            ax.text(.5, (y_top+y_bottom)/2, label, ha="center", va="center", color="#203B55", fontproperties=font, fontsize=10, fontweight="bold", zorder=4)
            retention = count / counts[0] if counts[0] else 0
            ax.annotate(f"{count:,}（保留 {retention:.1%}）", xy=(.5+bottom_width/2, (y_top+y_bottom)/2), xytext=(1.08, (y_top+y_bottom)/2), ha="left", va="center", color="#425466", fontproperties=font, fontsize=11, fontweight="bold", arrowprops={"arrowstyle": "-", "color": "#94A3B8", "lw": 1.15})
        bottom_y = len(stages) - (len(stages)-1) * (layer_height+layer_gap) - layer_height
        fig.suptitle("高温镍基合金：候选筛选漏斗", x=.055, y=.98, ha="left", fontproperties=font, fontsize=17, fontweight="bold")
        thresholds = sampling.get("screening_thresholds") or {}
        support_note = f"来源参考合金 {int(sampling.get('source_anchors', 0))} 个；各阶段按当前筛选门槛逐层保留"
        if thresholds:
            support_note += "。"
        fig.text(.055, .895, support_note, color="#5B6472", fontproperties=font, fontsize=9.5)
        ax.set_xlim(-.02, 1.38); ax.set_ylim(bottom_y-.35, len(stages)+.36); ax.axis("off")
        fig.tight_layout(rect=(0, 0, 1, .84))
        path = task_dir / "hot_end_screening_funnel.png"; fig.savefig(path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight"); plt.close(fig); assets["hot_end_screening_funnel"] = path
        if candidates:
            fig, ax = plt.subplots(figsize=(10.5, 6.0), facecolor="#FFFFFF")
            life = [item["creep_rupture"]["predicted_log10_hours"] for item in candidates]
            uts = [item["ultimate_tensile_strength_MPa"]["mean"] for item in candidates]
            score = [item["screening_score"] for item in candidates]
            points = ax.scatter(life, uts, c=score, cmap="Blues", s=42, edgecolor="white", linewidth=.45)
            top = (shortlisted or nearest or candidates)[0]
            top_label = "当前优先候选" if shortlisted else "下一步优先评估"
            ax.scatter([top["creep_rupture"]["predicted_log10_hours"]], [top["ultimate_tensile_strength_MPa"]["mean"]], marker="*", s=220, color="#E07A38", edgecolor="white", zorder=4)
            ax.set_xlabel("预测蠕变断裂寿命 log10(h)", fontproperties=font); ax.set_ylabel("短时抗拉强度 UTS（MPa）", fontproperties=font)
            ax.set_title("指定工况下的强度—寿命取舍", fontproperties=font, fontsize=15, weight="bold")
            colorbar = fig.colorbar(points, ax=ax); colorbar.set_label("综合筛选分数", fontproperties=font)
            ax.annotate(top_label, (top["creep_rupture"]["predicted_log10_hours"], top["ultimate_tensile_strength_MPa"]["mean"]), xytext=(8, 8), textcoords="offset points", fontproperties=font, color="#9A431D")
            path = task_dir / "hot_end_strength_life_tradeoff.png"; fig.tight_layout(); fig.savefig(path, dpi=220, bbox_inches="tight"); plt.close(fig); assets["hot_end_strength_life_tradeoff"] = path
            # 多个来源合金同时进入筛选时，成分对照图有助于追溯候选改动；
            # 默认模板只有一个来源时，该图会重复候选表信息，因此不向用户展示。
            if int(sampling.get("source_anchors", 0)) > 1:
                anchor = top.get("source_anchor") or {}
                anchor_composition = anchor.get("composition_wt_percent") or {}
                candidate_composition = top.get("composition_wt_percent") or {}
                elements = [element for element in candidate_composition if candidate_composition[element] >= .1 or anchor_composition.get(element, 0) >= .1]
            else:
                elements = []
            if elements:
                fig, ax = plt.subplots(figsize=(11.2, 6.1), facecolor="#FFFFFF")
                locations = np.arange(len(elements)); width = .37
                ax.bar(locations-width/2, [anchor_composition.get(element, 0) for element in elements], width, label="参考合金", color="#9DBAD1")
                ax.bar(locations+width/2, [candidate_composition.get(element, 0) for element in elements], width, label=("优先候选" if shortlisted else "下一步优先评估"), color="#1F5B89")
                ax.set_xticks(locations, elements); ax.set_ylabel("质量百分比（wt.%）", fontproperties=font)
                ax.set_title(("优先候选" if shortlisted else "下一步优先评估候选") + "相对参考合金的配方调整", fontproperties=font, fontsize=15, weight="bold")
                legend = ax.legend(); [label.set_fontproperties(font) for label in legend.get_texts()]
                ax.text(.01, .97, f"参考合金：{anchor.get('alloy_name', '-')}", transform=ax.transAxes, va="top", fontproperties=font, color="#41617C")
                path = task_dir / "hot_end_composition_traceability.png"; fig.tight_layout(); fig.savefig(path, dpi=220, bbox_inches="tight"); plt.close(fig); assets["hot_end_composition_traceability"] = path
        from src.alloy_workflow.presentation import hot_end_summary_block
        summary = task_dir / "summary.md"; summary.write_text(hot_end_summary_block(result), encoding="utf-8"); assets["summary_markdown"] = summary
        return assets


RUNTIME = AlloyRuntime()
