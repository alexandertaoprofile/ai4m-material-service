from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import json
import math

import numpy as np

from predict import normalize_composition, exact_rbf, smooth_core


DEFAULT_MODEL = Path(__file__).resolve().parent / "model_artifact.json"


def load_artifact(path: Path) -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    fmap = artifact["joint_model"]["feature_map"]
    for key in ("mean", "scale", "exponents", "landmarks"):
        if key in fmap:
            fmap[key] = np.asarray(fmap[key], dtype=int if key == "exponents" else float)
    artifact["joint_model"]["coef"] = np.asarray(artifact["joint_model"]["coef"], float)
    proxy = artifact.get("optical_proxy")
    if proxy:
        for key in (
            "composition_mean", "composition_scale", "centers", "coef",
            "optical_mean", "optical_scale", "loadings",
        ):
            proxy[key] = np.asarray(proxy[key], float)
    grid = artifact["curve_basis_training_grid"]
    grid["temperature_K"] = np.asarray(grid["temperature_K"], float)
    grid["basis"] = np.asarray(grid["basis"], float)
    domain = artifact["domain_distance"]
    domain["mean"] = np.asarray(domain["mean"], float)
    domain["scale"] = np.asarray(domain["scale"], float)
    domain["training_points"] = np.asarray(domain["training_points"], float)
    calibration = artifact["uncertainty_calibration"]
    calibration["curve_abs_error_q90_by_temperature"] = np.asarray(
        calibration["curve_abs_error_q90_by_temperature"], float
    )
    return artifact


def optical_latent(base: np.ndarray, state: dict) -> np.ndarray:
    coords = (base[:, [1, 2]] - state["composition_mean"]) / state["composition_scale"]
    return exact_rbf(coords, state["centers"], float(state["ell"])) @ state["coef"]


def feature_map(inputs: np.ndarray, state: dict) -> np.ndarray:
    z = (inputs - state["mean"]) / state["scale"]
    if state["kind"] == "poly":
        return np.column_stack(
            [np.ones(len(z))]
            + [np.prod(z**exponent, axis=1) for exponent in state["exponents"]]
        )
    kernels = exact_rbf(z, state["landmarks"], float(state["ell"]))
    return np.column_stack([np.ones(len(z)), z, kernels])


def predict(request: dict, artifact: dict) -> dict:
    composition = normalize_composition(request)
    base = np.asarray(
        [[composition["cs_fraction"], composition["fa_fraction"], composition["iodide_fraction"]]],
        float,
    )
    spec = artifact["selected_spec"]
    inputs = base
    if spec["optical"]:
        inputs = np.column_stack([base, optical_latent(base, artifact["optical_proxy"])])
    phi = feature_map(inputs, artifact["joint_model"]["feature_map"])
    theta_raw = (phi @ artifact["joint_model"]["coef"])[0]
    anchor = float(theta_raw[0])
    ea_high = max(float(theta_raw[1]), 1e-4)
    ea_low = max(float(theta_raw[2]), 0.0)

    temperatures = np.asarray(
        request.get("temperatures_K", artifact["curve_basis_training_grid"]["temperature_K"]),
        float,
    )
    if temperatures.ndim != 1 or not len(temperatures):
        raise ValueError("temperatures_K must be a non-empty one-dimensional list")
    temperatures = np.sort(temperatures)
    if np.any((temperatures < 150.0) | (temperatures > 360.0)):
        raise ValueError("this model only supports 150-360 K; measured training grid is 170-330 K")
    x, core = smooth_core(temperatures, anchor, ea_high, ea_low, artifact)

    include_residual = bool(request.get("include_residual", False))
    residual = np.zeros_like(core)
    if include_residual and len(theta_raw) > 3:
        training_grid = artifact["curve_basis_training_grid"]["temperature_K"]
        residual_basis = artifact["curve_basis_training_grid"]["basis"][:, 3:]
        residual_grid = residual_basis @ theta_raw[3:]
        residual = np.interp(temperatures, training_grid, residual_grid)
    ln_sigma_t = core + residual
    sigma = np.exp(ln_sigma_t) / temperatures
    xb = 1.0 / (
        float(artifact["k_B_eV_per_K"]) * float(artifact["boundary_temperature_K"])
    )
    ln_a_high = anchor + ea_high * xb
    ln_a_low = anchor + ea_low * xb

    domain = artifact["domain_distance"]
    z = (base[0] - domain["mean"]) / domain["scale"]
    nearest = float(np.min(np.sqrt(np.sum((domain["training_points"] - z) ** 2, axis=1))))
    ranges = artifact["training_ranges"]
    inside_box = all(
        [
            ranges["cs_fraction"][0] <= base[0, 0] <= ranges["cs_fraction"][1],
            ranges["fa_fraction"][0] <= base[0, 1] <= ranges["fa_fraction"][1],
            ranges["iodide_fraction"][0] <= base[0, 2] <= ranges["iodide_fraction"][1],
        ]
    )
    calibration = artifact["uncertainty_calibration"]
    training_grid = artifact["curve_basis_training_grid"]["temperature_K"]
    curve_q90 = np.interp(
        temperatures, training_grid, calibration["curve_abs_error_q90_by_temperature"]
    )
    warnings = []
    if not inside_box or nearest > float(domain["nearest_neighbor_95pct"]):
        warnings.append("composition is outside or sparse relative to the measured composition grid")
    if abs(composition["cs_fraction"] - 0.0) > 0.03 and abs(composition["cs_fraction"] - 0.2) > 0.03:
        warnings.append("Cs fraction differs from both measured families (0 and 0.2); prediction is extrapolative")
    if theta_raw[2] < 0:
        warnings.append("raw low-temperature effective Ea was negative and was clipped to zero")
    if include_residual:
        warnings.append("broad learned residual is enabled; inspect curve diagnostics before scientific use")
    warnings.append("sigma assumes the supplied response is ln[sigma(T)*T]; absolute sigma units remain unverified")

    return {
        "model_version": artifact["model_version"],
        "mode": "pure_zero_shot",
        "composition": composition,
        "selected_training_objective": spec["objective_profile"],
        "residual_enabled": include_residual,
        "parameters": {
            "Ea_high_eV": ea_high,
            "Ea_low_eV": ea_low,
            "Ea_high_raw_eV": float(theta_raw[1]),
            "Ea_low_raw_eV": float(theta_raw[2]),
            "anchor_220K_ln_sigmaT": anchor,
            "ln_A_high": ln_a_high,
            "ln_A_low": ln_a_low,
            "A_high_dataset_units": math.exp(ln_a_high),
            "A_low_dataset_units": math.exp(ln_a_low),
            "transition_temperature_K": float(artifact["boundary_temperature_K"]),
        },
        "uncertainty": {
            "calibration": calibration["method"],
            "Ea_high_abs_error_q90_eV": calibration["Ea_high_abs_error_q90_eV"],
            "Ea_low_abs_error_q90_eV": calibration["Ea_low_abs_error_q90_eV"],
            "anchor_abs_error_q90": calibration["anchor_abs_error_q90"],
        },
        "domain": {
            "inside_training_box": bool(inside_box),
            "standardized_nearest_distance": nearest,
            "reference_95pct_distance": float(domain["nearest_neighbor_95pct"]),
        },
        "curve": [
            {
                "temperature_K": float(t),
                "x_1_over_kBT": float(xv),
                "predicted_ln_sigmaT": float(y),
                "predicted_sigma": float(s),
                "ln_sigmaT_abs_error_q90": float(q),
            }
            for t, xv, y, s, q in zip(temperatures, x, ln_sigma_t, sigma, curve_q90)
        ],
        "warnings": warnings,
    }


def main():
    parser = ArgumentParser(description="Joint-loss pure zero-shot perovskite transport predictor")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--request", type=Path, help="JSON request file")
    parser.add_argument("--composition", help="Perovskite formula")
    parser.add_argument("--temperatures", help="Comma-separated temperatures in K")
    parser.add_argument("--include-residual", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.request:
        request = json.loads(args.request.read_text(encoding="utf-8"))
    else:
        if not args.composition:
            parser.error("provide --request or --composition")
        request = {"composition": args.composition}
        if args.temperatures:
            request["temperatures_K"] = [float(v) for v in args.temperatures.split(",")]
        if args.include_residual:
            request["include_residual"] = True
    result = predict(request, load_artifact(args.model))
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
