#!/usr/bin/env python3
"""Benchmark interpretable and data-driven E11 surrogates for the short-CF RVE table.

The evaluation holds out one matrix system at a time.  This is deliberately
stricter than a random split: an unseen matrix must be predicted from its
recorded elastic descriptors rather than from a matrix-name lookup.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from sklearn.linear_model import Ridge
from sklearn.compose import TransformedTargetRegressor


FEATURES = [
    "matrix_E_MPa", "matrix_nu", "matrix_density_kgm3", "target_vf",
    "fiber_length_mm", "target_a11",
]
TARGET = "E11_MPa"
FIBER_E_MPA = 230_000.0
FIBER_DIAMETER_MM = 0.007


@dataclass
class ShearLagParameters:
    orientation_exponent: float
    length_rate: float


def mechanism_features(frame: pd.DataFrame) -> np.ndarray:
    """Return the inputs required by the finite-length, oriented ROM baseline."""
    return frame[["matrix_E_MPa", "target_vf", "fiber_length_mm", "target_a11"]].to_numpy(float)


def shear_lag_rom(x: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """Modified rule of mixtures with fitted orientation/length efficiencies.

    E11 = Em(1-Vf) + Ef*Vf*a11^p*[1-exp(-b*Lf/df)]
    """
    em, vf, length, a11 = x.T
    p, b = theta
    eta_orientation = np.clip(a11, 1e-6, 1.0) ** p
    eta_length = 1.0 - np.exp(-b * length / FIBER_DIAMETER_MM)
    return em * (1.0 - vf) + FIBER_E_MPA * vf * eta_orientation * eta_length


def fit_shear_lag_rom(x: np.ndarray, y: np.ndarray) -> ShearLagParameters:
    result = least_squares(
        lambda theta: shear_lag_rom(x, theta) - y,
        x0=np.array([1.0, 0.25]),
        bounds=([0.0, 0.0], [5.0, 10.0]),
    )
    return ShearLagParameters(*map(float, result.x))


def make_models() -> dict[str, object]:
    return {
        "poly2_ridge": make_pipeline(
            PolynomialFeatures(degree=2, include_bias=False), StandardScaler(), Ridge(alpha=1.0)
        ),
        "poly3_ridge": make_pipeline(
            PolynomialFeatures(degree=3, include_bias=False), StandardScaler(), Ridge(alpha=2.0)
        ),
        "gradient_boosting": GradientBoostingRegressor(
            n_estimators=300, max_depth=2, loss="huber", random_state=42
        ),
        "small_mlp": TransformedTargetRegressor(
            regressor=make_pipeline(
                StandardScaler(),
                MLPRegressor(
                    hidden_layer_sizes=(24, 12), activation="relu", solver="lbfgs",
                    alpha=0.03, max_iter=5_000, random_state=42,
                ),
            ),
            transformer=StandardScaler(),
        ),
    }


def metrics(y: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {
        "R2": float(r2_score(y, prediction)),
        "MAE_MPa": float(mean_absolute_error(y, prediction)),
        "RMSE_MPa": float(mean_squared_error(y, prediction) ** 0.5),
    }


def markdown_table(frame: pd.DataFrame) -> str:
    """Render the small report table without requiring pandas' tabulate extra."""
    headers = list(frame.columns)
    body = frame.copy()
    for column in headers:
        if pd.api.types.is_float_dtype(body[column]):
            body[column] = body[column].map(lambda value: f"{value:.4f}")
        else:
            body[column] = body[column].astype(str)
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    rows.extend("| " + " | ".join(row) + " |" for row in body.itertuples(index=False, name=None))
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="hea_surrogate/data/raw/cf/短纤维增强基体数据.csv")
    parser.add_argument("--output", default="hea_surrogate/reports/cf_short_fiber_model_benchmark_v1")
    args = parser.parse_args()

    data = pd.read_csv(args.input)
    data = data.loc[data["quality_flag_precheck"].eq("PASS")].reset_index(drop=True)
    x = data[FEATURES]
    y = data[TARGET].to_numpy(float)
    groups = data["matrix_name"].to_numpy()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    model_names = ["shear_lag_rom", *make_models(), "shear_lag_plus_gbr_residual"]
    oof = {name: np.full(len(data), np.nan) for name in model_names}
    fold_rows: list[dict[str, object]] = []
    mechanism_rows: list[dict[str, object]] = []

    for held_out in sorted(np.unique(groups)):
        test = groups == held_out
        train = ~test
        x_train, x_test = x.loc[train], x.loc[test]
        y_train, y_test = y[train], y[test]

        theta = fit_shear_lag_rom(mechanism_features(data.loc[train]), y_train)
        base_train = shear_lag_rom(mechanism_features(data.loc[train]), np.array([theta.orientation_exponent, theta.length_rate]))
        base_test = shear_lag_rom(mechanism_features(data.loc[test]), np.array([theta.orientation_exponent, theta.length_rate]))
        oof["shear_lag_rom"][test] = base_test
        mechanism_rows.append({"held_out_matrix": held_out, **theta.__dict__})

        fold_models = make_models()
        for name, model in fold_models.items():
            model.fit(x_train, y_train)
            oof[name][test] = model.predict(x_test)

        # Same held-out split, but the data model learns only the mechanism gap.
        residual = y_train - base_train
        residual_model = GradientBoostingRegressor(
            n_estimators=200, max_depth=2, loss="huber", random_state=42
        ).fit(x_train, residual)
        oof["shear_lag_plus_gbr_residual"][test] = base_test + residual_model.predict(x_test)

        for name in model_names:
            fold_rows.append({"model": name, "held_out_matrix": held_out, "n": int(test.sum()), **metrics(y_test, oof[name][test])})

    summary_rows = [{"model": name, "n": len(data), **metrics(y, prediction)} for name, prediction in oof.items()]
    summary = pd.DataFrame(summary_rows).sort_values("R2", ascending=False)
    fold_metrics = pd.DataFrame(fold_rows).sort_values(["model", "held_out_matrix"])
    prediction_table = data[["case", "matrix_name", TARGET, *FEATURES]].copy()
    for name, prediction in oof.items():
        prediction_table[f"prediction_{name}_MPa"] = prediction

    summary.to_csv(output / "metrics.csv", index=False)
    fold_metrics.to_csv(output / "fold_metrics.csv", index=False)
    prediction_table.to_csv(output / "oof_predictions.csv", index=False)
    pd.DataFrame(mechanism_rows).to_csv(output / "shear_lag_parameters_by_fold.csv", index=False)

    report = {
        "dataset": {"path": args.input, "target": TARGET, "records": len(data), "admission": "quality_flag_precheck=PASS"},
        "features": FEATURES,
        "validation": "leave-one-matrix-out; five folds; matrix_name is never an input feature",
        "mechanism_formula": "E11 = Em*(1-Vf) + Ef*Vf*a11^p*(1-exp(-b*Lf/df)); Ef=230000 MPa, df=0.007 mm; p and b are refit inside each training fold",
        "metrics": summary.to_dict(orient="records"),
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 短碳纤维 E11 代理模型对比（v1）", "",
        "统一使用 945 条预检通过 RVE 记录，采用留一基体验证。每一折均不使用被留出基体的任何记录；`matrix_name` 不作为特征。", "",
        "## 输入与输出", "",
        "输入：`matrix_E_MPa`、`matrix_nu`、`matrix_density_kgm3`、`target_vf`、`fiber_length_mm`、`target_a11`。输出：`E11_MPa`。", "",
        "## 机理基线", "",
        "$$E_{11}=E_m(1-V_f)+E_fV_fa_{11}^{p}[1-\\exp(-bL_f/d_f)]$$", "",
        "其中 $E_f=230000$ MPa、$d_f=0.007$ mm；$p,b$ 只用每折训练数据标定。混合模型以此基线为起点，再用梯度提升模型预测残差。", "",
        "## 汇总指标", "", markdown_table(summary), "",
        "## 解释与选型", "",
        "- `shear_lag_rom` 是可解释下限：它仅表示有限长度和主方向取向对纤维增刚效率的影响。", 
        "- `poly3_ridge` 给出显式、平滑的响应面；在当前规则 RVE 网格内尤其适合做可审计的首版代理模型。", 
        "- `shear_lag_plus_gbr_residual` 保留机理基线，同时拟合该 RVE 域内未被简化公式吸收的非线性。", 
        "- `small_mlp` 只作为神经网络对照；当前样本规模和固定物理边界下，若无稳定优势，不建议作为上线默认模型。", "",
        "各留出基体的指标见 `fold_metrics.csv`；逐记录的折外预测见 `oof_predictions.csv`。",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
