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

from src.alloy_workflow.application import AlloyOptimizationApplication
from src.alloy_workflow.runner import HEASurrogateRunner

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
        chart_text = "候选筛选漏斗生成候选通过初筛候选数量筛选候选强度硬度分布训练数据范围内边界附近预测屈服强度MPa元素含量atP5P50P95成分区间非最终配方0123456789NiCoCrAlTi—；.%（）"
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
        task_dir = self.results_root / result["taskid"] / "presentation"
        task_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        assets: dict[str, Path] = {}
        candidates = result.get("_presentation_candidates", result.get("initial_candidates", []))
        sampling = result.get("sampling", {})
        font = self._chart_font()

        fig, ax = plt.subplots(figsize=(6.5, 4))
        bars = ax.bar(["生成候选", "通过初筛"], [int(sampling.get("generated", 0)), int(sampling.get("feasible", 0))], color=["#9ecae1", "#2ca25f"])
        ax.bar_label(bars, padding=3)
        ax.set_ylabel("候选数量", fontproperties=font)
        ax.set_title("候选筛选漏斗", fontproperties=font)
        self._apply_chart_font(ax, font)
        ax.grid(axis="y", alpha=.25)
        path = task_dir / "screening_funnel.png"
        fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)
        assets["screening_funnel"] = path

        if candidates:
            fig, ax = plt.subplots(figsize=(7, 4.5))
            colors = ["#1f77b4" if item["applicability_domain"]["level"] == "inside" else "#ff7f0e" for item in candidates]
            ax.scatter([item["yield_strength_MPa"]["mean"] for item in candidates], [item["hardness_HV"]["mean"] for item in candidates], c=colors, alpha=.65)
            ax.scatter([], [], c="#1f77b4", label="训练数据范围内")
            ax.scatter([], [], c="#ff7f0e", label="训练数据边界附近")
            ax.legend(prop=font)
            ax.set_xlabel("预测屈服强度（MPa）", fontproperties=font)
            ax.set_ylabel("预测硬度（HV）", fontproperties=font)
            ax.set_title("筛选候选：强度—硬度分布", fontproperties=font)
            self._apply_chart_font(ax, font)
            ax.grid(alpha=.25)
            path = task_dir / "strength_hardness_tradeoff.png"
            fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)
            assets["strength_hardness_tradeoff"] = path

            ranges = result.get("derived_candidate_percentiles_at_pct", {})
            names = list(ranges)
            fig, ax = plt.subplots(figsize=(7, 4.5))
            low = [ranges[name]["p05"] for name in names]
            mid = [ranges[name]["p50"] for name in names]
            high = [ranges[name]["p95"] for name in names]
            ax.errorbar(names, mid, yerr=[np.subtract(mid, low), np.subtract(high, mid)], fmt="o", capsize=7, color="#4c78a8")
            ax.set_ylabel("元素含量（at.%；P5—P50—P95）", fontproperties=font)
            ax.set_title("候选成分区间（非最终配方）", fontproperties=font)
            self._apply_chart_font(ax, font)
            ax.grid(axis="y", alpha=.25)
            path = task_dir / "composition_percentiles.png"
            fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)
            assets["composition_percentiles"] = path

        from src.alloy_workflow.presentation import final_conclusion_block
        summary = task_dir / "summary.md"
        summary.write_text("\n".join(["### 合金配比探索结果", "", final_conclusion_block(result)]), encoding="utf-8")
        assets["summary_markdown"] = summary
        return assets


RUNTIME = AlloyRuntime()
