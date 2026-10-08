#!/usr/bin/env python3
"""Build the tokenized 1111-style customer Markdown for the CF model benchmark."""

from __future__ import annotations

import os
import json
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-short-cf")

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd


ROOT = Path("hea_surrogate/reports/cf_short_fiber_model_benchmark_v1")


def f(value: float, digits: int = 3) -> str:
    return f"{float(value):.{digits}f}"


def configure_chinese_font() -> None:
    """Use an available CJK font so customer-facing chart labels remain legible."""
    font_path = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
    if Path(font_path).exists():
        font_manager.fontManager.addfont(font_path)
        # DejaVu supplies Latin/math glyphs; Droid supplies the CJK glyphs.
        plt.rcParams["font.family"] = ["DejaVu Sans", "Droid Sans Fallback"]
    plt.rcParams["axes.unicode_minus"] = False


def render_visuals(metrics: pd.DataFrame, oof: pd.DataFrame, output: Path) -> None:
    configure_chinese_font()
    colors = {"poly3_ridge": "#147D92", "poly2_ridge": "#5CA4A9", "shear_lag_plus_gbr_residual": "#E58B32", "small_mlp": "#C85A54", "gradient_boosting": "#8B6FB3", "shear_lag_rom": "#8795A1"}
    labels = {"poly3_ridge": "三阶响应面", "poly2_ridge": "二阶响应面", "shear_lag_plus_gbr_residual": "机理＋残差", "small_mlp": "小型 MLP", "gradient_boosting": "梯度提升", "shear_lag_rom": "剪滞机理"}

    # 1. The funnel is a literal rendering of the admissions and model-selection steps.
    funnel = [("原始 RVE 记录", 980), ("严格训练/验证记录", 945), ("比较的代理模型", 6), ("优先模型队列", 3), ("默认模型", 1)]
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=180)
    max_value = 980
    for index, (label, count) in enumerate(funnel):
        width = 0.15 + 0.82 * (count / max_value) ** 0.38
        left = (1.0 - width) / 2
        color = ["#DCECEF", "#9CCDD3", "#58A6AD", "#287E89", "#165A68"][index]
        ax.barh(len(funnel) - 1 - index, width, left=left, height=0.72, color=color, edgecolor="white")
        ax.text(0.5, len(funnel) - 1 - index, f"{label}  ·  {count}", ha="center", va="center", color="white" if index >= 3 else "#12343B", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 1); ax.set_ylim(-0.7, len(funnel) - 0.3); ax.axis("off")
    ax.set_title("短碳纤维 E11 代理模型筛选漏斗", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.text(0, -0.58, "数据筛选与模型选择采用不同计数尺度；前两级为 RVE 记录，后三级为模型候选。", fontsize=9.5, color="#54646A")
    fig.tight_layout(); fig.savefig(output / "cf_model_screening_funnel.png", bbox_inches="tight"); plt.close(fig)

    # 2. Compare accuracy and absolute error without concealing their different scales.
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)
    for _, row in metrics.iterrows():
        name = row["model"]
        ax.scatter(row["R2"], row["MAE_MPa"], s=145, color=colors[name], edgecolor="white", linewidth=1.2, zorder=3)
        ax.annotate(labels[name], (row["R2"], row["MAE_MPa"]), xytext=(7, 5), textcoords="offset points", fontsize=10)
    ax.axvline(0.97, color="#AAB7B9", linestyle="--", linewidth=1)
    ax.text(0.9705, 290, "优先队列 R² 门槛", fontsize=9, color="#637477")
    ax.grid(alpha=.2); ax.set_xlim(.88, 1.002); ax.set_ylim(0, 405)
    ax.set_xlabel("留一基体折外 R²（越高越好）"); ax.set_ylabel("折外 MAE / MPa（越低越好）")
    ax.set_title("统一验证下的模型精度对比", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.text(.881, 380, "945 条预检通过 RVE 标签；每次完整留出一种基体。", fontsize=9.5, color="#54646A")
    fig.tight_layout(); fig.savefig(output / "cf_model_validation_comparison.png", bbox_inches="tight"); plt.close(fig)

    # 3. OOF parity plot and signed-error distribution for the selected model.
    selected = "prediction_poly3_ridge_MPa"
    truth = oof["E11_MPa"].to_numpy(float); prediction = oof[selected].to_numpy(float); error = prediction - truth
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 5.2), dpi=180, gridspec_kw={"width_ratios": [1.25, .85]})
    palette = {name: color for name, color in zip(sorted(oof["matrix_name"].unique()), ["#147D92", "#E58B32", "#8B6FB3", "#C85A54", "#5CA4A9"])}
    for matrix, group in oof.groupby("matrix_name"):
        left.scatter(group["E11_MPa"], group[selected], s=17, alpha=.7, color=palette[matrix], label=matrix.replace("Bambu_", ""))
    lo, hi = float(min(truth.min(), prediction.min())), float(max(truth.max(), prediction.max()))
    left.plot([lo, hi], [lo, hi], color="#343A40", linewidth=1.2, linestyle="--", label="理想一致线")
    left.set_xlabel("RVE 仿真真值 E11 / MPa"); left.set_ylabel("折外预测 E11 / MPa")
    left.set_title("预测值与 RVE 仿真标签", loc="left", fontsize=14, fontweight="bold")
    left.legend(fontsize=8, frameon=False, ncol=2); left.grid(alpha=.18)
    right.hist(error, bins=30, color="#147D92", alpha=.85, edgecolor="white")
    right.axvline(0, color="#343A40", linestyle="--", linewidth=1.1)
    right.set_xlabel("预测 − RVE 真值 / MPa"); right.set_ylabel("记录数")
    right.set_title("折外误差分布", loc="left", fontsize=14, fontweight="bold")
    right.grid(axis="y", alpha=.18)
    fig.suptitle("三阶正则化响应面：留一基体折外预测", x=.065, ha="left", fontsize=17, fontweight="bold")
    fig.text(.065, .01, "“真值”是 FAST_RF RVE 仿真标签，不是实物实验测量值。", fontsize=9.5, color="#54646A")
    fig.tight_layout(rect=(0, .04, 1, .93)); fig.savefig(output / "cf_poly3_oof_parity.png", bbox_inches="tight"); plt.close(fig)


def main() -> None:
    metrics = pd.read_csv(ROOT / "metrics.csv").sort_values("R2", ascending=False).reset_index(drop=True)
    names = {
        "poly3_ridge": "三阶正则化响应面",
        "poly2_ridge": "二阶正则化响应面",
        "shear_lag_plus_gbr_residual": "机理基线＋残差模型",
        "small_mlp": "小型神经网络（MLP）",
        "gradient_boosting": "纯数据梯度提升",
        "shear_lag_rom": "短纤维剪滞机理基线",
    }
    metrics["label"] = metrics["model"].map(names)
    oof = pd.read_csv(ROOT / "oof_predictions.csv")
    render_visuals(metrics, oof, ROOT)
    table = [
        "| 排名 | 模型 | 留一基体 R² | 折外 MAE | 作为首版服务模型 |",
        "|---:|---|---:|---:|---|",
    ]
    reasons = {
        "poly3_ridge": "优先采用：精度最高、关系式可审计、各基体稳定。",
        "poly2_ridge": "保留为简化可解释备选。",
        "shear_lag_plus_gbr_residual": "保留为机理增强交叉核对模型。",
        "small_mlp": "仅作神经网络对照，不作为默认模型。",
        "gradient_boosting": "仅作纯数据对照。",
        "shear_lag_rom": "作为物理趋势解释基线。",
    }
    for index, row in metrics.iterrows():
        table.append(f"| {index + 1} | {row.label} | {f(row.R2, 4)} | {float(row.MAE_MPa):.0f} MPa | {reasons[row.model]} |")

    top = metrics.iloc[0]
    md = [
        "# 短碳纤维增强复合材料设计：等效刚度代理模型筛选结果",
        "",
        "针对 **PLA、PETG-HF、ABS、ASA 与 PC 的短碳纤维增强热塑性复合材料**，在当前线弹性、致密、完美界面结合的 RVE 条件下，优先采用 **三阶正则化响应面** 预测主方向等效弹性模量 `E11`。它在未见基体的留一基体验证中得到 **R² = 0.9939、MAE = 71 MPa**，同时保留清晰的输入—输出关系，适合首版等效刚度与各向异性结构配方设计服务。",
        "",
        "## 设计条件",
        "",
        "| 项目 | 本次设定 |",
        "|---|---|",
        "| 服务对象 | 短碳纤维增强热塑性复合材料的等效刚度与各向异性结构配方设计 |",
        "| 基体范围 | Bambu PLA Basic、PETG-HF、ABS、ASA、PC |",
        "| 碳纤维 | `T300_like_TI_reference`；轴向模量 230000 MPa；直径 7 μm |",
        "| 设计变量 | 纤维体积分数 5–17.5 vol.%；长度 20–50 μm；主方向取向 `a11=0.333–0.9` |",
        "| 首版取向约束 | $a_{11}+a_{22}+a_{33}=1$，且 $a_{22}=a_{33}=(1-a_{11})/2$ |",
        "| 本页目标 | 比较并选择 `E11` 代理模型；不输出强度、疲劳、损伤、热性能或化学配方预测 |",
        "",
        "## 数据基础",
        "",
        "| 项目 | 当前数据基础 |",
        "|---|---|",
        "| 原始记录 | 980 条 `FAST_RF` RVE 线弹性仿真，`status=PASS` |",
        "| 严格建模集 | 945 条 `quality_flag_precheck=PASS` |",
        "| 复核样本 | 35 条为 5 vol.% / 50 μm 且预估纤维数低于 30；不进入本轮严格训练 |",
        "| 因子结构 | 5 基体 × 4 长度 × 7 体积分数 × 7 主方向取向 |",
        "| 预测标签 | `E11/E22/E33`、`G12/G13/G23` 与 6 个泊松比；本页模型选择以 `E11` 为目标 |",
        "| 物理边界 | 线弹性、致密、无孔隙、完美界面、无塑性/损伤/脱粘、单一纤维长度与直径 |",
        "| 数据完整性 | CSV SHA-256：`3540d8b1b2995f58eebf778ff9cecf931b9076f44e41667297e15d6d9677015c` |",
        "",
        "## 响应关系与模型方法",
        "",
        "### 机理基线：有限长度与取向增刚",
        "",
        r"$$E_{11}^{\mathrm{ROM}}=E_m(1-V_f)+E_fV_fa_{11}^{p}\left[1-\exp\left(-bL_f/d_f\right)\right]$$",
        "",
        "其中 $E_m$ 为基体模量，$E_f$ 为纤维轴向模量，$V_f$ 为纤维体积分数，$L_f/d_f$ 为长径比；$p,b$ 在每个训练折内标定。该式用于表达主方向取向和有限纤维长度对增刚效率的主要影响。",
        "",
        "### 首版代理关系：三阶正则化响应面",
        "",
        r"$$\widehat{E}_{11}=\mathrm{Ridge}\!\left(\Phi_3\left[E_m,\nu_m,\rho_m,V_f,L_f,a_{11}\right]\right)$$",
        "",
        r"其中 $\Phi_3$ 表示上述六项输入及其至三阶的交互项；Ridge 正则化限制高阶项过度放大。基体名称不作为模型输入，因此验证结果反映了由基体的已记录弹性描述符外推到未见基体的能力。",
        "",
        "## 筛选漏斗",
        "",
        "| 阶段 | 数量 | 当前判断 |",
        "|---|---:|---|",
        "| 原始 RVE 记录 | 980 | 全部 `status=PASS` |",
        "| 严格训练与验证记录 | 945 | 仅保留预检通过数据 |",
        "| 单一模型基体验证折 | 5 | 每折完整留出一个基体，避免同基体记录泄漏 |",
        "| 参与比较的代理模型 | 6 | 机理、响应面、树模型和 MLP 使用同一验证规则 |",
        "| 进入首版服务优先队列 | 3 | 三阶响应面、二阶响应面、机理＋残差模型 |",
        "| 默认上线模型 | 1 | 三阶正则化响应面 |",
        "",
        "{{VISUAL:cf_model_screening_funnel}}",
        "",
        "## 候选模型对比",
        "",
        *table,
        "",
        "{{VISUAL:cf_model_validation_comparison}}",
        "",
        "## 为什么保留机理模型",
        "",
        "短纤维增刚首先由纤维与基体的模量差、体积分数、有限长径比和取向效率控制；这正是剪滞改进混合法中 $E_m$、$E_f$、$V_f$、$L_f/d_f$ 与 $a_{11}$ 的来源。因此机理模型可直接解释：纤维含量增加、纤维更长、以及纤维更沿主方向排布时，`E11` 为什么会提高。",
        "",
        "但当前简化机理式不显式表示 RVE 内有限纤维数、纤维相互作用及取向张量微小离散；它单独的折外 R² 为 0.9076。服务因此保留“机理基线＋残差模型”作交叉核对，而将精度更高、仍具显式输入关系的三阶响应面作为默认预测器。相比之下，小型 MLP 在相同的未见基体验证下 R² 仅为 0.9227，既未提高精度，也降低了结果可审计性，因此不进入默认路径。",
        "",
        "## 折外预测图表",
        "",
        "三阶正则化响应面的每个预测值均来自该基体被完整留出时的模型，而非训练内回代结果。",
        "",
        "{{VISUAL:cf_poly3_oof_parity}}",
        "",
        "## 优先模型卡",
        "",
        "**短碳纤维 `E11` 响应面模型 v1**：当前数据范围内，优先用于配方筛选、等效刚度比较和候选验证排序。",
        "",
        "| 配方与模型条件 | 当前信息 |",
        "|---|---|",
        "| 默认模型 | 三阶正则化响应面（`poly3_ridge`） |",
        "| 模型输入 | `matrix_E_MPa`、`matrix_nu`、`matrix_density_kgm3`、`target_vf`、`fiber_length_mm`、`target_a11` |",
        "| 模型输出 | 主方向等效弹性模量 `E11_MPa` |",
        f"| 留一基体验证 | R² {f(top.R2, 4)}；MAE {float(top.MAE_MPa):.0f} MPa；RMSE {float(top.RMSE_MPa):.0f} MPa |",
        "| 可用设计范围 | 5 种已记录基体描述符范围；5–17.5 vol.%；20–50 μm；$a_{11}=0.333–0.9$ |",
        "| 输出用途 | 在当前 RVE 物理域内比较候选主方向刚度；用于验证优先级，不作为材料放行值 |",
        "",
        "| 其他结构输出 | 当前状态 |",
        "|---|---|",
        "| `E22/E33`、`G12/G13/G23`、泊松比 | 数据标签已完整保留；应按各自独立验证模型输出，不以本页 `E11` 指标替代 |",
        "| 强度、断裂应变、冲击、疲劳、蠕变 | 当前目录未收录可用于本服务的对应训练标签 |",
        "| 温湿度、孔隙率、界面强度、真实长度/取向分布 | 当前 RVE 数据未覆盖；候选进入实验前需补充这些真实工艺条件 |",
        "",
        "## 验证结论",
        "",
        "当前证据支持将三阶正则化响应面作为短碳纤维增强复合材料 `E11` 的首版代理模型；其在五种基体轮流完全留出的条件下保持 R² 0.989–0.995，说明它对当前固定 T300-like 纤维、固定直径和给定 RVE 边界内的平滑响应关系拟合稳定。",
        "",
        "机理基线＋残差模型同时保留，作为物理趋势核对与后续扩展至更多纤维牌号、界面与工艺变量时的优先路线。小型 MLP 在相同严格验证下未显示稳定优势，因此不作为首版默认预测器。",
        "",
        "下一步优先验证真实制件的纤维长度分布、取向分布、孔隙率和界面状态；再以对应拉伸、横向拉伸及剪切试验校准 RVE—实物偏差。",
    ]
    summary = "\n".join(md) + "\n"
    (ROOT / "summary.md").write_text(summary, encoding="utf-8")
    urls = json.loads((ROOT / "public_asset_urls.json").read_text(encoding="utf-8"))
    preview = summary.replace("{{VISUAL:cf_model_screening_funnel}}", f"![短碳纤维 E11 代理模型筛选漏斗]({urls['cf_model_screening_funnel']})\n\n*前两层为 RVE 记录数，后三层为模型候选数。*")
    preview = preview.replace("{{VISUAL:cf_model_validation_comparison}}", f"![统一验证下的模型精度对比]({urls['cf_model_validation_comparison']})\n\n*图中越靠右且越靠下，代表折外预测越准确。*")
    preview = preview.replace("{{VISUAL:cf_poly3_oof_parity}}", f"![三阶响应面预测与 RVE 仿真真值的差距]({urls['cf_poly3_oof_parity']})\n\n*左图每一点都是折外预测；右图为预测值减去 RVE 仿真真值。这里的真值为 FAST_RF RVE 仿真标签，不是实物实验测量值。*")
    (ROOT / "customer_result.md").write_text(preview, encoding="utf-8")


if __name__ == "__main__":
    main()
