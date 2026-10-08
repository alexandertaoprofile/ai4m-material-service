from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from train_physics_v2 import (
    BOUNDARY_K,
    HERE,
    KB_EV_K,
    V1_SUMMARY,
    apply_optical_proxy,
    exact_rbf,
    exponent_table,
    full_curve_basis,
    fit_optical_proxy,
    load_data,
    r2,
    standardizer_fit,
    to_serializable,
)


REGRESSORS = [
    {"kind": "poly", "degree": 1, "alpha": 0.1, "tag": "linear"},
    {"kind": "poly", "degree": 2, "alpha": 0.1, "tag": "quad01"},
    {"kind": "poly", "degree": 2, "alpha": 1.0, "tag": "quad1"},
    {"kind": "poly", "degree": 3, "alpha": 1.0, "tag": "cubic1"},
    {"kind": "rbf", "ell": 1.0, "alpha": 0.1, "tag": "rbf10"},
    {"kind": "rbf", "ell": 2.0, "alpha": 0.1, "tag": "rbf20"},
    {"kind": "rbf", "ell": 3.6, "alpha": 0.1, "tag": "rbf36"},
]


def candidate_list(family_name: str):
    result = []
    for pool in ("family", "all"):
        for reg in REGRESSORS:
            result.append({**reg, "pool": pool, "optical": False, "name": f"{pool}_comp_{reg['tag']}"})
    if family_name == "Cs=0.2":
        for pool in ("family", "all"):
            for reg in [r for r in REGRESSORS if r["tag"] in {"quad1", "rbf20", "rbf36"}]:
                result.append({**reg, "pool": pool, "optical": True, "name": f"{pool}_opt_{reg['tag']}"})
    return result


def fit_scalar(x: np.ndarray, y: np.ndarray, spec: dict):
    xm, xs = standardizer_fit(x)
    z = (x - xm) / xs
    ym = float(np.mean(y))
    ys = float(max(np.std(y), 1e-8))
    target = (y - ym) / ys
    state = {
        "kind": spec["kind"],
        "input_mean": xm,
        "input_scale": xs,
        "target_mean": ym,
        "target_scale": ys,
    }
    if spec["kind"] == "poly":
        exponents = exponent_table(z.shape[1], int(spec["degree"]))
        design = np.column_stack(
            [np.ones(len(z))] + [np.prod(z**e, axis=1) for e in exponents]
        )
        penalty = np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        coef = np.linalg.solve(
            design.T @ design + float(spec["alpha"]) * penalty,
            design.T @ target,
        )
        state.update({"exponents": exponents, "coef": coef})
    else:
        kernel = exact_rbf(z, z, float(spec["ell"]))
        coef = np.linalg.solve(kernel + float(spec["alpha"]) * np.eye(len(z)), target)
        state.update({"centers": z, "ell": float(spec["ell"]), "coef": coef})
    return state


def predict_scalar(x: np.ndarray, state: dict) -> np.ndarray:
    z = (x - state["input_mean"]) / state["input_scale"]
    if state["kind"] == "poly":
        design = np.column_stack(
            [np.ones(len(z))]
            + [np.prod(z**e, axis=1) for e in state["exponents"]]
        )
        value = design @ state["coef"]
    else:
        value = exact_rbf(z, state["centers"], state["ell"]) @ state["coef"]
    return value * state["target_scale"] + state["target_mean"]


def model_train_indices(available: np.ndarray, family: np.ndarray, family_name: str, spec: dict):
    if spec["pool"] == "family":
        return available[family[available] == family_name]
    return available


def model_inputs(base, comps, train_idx, eval_idx, optical):
    if not optical:
        return base[train_idx], base[eval_idx], None
    proxy = fit_optical_proxy(base, comps, train_idx)
    train_x = np.column_stack([base[train_idx], apply_optical_proxy(base[train_idx], proxy)])
    eval_x = np.column_stack([base[eval_idx], apply_optical_proxy(base[eval_idx], proxy)])
    return train_x, eval_x, proxy


def family_folds(indices: np.ndarray, base: np.ndarray, folds: int = 4):
    ordered = indices[np.lexsort((base[indices, 2], base[indices, 1]))]
    result = [[] for _ in range(folds)]
    for rank, idx in enumerate(ordered):
        result[rank % folds].append(int(idx))
    return [np.asarray(values, dtype=int) for values in result if values]


def select_head(
    outer_available: np.ndarray,
    family_name: str,
    target: np.ndarray,
    base: np.ndarray,
    comps: np.ndarray,
    family: np.ndarray,
):
    family_available = outer_available[family[outer_available] == family_name]
    folds = family_folds(family_available, base)
    scores = []
    for spec in candidate_list(family_name):
        errors = []
        for valid in folds:
            available = np.setdiff1d(outer_available, valid)
            train = model_train_indices(available, family, family_name, spec)
            x_train, x_valid, _ = model_inputs(base, comps, train, valid, spec["optical"])
            state = fit_scalar(x_train, target[train], spec)
            errors.extend(np.abs(target[valid] - predict_scalar(x_valid, state)).tolist())
        scores.append((float(np.mean(errors)), spec))
    return min(scores, key=lambda item: (item[0], item[1]["name"]))


def fit_selected_head(available, eval_idx, family_name, target, spec, base, comps, family):
    train = model_train_indices(available, family, family_name, spec)
    x_train, x_eval, optical_proxy = model_inputs(base, comps, train, eval_idx, spec["optical"])
    regressor = fit_scalar(x_train, target[train], spec)
    pred = predict_scalar(x_eval, regressor)
    return pred, {
        "spec": spec,
        "training_indices": train,
        "optical_proxy": optical_proxy,
        "regressor": regressor,
    }


def main():
    (
        _, _, comps, temps, x, curves, base, family, anchor, ea, prefactor, fit_quality
    ) = load_data()
    basis = full_curve_basis(x, temps, modes=2)
    core_basis = basis[:, :3]
    residual_basis = basis[:, 3:]
    # Residual coefficients use every temperature point but cannot alter the two
    # fitted slopes or the reference point by construction.
    true_core = np.asarray(
        [core_basis @ np.r_[anchor[i], ea[i, 0], ea[i, 1]] for i in range(len(comps))]
    )
    residual_targets = (curves - true_core) @ residual_basis
    targets = {
        "Ea_high": ea[:, 0],
        "Ea_low": ea[:, 1],
        "anchor_220K": anchor,
        "residual_1": residual_targets[:, 0],
        "residual_2": residual_targets[:, 1],
    }
    predictions = {name: np.zeros(len(comps)) for name in targets}
    raw_predictions = {name: np.zeros(len(comps)) for name in targets}
    selections = []
    n = len(comps)

    for outer in range(n):
        available = np.setdiff1d(np.arange(n), [outer])
        fam = family[outer]
        for target_name, target in targets.items():
            score, spec = select_head(available, fam, target, base, comps, family)
            pred, _ = fit_selected_head(
                available, np.asarray([outer]), fam, target, spec, base, comps, family
            )
            raw_predictions[target_name][outer] = pred[0]
            predictions[target_name][outer] = pred[0]
            selections.append(
                {
                    "composition": comps[outer],
                    "family": fam,
                    "target": target_name,
                    "candidate": spec["name"],
                    "inner_MAE": score,
                }
            )
        predictions["Ea_high"][outer] = max(predictions["Ea_high"][outer], 1e-4)
        predictions["Ea_low"][outer] = max(predictions["Ea_low"][outer], 0.0)
        if (outer + 1) % 10 == 0 or outer + 1 == n:
            print(f"parameter-head nested LOCO {outer + 1}/{n}", flush=True)

    predicted_theta = np.column_stack(
        [
            predictions["anchor_220K"],
            predictions["Ea_high"],
            predictions["Ea_low"],
            predictions["residual_1"],
            predictions["residual_2"],
        ]
    )
    curve_pred = predicted_theta @ basis.T
    ea_pred = predicted_theta[:, 1:3]
    xb = 1.0 / (KB_EV_K * BOUNDARY_K)
    pref_pred = np.column_stack(
        [predicted_theta[:, 0] + ea_pred[:, 0] * xb, predicted_theta[:, 0] + ea_pred[:, 1] * xb]
    )
    derived_ln_sigma = curve_pred - np.log(temps)[None, :]

    summary_rows = []
    for fam in ["All", "Cs=0", "Cs=0.2"]:
        idx = np.arange(n) if fam == "All" else np.where(family == fam)[0]
        summary_rows.append(
            {
                "family": fam,
                "n": len(idx),
                "Ea_high_MAE_eV": np.mean(np.abs(ea[idx, 0] - ea_pred[idx, 0])),
                "Ea_high_R2": r2(ea[idx, 0], ea_pred[idx, 0]),
                "Ea_low_MAE_eV": np.mean(np.abs(ea[idx, 1] - ea_pred[idx, 1])),
                "Ea_low_R2": r2(ea[idx, 1], ea_pred[idx, 1]),
                "anchor_220K_MAE": np.mean(np.abs(anchor[idx] - predicted_theta[idx, 0])),
                "anchor_220K_R2": r2(anchor[idx], predicted_theta[idx, 0]),
                "ln_A_high_MAE": np.mean(np.abs(prefactor[idx, 0] - pref_pred[idx, 0])),
                "ln_A_low_MAE": np.mean(np.abs(prefactor[idx, 1] - pref_pred[idx, 1])),
                "curve_MAE": np.mean(np.abs(curves[idx] - curve_pred[idx])),
                "curve_pooled_R2": r2(curves[idx].ravel(), curve_pred[idx].ravel()),
                "curve_mean_per_composition_R2": np.mean(
                    [r2(curves[i], curve_pred[i]) for i in idx]
                ),
                "ln_sigmaT_reversal_fraction": np.mean(
                    [np.any(np.diff(curve_pred[i]) < 0) for i in idx]
                ),
                "derived_sigma_reversal_fraction": np.mean(
                    [np.any(np.diff(derived_ln_sigma[i]) < 0) for i in idx]
                ),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(HERE / "head_nested_loco_summary.csv", index=False, encoding="utf-8-sig")

    parameter_rows = []
    selected_df = pd.DataFrame(selections)
    for i, comp in enumerate(comps):
        row = {
            "composition": comp,
            "family": family[i],
            "Ea_high_true_eV": ea[i, 0],
            "Ea_high_pred_eV": ea_pred[i, 0],
            "Ea_high_raw_eV": raw_predictions["Ea_high"][i],
            "Ea_low_true_eV": ea[i, 1],
            "Ea_low_pred_eV": ea_pred[i, 1],
            "Ea_low_raw_eV": raw_predictions["Ea_low"][i],
            "anchor_220K_true": anchor[i],
            "anchor_220K_pred": predicted_theta[i, 0],
            "ln_A_high_true": prefactor[i, 0],
            "ln_A_high_pred": pref_pred[i, 0],
            "ln_A_low_true": prefactor[i, 1],
            "ln_A_low_pred": pref_pred[i, 1],
            "fit_R2_high": fit_quality[i, 0],
            "fit_R2_low": fit_quality[i, 1],
        }
        for target_name in targets:
            chosen = selected_df[
                (selected_df.composition == comp) & (selected_df.target == target_name)
            ].iloc[0]
            row[f"{target_name}_model"] = chosen.candidate
            row[f"{target_name}_inner_MAE"] = chosen.inner_MAE
        parameter_rows.append(row)
    pd.DataFrame(parameter_rows).to_csv(
        HERE / "head_nested_loco_parameter_predictions.csv", index=False, encoding="utf-8-sig"
    )
    selected_df.to_csv(HERE / "head_nested_model_selections.csv", index=False, encoding="utf-8-sig")

    curve_rows = []
    for i, comp in enumerate(comps):
        for j, temp in enumerate(temps):
            curve_rows.append(
                {
                    "composition": comp,
                    "family": family[i],
                    "temperature_K": temp,
                    "x_1_over_kBT": x[j],
                    "observed_ln_sigmaT": curves[i, j],
                    "predicted_ln_sigmaT": curve_pred[i, j],
                    "predicted_sigma_assuming_ln_sigmaT": math.exp(curve_pred[i, j]) / temp,
                    "residual": curves[i, j] - curve_pred[i, j],
                }
            )
    pd.DataFrame(curve_rows).to_csv(
        HERE / "head_nested_loco_curve_predictions.csv", index=False, encoding="utf-8-sig"
    )

    # Final artifact: choose and fit one head per family and physical parameter.
    final_heads = {}
    all_idx = np.arange(n)
    for fam in ("Cs=0", "Cs=0.2"):
        final_heads[fam] = {}
        for target_name, target in targets.items():
            score, spec = select_head(all_idx, fam, target, base, comps, family)
            fam_eval = np.where(family == fam)[0][:1]
            _, state = fit_selected_head(
                all_idx, fam_eval, fam, target, spec, base, comps, family
            )
            state.pop("training_indices", None)
            state["inner_cv_mae"] = score
            final_heads[fam][target_name] = state

    errors = np.abs(ea - ea_pred)
    anchor_errors = np.abs(anchor - predicted_theta[:, 0])
    curve_errors = np.abs(curves - curve_pred)
    base_mean, base_scale = standardizer_fit(base)
    base_z = (base - base_mean) / base_scale
    nearest = []
    for i in range(n):
        dist = np.sqrt(np.sum((base_z[np.arange(n) != i] - base_z[i]) ** 2, axis=1))
        nearest.append(float(dist.min()))
    artifact = {
        "model_version": "perovskite_transport_parameter_heads_v2.1",
        "created_date": "2026-09-08",
        "response_definition": "ln[sigma(T)*T] as instructed; absolute sigma units remain unverified",
        "boundary_temperature_K": BOUNDARY_K,
        "transition_width_x": 0.5,
        "residual_modes": 2,
        "k_B_eV_per_K": KB_EV_K,
        "input_order": ["cs_fraction", "fa_fraction", "iodide_fraction"],
        "physics_parameterization": [
            "anchor_220K", "Ea_high", "Ea_low", "broad_residual_1", "broad_residual_2"
        ],
        "training_compositions": comps,
        "training_ranges": {
            "cs_fraction": [float(base[:, 0].min()), float(base[:, 0].max())],
            "fa_fraction": [float(base[:, 1].min()), float(base[:, 1].max())],
            "iodide_fraction": [float(base[:, 2].min()), float(base[:, 2].max())],
        },
        "domain_distance": {
            "mean": base_mean,
            "scale": base_scale,
            "training_points": base_z,
            "nearest_neighbor_95pct": float(np.quantile(nearest, 0.95)),
        },
        "curve_basis_training_grid": {
            "temperature_K": temps,
            "x_1_over_kBT": x,
            "basis": basis,
        },
        "heads": final_heads,
        "uncertainty_calibration": {
            "method": "empirical nested-LOCO absolute-error quantiles",
            "Ea_high_abs_error_q90_eV": float(np.quantile(errors[:, 0], 0.9)),
            "Ea_low_abs_error_q90_eV": float(np.quantile(errors[:, 1], 0.9)),
            "anchor_abs_error_q90": float(np.quantile(anchor_errors, 0.9)),
            "curve_abs_error_q90_by_temperature": np.quantile(curve_errors, 0.9, axis=0),
        },
        "nested_loco_summary": summary.to_dict(orient="records"),
        "selection_counts": dict(Counter(selected_df.candidate)),
    }
    (HERE / "head_model_artifact.json").write_text(
        json.dumps(to_serializable(artifact), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    comparison = {
        "v2_1_parameter_heads_nested_loco": summary.to_dict(orient="records"),
        "v2_0_joint_nested_loco": pd.read_csv(HERE / "nested_loco_summary.csv").to_dict(orient="records"),
    }
    if V1_SUMMARY.exists():
        comparison["v1_development_loco"] = pd.read_csv(V1_SUMMARY).to_dict(orient="records")
    (HERE / "all_model_metrics.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n", summary.to_string(index=False))
    print("\nselection counts", Counter(selected_df.candidate))


if __name__ == "__main__":
    main()
