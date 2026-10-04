#!/usr/bin/env python3
"""Render truthful presentation assets from a new-material pipeline manifest."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.animation import PillowWriter
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from pymatgen.core import Structure


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# A shared deep-navy workspace keeps the constraint card, rotating structure
# preview and thermodynamic scorecard visually coherent in the client.
SURFACE = "#06182F"
PANEL = "#0B2848"
PANEL_EDGE = "#28577D"
TEXT = "#F4F8FF"
MUTED = "#B7CBE0"
ACCENT = "#63C7FF"
STRUCTURE_LINE = "#C7D6E4"
STRUCTURE_LINE_RGB = (199, 214, 228)
POLYHEDRON_ALPHA = 85 / 255


def configure_chinese_font() -> None:
    """Use the bundled CJK font that covers Chinese, formulas and SI units.

    Droid Sans Fallback is CJK-only on this host: it renders Chinese but turns
    element symbols, underscores, inequalities and ``eV/atom`` into boxes.
    Noto Sans CJK SC contains both the CJK and Latin/math glyphs used in the
    cards, so one explicit font is safer than relying on Matplotlib fallback.
    """
    font_path = Path(__file__).resolve().parents[1] / "assets/fonts/NotoSansCJKsc-Regular.otf"
    if not font_path.exists():
        raise RuntimeError(f"Required presentation font is missing: {font_path}")
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["axes.unicode_minus"] = False


configure_chinese_font()


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def color(element: str) -> str:
    """Return the exact element colour used by the interactive GLB."""
    from tools.structure_to_glb import element_rgb

    red, green, blue = element_rgb(element)
    return f"#{red:02X}{green:02X}{blue:02X}"


def get_top(manifest: dict) -> tuple[dict, dict]:
    ranked = manifest.get("ranked_candidates") or []
    if not ranked:
        raise ValueError("No ranked candidate is available for presentation rendering.")
    if manifest.get("generation", {}).get("backend") == "omatg":
        from src.material_workflow.omatg_chart import primary_structure
        top = primary_structure(manifest) or ranked[0]
    else:
        top = ranked[0]
    candidate = top.get("candidate") or {}
    validation = top.get("validation") or {}
    return candidate, validation


def axes_limits(coords: np.ndarray) -> tuple[tuple[float, float], ...]:
    low = coords.min(axis=0)
    high = coords.max(axis=0)
    span = max(float((high - low).max()), 1.0)
    pad = span * 0.18
    return tuple((float(low[i] - pad), float(high[i] + pad)) for i in range(3))


def structure_connections(structure: Structure) -> list[tuple[int, int, np.ndarray]]:
    """Find CrystalNN structural-neighbour connections for the MP-style preview.

    These lines show periodic near-neighbour topology and coordination.  They
    are intentionally not presented as calculated bond orders or bond energies.
    """
    try:
        from tools.structure_to_glb import get_crystalnn_bonds

        # The display is a finite visual supercell.  Neighbours beyond its
        # outer boundary are physically valid periodic images, but omitting
        # them avoids dangling rods in a browser or a 2-D preview.
        return [connection for connection in get_crystalnn_bonds(structure) if not np.any(connection[2])]
    except Exception:
        # A presentation asset must still be produced if neighbour analysis
        # cannot resolve a difficult structure.
        return []


def structure_polyhedra(structure: Structure) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Return the same recognised coordination polyhedra used by the GLB."""
    try:
        from tools.structure_to_glb import (
            TYPICAL_ANIONS,
            get_polyhedra,
            pick_poly_centers_mp_like,
        )

        centers = pick_poly_centers_mp_like(structure, "auto")
        vertex_filter = set(TYPICAL_ANIONS) if "P" in centers else None
        return get_polyhedra(
            structure=structure,
            centers=centers,
            cn_allowed={4},
            max_neighbors=4,
            vertex_filter=vertex_filter,
        )
    except Exception:
        return []


def display_supercell(structure: Structure) -> Structure:
    """Build the same standardized 2×2×2 structure used for the GLB."""
    from tools.structure_to_glb import standardize_structure

    displayed = standardize_structure(structure)
    displayed.make_supercell([2, 2, 2])
    return displayed


def draw_structure(
    ax,
    structure: Structure,
    title: str,
    angle: int = 30,
    connections: list[tuple[int, int, np.ndarray]] | None = None,
    polyhedra: list[tuple[np.ndarray, np.ndarray, str]] | None = None,
) -> None:
    coords = np.asarray(structure.cart_coords)
    elements = [site.specie.symbol for site in structure]
    line_coordinates = []
    for first, second, image in connections or []:
        p0 = coords[first]
        p1 = coords[second] + image @ structure.lattice.matrix
        line_coordinates.extend((p0, p1))
        ax.plot(*zip(p0, p1), color=STRUCTURE_LINE, linewidth=0.65, alpha=0.58, zorder=1)
    for _center, vertices, element in polyhedra or []:
        try:
            import trimesh

            hull = trimesh.convex.convex_hull(vertices)
            collection = Poly3DCollection(
                hull.vertices[hull.faces],
                facecolors=color(element),
                edgecolors=color(element),
                linewidths=0.25,
                alpha=POLYHEDRON_ALPHA,
            )
            ax.add_collection3d(collection)
        except Exception:
            continue
    for element in sorted(set(elements)):
        ids = [index for index, value in enumerate(elements) if value == element]
        ax.scatter(coords[ids, 0], coords[ids, 1], coords[ids, 2], s=100, color=color(element),
                   edgecolors="#FFFFFF", linewidths=0.65, label=element, depthshade=True)
    limits = axes_limits(np.vstack([coords, line_coordinates]) if line_coordinates else coords)
    ax.set_xlim(*limits[0]); ax.set_ylim(*limits[1]); ax.set_zlim(*limits[2])
    ax.set_box_aspect([1, 1, 1])
    ax.view_init(elev=22, azim=angle)
    ax.set_title(title, color=TEXT, fontsize=12, pad=8)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
    ax.set_facecolor(PANEL)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_facecolor(PANEL)
        axis.pane.set_edgecolor(PANEL_EDGE)
    legend = ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=2)
    for text in legend.get_texts():
        text.set_color(TEXT)


def render_structure_png(structure: Structure, output: Path, formula: str, *, relaxed: bool) -> None:
    figure = plt.figure(figsize=(8, 7), facecolor=SURFACE)
    axis = figure.add_subplot(111, projection="3d")
    displayed = display_supercell(structure)
    draw_structure(axis, displayed, f"Generated candidate · {formula}",
                   connections=structure_connections(displayed),
                   polyhedra=structure_polyhedra(displayed))
    subtitle = "Generated candidate after MatterSim relaxation" if relaxed else "Generated candidate · relaxation pending"
    figure.text(0.5, 0.04, subtitle, ha="center", color=MUTED, fontsize=10)
    figure.tight_layout(rect=(0, 0.06, 1, 1))
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_rotation_gif(structure: Structure, output: Path, formula: str, *, caption=None) -> None:
    figure = plt.figure(figsize=(6, 6), facecolor=SURFACE)
    axis = figure.add_subplot(111, projection="3d")
    writer = PillowWriter(fps=8)
    displayed = display_supercell(structure)
    connections = structure_connections(displayed)
    polyhedra = structure_polyhedra(displayed)
    with writer.saving(figure, str(output), dpi=110):
        for angle in range(0, 360, 20):
            axis.clear()
            draw_structure(axis, displayed, caption or f"候选晶体旋转预览 · {formula}", angle=angle,
                           connections=connections, polyhedra=polyhedra)
            writer.grab_frame()
    plt.close(figure)


def format_energy_ev_per_atom(value: float | None) -> str:
    """Format every thermodynamic energy on the card in its canonical unit."""
    return "待计算" if value is None else f"{float(value):.4f} eV/atom"


def render_scorecard(output: Path, formula: str, validation: dict, constraints: dict, display_name: str = "") -> None:
    hull = validation.get("energy_above_hull")
    formation = validation.get("formation_energy_per_atom")
    threshold = float((constraints.get("target_properties") or {}).get("energy_above_hull", 0.05))
    hard_limit = (constraints.get("parameter_sources") or {}).get("energy_above_hull_mode") == "硬性上限"
    figure = plt.figure(figsize=(12, 5.4), facecolor=SURFACE)
    left = figure.add_axes([0.06, 0.24, 0.50, 0.47], facecolor=PANEL)
    right = figure.add_axes([0.62, 0.18, 0.33, 0.66], facecolor=PANEL)
    is_priority = (validation.get("metadata") or {}).get("screening", {}).get("state") == "passed"
    structure_label = ("优选结构" if is_priority else "待复核结构") + f"（{display_name}）" if display_name else "候选晶体"
    title = structure_label + "的稳定性估计"
    figure.text(0.06, 0.82, title, color=TEXT, fontweight="bold", fontsize=20)
    figure.text(0.06, 0.765, "MatterSim + MP2020 校正参考相 · 混合基准估算", color=ACCENT, fontsize=10)
    if hull is None:
        left.text(0.5, 0.55, "热力学计算待完成", color=TEXT, fontsize=19, ha="center", transform=left.transAxes)
    else:
        max_value = max(threshold * 2.2, float(hull) * 1.35, 0.08)
        left.barh([0], [max_value], color="#173D61", height=0.32)
        left.barh([0], [float(hull)], color="#3FC49A" if hull <= threshold else "#FFD166", height=0.32)
        left.axvline(threshold, color="#FFD166", linewidth=2, linestyle="--")
        left.set_xlim(0, max_value); left.set_ylim(-0.48, 0.48); left.set_yticks([])
        # Axis-relative captions remain inside the panel even at E_hull=0 or
        # a zero threshold; data-relative centred labels get cut off there.
        left.text(0.03, 0.82, f"稳定性差值 = {format_energy_ev_per_atom(hull)}", color=TEXT, ha="left", fontsize=14, fontweight="bold", transform=left.transAxes, bbox={"facecolor": PANEL, "edgecolor": "none", "pad": 1.5})
        left.text(0.03, 0.08, f"{'硬性上限' if hard_limit else '复核参考线'} = {format_energy_ev_per_atom(threshold)}", color="#FFD166", ha="left", fontsize=10, transform=left.transAxes, bbox={"facecolor": PANEL, "edgecolor": "none", "pad": 1.5})
        left.set_xlabel("相对竞争相组合的能量差（eV/atom，越低越优）", color=MUTED, labelpad=8)
        left.tick_params(axis="x", colors=MUTED)
    for spine in left.spines.values(): spine.set_visible(False)
    right.axis("off")
    right.text(0.06, 0.88, formula, color=TEXT, fontsize=25, fontweight="bold", transform=right.transAxes)
    right.text(0.06, 0.79, structure_label + " · 能量计算结果", color=ACCENT, fontsize=10, transform=right.transAxes)
    right.text(0.06, 0.735, "形成能比较元素参考态，稳定性差值比较其他相组合", color=MUTED, fontsize=8.5, transform=right.transAxes)
    screening = (validation.get("metadata") or {}).get("screening") or {}
    next_step = "建议进行 DFT 验证" if hull is not None and hull <= threshold else ("热力学计算待完成" if hull is None else "继续性质评价并优先复核稳定性")
    if screening and screening.get("state") != "passed":
        next_step = "已设条件尚未全部满足，需复核"
    rows = [
        ("形成能（相对元素参考态）", format_energy_ev_per_atom(formation)),
        ("稳定性差值（凸包能估算）", format_energy_ev_per_atom(hull)),
        ("下一步", next_step),
        ("评价方法", "模型能量与参考相能量比较" if hull is not None else "基础结构检查"),
    ]
    y = 0.61
    for label, value in rows:
        right.text(0.06, y, label, color=ACCENT, fontsize=9, transform=right.transAxes)
        right.text(0.06, y - 0.075, value, color=TEXT, fontsize=12, transform=right.transAxes)
        y -= 0.17
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_design_brief(output: Path, structure: Structure, constraints: dict) -> None:
    """Render the input side of the story without claiming uncomputed properties."""
    from src.material_workflow.omatg_chart import ELEMENT_NAMES

    figure = plt.figure(figsize=(12, 4.4), facecolor=SURFACE)
    figure.text(0.06, 0.82, "材料组成与评价条件", color=TEXT, fontsize=22, fontweight="bold")
    formula = constraints.get("target_formula")
    scope = "固定化学式：" + formula if formula else "允许元素：" + " / ".join(constraints.get("allowed_elements") or []) if constraints.get("allowed_elements") else "已确认开放化学空间：未限定具体元素"
    figure.text(0.06, 0.755, scope, color=ACCENT, fontsize=10)

    elements = []
    for item in structure.composition.elements:
        symbol = item.symbol
        if symbol not in elements:
            elements.append(symbol)
    element_rows = (len(elements) + 3) // 4
    for index, element in enumerate(elements):
        x = 0.09 + index % 4 * 0.11
        y = 0.48 if element_rows == 1 else 0.63 - index // 4 * 0.32 / max(element_rows - 1, 1)
        radius = 0.026 if element_rows > 3 else 0.034
        circle = plt.Circle((x, y), radius, color=color(element), transform=figure.transFigure)
        figure.add_artist(circle)
        figure.text(x, y, element, ha="center", va="center", color=SURFACE, fontsize=10, fontweight="bold")
        figure.text(x, y - radius - 0.035, ELEMENT_NAMES.get(element, element), ha="center", va="center", color=TEXT, fontsize=10)
    figure.text(0.06, 0.18, "当前候选晶体包含的元素", color=ACCENT, fontsize=10)

    target_properties = constraints.get("target_properties") or {}
    hull = target_properties.get("energy_above_hull")
    hard_limit = (constraints.get("parameter_sources") or {}).get("energy_above_hull_mode") == "硬性上限"
    target_text = f"{'硬性上限' if hard_limit else '复核参考线'} {float(hull):g} eV/atom" if hull is not None else "未设置稳定性参考线"
    figure.text(0.56, 0.60, "本次稳定性比较口径（凸包能估算）", color=ACCENT, fontsize=10)
    figure.text(0.56, 0.52, target_text, color=TEXT, fontsize=16, fontweight="bold")

    target_labels = {
        "high_temperature_strength": "高温强度", "creep_resistance": "抗蠕变能力",
        "oxidation_resistance": "抗氧化能力", "thermal_fatigue": "热疲劳性能",
        "additive_manufacturability": "增材制造适配性", "ionic_conductivity": "离子电导率",
        "band_gap": "带隙", "bulk_modulus": "体积模量", "shear_modulus": "剪切模量",
        "electron_effective_mass": "电子有效质量", "hole_effective_mass": "空穴有效质量", "dielectric_constant_x": "x方向介电常数",
    }
    focus = [target_labels.get(name, name.replace("_", " ")) for name in (constraints.get("validation_targets") or {})]
    focus_text = " · ".join(focus) if focus else "结构与热力学初筛"
    figure.text(0.56, 0.34, "关注性能（已计算项目见报告）", color=ACCENT, fontsize=10)
    figure.text(0.56, 0.25, focus_text, color=TEXT, fontsize=12, wrap=True)
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_evidence_coverage(output: Path, validation: dict, constraints: dict, *, relaxed: bool) -> None:
    """Show exactly which evidence is complete and which remains a DFT/experiment task."""
    hull_ready = validation.get("energy_above_hull") is not None
    stages = [
        ("CANDIDATE\nGENERATION", True),
        ("STRUCTURAL\nADMISSION", validation.get("is_valid") is True),
        ("MLFF\nRELAXATION", relaxed),
        ("LOCAL PHASE\nCOMPARISON", hull_ready),
        ("TARGET PROPERTY\nVALIDATION", False),
    ]
    figure, axis = plt.subplots(figsize=(12, 3.8), facecolor=SURFACE)
    axis.set_facecolor(SURFACE)
    axis.set_xlim(0, len(stages)); axis.set_ylim(0, 1); axis.axis("off")
    figure.text(0.06, 0.83, "EVIDENCE COVERAGE", color=TEXT, fontsize=21, fontweight="bold")
    figure.text(0.06, 0.755, "Complete evidence is separated from work reserved for DFT or experiment.", color=ACCENT, fontsize=10)
    for index, (label, completed) in enumerate(stages):
        background = "#123B62" if completed else "#102B47"
        accent = "#63C7FF" if completed else "#FFD166"
        state = "COMPLETE" if completed else "PENDING"
        rectangle = plt.Rectangle((index + 0.06, 0.20), 0.84, 0.36, facecolor=background, edgecolor=accent, linewidth=1.5)
        axis.add_patch(rectangle)
        axis.text(index + 0.48, 0.47, state, ha="center", color=accent, fontsize=9, fontweight="bold")
        axis.text(index + 0.48, 0.29, label, ha="center", color=TEXT, fontsize=10, linespacing=1.35)
        if index < len(stages) - 1:
            axis.text(index + 0.93, 0.38, ">", color=ACCENT, fontsize=17, ha="center")
    focus = list((constraints.get("validation_targets") or {}).keys())
    if focus:
        axis.text(0.06, 0.07, "Pending target-property evidence: " + ", ".join(focus).replace("_", " "), color=MUTED, fontsize=10)
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def try_export_glb(structure: Structure, output: Path) -> str | None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from tools.structure_to_glb import export_glb_mpstyle

        info = export_glb_mpstyle(
            structure,
            str(output),
            supercell=(2, 2, 2),
            # The GIF consumes the same standardized 2×2×2 structure,
            # CrystalNN graph and recognised polyhedra; it only adds rotation.
            poly_mode="auto",
            draw_bonds=True,
            draw_coordination_connections=False,
            draw_periodic_boundary_bonds=False,
            draw_lattice_outline=False,
        )
        if not info.get("ok"):
            print("GLB export did not report a successful result", flush=True)
        return str(output) if info.get("ok") else None
    except Exception as exc:
        print(f"GLB export failed: {type(exc).__name__}", flush=True)
        return None


def render_omatg_comparison(manifest, output):
    from src.material_workflow.omatg_chart import build_comparison_data, finite_number, stability_number

    data = build_comparison_data(manifest)
    rows = data["comparison_rows"]
    colors = {"passed": "#3FC49A", "review": "#FFD166", "failed": "#FF9170", "incomplete": "#E4BF71", "duplicate": "#8DA6BB"}
    figure = plt.figure(figsize=(14.8, max(9.5, 7.0 + len(rows) * 0.5)), facecolor=SURFACE)
    figure.text(0.035, 0.946, data["title"], color=TEXT, fontsize=23, fontweight="bold")
    figure.text(0.035, 0.905, data["scope"], color=MUTED, fontsize=11.5)
    passing = [row for row in data["rows"] if row["state"] in {"passed", "review"}]
    if passing:
        priority = f'优选结构（{passing[0]["display_name"]}）{passing[0]["name"]} {passing[0]["formula"]}'
        lead = "建议先验证：" + priority + ("；稳定性高于参考线，需高精度复核。" if passing[0]["state"] == "review" else "。")
    else:
        lead = "先按具体原因处理：检查原子排布、恢复结构优化或核对参考相计算。"
    figure.text(0.035, 0.877, lead, color=ACCENT, fontsize=11.5)

    flow = figure.add_axes([0.035, 0.705, 0.93, 0.16])
    flow.set_xlim(0, 6); flow.set_ylim(0, 1); flow.axis("off")
    for index, stage in enumerate(data["stages"]):
        face = "#123E3C" if index == 5 and stage["count"] else PANEL
        edge = colors["passed"] if index == 5 and stage["count"] else PANEL_EDGE
        flow.add_patch(plt.Rectangle((index + 0.015, 0.035), 0.91, 0.93, facecolor=face, edgecolor=edge, linewidth=1.5))
        flow.text(index + 0.47, 0.80, f'{index + 1}. {stage["label"]}', color=TEXT, fontsize=11.5, ha="center", fontweight="bold")
        flow.text(index + 0.47, 0.47, str(stage["count"]), color=colors["passed"] if index == 5 and stage["count"] else TEXT, fontsize=29, ha="center", fontweight="bold")
        flow.text(index + 0.47, 0.20, stage["caption"], color=MUTED, fontsize=9.5, ha="center", va="center", linespacing=1.35)
        if index < 5:
            flow.text(index + 0.97, 0.5, "›", color=MUTED, fontsize=20, ha="center", va="center")
    figure.text(0.035, 0.672, "从左到右看：先确认组成与原子排布，再优化结构、比较稳定性，最后保留可比较结构并合并相似结构。", color=MUTED, fontsize=10.8)

    reasons = figure.add_axes([0.035, 0.145, 0.405, 0.49])
    reasons.set_xlim(0, 1); reasons.set_ylim(0, 1)
    reasons.axis("off")
    reasons.add_patch(plt.Rectangle((0, 0), 1, 1, transform=reasons.transAxes, facecolor=PANEL, edgecolor=PANEL_EDGE))
    remaining = data["stages"][0]["count"] - len(passing)
    reason_title = f"其余 {remaining} 个结构的评价结果" if remaining and passing else f"全部 {remaining} 个结构的评价结果" if remaining else "候选结构的评价完成情况"
    reasons.text(0.045, 0.924, reason_title, color=TEXT, fontsize=15, fontweight="bold")
    reasons.text(0.045, 0.856, "橙色：条件未通过  ·  黄色：结果缺失  ·  灰色：已合并", color=MUTED, fontsize=10)
    step = min(0.157, 0.72 / max(len(data["reasons"]), 1))
    for index, reason in enumerate(data["reasons"]):
        y = 0.758 - index * step
        reasons.scatter([0.06], [y], color=colors[reason["state"]], s=55)
        reasons.text(0.10, y, reason["label"], color=TEXT, fontsize=12, va="center")
        reasons.text(0.935, y, f'{reason["count"]} 个', color=colors[reason["state"]], fontsize=13, va="center", ha="right", fontweight="bold")
        reasons.text(0.10, y - min(0.06, step * 0.38), reason["detail"], color=MUTED, fontsize=10, va="center")
    if not data["reasons"]:
        message = "全部候选均可继续比较。" + ("黄色结构需复核稳定性。" if data["outcomes"]["review"] else "") if data["rows"] else "本次尚未生成可检查的候选。"
        reasons.text(0.08, 0.57, message, color=TEXT, fontsize=12)
    reasons.text(0.045, 0.028, "完整候选及具体原因，保留在报告明细表中。", color=MUTED, fontsize=9.5)

    figure.add_artist(plt.Rectangle((0.465, 0.145), 0.50, 0.49, transform=figure.transFigure, facecolor=PANEL, edgecolor=PANEL_EDGE, zorder=0))
    figure.text(0.485, 0.598, "候选晶体的稳定性比较", color=TEXT, fontsize=15, fontweight="bold")
    figure.text(0.485, 0.561, "差值越小越有利；本批次按保存的稳定性上限判定。" if data["hard_limit"] else "差值越小越有利；超过参考线仍可比较，但需复核。", color=MUTED, fontsize=10)
    axis = figure.add_axes([0.655, 0.245, 0.215, 0.255], facecolor=PANEL)
    axis.tick_params(colors=MUTED, labelsize=9)
    for spine in axis.spines.values():
        spine.set_visible(False)
    if rows:
        values = [row["hull"] for row in rows if row["hull"] is not None]
        threshold = data["threshold"]
        max_value = max(values + ([threshold] if threshold is not None else []) + [0.01])
        lower = min(values + [0.0]) * 1.15
        upper = max_value * 1.2
        axis.set_xlim(lower, upper)
        axis.set_ylim(len(rows) - 0.45, -0.65)
        if threshold is not None:
            axis.axvspan(lower, threshold, color=colors["passed"], alpha=0.10)
            axis.axvline(threshold, color=colors["incomplete"], linestyle="--", linewidth=1.7)
            hard_limit = data["hard_limit"]
            axis.text(threshold, 1.025, f"{'硬性上限' if hard_limit else '参考线'} {threshold:g}", transform=axis.get_xaxis_transform(), color=colors["incomplete"], ha="center", fontsize=9.5)
        axis.set_yticks(range(len(rows)), [f'{row["name"]}\n{row["formula"]} · {row["display_name"]}' for row in rows])
        axis.tick_params(axis="y", labelcolor=TEXT, labelsize=9.5, length=0, pad=9)
        axis.grid(axis="x", color=PANEL_EDGE, alpha=0.35, linewidth=0.7)
        for index, row in enumerate(rows):
            value = row["hull"]
            if value is None:
                axis.text(1.045, index, "没有稳定性数值\n" + row["missing_hull_label"], transform=axis.get_yaxis_transform(), color=colors["incomplete"], va="center", fontsize=9.3, linespacing=1.25)
                continue
            shade = colors[row["state"]]
            axis.barh(index, value, height=0.43, color=shade, alpha=0.85, zorder=2)
            # A point preserves a genuine zero; never lengthen it into a fake bar.
            axis.scatter([value], [index], color=shade, s=42, zorder=4, clip_on=False)
            label = row["status_label"]
            axis.text(1.045, index, f"{stability_number(value, threshold)}\n{label}", transform=axis.get_yaxis_transform(), color=shade, fontsize=9.3, va="center", linespacing=1.25)
        selected = (manifest.get("generation", {}).get("metadata") or {}).get("selected_dng_composition") or {}
        selected_hull = finite_number(selected.get("energy_above_hull"))
        if selected_hull is not None:
            axis.axvline(selected_hull, color=ACCENT, linestyle=":", label=f"来源 DNG：{selected_hull:.5f}")
            axis.legend(facecolor=PANEL, labelcolor=TEXT, fontsize=8, loc="lower right")
        axis.set_xlabel("稳定性差值（eV/atom，越低越优）", color=MUTED, fontsize=10, labelpad=9)
    else:
        axis.axis("off")
        axis.text(-0.25, 0.50, "本次没有收敛结构可供稳定性比较\n停止阶段与原因见左侧及报告明细", transform=axis.transAxes, color=MUTED, fontsize=12, va="center", linespacing=1.7)
    figure.text(0.035, 0.085, "读图提示：“稳定性差值”表示相对其他相组合多出的能量；圆点标示零值，缺少数值时标注具体原因。", color=MUTED, fontsize=10.5)
    figure.text(0.035, 0.045, "结果用于挑选验证对象。结构由 OMatG 生成，稳定性为 MatterSim + MP2020 混合基准估算；后续需更高精度计算或实验验证。", color=MUTED, fontsize=10)
    figure.savefig(output, dpi=160, facecolor=figure.get_facecolor())
    plt.close(figure)


def main() -> None:
    args = arguments()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    is_omatg = manifest.get("generation", {}).get("backend") == "omatg"
    if is_omatg and not manifest.get("ranked_candidates"):
        args.output_dir.mkdir(parents=True, exist_ok=True)
        comparison = args.output_dir / "candidate_comparison.png"
        render_omatg_comparison(manifest, comparison)
        output = {"status": "ok", "assets": [{"path": str(comparison), "type": "MaterialsPNG", "name": "候选结构的评价过程与稳定性比较"}], "formal_structure_ready": False, "glb_available": False}
        (args.output_dir / "presentation_manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False))
        return
    candidate, validation = get_top(manifest)
    cif_path = Path(candidate["cif_path"])
    structure = Structure.from_file(cif_path)
    artifacts = validation.get("artifacts") or {}
    structure_admitted = validation.get("is_valid") is True
    relaxed_structure_value = artifacts.get("relaxed_structure_path")
    relaxed_path = Path(relaxed_structure_value) if relaxed_structure_value else None
    relaxed = False
    if relaxed_path is not None and relaxed_path.is_file():
        try:
            from ase.io import read
            from pymatgen.io.ase import AseAtomsAdaptor

            structure = AseAtomsAdaptor.get_structure(read(relaxed_path, index=-1))
            if structure.composition.reduced_formula != Structure.from_file(cif_path).composition.reduced_formula:
                raise ValueError("Relaxed structure composition does not match the selected candidate CIF.")
            relaxed = True
        except Exception:
            # A mismatched or unreadable relaxed structure must not be shown as
            # evidence for this candidate.  Fall back to its own source CIF.
            structure = Structure.from_file(cif_path)
    elif len(manifest.get("ranked_candidates") or []) == 1:
        # Backward compatibility for historical single-candidate jobs only.
        # Multi-frame legacy files are intentionally never used for a
        # multi-candidate result because their final frame is ambiguous.
        legacy_structure_value = artifacts.get("relaxed_structures")
        legacy_path = Path(legacy_structure_value) if legacy_structure_value else None
        if legacy_path is not None and legacy_path.is_file():
            try:
                from ase.io import read
                from pymatgen.io.ase import AseAtomsAdaptor

                structure = AseAtomsAdaptor.get_structure(read(legacy_path, index=-1))
                relaxed = True
            except Exception:
                pass
    formula = candidate.get("formula_pretty") or structure.composition.reduced_formula
    args.output_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    if not is_omatg:
        design_brief = args.output_dir / "design_brief.png"
        render_design_brief(design_brief, structure, manifest.get("constraints") or {})
        assets.append({"path": str(design_brief), "type": "MaterialsPNG", "name": "材料组成与评价条件", "docs": "允许的化学空间、当前晶体的实际元素，以及本次稳定性条件和关注性能。"})
    if manifest["generation"].get("backend") == "omatg":
        comparison = args.output_dir / "candidate_comparison.png"
        render_omatg_comparison(manifest, comparison)
        assets.append({"path": str(comparison), "type": "MaterialsPNG", "name": "晶体生成与评价结果", "docs": "候选编号与报告一致；列出实际完成数量、未满足条件及结果缺失原因，并比较已完成结构优化的候选稳定性。"})
    formal_structure_ready = structure_admitted and relaxed
    if manifest["generation"].get("backend") == "omatg":
        formal_structure_ready = formal_structure_ready and (validation.get("metadata") or {}).get("mattersim", {}).get("relaxation_converged") is True
    if formal_structure_ready:
        rotation_gif = args.output_dir / "candidate_rotation.gif"
        scorecard = args.output_dir / "stability_scorecard.png"
        glb_path = args.output_dir / "candidate_structure.glb"
        from src.material_workflow.omatg_chart import scheme_names
        ranked_ids = [row.get("candidate", {}).get("candidate_id") for row in manifest.get("ranked_candidates") or []]
        display_name = scheme_names((identifier for identifier in ranked_ids if identifier), manifest.get("taskid", "")).get(candidate.get("candidate_id"), "")
        priority = validation.get("metadata", {}).get("screening", {}).get("state") == "passed"
        label = ("优选结构" if priority else "待复核结构") + f"（{display_name}）" if is_omatg else "候选晶体"
        render_rotation_gif(structure, rotation_gif, formula, caption=f"{label}\n{formula}")
        if not is_omatg:
            render_scorecard(scorecard, formula, validation, manifest.get("constraints") or {}, display_name)
        glb = try_export_glb(structure, glb_path)
        assets.extend([
            {"path": str(rotation_gif), "type": "MaterialsPNG", "name": f"{label}的旋转预览 · {formula}", "docs": "这个候选已完成结构检查和结构优化，可从不同角度查看原子排布。"},
        ])
        if not is_omatg:
            assets.append({"path": str(scorecard), "type": "MaterialsPNG", "name": f"候选晶体的稳定性估计（{formula}）", "docs": "候选相对竞争相组合的能量差及复核参考线。"})
        if glb:
            assets.append({"path": glb, "type": "MaterialsGLB", "name": f"{label}的三维模型 · {formula}", "docs": "颜色区分组成元素，连线展示相邻原子的排布；三维模型与旋转预览对应同一保留结构。"})
    readiness_message = "候选已完成结构检查和结构优化，可查看结构模型及报告中的评价结果。" if formal_structure_ready else "本轮未形成通过检查且优化收敛的结构，具体停止阶段和原因见报告；恢复评价后可生成三维预览。"
    output = {"status": "ok", "formula": formula, "assets": assets, "glb_available": bool(formal_structure_ready and glb), "formal_structure_ready": formal_structure_ready, "message": readiness_message}
    if is_omatg and formal_structure_ready and not glb:
        output["glb_error_code"] = "export_failed"
    (args.output_dir / "presentation_manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
