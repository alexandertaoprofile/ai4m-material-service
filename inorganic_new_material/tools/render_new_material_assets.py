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
from matplotlib.animation import PillowWriter
from pymatgen.core import Structure


COLORS = {
    "Nb": "#59C3C3", "Mo": "#F4A261", "Ta": "#8E7DBE", "W": "#E9C46A",
    "Li": "#9AD0F5", "P": "#F4A261", "S": "#F9D65C", "Cl": "#79C267",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def color(element: str) -> str:
    return COLORS.get(element, "#AAB7C4")


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


def draw_structure(ax, structure: Structure, title: str, angle: int = 30) -> None:
    coords = np.asarray(structure.cart_coords)
    elements = [site.specie.symbol for site in structure]
    for element in sorted(set(elements)):
        ids = [index for index, value in enumerate(elements) if value == element]
        ax.scatter(coords[ids, 0], coords[ids, 1], coords[ids, 2], s=100, color=color(element),
                   edgecolors="#14213D", linewidths=0.65, label=element, depthshade=True)
    limits = axes_limits(coords)
    ax.set_xlim(*limits[0]); ax.set_ylim(*limits[1]); ax.set_zlim(*limits[2])
    ax.set_box_aspect([1, 1, 1])
    ax.view_init(elev=22, azim=angle)
    ax.set_title(title, color="#EAF2FF", fontsize=12, pad=8)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
    ax.set_facecolor("#10243E")
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_facecolor("#10243E")
        axis.pane.set_edgecolor("#406080")
    legend = ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=2)
    for text in legend.get_texts():
        text.set_color("#EAF2FF")


def render_structure_png(structure: Structure, output: Path, formula: str) -> None:
    figure = plt.figure(figsize=(8, 7), facecolor="#0B172A")
    axis = figure.add_subplot(111, projection="3d")
    draw_structure(axis, structure, f"Generated candidate · {formula}")
    figure.text(0.5, 0.04, "MatterGen candidate after MatterSim relaxation", ha="center", color="#A8C7E8", fontsize=10)
    figure.tight_layout(rect=(0, 0.06, 1, 1))
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def render_rotation_gif(structure: Structure, output: Path, formula: str) -> None:
    figure = plt.figure(figsize=(6, 6), facecolor="#0B172A")
    axis = figure.add_subplot(111, projection="3d")
    writer = PillowWriter(fps=8)
    with writer.saving(figure, str(output), dpi=110):
        for angle in range(0, 360, 20):
            axis.clear()
            draw_structure(axis, structure, f"{formula} · rotation", angle=angle)
            writer.grab_frame()
    plt.close(figure)


def render_scorecard(output: Path, formula: str, validation: dict, constraints: dict) -> None:
    hull = validation.get("energy_above_hull")
    formation = validation.get("formation_energy_per_atom")
    threshold = float((constraints.get("target_properties") or {}).get("energy_above_hull", 0.05))
    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 5), facecolor="#0B172A", gridspec_kw={"width_ratios": [1.2, 1]})
    for axis in (left, right):
        axis.set_facecolor("#10243E")
    left.set_title("THERMODYNAMIC SCREEN", loc="left", color="#EAF2FF", fontweight="bold", fontsize=14, pad=16)
    if hull is None:
        left.text(0.05, 0.55, "E_hull pending", color="#EAF2FF", fontsize=22, transform=left.transAxes)
    else:
        max_value = max(threshold * 2.2, float(hull) * 1.35, 0.08)
        left.barh([0], [max_value], color="#203B59", height=0.24)
        left.barh([0], [float(hull)], color="#53D3A1" if hull <= threshold else "#F36C6C", height=0.24)
        left.axvline(threshold, color="#F5C451", linewidth=2, linestyle="--")
        left.text(float(hull), 0.22, f"{float(hull) * 1000:.1f} meV/atom", color="#EAF2FF", ha="center", fontsize=15, fontweight="bold")
        left.text(threshold, -0.3, "screen threshold", color="#F5C451", ha="center", fontsize=9)
        left.set_xlim(0, max_value); left.set_yticks([]); left.set_xlabel("Energy above hull (eV/atom)", color="#A8C7E8")
        left.tick_params(axis="x", colors="#A8C7E8")
    for spine in left.spines.values(): spine.set_visible(False)
    right.axis("off")
    right.text(0.04, 0.88, formula, color="#FFFFFF", fontsize=23, fontweight="bold", transform=right.transAxes)
    rows = [
        ("FORMATION ENERGY", "N/A" if formation is None else f"{float(formation):.4f} eV/atom"),
        ("E ABOVE HULL", "N/A" if hull is None else f"{float(hull):.4f} eV/atom"),
        ("DECISION", "SHORTLIST FOR DFT" if hull is not None and hull <= threshold else "REVIEW / REGENERATE"),
        ("EVIDENCE", "MatterSim + MP local phase diagram"),
    ]
    y = 0.7
    for label, value in rows:
        right.text(0.04, y, label, color="#7BA7D1", fontsize=8, transform=right.transAxes)
        right.text(0.04, y - 0.09, value, color="#EAF2FF", fontsize=11, transform=right.transAxes)
        y -= 0.19
    figure.tight_layout(pad=2)
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


def try_export_glb(cif_path: Path, output: Path) -> str | None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from tools.structure_to_glb import export_glb_mpstyle

        info = export_glb_mpstyle(Structure.from_file(cif_path), str(output), poly_mode="none")
        return str(output) if info.get("ok") else None
    except Exception:
        return None


def main() -> None:
    args = arguments()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    candidate, validation = get_top(manifest)
    cif_path = Path(candidate["cif_path"])
    structure = Structure.from_file(cif_path)
    relaxed_path = Path((validation.get("artifacts") or {}).get("relaxed_structures") or "")
    if relaxed_path.exists():
        try:
            from ase.io import read
            from pymatgen.io.ase import AseAtomsAdaptor

            structure = AseAtomsAdaptor.get_structure(read(relaxed_path, index=-1))
        except Exception:
            pass
    formula = candidate.get("formula_pretty") or structure.composition.reduced_formula
    args.output_dir.mkdir(parents=True, exist_ok=True)
    structure_png = args.output_dir / "candidate_structure.png"
    rotation_gif = args.output_dir / "candidate_rotation.gif"
    scorecard = args.output_dir / "stability_scorecard.png"
    render_structure_png(structure, structure_png, formula)
    render_rotation_gif(structure, rotation_gif, formula)
    render_scorecard(scorecard, formula, validation, manifest.get("constraints") or {})
    glb_path = args.output_dir / "candidate_structure.glb"
    glb = try_export_glb(cif_path, glb_path)
    assets = [
        {"path": str(structure_png), "type": "MaterialsPNG", "name": "候选晶体结构", "docs": "MatterGen 生成并经 MatterSim 松弛后的三维结构视图"},
        {"path": str(rotation_gif), "type": "MaterialsPNG", "name": "晶体结构旋转预览", "docs": "候选晶体结构旋转 GIF"},
        {"path": str(scorecard), "type": "MaterialsPNG", "name": "热力学筛选评分卡", "docs": "MatterSim--MP 热力学初筛证据"},
    ]
    if glb:
        assets.append({"path": glb, "type": "MaterialsGLB", "name": "候选晶体三维模型", "docs": "可交互查看的 GLB 晶体结构"})
    output = {"status": "ok", "formula": formula, "assets": assets, "glb_available": bool(glb)}
    (args.output_dir / "presentation_manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
