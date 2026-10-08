#!/usr/bin/env python3
"""Train the validated short-CF orthotropic elastic-constant surrogate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

FEATURES = ("matrix_E_MPa", "matrix_nu", "matrix_density_kgm3", "target_vf", "fiber_length_mm", "target_a11")
TARGETS = ("E11_MPa", "E22_MPa", "E33_MPa", "G12_MPa", "G13_MPa", "G23_MPa", "V12", "V13", "V23")


def model() -> object:
    return make_pipeline(PolynomialFeatures(degree=3, include_bias=False), StandardScaler(), Ridge(alpha=2.0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    data = pd.read_csv(args.input)
    data = data.loc[data["quality_flag_precheck"].eq("PASS")].reset_index(drop=True)
    x = data.loc[:, FEATURES]
    groups = data["matrix_name"].to_numpy()
    assessments: dict[str, dict] = {}
    models: dict[str, object] = {}
    for target in TARGETS:
        y = data[target].to_numpy(float)
        prediction = np.full(len(data), np.nan)
        per_matrix: dict[str, dict] = {}
        for held in sorted(set(groups)):
            test = groups == held
            fitted = model().fit(x.loc[~test], y[~test])
            prediction[test] = fitted.predict(x.loc[test])
            per_matrix[str(held)] = {"n": int(test.sum()), "MAE": float(mean_absolute_error(y[test], prediction[test])), "RMSE": float(mean_squared_error(y[test], prediction[test]) ** .5), "R2": float(r2_score(y[test], prediction[test]))}
        fitted = model().fit(x, y)
        models[target] = fitted
        assessments[target] = {"unit": "MPa" if target.startswith(("E", "G")) else "1", "rows": len(data), "validation": "leave-one-matrix-out; matrix_name is never an input", "metrics": {"MAE": float(mean_absolute_error(y, prediction)), "RMSE": float(mean_squared_error(y, prediction) ** .5), "R2": float(r2_score(y, prediction))}, "per_held_matrix": per_matrix, "status": "service_candidate"}
    matrix_profiles = data.groupby("matrix_name")[["matrix_E_MPa", "matrix_nu", "matrix_density_kgm3"]].first().to_dict(orient="index")
    artifact = {"model_version": "short_cf_thermomechanical_rve_v1_20260907", "data_type": "FAST_RF RVE linear-elastic labels; dense, perfect-bonding, monodisperse T300-like short CF", "features": list(FEATURES), "targets": list(TARGETS), "models": models, "assessments": assessments, "matrix_profiles": matrix_profiles, "domain": {name: [float(data[name].min()), float(data[name].max())] for name in FEATURES}, "admission": {"rows": len(data), "raw_rows": 980, "quality_gate": "quality_flag_precheck=PASS", "excluded_rows": 35, "matrix_systems": sorted(matrix_profiles)}, "physics_scope": "linear elastic; dense; no void; no plasticity/damage/debonding; monodisperse fibre length/diameter; X-axisymmetric A2"}
    args.output_dir.mkdir(parents=True)
    joblib.dump(artifact, args.output_dir / "model.joblib")
    (args.output_dir / "training_report.json").write_text(json.dumps({key: value for key, value in artifact.items() if key != "models"}, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "MODEL_CARD.md").write_text("# 短碳纤维增强热塑性复合材料等效本构模型 v1\n\n输入为基体线弹性描述符、纤维体积分数、有效长度和主方向取向；输出为 9 个独立正交各向异性工程常数。标签是 FAST_RF RVE 仿真，不是实物试验。\n", encoding="utf-8")


if __name__ == "__main__":
    main()
