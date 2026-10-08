#!/usr/bin/env python3
"""Train the auditable broad-domain austenitic-stainless design model.

The Cambridge MAP release is retained as a model-development dataset.  Its
per-row source key is preserved, but it is not silently promoted to a
page/table-verified catalogue observation.  Cryogenic NASA observations stay
in a separate reference layer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pickle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/cryogenic_stainless_20260902/cambridge_map/STMECH_AUS_SS/STMECH_AUS_SS.txt"
REF = ROOT / "data/processed/reusable_rocket_stainless_reference_baseline/20260902_v0/admitted_tensile_observations.csv"
OUT = ROOT / "data/processed/reusable_rocket_stainless_design/20260902_v1"

ELEMENTS = ["Cr","Ni","Mo","Mn","Si","Nb","Ti","Zr","Ta","V","W","Cu","N","C","B","P","S","Co","Al","Sn","Pb"]
NUMERIC = ELEMENTS + ["Solution_treatment_temperature","Solution_treatment_time(s)",
    "Water_Quenched_after_s.t.","Air_Quenched_after_s.t.","Grains mm-2",
    "Size of ingot","Temperature (K)"]
CATEGORICAL = ["Type of melting","Product form"]
TARGETS = {"yield_0p2_MPa":"0.2%proof_stress (M Pa)", "uts_MPa":"UTS (M Pa)",
           "elongation_pct":"Elongation (%)"}


def load_map() -> pd.DataFrame:
    data = pd.read_csv(RAW, sep="\t", skiprows=5, na_values=["Na", "na"])
    for col in NUMERIC + list(TARGETS.values()):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    # Keep every valid YS/UTS row.  Elongation is trained on its own complete
    # subset below; missing elongation must not discard a strength label.
    data = data.dropna(subset=["Temperature (K)", "0.2%proof_stress (M Pa)", "UTS (M Pa)"]).copy()
    data = data[(data["Temperature (K)"] >= 293) & (data["Temperature (K)"] <= 1273)]
    data = data[(data["0.2%proof_stress (M Pa)"] > 0) & (data["UTS (M Pa)"] > 0)]
    data["composition_sum_wt_pct"] = data[ELEMENTS].fillna(0).sum(axis=1)
    data = data[data["composition_sum_wt_pct"].between(10, 60)].copy()
    # A thermomechanical state is a group: no temperature point from this
    # state is allowed into both a CV training and test fold.
    group_columns = ELEMENTS + ["Solution_treatment_temperature","Solution_treatment_time(s)",
        "Water_Quenched_after_s.t.","Air_Quenched_after_s.t.","Grains mm-2",
        "Type of melting","Size of ingot","Product form","Comments"]
    data["state_group"] = data[group_columns].astype("string").fillna("<NA>").agg("|".join, axis=1)
    data["source_file"] = RAW.name
    # Most MAP records preserve a NRIM/BSCC key in Comments.  Where the source
    # file has no such key, retain its one-based tabular record number instead
    # of serialising a missing provenance value.
    fallback_locator = "STMECH_AUS_SS tabular record " + (data.index + 1).astype(str)
    comment_locator = data["Comments"].astype("string").str.strip()
    data["source_locator"] = comment_locator.where(comment_locator.notna() & comment_locator.ne(""), fallback_locator)
    data["evidence_level"] = "B_model_training_source"
    data["admission"] = "included_model_training_not_catalogue_fact"
    return data.reset_index(drop=True)


def design_matrix(data: pd.DataFrame, state: dict | None = None) -> tuple[np.ndarray, dict]:
    """Ridge descriptors: composition/process, temperature curvature and interactions."""
    numeric = data[NUMERIC].copy()
    if state is None:
        medians = numeric.median().to_dict()
        cats = {c: sorted(data[c].astype("string").fillna("<NA>").unique().tolist()) for c in CATEGORICAL}
    else:
        medians, cats = state["medians"], state["categories"]
    numeric = numeric.fillna(medians).astype(float)
    t = numeric["Temperature (K)"].to_numpy() / 1000.0
    base = numeric.to_numpy()
    interactions = np.column_stack([numeric[e].to_numpy() * t for e in ["Cr", "Ni", "Mo", "Mn", "N", "C"]])
    parts = [base, t[:, None] ** 2, interactions]
    for column in CATEGORICAL:
        values = data[column].astype("string").fillna("<NA>")
        parts.append(np.column_stack([(values == item).astype(float).to_numpy() for item in cats[column]]))
    matrix = np.column_stack(parts)
    if state is None:
        mean, scale = matrix.mean(axis=0), matrix.std(axis=0)
        scale[scale < 1e-12] = 1.0
        state = {"medians": medians, "categories": cats, "mean": mean.tolist(), "scale": scale.tolist()}
    return (matrix - np.asarray(state["mean"])) / np.asarray(state["scale"]), state


def fit_ridge(data: pd.DataFrame, target: str) -> dict:
    x, state = design_matrix(data)
    x = np.column_stack([np.ones(len(x)), x])
    y = data[target].to_numpy(dtype=float)
    penalty = np.eye(x.shape[1]) * 5.0
    penalty[0, 0] = 0.0
    coef = np.linalg.solve(x.T @ x + penalty, x.T @ y)
    return {"state": state, "coef": coef.tolist()}


def predict(data: pd.DataFrame, model: dict) -> np.ndarray:
    x, _ = design_matrix(data, model["state"])
    return np.column_stack([np.ones(len(x)), x]) @ np.asarray(model["coef"])


def score_cv(data: pd.DataFrame, target: str) -> dict:
    unique = sorted(data["state_group"].unique())
    folds = np.array_split(np.asarray(unique), 3)
    y, pred = data[target].to_numpy(dtype=float), np.full(len(data), np.nan)
    for fold in folds:
        test = data["state_group"].isin(fold).to_numpy()
        model = fit_ridge(data.loc[~test], target)
        pred[test] = predict(data.loc[test], model)
    mae = float(np.mean(np.abs(y - pred)))
    r2 = float(1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2))
    return {"group_kfold_splits": 3, "MAE_MPa_or_pct": mae, "R2": r2, "n": int(len(y))}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load_map()
    # Preserve original MAP fields plus explicit canonical labels/provenance.
    for canonical, raw in TARGETS.items():
        data[canonical] = data[raw]
    data.to_csv(OUT / "map_model_training_observations.csv", index=False)
    reports, models = {}, {}
    for canonical in TARGETS:
        subset = data.dropna(subset=[canonical]).copy()
        reports[canonical] = score_cv(subset, canonical)
        models[canonical] = fit_ridge(subset, canonical)
    artifact = {
        "model_version": "reusable_rocket_austenitic_design_v1",
        "features": {"numeric": NUMERIC, "categorical": CATEGORICAL},
        "temperature_domain_K": [float(data["Temperature (K)"].min()), float(data["Temperature (K)"].max())],
        "element_ranges_wt_pct": {e: {"min": float(data[e].fillna(0).min()), "max": float(data[e].fillna(0).max())} for e in ELEMENTS},
        "models": models,
    }
    with (OUT / "austenitic_design_v1.pkl").open("wb") as handle:
        pickle.dump(artifact, handle)
    group_count = int(data["state_group"].nunique())
    gate = {
        "service_name": "可回收火箭不锈钢配方设计",
        "artifact": artifact["model_version"],
        "source": {"dataset": "University of Cambridge MAP STMECH_AUS_SS", "rows": int(len(data)),
                   "row_provenance": "Comments field; NRIM creep datasheets / BSCC source key",
                   "catalogue_status": "模型训练源；原始 PDF 页码映射完成前不写入材料事实目录"},
        "independent_thermomechanical_groups": group_count,
        "validation": reports,
        "formal_scope": "293–1273 K、固溶处理奥氏体不锈钢的短时拉伸配方筛选；输出为待试验候选优先级。",
        "cryo_scope": "20/77/90/111 K 不使用本模型外推；转入 NASA 301/304L 已收录参考状态比较与低温试验优先级。",
        "release_checks": {
            "labels_over_500": int(len(data)) >= 500,
            "groups_over_30": group_count >= 30,
            "group_cv_r2_yield_over_0p6": reports["yield_0p2_MPa"]["R2"] >= 0.6,
            "group_cv_r2_uts_over_0p6": reports["uts_MPa"]["R2"] >= 0.6,
        },
        "not_predicted": ["fatigue", "fracture toughness", "weld performance", "LOX compatibility", "30X proprietary performance"],
    }
    gate["formal_release_status"] = "conditional_ready" if all(gate["release_checks"].values()) else "data_or_validation_gate_not_met"
    (OUT / "model_card.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# 可回收火箭不锈钢配方设计：v1 广域拉伸模型\n\n"
        "此模型以公开奥氏体不锈钢成分—固溶处理—温度数据训练，并采用同一热机械状态分组交叉验证。"
        "它只覆盖 293–1273 K 的短时拉伸筛选；低温贮箱温区由单独的可追溯 301/304L 参考层承接。\n",
        encoding="utf-8")
    print(json.dumps({"rows": len(data), "groups": group_count, "validation": reports,
                      "release": gate["formal_release_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
