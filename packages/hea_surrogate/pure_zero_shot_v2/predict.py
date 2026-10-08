from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import json
import math
import re

import numpy as np


DEFAULT_MODEL = Path(__file__).resolve().parent / "head_model_artifact.json"


def _arrays_in_regressor(state: dict) -> dict:
    result = dict(state)
    for key in ("input_mean", "input_scale", "exponents", "coef", "centers"):
        if key in result:
            result[key] = np.asarray(result[key], dtype=float if key != "exponents" else int)
    return result


def _arrays_in_optical(state):
    if state is None:
        return None
    result = dict(state)
    for key in (
        "composition_mean", "composition_scale", "centers", "coef",
        "optical_mean", "optical_scale", "loadings",
    ):
        if key in result:
            result[key] = np.asarray(result[key], dtype=float)
    return result


def load_artifact(path: Path) -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact["domain_distance"]["mean"] = np.asarray(artifact["domain_distance"]["mean"], float)
    artifact["domain_distance"]["scale"] = np.asarray(artifact["domain_distance"]["scale"], float)
    artifact["domain_distance"]["training_points"] = np.asarray(
        artifact["domain_distance"]["training_points"], float
    )
    for family in artifact["heads"].values():
        for head in family.values():
            head["regressor"] = _arrays_in_regressor(head["regressor"])
            head["optical_proxy"] = _arrays_in_optical(head.get("optical_proxy"))
    grid = artifact["curve_basis_training_grid"]
    grid["temperature_K"] = np.asarray(grid["temperature_K"], float)
    grid["basis"] = np.asarray(grid["basis"], float)
    calibration = artifact["uncertainty_calibration"]
    calibration["curve_abs_error_q90_by_temperature"] = np.asarray(
        calibration["curve_abs_error_q90_by_temperature"], float
    )
    return artifact


def _amount(prefix: str, token: str) -> float:
    match = re.search(rf"{token}([0-9]*\.?[0-9]*)", prefix)
    if not match:
        return 0.0
    return float(match.group(1)) if match.group(1) else 1.0


def parse_formula(formula: str) -> dict:
    compact = re.sub(r"\s+", "", formula)
    if "Pb" not in compact:
        raise ValueError("formula must contain Pb, for example Cs0.2FA0.4MA0.4Pb(I0.6Br0.4)3")
    a_site = compact.split("Pb", 1)[0]
    cs = _amount(a_site, "Cs")
    fa = _amount(a_site, "FA")
    ma = _amount(a_site, "MA")
    halide = compact.split("Pb", 1)[1]
    iodide = _amount(halide, "I")
    bromide = _amount(halide, "Br")
    if iodide == 0.0 and bromide == 0.0:
        raise ValueError("formula must contain I and/or Br")
    a_total = cs + fa + ma
    x_total = iodide + bromide
    if abs(a_total - 1.0) > 0.03:
        raise ValueError(f"A-site fractions sum to {a_total:.4f}, expected approximately 1")
    if abs(x_total - 1.0) > 0.03:
        # In PbI3/PbBr3 the trailing 3 is stoichiometry, while the model coordinate is 1.
        if abs(x_total - 3.0) < 0.03 and (iodide == 0.0 or bromide == 0.0):
            iodide /= 3.0
            bromide /= 3.0
            x_total = 1.0
        else:
            raise ValueError(f"halide fractions sum to {x_total:.4f}, expected approximately 1")
    return {
        "cs_fraction": cs / a_total,
        "fa_fraction": fa / a_total,
        "ma_fraction": ma / a_total,
        "iodide_fraction": iodide / x_total,
        "bromide_fraction": bromide / x_total,
    }


def normalize_composition(request: dict) -> dict:
    if request.get("composition"):
        return parse_formula(str(request["composition"]))
    source = request.get("fractions", request)
    needed = ["cs_fraction", "fa_fraction", "ma_fraction", "iodide_fraction", "bromide_fraction"]
    missing = [key for key in needed if key not in source]
    if missing:
        raise ValueError(f"missing composition fields: {', '.join(missing)}")
    values = {key: float(source[key]) for key in needed}
    if any(value < 0 for value in values.values()):
        raise ValueError("composition fractions must be non-negative")
    a_total = sum(values[key] for key in needed[:3])
    x_total = values["iodide_fraction"] + values["bromide_fraction"]
    if abs(a_total - 1.0) > 0.03 or abs(x_total - 1.0) > 0.03:
        raise ValueError("A-site and halide fractions must each sum to approximately 1")
    for key in needed[:3]:
        values[key] /= a_total
    values["iodide_fraction"] /= x_total
    values["bromide_fraction"] /= x_total
    return values


def exact_rbf(a: np.ndarray, b: np.ndarray, ell: float) -> np.ndarray:
    d2 = np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=2)
    return np.exp(-0.5 * d2 / (ell**2))


def apply_optical_proxy(base: np.ndarray, state) -> np.ndarray:
    if state is None:
        return np.zeros((len(base), 2))
    coords = (base[:, [1, 2]] - state["composition_mean"]) / state["composition_scale"]
    return exact_rbf(coords, state["centers"], float(state["ell"])) @ state["coef"]


def predict_regressor(inputs: np.ndarray, state: dict) -> np.ndarray:
    z = (inputs - state["input_mean"]) / state["input_scale"]
    if state["kind"] == "poly":
        design = np.column_stack(
            [np.ones(len(z))]
            + [np.prod(z**exponent, axis=1) for exponent in state["exponents"]]
        )
        value = design @ state["coef"]
    else:
        value = exact_rbf(z, state["centers"], float(state["ell"])) @ state["coef"]
    return value * float(state["target_scale"]) + float(state["target_mean"])


def evaluate_head(base: np.ndarray, head: dict) -> float:
    if head["spec"]["optical"]:
        inputs = np.column_stack([base, apply_optical_proxy(base, head["optical_proxy"])])
    else:
        inputs = base
    return float(predict_regressor(inputs, head["regressor"])[0])


def smooth_core(temperature: np.ndarray, anchor: float, ea_high: float, ea_low: float, artifact: dict):
    kb = float(artifact["k_B_eV_per_K"])
    boundary = float(artifact["boundary_temperature_K"])
    width = float(artifact["transition_width_x"])
    x = 1.0 / (kb * temperature)
    xb = 1.0 / (kb * boundary)
    d = x - xb
    high_integral = -width * (np.logaddexp(0.0, -d / width) - math.log(2.0))
    basis = np.column_stack([np.ones(len(x)), -high_integral, -(d - high_integral)])
    return x, basis @ np.asarray([anchor, ea_high, ea_low])


def predict(request: dict, artifact: dict) -> dict:
    composition = normalize_composition(request)
    base = np.asarray(
        [[composition["cs_fraction"], composition["fa_fraction"], composition["iodide_fraction"]]],
        float,
    )
    requested_temperatures = request.get("temperatures_K")
    if requested_temperatures is None:
        temperatures = artifact["curve_basis_training_grid"]["temperature_K"].copy()
    else:
        temperatures = np.asarray(requested_temperatures, float)
        if temperatures.ndim != 1 or not len(temperatures):
            raise ValueError("temperatures_K must be a non-empty one-dimensional list")
        if np.any((temperatures < 150.0) | (temperatures > 360.0)):
            raise ValueError("this model only supports 150-360 K; its measured training grid is 170-330 K")
        temperatures = np.sort(temperatures)

    cs = composition["cs_fraction"]
    family_name = "Cs=0" if abs(cs - 0.0) <= abs(cs - 0.2) else "Cs=0.2"
    heads = artifact["heads"][family_name]
    raw = {name: evaluate_head(base, head) for name, head in heads.items()}
    ea_high = max(raw["Ea_high"], 1e-4)
    ea_low = max(raw["Ea_low"], 0.0)
    anchor = raw["anchor_220K"]
    x, core = smooth_core(temperatures, anchor, ea_high, ea_low, artifact)

    training_grid = artifact["curve_basis_training_grid"]["temperature_K"]
    residual_basis = artifact["curve_basis_training_grid"]["basis"][:, 3:]
    residual_on_grid = residual_basis @ np.asarray([raw["residual_1"], raw["residual_2"]])
    residual = np.interp(temperatures, training_grid, residual_on_grid)
    ln_sigma_t = core + residual
    sigma = np.exp(ln_sigma_t) / temperatures

    xb = 1.0 / (float(artifact["k_B_eV_per_K"]) * float(artifact["boundary_temperature_K"]))
    ln_a_high = anchor + ea_high * xb
    ln_a_low = anchor + ea_low * xb
    calibration = artifact["uncertainty_calibration"]
    curve_q90 = np.interp(
        temperatures,
        training_grid,
        calibration["curve_abs_error_q90_by_temperature"],
    )
    domain = artifact["domain_distance"]
    z = (base[0] - domain["mean"]) / domain["scale"]
    nearest = float(np.min(np.sqrt(np.sum((domain["training_points"] - z) ** 2, axis=1))))
    ranges = artifact["training_ranges"]
    inside_box = (
        ranges["cs_fraction"][0] <= base[0, 0] <= ranges["cs_fraction"][1]
        and ranges["fa_fraction"][0] <= base[0, 1] <= ranges["fa_fraction"][1]
        and ranges["iodide_fraction"][0] <= base[0, 2] <= ranges["iodide_fraction"][1]
    )
    warnings = []
    if abs(cs - (0.0 if family_name == "Cs=0" else 0.2)) > 0.03:
        warnings.append("Cs fraction lies between/outside the two measured families (0 and 0.2); this is extrapolation")
    if not inside_box or nearest > float(domain["nearest_neighbor_95pct"]):
        warnings.append("composition is outside or sparse relative to the measured composition grid")
    if raw["Ea_low"] < 0:
        warnings.append("raw low-temperature effective Ea was negative and was clipped to zero for curve generation")
    warnings.append("sigma values assume the supplied response really is ln[sigma(T)*T]; sigma units are not yet documented")

    return {
        "model_version": artifact["model_version"],
        "mode": "pure_zero_shot",
        "composition": composition,
        "family_model": family_name,
        "parameters": {
            "Ea_high_eV": ea_high,
            "Ea_low_eV": ea_low,
            "Ea_high_raw_eV": raw["Ea_high"],
            "Ea_low_raw_eV": raw["Ea_low"],
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
    parser = ArgumentParser(description="Pure zero-shot perovskite transport prediction")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--request", type=Path, help="JSON request file")
    parser.add_argument("--composition", help="Perovskite formula")
    parser.add_argument("--temperatures", help="Comma-separated temperatures in K")
    parser.add_argument("--output", type=Path, help="Write JSON result to this file")
    args = parser.parse_args()
    if args.request:
        request = json.loads(args.request.read_text(encoding="utf-8"))
    else:
        if not args.composition:
            parser.error("provide --request or --composition")
        request = {"composition": args.composition}
        if args.temperatures:
            request["temperatures_K"] = [float(value) for value in args.temperatures.split(",")]
    artifact = load_artifact(args.model)
    result = predict(request, artifact)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
