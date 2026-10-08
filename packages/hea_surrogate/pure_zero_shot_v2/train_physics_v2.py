from __future__ import annotations

from collections import Counter
from itertools import product
from pathlib import Path
import json
import math
import os

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
HERE = Path(os.environ.get("PEROVSKITE_OUTPUT_DIR", SCRIPT_DIR))
DATA = Path(
    os.environ.get(
        "PEROVSKITE_DATA_CSV",
        str(SCRIPT_DIR / "data" / "transport_data.csv"),
    )
)
OPTICAL = Path(
    os.environ.get(
        "PEROVSKITE_OPTICAL_CSV",
        str(SCRIPT_DIR / "data" / "optical_features_raw.csv"),
    )
)
V1_SUMMARY = Path(
    os.environ.get(
        "PEROVSKITE_V1_SUMMARY_CSV",
        str(SCRIPT_DIR / "data" / "v1_development_summary.csv"),
    )
)
BOUNDARY_K = 220.0
KB_EV_K = 8.617333262145e-5
SEED = 20260908

OPTICAL_COLUMNS = [
    "pl_peak_eV",
    "pl_fwhm_nm",
    "pl_weighted_sd_nm",
    "pl_skew",
    "pl_map_energy_mean",
    "pl_map_energy_sd",
    "pl_map_energy_iqr",
    "pl_map_energy_skew",
    "uv_edge_eV",
    "uv_edge_width_nm",
    "uv_tail_proxy_eV",
]

# These are deliberately small models.  The validation set has only one held-out
# composition at a time, so model capacity is constrained before tuning.
BASE_CANDIDATES = [
    {"name": "comp_linear_a03", "optical": False, "kind": "poly", "degree": 1, "alpha": 0.3},
    {"name": "comp_quad_a003", "optical": False, "kind": "poly", "degree": 2, "alpha": 0.03},
    {"name": "comp_quad_a03", "optical": False, "kind": "poly", "degree": 2, "alpha": 0.3},
    {"name": "comp_rbf8_e12", "optical": False, "kind": "rbf", "landmarks": 8, "ell": 1.2, "alpha": 0.1},
    {"name": "comp_rbf12_e22", "optical": False, "kind": "rbf", "landmarks": 12, "ell": 2.2, "alpha": 0.1},
    {"name": "opt_quad_a03", "optical": True, "kind": "poly", "degree": 2, "alpha": 0.3},
    {"name": "opt_rbf12_e22", "optical": True, "kind": "rbf", "landmarks": 12, "ell": 2.2, "alpha": 0.1},
]

OBJECTIVE_PROFILES = {
    "balanced": {"ea": 5.0, "curve": 1.0, "anchor": 1.5, "prefactor": 0.75, "residual": 0.12},
    "ea_central": {"ea": 20.0, "curve": 1.0, "anchor": 1.5, "prefactor": 0.25, "residual": 0.20},
}

CANDIDATES = [
    {**base, "name": f"{base['name']}__{profile}", "objective_profile": profile}
    for base in BASE_CANDIDATES
    for profile in OBJECTIVE_PROFILES
]


def r2(y: np.ndarray, pred: np.ndarray) -> float:
    y = np.asarray(y, float)
    pred = np.asarray(pred, float)
    den = np.sum((y - y.mean()) ** 2)
    return float(1.0 - np.sum((y - pred) ** 2) / den) if den > 0 else float("nan")


def line_stats(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    xc = x - x.mean()
    yc = y - y.mean()
    slope = float(np.dot(xc, yc) / np.dot(xc, xc))
    intercept = float(y.mean() - slope * x.mean())
    pred = intercept + slope * x
    return -slope, intercept, r2(y, pred)


def load_data():
    df = pd.read_csv(DATA)
    df["temperature_grid_K"] = df.temperature_K.round().astype(int)
    meta = df.drop_duplicates("composition").sort_values("composition").reset_index(drop=True)
    comps = meta.composition.to_numpy()
    temps = np.sort(df.temperature_grid_K.unique()).astype(float)
    x = 1.0 / (KB_EV_K * temps)
    curves = np.vstack(
        [
            df[df.composition == c]
            .set_index("temperature_grid_K")
            .ln_sigmaT.reindex(temps.astype(int))
            .to_numpy(float)
            for c in comps
        ]
    )
    # Cs, FA and iodide are independent coordinates; MA and bromide are complements.
    base = meta[["cs_fraction", "fa_fraction", "iodide_fraction"]].to_numpy(float)
    family = np.where(base[:, 0] < 0.1, "Cs=0", "Cs=0.2")
    anchor_idx = int(np.argmin(np.abs(temps - BOUNDARY_K)))
    anchor = curves[:, anchor_idx]
    ea = []
    pref = []
    fit_quality = []
    for y in curves:
        high = line_stats(x[temps >= BOUNDARY_K], y[temps >= BOUNDARY_K])
        low = line_stats(x[temps <= BOUNDARY_K], y[temps <= BOUNDARY_K])
        ea.append([high[0], low[0]])
        pref.append([high[1], low[1]])
        fit_quality.append([high[2], low[2]])
    return (
        df,
        meta,
        comps,
        temps,
        x,
        curves,
        base,
        family,
        anchor,
        np.asarray(ea),
        np.asarray(pref),
        np.asarray(fit_quality),
    )


def smooth_two_regime_basis(x: np.ndarray, width_x: float = 0.5) -> np.ndarray:
    xb = 1.0 / (KB_EV_K * BOUNDARY_K)
    d = np.asarray(x, float) - xb
    high_integral = -width_x * (np.logaddexp(0.0, -d / width_x) - math.log(2.0))
    return np.column_stack([np.ones(len(x)), -high_integral, -(d - high_integral)])


def segment_slope_operator(x: np.ndarray, temps: np.ndarray) -> np.ndarray:
    rows = []
    for mask in (temps >= BOUNDARY_K, temps <= BOUNDARY_K):
        xm = x[mask]
        xc = xm - xm.mean()
        row = np.zeros(len(x))
        row[mask] = -xc / np.dot(xc, xc)
        rows.append(row)
    return np.vstack(rows)


def residual_basis(x: np.ndarray, temps: np.ndarray, modes: int = 2) -> np.ndarray:
    """Broad residual modes that cannot change either fitted Ea or the 220 K anchor."""
    slope = segment_slope_operator(x, temps)
    anchor = np.zeros(len(temps))
    anchor[int(np.argmin(np.abs(temps - BOUNDARY_K)))] = 1.0
    constraints = np.vstack([slope, anchor])
    null_projector = np.eye(len(temps)) - constraints.T @ np.linalg.solve(
        constraints @ constraints.T, constraints
    )
    grid = np.arange(len(temps), dtype=float)
    raw = np.column_stack(
        [np.cos(np.pi * k * (grid + 0.5) / len(temps)) for k in range(1, modes + 4)]
    )
    kept = []
    for vec in (null_projector @ raw).T:
        for old in kept:
            vec = vec - old * np.dot(old, vec)
        norm = np.linalg.norm(vec)
        if norm > 1e-9:
            kept.append(vec / norm)
        if len(kept) == modes:
            break
    return np.column_stack(kept)


def full_curve_basis(x: np.ndarray, temps: np.ndarray, modes: int = 2) -> np.ndarray:
    return np.column_stack([smooth_two_regime_basis(x), residual_basis(x, temps, modes)])


def standardizer_fit(x: np.ndarray):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-10] = 1.0
    return mean, scale


def exact_rbf(a: np.ndarray, b: np.ndarray, ell: float) -> np.ndarray:
    d2 = np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=2)
    return np.exp(-0.5 * d2 / (ell**2))


def fit_optical_proxy(base: np.ndarray, comps: np.ndarray, train_idx: np.ndarray):
    optical = pd.read_csv(OPTICAL).set_index("composition")
    opt_idx = np.asarray([i for i in train_idx if comps[i] in optical.index], dtype=int)
    if len(opt_idx) < 8:
        return None
    values = optical.loc[comps[opt_idx], OPTICAL_COLUMNS].to_numpy(float)
    med = np.nanmedian(values, axis=0)
    values = np.where(np.isfinite(values), values, med)
    om, os = standardizer_fit(values)
    standardized = (values - om) / os
    _, _, vt = np.linalg.svd(standardized, full_matrices=False)
    loadings = vt[:2]
    latent = standardized @ loadings.T
    # Optical samples all belong to Cs=0.2; FA and halide fraction carry variation.
    coords = base[opt_idx][:, [1, 2]]
    cm, cs = standardizer_fit(coords)
    coords_z = (coords - cm) / cs
    kernel = exact_rbf(coords_z, coords_z, 1.2)
    coef = np.linalg.solve(kernel + 0.1 * np.eye(len(coords_z)), latent)
    return {
        "composition_mean": cm,
        "composition_scale": cs,
        "centers": coords_z,
        "coef": coef,
        "ell": 1.2,
        "optical_mean": om,
        "optical_scale": os,
        "loadings": loadings,
        "n_optical_training": int(len(opt_idx)),
    }


def apply_optical_proxy(base: np.ndarray, state) -> np.ndarray:
    if state is None:
        return np.zeros((len(base), 2))
    coords = (base[:, [1, 2]] - state["composition_mean"]) / state["composition_scale"]
    return exact_rbf(coords, state["centers"], state["ell"]) @ state["coef"]


def make_inputs(base: np.ndarray, comps: np.ndarray, train_idx: np.ndarray, indices: np.ndarray, optical: bool):
    if not optical:
        return base[indices], None
    state = fit_optical_proxy(base, comps, train_idx)
    latent = apply_optical_proxy(base[indices], state)
    return np.column_stack([base[indices], latent]), state


def exponent_table(n_features: int, degree: int) -> np.ndarray:
    return np.asarray(
        [e for e in product(range(degree + 1), repeat=n_features) if 0 < sum(e) <= degree],
        dtype=int,
    )


def farthest_landmarks(x: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(x))
    center = x.mean(axis=0)
    first = int(np.argmax(np.sum((x - center) ** 2, axis=1)))
    chosen = [first]
    distance = np.sum((x - x[first]) ** 2, axis=1)
    while len(chosen) < count:
        nxt = int(np.argmax(distance))
        chosen.append(nxt)
        distance = np.minimum(distance, np.sum((x - x[nxt]) ** 2, axis=1))
    return x[np.asarray(chosen)]


def fit_feature_map(x: np.ndarray, spec: dict):
    mean, scale = standardizer_fit(x)
    z = (x - mean) / scale
    state = {"mean": mean, "scale": scale, "kind": spec["kind"]}
    if spec["kind"] == "poly":
        state["exponents"] = exponent_table(z.shape[1], int(spec["degree"]))
    else:
        state["landmarks"] = farthest_landmarks(z, int(spec["landmarks"]))
        state["ell"] = float(spec["ell"])
    return state


def apply_feature_map(x: np.ndarray, state: dict) -> np.ndarray:
    z = (x - state["mean"]) / state["scale"]
    if state["kind"] == "poly":
        exponents = state["exponents"]
        nonlinear = np.column_stack([np.prod(z**e, axis=1) for e in exponents])
        return np.column_stack([np.ones(len(z)), nonlinear])
    kernels = exact_rbf(z, state["landmarks"], state["ell"])
    return np.column_stack([np.ones(len(z)), z, kernels])


def safe_scale(values: np.ndarray, floor: float) -> np.ndarray:
    out = np.std(values, axis=0)
    return np.maximum(out, floor)


def fit_joint_model(
    inputs: np.ndarray,
    curves: np.ndarray,
    ea: np.ndarray,
    anchor: np.ndarray,
    prefactor: np.ndarray,
    curve_basis: np.ndarray,
    spec: dict,
):
    weights = OBJECTIVE_PROFILES[spec.get("objective_profile", "balanced")]
    fmap = fit_feature_map(inputs, spec)
    phi = apply_feature_map(inputs, fmap)
    n, p = phi.shape
    q = curve_basis.shape[1]
    scales = {
        "curve": float(max(np.std(curves), 0.5)),
        "ea": safe_scale(ea, 0.03),
        "anchor": float(max(np.std(anchor), 0.5)),
        "prefactor": safe_scale(prefactor, 0.5),
    }
    xb = 1.0 / (KB_EV_K * BOUNDARY_K)
    selector_anchor = np.r_[1.0, np.zeros(q - 1)]
    selector_ea_high = np.r_[0.0, 1.0, np.zeros(q - 2)]
    selector_ea_low = np.r_[0.0, 0.0, 1.0, np.zeros(q - 3)]
    selector_pref_high = selector_anchor + xb * selector_ea_high
    selector_pref_low = selector_anchor + xb * selector_ea_low
    rows = []
    targets = []

    def add(phi_i, selector, value, factor):
        rows.append(np.kron(phi_i, selector) * factor)
        targets.append(float(value) * factor)

    for i in range(n):
        curve_factor = math.sqrt(weights["curve"] / len(curve_basis)) / scales["curve"]
        for j, selector in enumerate(curve_basis):
            add(phi[i], selector, curves[i, j], curve_factor)
        add(
            phi[i], selector_ea_high, ea[i, 0],
            math.sqrt(weights["ea"] / 2.0) / scales["ea"][0],
        )
        add(
            phi[i], selector_ea_low, ea[i, 1],
            math.sqrt(weights["ea"] / 2.0) / scales["ea"][1],
        )
        add(
            phi[i], selector_anchor, anchor[i],
            math.sqrt(weights["anchor"]) / scales["anchor"],
        )
        add(
            phi[i], selector_pref_high, prefactor[i, 0],
            math.sqrt(weights["prefactor"] / 2.0) / scales["prefactor"][0],
        )
        add(
            phi[i], selector_pref_low, prefactor[i, 1],
            math.sqrt(weights["prefactor"] / 2.0) / scales["prefactor"][1],
        )
        for k in range(3, q):
            selector = np.zeros(q)
            selector[k] = 1.0
            add(
                phi[i], selector, 0.0,
                math.sqrt(weights["residual"] / max(1, q - 3)),
            )
    design = np.vstack(rows)
    target = np.asarray(targets)
    penalty = np.eye(p * q)
    penalty[:q, :q] = 0.0  # do not shrink the population mean for any output
    gram = design.T @ design + float(spec["alpha"]) * penalty
    coef = np.linalg.solve(gram, design.T @ target).reshape(p, q)
    return {"feature_map": fmap, "coef": coef, "scales": scales, "q": q}


def predict_joint(inputs: np.ndarray, model: dict, curve_basis: np.ndarray):
    phi = apply_feature_map(inputs, model["feature_map"])
    theta = phi @ model["coef"]
    # The experimental low-temperature fit contains a few negative effective slopes.
    # For generated transport curves, use a small non-negative physical floor while
    # retaining raw values in diagnostic output.
    theta_physical = theta.copy()
    theta_physical[:, 1] = np.maximum(theta_physical[:, 1], 1e-4)
    theta_physical[:, 2] = np.maximum(theta_physical[:, 2], 0.0)
    curves = theta_physical @ curve_basis.T
    return theta, theta_physical, curves


def inner_folds(indices: np.ndarray, family: np.ndarray, base: np.ndarray, folds: int = 4):
    buckets = [[] for _ in range(folds)]
    for fam in np.unique(family[indices]):
        members = indices[family[indices] == fam]
        members = members[np.lexsort((base[members, 2], base[members, 1]))]
        for rank, idx in enumerate(members):
            buckets[rank % folds].append(int(idx))
    return [np.asarray(sorted(bucket), dtype=int) for bucket in buckets if bucket]


def validation_score(true_ea, pred_ea, true_curve, pred_curve, true_anchor, pred_anchor, scales):
    ea_term = np.mean(np.abs(true_ea - pred_ea) / scales["ea"])
    curve_term = np.mean(np.abs(true_curve - pred_curve)) / scales["curve"]
    anchor_term = np.mean(np.abs(true_anchor - pred_anchor)) / scales["anchor"]
    return float(2.0 * ea_term + curve_term + 0.5 * anchor_term)


def choose_candidate(
    outer_train: np.ndarray,
    base: np.ndarray,
    comps: np.ndarray,
    family: np.ndarray,
    curves: np.ndarray,
    ea: np.ndarray,
    anchor: np.ndarray,
    prefactor: np.ndarray,
    curve_basis: np.ndarray,
):
    folds = inner_folds(outer_train, family, base)
    global_scales = {
        "curve": float(max(np.std(curves[outer_train]), 0.5)),
        "ea": safe_scale(ea[outer_train], 0.03),
        "anchor": float(max(np.std(anchor[outer_train]), 0.5)),
    }
    scored = []
    for spec in CANDIDATES:
        fold_scores = []
        for valid in folds:
            train = np.setdiff1d(outer_train, valid)
            x_train, _ = make_inputs(base, comps, train, train, spec["optical"])
            x_valid, _ = make_inputs(base, comps, train, valid, spec["optical"])
            model = fit_joint_model(
                x_train, curves[train], ea[train], anchor[train], prefactor[train], curve_basis, spec
            )
            _, theta, pred_curve = predict_joint(x_valid, model, curve_basis)
            fold_scores.append(
                validation_score(
                    ea[valid], theta[:, 1:3], curves[valid], pred_curve,
                    anchor[valid], theta[:, 0], global_scales,
                )
            )
        scored.append((float(np.mean(fold_scores)), spec))
    return min(scored, key=lambda item: (item[0], item[1]["name"]))


def to_serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {key: to_serializable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_serializable(val) for val in value]
    return value


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    (
        _, meta, comps, temps, x, curves, base, family, anchor, ea, prefactor, fit_quality
    ) = load_data()
    basis = full_curve_basis(x, temps, modes=2)
    n = len(comps)
    ea_pred = np.zeros_like(ea)
    ea_raw = np.zeros_like(ea)
    anchor_pred = np.zeros_like(anchor)
    curve_pred = np.zeros_like(curves)
    residual_coefficients = np.zeros((n, basis.shape[1] - 3))
    selections = []

    for outer in range(n):
        outer_train = np.setdiff1d(np.arange(n), [outer])
        score, spec = choose_candidate(
            outer_train, base, comps, family, curves, ea, anchor, prefactor, basis
        )
        x_train, _ = make_inputs(base, comps, outer_train, outer_train, spec["optical"])
        x_test, _ = make_inputs(base, comps, outer_train, np.asarray([outer]), spec["optical"])
        model = fit_joint_model(
            x_train, curves[outer_train], ea[outer_train], anchor[outer_train],
            prefactor[outer_train], basis, spec,
        )
        raw_theta, physical_theta, pred = predict_joint(x_test, model, basis)
        ea_raw[outer] = raw_theta[0, 1:3]
        ea_pred[outer] = physical_theta[0, 1:3]
        anchor_pred[outer] = physical_theta[0, 0]
        residual_coefficients[outer] = physical_theta[0, 3:]
        curve_pred[outer] = pred[0]
        selections.append({"outer": comps[outer], "candidate": spec["name"], "inner_score": score})
        if (outer + 1) % 10 == 0 or outer + 1 == n:
            print(f"nested LOCO {outer + 1}/{n}", flush=True)

    xb = 1.0 / (KB_EV_K * BOUNDARY_K)
    pref_pred = np.column_stack(
        [anchor_pred + ea_pred[:, 0] * xb, anchor_pred + ea_pred[:, 1] * xb]
    )
    derived_ln_sigma = curve_pred - np.log(temps)[None, :]
    summary_rows = []
    for fam in ["All", "Cs=0", "Cs=0.2"]:
        idx = np.arange(n) if fam == "All" else np.where(family == fam)[0]
        per_curve_r2 = [r2(curves[i], curve_pred[i]) for i in idx]
        summary_rows.append(
            {
                "family": fam,
                "n": len(idx),
                "Ea_high_MAE_eV": np.mean(np.abs(ea[idx, 0] - ea_pred[idx, 0])),
                "Ea_high_R2": r2(ea[idx, 0], ea_pred[idx, 0]),
                "Ea_low_MAE_eV": np.mean(np.abs(ea[idx, 1] - ea_pred[idx, 1])),
                "Ea_low_R2": r2(ea[idx, 1], ea_pred[idx, 1]),
                "anchor_220K_MAE": np.mean(np.abs(anchor[idx] - anchor_pred[idx])),
                "anchor_220K_R2": r2(anchor[idx], anchor_pred[idx]),
                "ln_A_high_MAE": np.mean(np.abs(prefactor[idx, 0] - pref_pred[idx, 0])),
                "ln_A_low_MAE": np.mean(np.abs(prefactor[idx, 1] - pref_pred[idx, 1])),
                "curve_MAE": np.mean(np.abs(curves[idx] - curve_pred[idx])),
                "curve_pooled_R2": r2(curves[idx].ravel(), curve_pred[idx].ravel()),
                "curve_mean_per_composition_R2": np.mean(per_curve_r2),
                "ln_sigmaT_reversal_fraction": np.mean(
                    [np.any(np.diff(curve_pred[i]) < 0) for i in idx]
                ),
                "derived_sigma_reversal_fraction": np.mean(
                    [np.any(np.diff(derived_ln_sigma[i]) < 0) for i in idx]
                ),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(HERE / "nested_loco_summary.csv", index=False, encoding="utf-8-sig")

    ea_rows = []
    for i, comp in enumerate(comps):
        ea_rows.append(
            {
                "composition": comp,
                "family": family[i],
                "Ea_high_true_eV": ea[i, 0],
                "Ea_high_pred_eV": ea_pred[i, 0],
                "Ea_high_raw_eV": ea_raw[i, 0],
                "Ea_low_true_eV": ea[i, 1],
                "Ea_low_pred_eV": ea_pred[i, 1],
                "Ea_low_raw_eV": ea_raw[i, 1],
                "anchor_220K_true": anchor[i],
                "anchor_220K_pred": anchor_pred[i],
                "ln_A_high_true": prefactor[i, 0],
                "ln_A_high_pred": pref_pred[i, 0],
                "ln_A_low_true": prefactor[i, 1],
                "ln_A_low_pred": pref_pred[i, 1],
                "fit_R2_high": fit_quality[i, 0],
                "fit_R2_low": fit_quality[i, 1],
                "selected_candidate": selections[i]["candidate"],
                "inner_validation_score": selections[i]["inner_score"],
            }
        )
    pd.DataFrame(ea_rows).to_csv(
        HERE / "nested_loco_parameter_predictions.csv", index=False, encoding="utf-8-sig"
    )

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
        HERE / "nested_loco_curve_predictions.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(selections).to_csv(
        HERE / "nested_model_selections.csv", index=False, encoding="utf-8-sig"
    )

    # Use the same grouped inner validation once more to select the deployable full-data model.
    _, final_spec = choose_candidate(
        np.arange(n), base, comps, family, curves, ea, anchor, prefactor, basis
    )
    final_inputs, optical_state = make_inputs(
        base, comps, np.arange(n), np.arange(n), final_spec["optical"]
    )
    final_model = fit_joint_model(final_inputs, curves, ea, anchor, prefactor, basis, final_spec)

    errors_ea = np.abs(ea - ea_pred)
    errors_anchor = np.abs(anchor - anchor_pred)
    errors_curve = np.abs(curves - curve_pred)
    nearest_distances = []
    base_mean, base_scale = standardizer_fit(base)
    base_z = (base - base_mean) / base_scale
    for i in range(n):
        distance = np.sqrt(np.sum((base_z[np.arange(n) != i] - base_z[i]) ** 2, axis=1))
        nearest_distances.append(float(distance.min()))

    artifact = {
        "model_version": "perovskite_transport_physics_v2.0",
        "created_date": "2026-09-08",
        "response_definition": "ln[sigma(T)*T] as instructed; absolute sigma units remain unverified",
        "boundary_temperature_K": BOUNDARY_K,
        "transition_width_x": 0.5,
        "residual_modes": 2,
        "k_B_eV_per_K": KB_EV_K,
        "input_order": ["cs_fraction", "fa_fraction", "iodide_fraction"],
        "training_compositions": comps.tolist(),
        "training_ranges": {
            "cs_fraction": [float(base[:, 0].min()), float(base[:, 0].max())],
            "fa_fraction": [float(base[:, 1].min()), float(base[:, 1].max())],
            "iodide_fraction": [float(base[:, 2].min()), float(base[:, 2].max())],
        },
        "domain_distance": {
            "mean": base_mean,
            "scale": base_scale,
            "training_points": base_z,
            "nearest_neighbor_95pct": float(np.quantile(nearest_distances, 0.95)),
        },
        "selected_spec": final_spec,
        "objective_profiles": OBJECTIVE_PROFILES,
        "curve_basis_training_grid": {
            "temperature_K": temps,
            "x_1_over_kBT": x,
            "basis": basis,
        },
        "optical_proxy": optical_state,
        "joint_model": final_model,
        "uncertainty_calibration": {
            "method": "empirical nested-LOCO absolute-error quantiles",
            "Ea_high_abs_error_q90_eV": float(np.quantile(errors_ea[:, 0], 0.9)),
            "Ea_low_abs_error_q90_eV": float(np.quantile(errors_ea[:, 1], 0.9)),
            "anchor_abs_error_q90": float(np.quantile(errors_anchor, 0.9)),
            "curve_abs_error_q90_by_temperature": np.quantile(errors_curve, 0.9, axis=0),
        },
        "nested_loco_summary": summary.to_dict(orient="records"),
        "selection_counts": dict(Counter(s["candidate"] for s in selections)),
    }
    artifact = to_serializable(artifact)
    (HERE / "model_artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    comparison = {"v2_nested_loco": summary.to_dict(orient="records")}
    if V1_SUMMARY.exists():
        comparison["v1_development_loco"] = pd.read_csv(V1_SUMMARY).to_dict(orient="records")
    (HERE / "v1_v2_metrics.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n", summary.to_string(index=False))
    print("\nfinal candidate", final_spec["name"])
    print("selection counts", Counter(s["candidate"] for s in selections))


if __name__ == "__main__":
    main()
