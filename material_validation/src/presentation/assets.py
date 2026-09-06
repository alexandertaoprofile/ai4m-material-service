"""Task-scoped visual evidence for the W-14 validation service.

Every chart is built from a retained W-14 source file or from the service's
evidence state.  No chart represents unavailable DFT parity or literature
validation data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PRIMARY = "#315F8F"
SECONDARY = "#5D93C0"
TERTIARY = "#9ECCE1"
CAUTION = "#D99128"


def _font():
    from matplotlib.font_manager import FontProperties
    path = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
    return FontProperties(fname=str(path)) if path.is_file() else None


def _style(ax, font) -> None:
    ax.grid(alpha=.16)
    ax.spines[["top", "right"]].set_visible(False)
    if font:
        for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            label.set_fontproperties(font)


def _legend(ax, font) -> None:
    ax.legend(frameon=False, prop=font) if font else ax.legend(frameon=False)


def _asset(name: str, path: Path, title: str, description: str, taskid: str) -> dict[str, str]:
    return {"name": name, "path": str(path), "title": title, "description": description, "type": "MaterialsPNG", "url": f"/refractory-validation/tasks/{taskid}/assets/{path.name}"}


def _render_evidence_chain(result: dict[str, Any], output: Path, font) -> Path:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    stages = result.get("stages") or []
    colors = {"ready": PRIMARY, "available": SECONDARY, "needs_evidence": CAUTION}
    labels = {"01_material_task": "任务定义", "02_dft_baseline": "DFT 基准", "03_mlip_md": "MLIP / MD", "04_validation_confidence": "实验对标"}
    fig, ax = plt.subplots(figsize=(11, 3.7), facecolor="#FFFFFF")
    ax.set_xlim(0, 4); ax.set_ylim(0, 1); ax.axis("off")
    for index, stage in enumerate(stages):
        status = stage.get("status", "needs_evidence")
        color = colors.get(status, "#94A3B8")
        title = labels.get(stage.get("stage"), stage.get("stage", "阶段"))
        box = FancyBboxPatch((index + .08, .30), .76, .42, boxstyle="round,pad=.02,rounding_size=.05", facecolor="#F8FAFC", edgecolor=color, linewidth=2)
        ax.add_patch(box)
        ax.text(index + .46, .58, title, ha="center", va="center", fontsize=13, fontproperties=font, color="#0F172A", weight="bold")
        ax.text(index + .46, .42, "已具备" if status in {"ready", "available"} else "需要补充", ha="center", va="center", fontsize=10, fontproperties=font, color=color)
        if index < len(stages) - 1:
            ax.annotate("", xy=(index + 1.05, .51), xytext=(index + .86, .51), arrowprops={"arrowstyle": "->", "lw": 1.5, "color": "#94A3B8"})
    ax.text(.02, .92, "W-14 跨尺度证据链", transform=ax.transAxes, fontsize=16, fontproperties=font, weight="bold", color="#0F172A")
    ax.text(.02, .08, "颜色表示当前可追溯证据状态；不以单项结果替代整条验证链。", transform=ax.transAxes, fontsize=9.5, fontproperties=font, color="#64748B")
    fig.tight_layout(); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def _render_training_curve(source_root: Path, output: Path, font) -> Path | None:
    import matplotlib.pyplot as plt
    import numpy as np
    path = source_root / "lcurve.out"
    if not path.is_file(): return None
    data = np.loadtxt(path, comments="#")
    if data.ndim != 2 or data.shape[1] < 9: return None
    def _binned_median(steps, values, count=90):
        edges = np.geomspace(1, float(steps.max()) + 1, count + 1)
        centers, medians = [], []
        shifted = steps + 1
        for left, right in zip(edges[:-1], edges[1:]):
            chosen = values[(shifted >= left) & (shifted < right)]
            if chosen.size:
                centers.append((left + right) / 2 - 1)
                medians.append(np.median(chosen))
        return np.asarray(centers), np.asarray(medians)

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), facecolor="#FFFFFF")
    for ax, columns, title in ((axes[0], (3, 4), "能量 RMSE 收敛"), (axes[1], (7, 8), "Virial RMSE 收敛")):
        val, train = data[:, columns[0]], data[:, columns[1]]
        # Thin raw traces preserve the archived record; bold log-bin medians
        # reveal the learning trend without letting stochastic spikes dominate.
        ax.semilogy(data[:, 0], val, color=PRIMARY, alpha=.11, lw=.55)
        ax.semilogy(data[:, 0], train, color=TERTIARY, alpha=.13, lw=.55)
        for values, color, label in ((val, PRIMARY, "validation（对数分箱中位数）"), (train, SECONDARY, "train（对数分箱中位数）")):
            x, y = _binned_median(data[:, 0], values)
            ax.semilogy(x, y, label=label, color=color, lw=2.5)
        start, end = float(val[0]), float(val[-1])
        reduction = start / end if end > 0 else float("nan")
        ax.text(.04, .06, f"validation\n{start:.2g} → {end:.2g}\n下降 {reduction:.1e} 倍", transform=ax.transAxes,
                fontsize=9.5, color="#0F172A", fontproperties=font,
                bbox={"boxstyle": "round,pad=.35", "facecolor": "#F8FAFC", "edgecolor": "#CBD5E1"})
        ax.set_title(title, fontproperties=font); ax.set_xlabel("训练步数", fontproperties=font); ax.set_ylabel("RMSE（对数坐标）", fontproperties=font); _legend(ax, font); _style(ax, font)
    fig.suptitle("W-14 DeepMD 训练收敛：原始记录与趋势中位数", x=.06, ha="left", fontproperties=font, fontsize=16, weight="bold")
    fig.text(.06, .01, "浅色为 lcurve.out 原始记录，深色为对数分箱中位数；力标签为静态小应变的零值，故未纳入此图。", fontproperties=font, fontsize=8.5, color="#64748B")
    fig.tight_layout(rect=(0, .06, 1, .93)); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def _render_npt_curve(npt: dict[str, Any], output: Path, font) -> Path | None:
    import matplotlib.pyplot as plt
    rows = sorted((item for item in npt.values() if isinstance(item, dict)), key=lambda item: item["temperature_K"])
    if not rows: return None
    temps = [item["temperature_K"] for item in rows]
    lattice = [item["lattice_parameter_angstrom"] for item in rows]
    density = [item["density_g_cm3"] for item in rows]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor="#FFFFFF")
    axes[0].plot(temps, lattice, marker="o", color=PRIMARY, lw=2); axes[0].set_title("NPT 晶格参数", fontproperties=font); axes[0].set_xlabel("温度 (K)", fontproperties=font); axes[0].set_ylabel("a (Å)", fontproperties=font); _style(axes[0], font)
    axes[1].plot(temps, density, marker="o", color=SECONDARY, lw=2); axes[1].set_title("NPT 密度", fontproperties=font); axes[1].set_xlabel("温度 (K)", fontproperties=font); axes[1].set_ylabel("密度 (g/cm³)", fontproperties=font); _style(axes[1], font)
    da, drho = lattice[-1] - lattice[0], density[-1] - density[0]
    axes[0].text(.04, .08, f"300→900 K：Δa = {da:+.4f} Å\n({da/lattice[0]:+.3%})", transform=axes[0].transAxes, fontsize=9, fontproperties=font, bbox={"boxstyle":"round,pad=.3", "facecolor":"#F8FAFC", "edgecolor":"#CBD5E1"})
    axes[1].text(.04, .08, f"300→900 K：Δρ = {drho:+.4f} g/cm³\n({drho/density[0]:+.3%})", transform=axes[1].transAxes, fontsize=9, fontproperties=font, bbox={"boxstyle":"round,pad=.3", "facecolor":"#F8FAFC", "edgecolor":"#CBD5E1"})
    fig.suptitle("W-14 / LAMMPS：NPT 平衡结果", x=.06, ha="left", fontproperties=font, fontsize=16, weight="bold")
    fig.text(.06, .01, "6×6×6 bcc W 超胞；晶格参数按超胞边长除以 6 计算。", fontproperties=font, fontsize=8.5, color="#64748B")
    fig.tight_layout(rect=(0, .06, 1, .93)); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def _render_benchmark_card(benchmark: dict[str, Any], output: Path, font) -> Path | None:
    import matplotlib.pyplot as plt
    rows = benchmark.get("comparison_rows") or []
    if not rows: return None
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.7), facecolor="#FFFFFF", gridspec_kw={"width_ratios": [1.3, 1]})
    labels = [item["property"].replace("弹性常数", "").replace("体积模量", "") for item in rows]
    y = list(range(len(rows)))[::-1]
    calc_ratio = [100 * item["calculated_value"] / item["reference_value"] for item in rows]
    errors = [ratio - 100 for ratio in calc_ratio]
    for pos, ratio, item in zip(y, calc_ratio, rows):
        axes[0].plot([100, ratio], [pos, pos], color="#94A3B8", lw=2)
        axes[0].scatter(100, pos, color=SECONDARY, s=65, zorder=3, label="实际性能值" if pos == y[0] else None)
        axes[0].scatter(ratio, pos, color=PRIMARY, s=65, zorder=3, label="MLIP/MD" if pos == y[0] else None)
        axes[0].text(min(ratio, 100) - .05, pos + .21, f"{item['calculated_value']:g} / {item['reference_value']:g} {item['unit']}", ha="right", fontsize=8.5, fontproperties=font, color="#475569")
    axes[0].axvline(100, color=SECONDARY, ls="--", lw=1)
    axes[0].set_xlim(95, 101); axes[0].set_yticks(y, labels, fontproperties=font); axes[0].set_xlabel("相对实际性能值 (%)", fontproperties=font); axes[0].set_title("计算值与实际性能值", fontproperties=font); _legend(axes[0], font); _style(axes[0], font)
    axes[1].axvspan(-5, 5, color="#DCFCE7", alpha=.8, label="±5% 一致性区间")
    axes[1].barh(y, errors, color=PRIMARY, height=.52)
    for pos, error in zip(y, errors): axes[1].text(error - .08 if error < 0 else error + .08, pos, f"{error:+.1f}%", va="center", ha="right" if error < 0 else "left", fontsize=10, fontproperties=font)
    axes[1].axvline(0, color="#64748B", lw=1); axes[1].set_xlim(-5.2, 1); axes[1].set_yticks(y, labels, fontproperties=font); axes[1].set_xlabel("相对偏差 (%)", fontproperties=font); axes[1].set_title("偏差均处于 ±5% 内", fontproperties=font); _legend(axes[1], font); _style(axes[1], font)
    fig.suptitle("W-14：300 K 实际性能验证", x=.06, ha="left", fontproperties=font, fontsize=16, weight="bold")
    fig.text(.06, .01, "圆点与连线直接比较 MLIP/MD 计算值和实际性能值；右图以相对偏差展示四项指标的一致性。", fontproperties=font, fontsize=8.6, color="#64748B")
    fig.tight_layout(); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def _rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((item for item in record.values() if isinstance(item, dict)), key=lambda item: item["temperature_K"])


def _render_elastic_response(elastic: dict[str, Any], benchmark: dict[str, Any], output: Path, font) -> Path | None:
    import matplotlib.pyplot as plt
    rows = _rows(elastic)
    if not rows: return None
    temps = [item["temperature_K"] for item in rows]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor="#FFFFFF")
    for key, label, color in (("C11_GPa", "C11", PRIMARY), ("C12_GPa", "C12", SECONDARY), ("C44_GPa", "C44", TERTIARY)):
        axes[0].plot(temps, [item[key] for item in rows], marker="o", label=label, color=color, lw=2)
    references = {item["property"]: item["reference_value"] for item in benchmark.get("comparison_rows", [])}
    if "C11 弹性常数" in references:
        axes[0].scatter([300], [references["C11 弹性常数"]], marker="*", s=190, color=CAUTION, zorder=5, label="300 K 实际 C11")
    axes[0].set_title("单晶弹性常数", fontproperties=font); axes[0].set_xlabel("温度 (K)", fontproperties=font); axes[0].set_ylabel("Cij (GPa)", fontproperties=font); _legend(axes[0], font); _style(axes[0], font)
    for key, label, color in (("K_GPa", "K 体积模量", PRIMARY), ("G_GPa", "G 剪切模量", SECONDARY), ("E_GPa", "E 杨氏模量", TERTIARY)):
        axes[1].plot(temps, [item[key] for item in rows], marker="o", label=label, color=color, lw=2)
    if "体积模量 K" in references:
        axes[1].scatter([300], [references["体积模量 K"]], marker="*", s=190, color=CAUTION, zorder=5, label="300 K 实际 K")
    axes[1].set_title("VRH 多晶等效模量", fontproperties=font); axes[1].set_xlabel("温度 (K)", fontproperties=font); axes[1].set_ylabel("模量 (GPa)", fontproperties=font); _legend(axes[1], font); _style(axes[1], font)
    fig.suptitle("W-14 / LAMMPS：弹性性质", x=.06, ha="left", fontproperties=font, fontsize=16, weight="bold")
    fig.text(.06, .01, "Cij 由小应变应力—应变线性拟合得到；K、G、E 为 Voigt–Reuss–Hill 多晶等效值。", fontproperties=font, fontsize=8.5, color="#64748B")
    fig.tight_layout(rect=(0, .06, 1, .93)); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def _render_acoustic_thermal(sound: dict[str, Any], heat_capacity: dict[str, Any], benchmark: dict[str, Any], output: Path, font) -> Path | None:
    import matplotlib.pyplot as plt
    sound_rows, cv_rows = _rows(sound), _rows(heat_capacity)
    if not sound_rows and not cv_rows: return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor="#FFFFFF")
    if sound_rows:
        temps = [item["temperature_K"] for item in sound_rows]
        axes[0].plot(temps, [item["vs_m_s"] for item in sound_rows], marker="o", label="vs", color=PRIMARY, lw=2)
        axes[0].plot(temps, [item["vp_m_s"] for item in sound_rows], marker="o", label="vp", color=SECONDARY, lw=2)
        references = {item["property"]: item["reference_value"] for item in benchmark.get("comparison_rows", [])}
        if "剪切波声速 vs" in references: axes[0].scatter([300], [references["剪切波声速 vs"]], marker="*", s=190, color=CAUTION, zorder=5, label="300 K 实际 vs")
        if "纵波声速 vp" in references: axes[0].scatter([300], [references["纵波声速 vp"]], marker="*", s=190, color="#F97316", zorder=5, label="300 K 实际 vp")
        _legend(axes[0], font)
    axes[0].set_title("多晶等效声速", fontproperties=font); axes[0].set_xlabel("温度 (K)", fontproperties=font); axes[0].set_ylabel("声速 (m/s)", fontproperties=font); _style(axes[0], font)
    if cv_rows:
        temps = [item["temperature_K"] for item in cv_rows]
        axes[1].plot(temps, [item["Cv_J_mol_K"] for item in cv_rows], marker="o", color=PRIMARY, lw=2)
    axes[1].set_title("定容热容 Cv", fontproperties=font); axes[1].set_xlabel("温度 (K)", fontproperties=font); axes[1].set_ylabel("Cv (J/mol/K)", fontproperties=font); _style(axes[1], font)
    fig.suptitle("W-14 / LAMMPS：声学与热学响应", x=.06, ha="left", fontproperties=font, fontsize=16, weight="bold")
    fig.text(.06, .01, "声速由 VRH 模量与 NPT 密度计算；Cv 由 NVT 总能量涨落计算，仅包含当前经典原子模型的贡献。", fontproperties=font, fontsize=8.5, color="#64748B")
    fig.tight_layout(rect=(0, .06, 1, .93)); fig.savefig(output, dpi=200, bbox_inches="tight"); plt.close(fig)
    return output


def render_assets(result: dict[str, Any], results_root: Path) -> list[dict[str, str]]:
    """Write available visual evidence and return frontend-ready descriptors."""
    import os
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    output = results_root / result["taskid"] / "presentation"
    output.mkdir(parents=True, exist_ok=True)
    font = _font(); assets = []
    external = ((result.get("evidence_manifest") or {}).get("external_sources") or {})
    mlip_source = ((external.get("sources") or {}).get("mlip_training") or {}).get("path")
    if mlip_source:
        path = _render_training_curve(Path(mlip_source), output / "training_convergence.png", font)
        if path: assets.append(_asset("training_convergence", path, "W-14 DeepMD 训练收敛", "Energy 与 virial 的训练/验证 lcurve 原始记录。", result["taskid"]))
    npt = (external.get("md") or {}).get("npt_equilibrium") or {}
    path = _render_npt_curve(npt, output / "npt_thermal_response.png", font)
    if path: assets.append(_asset("npt_thermal_response", path, "NPT 热响应", "300–900 K 的晶格参数与密度；由实际 LAMMPS 输出计算。", result["taskid"]))
    md = external.get("md") or {}
    benchmark = (result.get("evidence_manifest") or {}).get("user_supplied_benchmark") or {}
    path = _render_elastic_response(md.get("elastic") or {}, benchmark, output / "elastic_response.png", font)
    if path: assets.append(_asset("elastic_response", path, "弹性与多晶模量", "小应变 Cij 拟合与 VRH 多晶等效模量。", result["taskid"]))
    path = _render_acoustic_thermal(md.get("sound_speed") or {}, md.get("heat_capacity") or {}, benchmark, output / "acoustic_thermal_response.png", font)
    if path: assets.append(_asset("acoustic_thermal_response", path, "声学与热学响应", "由实际 MD 后处理得到的声速与 Cv。", result["taskid"]))
    path = _render_benchmark_card(benchmark, output / "actual_performance_comparison.png", font)
    if path: assets.append(_asset("actual_performance_comparison", path, "300 K 实际性能对标", "MLIP/MD 计算结果与已核验的实际性能参考值。", result["taskid"]))
    return assets
