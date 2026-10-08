"""Data-faithful composition doughnuts, reusable across material domains."""
from pathlib import Path
import math

import matplotlib.pyplot as plt


PALETTE = ["#326B9B", "#6EABC6", "#91BACB", "#E0B875", "#83A79A",
           "#AC9BBE", "#CE9A81", "#9DAFBE"]


def composition_slices(composition, *, minor_threshold=1.0):
    """Return exact slices; 'other' is a display aggregation, not a new input."""
    rows = []
    for component, amount in composition.items():
        if isinstance(amount, bool):
            raise ValueError("Composition values must be numeric, not boolean")
        amount = float(amount)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("Composition values must be finite and non-negative")
        if amount > 0:
            rows.append((component, amount))
    rows.sort(key=lambda row: (-row[1], row[0]))
    if not rows or not math.isclose(math.fsum(v for _, v in rows), 100, abs_tol=.001):
        raise ValueError("Use a complete, closed percentage composition")
    main = [(k, v) for k, v in rows if v >= minor_threshold]
    minor = [(k, v) for k, v in rows if v < minor_threshold]
    other = math.fsum(v for _, v in minor)
    slices = main + ([("其他组分", other)] if minor else [])
    assert math.isclose(math.fsum(v for _, v in slices), math.fsum(v for _, v in rows), abs_tol=1e-12)
    return {"slices": slices, "minor": minor, "positive_components": rows,
            "total": math.fsum(v for _, v in rows), "minor_threshold": minor_threshold}


def render_composition(composition, path, *, names=None, unit="wt.%", title="优选配方组成"):
    """Ring for proportions, exact legend for all positive components."""
    if unit not in {"wt.%", "at.%", "mol.%", "vol.%"}:
        raise ValueError("Specify a percentage basis; do not infer or convert it")
    names = names or {}
    data = composition_slices(composition)
    slices, minor = data["slices"], data["minor"]
    extra = len(minor) + 1 if minor else 0
    height = max(4.4, .31 * (len(slices) + extra) + 1.2)
    fig = plt.figure(figsize=(9.5, height), layout="constrained")
    grid = fig.add_gridspec(1, 2, width_ratios=[1.12, 1])
    ax = fig.add_subplot(grid[0]); legend = fig.add_subplot(grid[1])
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(slices))]
    if minor:
        colors[-1] = "#CBD3DA"
    ax.pie([v for _, v in slices], colors=colors, startangle=90, counterclock=False,
           wedgeprops={"width": .32, "edgecolor": "white", "linewidth": 1.7},
           autopct=lambda pct: f"{pct:.1f}%" if pct >= 4 else "",
           pctdistance=.83, textprops={"fontsize": 11, "color": "#253746"})
    basis = {"wt.%": "质量组成", "at.%": "原子组成", "mol.%": "摩尔组成", "vol.%": "体积组成"}[unit]
    ax.text(0, .10, basis, ha="center", va="center", fontsize=14, color="#536572")
    ax.text(0, -.18, "100%", ha="center", va="center", fontsize=26, color="#253746")
    ax.set_aspect("equal")
    legend.set(xlim=(0, 1), ylim=(0, 1)); legend.axis("off")
    legend.text(.04, .98, f"组分含量（{unit}）", fontsize=12, weight="bold", va="top")
    line_count = len(slices) + extra
    step = min(.102, .80 / max(line_count, 1)); y = .89
    def number(value):
        return f"{value:.5f}".rstrip("0").rstrip(".")
    def label(component):
        name = names.get(component, component)
        return f"{name}（{component}）" if name != component else component
    for index, (component, amount) in enumerate(slices):
        legend.scatter(.07, y, s=100, color=colors[index], marker="s")
        text = "其他组分（合计）" if component == "其他组分" else label(component)
        legend.text(.13, y, text, va="center", fontsize=10.5)
        legend.text(.97, y, number(amount) + "%", ha="right", va="center", fontsize=10.5)
        y -= step
    if minor:
        y -= step * .15
        legend.text(.13, y, "其他组分明细", fontsize=10, color="#536572", va="center")
        y -= step
        for component, amount in minor:
            legend.text(.16, y, label(component), va="center", fontsize=10)
            legend.text(.97, y, number(amount) + "%", ha="right", va="center", fontsize=10)
            y -= step
    fig.suptitle(title, fontsize=16)
    fig.savefig(Path(path), dpi=160, facecolor="white")
    plt.close(fig)
    return data
