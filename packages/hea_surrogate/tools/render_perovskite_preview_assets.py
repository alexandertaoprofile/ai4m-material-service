#!/usr/bin/env python3
"""Render presentation assets from a MatterSim-relaxed perovskite candidate.

The structure panel is an atomistic visualisation of the supplied relaxed
structure.  The scorecard is deliberately limited to values in
``mattersim_results.json`` and labels them as MLFF rapid screening results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from ase.io import read
from matplotlib import font_manager


SURFACE = "#06182F"
PANEL = "#0B2848"
PANEL_EDGE = "#28577D"
TEXT = "#F4F8FF"
MUTED = "#B7CBE0"
ACCENT = "#63C7FF"
COLORS = {"Cs": "#6DB7E8", "Pb": "#E28AA9", "I": "#9B7AC8", "Br": "#EE9B57"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relaxed-structure", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def configure_font() -> None:
    font_path = Path("/data/se42/alpha_project/material_service_hub/material_discovery/assets/fonts/NotoSansCJKsc-Regular.otf")
    if not font_path.is_file():
        raise FileNotFoundError(font_path)
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def formula_subscripts(formula: str) -> str:
    """Keep a portable ASCII formula in CJK presentation fonts.

    The bundled Noto CJK font lacks Unicode subscript glyphs; ASCII is more
    truthful than rendering a missing-glyph box in a customer-facing asset.
    """
    return formula


def render_structure(atoms, formula: str, output: Path) -> None:
    displayed = atoms.repeat((2, 2, 2))
    coords = displayed.get_positions()
    symbols = displayed.get_chemical_symbols()
    figure = plt.figure(figsize=(8.4, 7.1), facecolor=SURFACE)
    axis = figure.add_subplot(111, projection="3d")
    axis.set_facecolor(PANEL)

    # Pb–halide neighbour segments show coordination topology only; they do
    # not assert a calculated bond order.
    for index, symbol in enumerate(symbols):
        if symbol != "Pb":
            continue
        neighbours = []
        for other, other_symbol in enumerate(symbols):
            if other_symbol not in {"I", "Br"}:
                continue
            distance = float(np.linalg.norm(coords[index] - coords[other]))
            if 0.01 < distance <= 4.2:
                neighbours.append((distance, other))
        for _, other in sorted(neighbours)[:6]:
            axis.plot(*zip(coords[index], coords[other]), color="#CBD9E8", linewidth=.55, alpha=.34, zorder=1)

    for element in ("Cs", "Pb", "I", "Br"):
        ids = [index for index, value in enumerate(symbols) if value == element]
        if ids:
            axis.scatter(coords[ids, 0], coords[ids, 1], coords[ids, 2], s=84,
                         color=COLORS[element], edgecolors="#FFFFFF", linewidths=.6,
                         label=element, depthshade=True, zorder=3)

    low, high = coords.min(axis=0), coords.max(axis=0)
    span = max(float((high - low).max()), 1.0)
    for setter, value in zip((axis.set_xlim, axis.set_ylim, axis.set_zlim), range(3)):
        setter(low[value] - span * .12, high[value] + span * .12)
    axis.set_box_aspect((1, 1, 1))
    axis.view_init(elev=22, azim=37)
    axis.set_xticks([]); axis.set_yticks([]); axis.set_zticks([])
    for dimension in (axis.xaxis, axis.yaxis, axis.zaxis):
        dimension.pane.set_facecolor(PANEL)
        dimension.pane.set_edgecolor(PANEL_EDGE)
    axis.set_title(f"生成候选 · {formula_subscripts(formula)}", color=TEXT, fontsize=14, pad=10)
    legend = axis.legend(loc="upper left", frameon=False, ncol=2, fontsize=9)
    for label in legend.get_texts():
        label.set_color(TEXT)
    figure.text(.5, .04, "MatterGen 生成结构，经 MatterSim 松弛后的周期性原子模型", ha="center", color=MUTED, fontsize=10)
    figure.tight_layout(rect=(0, .06, 1, 1))
    figure.savefig(output, dpi=220, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)


def render_scorecard(candidate: dict, output: Path) -> None:
    formula = formula_subscripts(candidate["formula_pretty"])
    hull = float(candidate["energy_above_hull_ev"])
    formation = float(candidate["formation_energy_per_atom_ev"])
    figure = plt.figure(figsize=(8.4, 5.4), facecolor=SURFACE)
    figure.text(.07, .86, f"{formula} · 快速稳定性预测", color=TEXT, fontsize=21, fontweight="bold")
    figure.text(.07, .79, "MatterSim MLFF 松弛 + MP2020correction 竞争相参考", color=ACCENT, fontsize=10)

    rows = [
        ("高于凸包能\nE_hull", f"{hull:.4f}\neV/atom"),
        ("形成能", f"${formation:.4f}$\neV/atom"),
        ("初筛结论", "低于 0.05 eV/atom\n进入后续验证"),
    ]
    left, width, top, height = .07, .86, .65, .16
    for index, (label, value) in enumerate(rows):
        y = top - index * .18
        figure.patches.append(plt.Rectangle((left, y), width, height, transform=figure.transFigure, facecolor=PANEL, edgecolor=PANEL_EDGE, linewidth=1.0))
        figure.text(left + .04, y + .08, label, color=MUTED, fontsize=13, va="center")
        figure.text(left + .43, y + .08, value, color=TEXT, fontsize=15, fontweight="bold", va="center")
    figure.text(.07, .08, "该图用于结构候选排序；不是 DFT、器件活化能或实验可制备性结论。", color=MUTED, fontsize=9.5)
    figure.savefig(output, dpi=220, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)


def main() -> None:
    args = arguments()
    configure_font()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    atoms = read(args.relaxed_structure)
    result = json.loads(args.results.read_text(encoding="utf-8"))
    candidate = result["candidates"][0]
    formula = candidate["formula_pretty"]
    render_structure(atoms, formula, args.output_dir / "cspbibr2_relaxed_structure.png")
    render_scorecard(candidate, args.output_dir / "cspbibr2_mattersim_scorecard.png")
    print(args.output_dir / "cspbibr2_relaxed_structure.png")
    print(args.output_dir / "cspbibr2_mattersim_scorecard.png")


if __name__ == "__main__":
    main()
