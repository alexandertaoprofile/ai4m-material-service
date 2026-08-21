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
    subtitle = "MatterGen candidate after MatterSim relaxation" if relaxed else "MatterGen-generated candidate · relaxation pending"
    figure.text(0.5, 0.04, subtitle, ha="center", color=MUTED, fontsize=10)
    figure.tight_layout(rect=(0, 0.06, 1, 1))
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_rotation_gif(structure: Structure, output: Path, formula: str) -> None:
    figure = plt.figure(figsize=(6, 6), facecolor=SURFACE)
    axis = figure.add_subplot(111, projection="3d")
    writer = PillowWriter(fps=8)
    displayed = display_supercell(structure)
    connections = structure_connections(displayed)
    polyhedra = structure_polyhedra(displayed)
    with writer.saving(figure, str(output), dpi=110):
        for angle in range(0, 360, 20):
            axis.clear()
            draw_structure(axis, displayed, f"候选晶体旋转预览 · {formula}", angle=angle,
                           connections=connections, polyhedra=polyhedra)
            writer.grab_frame()
    plt.close(figure)


def render_scorecard(output: Path, formula: str, validation: dict, constraints: dict) -> None:
    hull = validation.get("energy_above_hull")
    formation = validation.get("formation_energy_per_atom")
    threshold = float((constraints.get("target_properties") or {}).get("energy_above_hull", 0.05))
    figure = plt.figure(figsize=(12, 5.4), facecolor=SURFACE)
    left = figure.add_axes([0.06, 0.24, 0.50, 0.47], facecolor=PANEL)
    right = figure.add_axes([0.62, 0.18, 0.33, 0.66], facecolor=PANEL)
    figure.text(0.06, 0.82, "热力学初筛", color=TEXT, fontweight="bold", fontsize=20)
    figure.text(0.06, 0.765, "MatterSim 势函数 + Materials Project 同元素竞争相", color=ACCENT, fontsize=10)
    if hull is None:
        left.text(0.5, 0.55, "热力学计算待完成", color=TEXT, fontsize=19, ha="center", transform=left.transAxes)
    else:
        max_value = max(threshold * 2.2, float(hull) * 1.35, 0.08)
        left.barh([0], [max_value], color="#173D61", height=0.32)
        left.barh([0], [float(hull)], color="#3FC49A" if hull <= threshold else "#FF7777", height=0.32)
        left.axvline(threshold, color="#FFD166", linewidth=2, linestyle="--")
        left.set_xlim(0, max_value); left.set_ylim(-0.48, 0.48); left.set_yticks([])
        left.text(float(hull), 0.27, f"{float(hull) * 1000:.1f} meV/atom", color=TEXT, ha="center", fontsize=14, fontweight="bold")
        left.text(threshold, -0.37, f"筛选阈值 = {threshold:.3f}", color="#FFD166", ha="center", fontsize=10)
        left.set_xlabel("高于凸包能（eV/atom）", color=MUTED, labelpad=8)
        left.tick_params(axis="x", colors=MUTED)
    for spine in left.spines.values(): spine.set_visible(False)
    right.axis("off")
    right.text(0.06, 0.88, formula, color=TEXT, fontsize=25, fontweight="bold", transform=right.transAxes)
    right.text(0.06, 0.79, "候选结构 · 计算证据摘要", color=ACCENT, fontsize=10, transform=right.transAxes)
    rows = [
        ("形成能", "待计算" if formation is None else f"{float(formation):.4f} eV/atom"),
        ("高于凸包能", "待计算" if hull is None else f"{float(hull):.4f} eV/atom"),
        ("下一步", "建议进行 DFT 验证" if hull is not None and hull <= threshold else ("热力学计算待完成" if hull is None else "建议复核或重新生成")),
        ("证据来源", "机器学习势函数 + 同元素竞争相" if hull is not None else "基础结构检查"),
    ]
    y = 0.65
    for label, value in rows:
        right.text(0.06, y, label, color=ACCENT, fontsize=9, transform=right.transAxes)
        right.text(0.06, y - 0.075, value, color=TEXT, fontsize=12, transform=right.transAxes)
        y -= 0.17
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_design_brief(output: Path, structure: Structure, constraints: dict) -> None:
    """Render the input side of the story without claiming uncomputed properties."""
    figure = plt.figure(figsize=(12, 4.4), facecolor=SURFACE)
    figure.text(0.06, 0.82, "设计约束卡", color=TEXT, fontsize=22, fontweight="bold")
    notes = " ".join(str(value) for value in (constraints.get("notes") or []))
    source = "领域模板约束" if "领域起始模板" in notes else "用户及上下文约束"
    figure.text(0.06, 0.755, source, color=ACCENT, fontsize=10)

    elements = []
    for item in structure.composition.elements:
        symbol = item.symbol
        if symbol not in elements:
            elements.append(symbol)
    for index, element in enumerate(elements):
        x = 0.08 + index * 0.105
        circle = plt.Circle((x, 0.48), 0.041, color=color(element), transform=figure.transFigure)
        figure.add_artist(circle)
        figure.text(x, 0.48, element, ha="center", va="center", color=SURFACE, fontsize=11, fontweight="bold")
    figure.text(0.06, 0.25, "元素体系", color=ACCENT, fontsize=10)

    target_properties = constraints.get("target_properties") or {}
    hull = target_properties.get("energy_above_hull")
    target_text = f"稳定性偏好：E_hull ≤ {float(hull):.3f} eV/atom" if hull is not None else "按元素体系条件生成"
    figure.text(0.56, 0.60, "生成目标", color=ACCENT, fontsize=10)
    figure.text(0.56, 0.52, target_text, color=TEXT, fontsize=17, fontweight="bold")

    target_labels = {
        "high_temperature_strength": "高温强度", "creep_resistance": "抗蠕变能力",
        "oxidation_resistance": "抗氧化能力", "thermal_fatigue": "热疲劳性能",
        "additive_manufacturability": "增材制造适配性", "ionic_conductivity": "离子电导率",
        "band_gap": "带隙验证",
    }
    focus = [target_labels.get(name, name.replace("_", " ")) for name in (constraints.get("validation_targets") or {})]
    focus_text = " · ".join(focus) if focus else "结构与热力学初筛"
    figure.text(0.56, 0.34, "后续验证关注点", color=ACCENT, fontsize=10)
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
        return str(output) if info.get("ok") else None
    except Exception:
        return None


def main() -> None:
    args = arguments()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
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
    design_brief = args.output_dir / "design_brief.png"
    render_design_brief(design_brief, structure, manifest.get("constraints") or {})
    assets = [
        {"path": str(design_brief), "type": "MaterialsPNG", "name": "设计约束卡", "docs": "元素体系、生成引导目标与待验证的工程关注点"},
    ]
    formal_structure_ready = structure_admitted and relaxed
    if formal_structure_ready:
        rotation_gif = args.output_dir / "candidate_rotation.gif"
        scorecard = args.output_dir / "stability_scorecard.png"
        glb_path = args.output_dir / "candidate_structure.glb"
        render_rotation_gif(structure, rotation_gif, formula)
        render_scorecard(scorecard, formula, validation, manifest.get("constraints") or {})
        glb = try_export_glb(structure, glb_path)
        assets.extend([
            {"path": str(rotation_gif), "type": "MaterialsPNG", "name": f"候选晶体旋转预览（{formula}）", "docs": "这个候选已完成结构检查和结构优化，可从不同角度查看原子排布。"},
            {"path": str(scorecard), "type": "MaterialsPNG", "name": f"热力学初筛评分卡（{formula}）", "docs": "基于结构优化结果的热力学初筛参考。"},
        ])
        if glb:
            assets.append({"path": glb, "type": "MaterialsGLB", "name": f"候选晶体三维模型（{formula}）", "docs": "该 GLB 与旋转预览使用同一标准化 2×2×2 结构、元素色板、CrystalNN 周期近邻连线与识别出的局部配位多面体；GIF 仅增加自动旋转。连线用于阅读结构拓扑，不代表计算得到的键级或键能。"})
    readiness_message = "候选已完成结构检查和结构优化，可查看结构模型与热力学初筛。" if formal_structure_ready else "候选仍在结构检查或结构优化阶段，暂不展示正式结构模型和热力学初筛结果。"
    output = {"status": "ok", "formula": formula, "assets": assets, "glb_available": bool(formal_structure_ready and glb), "formal_structure_ready": formal_structure_ready, "message": readiness_message}
    (args.output_dir / "presentation_manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
