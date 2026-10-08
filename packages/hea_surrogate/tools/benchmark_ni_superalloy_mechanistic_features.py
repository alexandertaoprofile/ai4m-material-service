#!/usr/bin/env python3
"""Tag-disjoint offline ablation for Ni-superalloy mechanism-inspired features.

M0 exactly retains the current serving feature families: composition, route,
casting gradient, recorded heat-treatment stages and service condition.  M1
adds deterministic composition and process descriptors.  It intentionally does
not claim CALPHAD features: no validated Ni thermodynamic database is supplied
to this program.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from ase.data import atomic_masses, atomic_numbers, covalent_radii
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[1]
ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = ["cc", "ds", "sc", "am/sebm", "am/lc"]
VEC = {"Al": 3, "B": 3, "C": 4, "Co": 9, "Cr": 6, "Fe": 8, "Hf": 4, "Ir": 9, "Mn": 7, "Mo": 6, "Nb": 5, "Ni": 10, "Re": 7, "Ru": 8, "Si": 4, "Ta": 5, "Ti": 4, "V": 5, "W": 6, "Y": 3, "Zr": 4}
R = 8.314462618


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def heat_stages(value: str) -> list[float]:
    result: list[float] = []
    for family, count in (("solution", 3), ("precipitation", 4)):
        for stage in range(1, count + 1):
            for suffix in ("temp_C", "time_h", "heating_C_per_h"):
                match = re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)", value or "")
                result.append(number(match.group(1)) if match else float("nan"))
    return result


def composition(row: dict[str, str]) -> np.ndarray:
    values = np.asarray([number(row.get(f"{element}_wt_percent")) for element in ELEMENTS], dtype=float)
    values = np.nan_to_num(values, nan=0.0)
    total = values.sum()
    return values / total if total > 0 else values


def base_features(row: dict[str, str], *, creep: bool) -> list[float]:
    route = str(row.get("casting_code") or "")
    condition = [number(row.get("temperature_K"))]
    if creep:
        stress = number(row.get("stress_MPa"))
        condition.append(math.log10(stress) if stress > 0 else float("nan"))
    return [*condition, *[number(row.get(f"{element}_wt_percent")) for element in ELEMENTS], *[float(route == item) for item in ROUTES], number(row.get("casting_gradient_K_per_mm")), *heat_stages(str(row.get("heat_treatment") or ""))]


def mechanism_features(row: dict[str, str], *, creep: bool) -> list[float]:
    x = composition(row)
    z = np.asarray([atomic_numbers[item] for item in ELEMENTS], dtype=float)
    mass = np.asarray([atomic_masses[atomic_numbers[item]] for item in ELEMENTS], dtype=float)
    radius = np.asarray([covalent_radii[atomic_numbers[item]] for item in ELEMENTS], dtype=float)
    vec = np.asarray([VEC[item] for item in ELEMENTS], dtype=float)
    mean_radius = float(x @ radius) if x.sum() else float("nan")
    def moments(values: np.ndarray) -> tuple[float, float]:
        mean = float(x @ values) if x.sum() else float("nan")
        return mean, float(np.sqrt(x @ ((values - mean) ** 2))) if math.isfinite(mean) else float("nan")
    z_mean, z_std = moments(z); mass_mean, mass_std = moments(mass); vec_mean, vec_std = moments(vec)
    entropy = float(-R * np.sum(x[x > 0] * np.log(x[x > 0]))) if x.sum() else float("nan")
    size_delta = float(np.sqrt(x @ ((1 - radius / mean_radius) ** 2))) if mean_radius > 0 else float("nan")
    amount = {element: float(x[index]) for index, element in enumerate(ELEMENTS)}
    stage = heat_stages(str(row.get("heat_treatment") or ""))
    solution = [(stage[index], stage[index + 1]) for index in range(0, 9, 3)]
    precipitation = [(stage[index], stage[index + 1]) for index in range(9, 21, 3)]
    finite = lambda values: [value for value in values if math.isfinite(value)]
    process = [
        max(finite([temperature for temperature, _ in solution]), default=float("nan")),
        sum(finite([duration for _, duration in solution])),
        max(finite([temperature for temperature, _ in precipitation]), default=float("nan")),
        sum(finite([duration for _, duration in precipitation])),
    ]
    descriptors = [
        z_mean, z_std, mass_mean, mass_std, vec_mean, vec_std, mean_radius, size_delta, entropy,
        amount["Al"] + amount["Ti"] + amount["Ta"] + amount["Nb"],  # gamma-prime-former fraction
        amount["Re"] + amount["W"] + amount["Mo"],                    # refractory solid-solution fraction
        amount["Cr"], amount["Co"], amount["C"] + amount["B"] + amount["Hf"],
        *process,
    ]
    if creep:
        temperature = number(row.get("temperature_K")); stress = number(row.get("stress_MPa"))
        log_stress = math.log10(stress) if stress > 0 else float("nan")
        descriptors.extend([1.0 / temperature if temperature > 0 else float("nan"), temperature * log_stress if math.isfinite(log_stress) else float("nan")])
    return descriptors


def metric(y: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {"MAE": float(mean_absolute_error(y, prediction)), "RMSE": float(mean_squared_error(y, prediction) ** .5), "R2": float(r2_score(y, prediction))}


def predict(train: list[dict[str, str]], test: list[dict[str, str]], target: str, feature_fn: Any, *, creep: bool, seed: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Fit one deterministic HGBR and return held-out target/prediction pairs."""
    y_train = np.asarray([number(row[target]) for row in train])
    y_test = np.asarray([number(row[target]) for row in test])
    x_train = np.asarray([feature_fn(row) for row in train])
    x_test = np.asarray([feature_fn(row) for row in test])
    keep = np.any(~np.isnan(x_train), axis=0)
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("regressor", HistGradientBoostingRegressor(
            learning_rate=.05,
            max_leaf_nodes=12 if creep else 10,
            min_samples_leaf=10 if creep else 8,
            l2_regularization=4 if creep else 3,
            max_iter=400 if creep else 350,
            random_state=seed,
        )),
    ])
    model.fit(x_train[:, keep], y_train)
    return y_test, model.predict(x_test[:, keep]), int(keep.sum())


def ablate(rows: list[dict[str, str]], target: str, *, creep: bool, seeds: tuple[int, ...]) -> dict[str, Any]:
    train = [row for row in rows if row["split"] == "train"]
    test = [row for row in rows if row["split"] == "test"]
    y_train = np.asarray([number(row[target]) for row in train]); y_test = np.asarray([number(row[target]) for row in test])
    variants = {"M0_serving_features": lambda row: base_features(row, creep=creep), "M1_mechanism_inspired": lambda row: [*base_features(row, creep=creep), *mechanism_features(row, creep=creep)]}
    report: dict[str, Any] = {"train_records": len(train), "test_records": len(test), "test_tags": len({row["tag"] for row in test}), "variants": {}}
    for name, feature_fn in variants.items():
        predictions = []
        feature_count = 0
        for seed in seeds:
            _, prediction, feature_count = predict(train, test, target, feature_fn, creep=creep, seed=seed)
            predictions.append(prediction)
        stacked = np.asarray(predictions)
        report["variants"][name] = {"feature_count": feature_count, "test": metric(y_test, stacked.mean(axis=0)), "seed_MAE": [float(mean_absolute_error(y_test, item)) for item in stacked], "ensemble_prediction_std_mean": float(stacked.std(axis=0).mean())}
    baseline = report["variants"]["M0_serving_features"]["test"]
    improved = report["variants"]["M1_mechanism_inspired"]["test"]
    report["M1_minus_M0"] = {key: float(improved[key] - baseline[key]) for key in ("MAE", "RMSE", "R2")}
    return report


def leave_one_tag_out(rows: list[dict[str, str]], target: str, *, creep: bool, seed: int) -> dict[str, Any]:
    """Evaluate generalisation to each entirely unseen alloy/data tag.

    The serving test has only four or five tags.  This evaluates every tag in
    turn, without using any row from the held-out tag for fitting or imputation.
    It is deliberately restricted to the two short-strength targets: the
    creep table has hundreds of tags and should be evaluated separately.
    """
    variants = {
        "M0_serving_features": lambda row: base_features(row, creep=creep),
        "M1_mechanism_inspired": lambda row: [*base_features(row, creep=creep), *mechanism_features(row, creep=creep)],
    }
    tags = sorted({row["tag"] for row in rows})
    by_variant: dict[str, dict[str, Any]] = {name: {"y": [], "prediction": [], "per_tag": []} for name in variants}
    for held_out in tags:
        train = [row for row in rows if row["tag"] != held_out]
        test = [row for row in rows if row["tag"] == held_out]
        for name, feature_fn in variants.items():
            y, prediction, _ = predict(train, test, target, feature_fn, creep=creep, seed=seed)
            entry = by_variant[name]
            entry["y"].extend(y.tolist())
            entry["prediction"].extend(prediction.tolist())
            entry["per_tag"].append({"tag": held_out, "records": len(test), "MAE": float(mean_absolute_error(y, prediction))})
    report: dict[str, Any] = {"protocol": "leave-one-tag-out; all rows with the held-out tag are excluded from fitting and imputation", "tags": len(tags), "records": len(rows), "variants": {}}
    for name, entry in by_variant.items():
        y = np.asarray(entry["y"]); prediction = np.asarray(entry["prediction"])
        per_tag = entry["per_tag"]
        report["variants"][name] = {
            "pooled": metric(y, prediction),
            "tag_MAE_mean": float(np.mean([item["MAE"] for item in per_tag])),
            "tag_MAE_median": float(np.median([item["MAE"] for item in per_tag])),
            "per_tag": per_tag,
        }
    m0 = {item["tag"]: item["MAE"] for item in report["variants"]["M0_serving_features"]["per_tag"]}
    m1 = {item["tag"]: item["MAE"] for item in report["variants"]["M1_mechanism_inspired"]["per_tag"]}
    deltas = {tag: float(m1[tag] - m0[tag]) for tag in tags}
    report["M1_minus_M0"] = {
        "pooled": {key: float(report["variants"]["M1_mechanism_inspired"]["pooled"][key] - report["variants"]["M0_serving_features"]["pooled"][key]) for key in ("MAE", "RMSE", "R2")},
        "tag_MAE_mean": float(report["variants"]["M1_mechanism_inspired"]["tag_MAE_mean"] - report["variants"]["M0_serving_features"]["tag_MAE_mean"]),
        "better_tags": int(sum(value < 0 for value in deltas.values())),
        "worse_tags": int(sum(value > 0 for value in deltas.values())),
        "tied_tags": int(sum(value == 0 for value in deltas.values())),
        "largest_improvement_MAE": float(min(deltas.values())),
        "largest_regression_MAE": float(max(deltas.values())),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports/ni_superalloy/20260901_mechanistic_feature_ablation_v1/report.json")
    parser.add_argument("--leave-one-tag-out", action="store_true", help="also evaluate M0/M1 by holding out every short-strength tag in turn")
    args = parser.parse_args()
    # The current serving short-strength artifact was fitted from v2.  Reuse
    # that frozen package for a like-for-like retraining comparison.
    strength_dir = ROOT / "data/processed/ni_superalloy_training/20260831_cambridge_cast_ds_sc_v2"
    split = {row["tag"]: row["split"] for row in read_csv(strength_dir / "splits/tag_disjoint_split_v1.csv")}
    strength_rows = [{**row, "split": split[row["tag"]]} for row in read_csv(strength_dir / "cast_ds_sc_short_time_strength_admitted.csv")]
    creep_dir = ROOT / "data/processed/ni_superalloy_training/20260901_cambridge_creep_rupture_v1"
    creep_rows = read_csv(creep_dir / "rupture_life_admitted.csv")
    report = {
        "protocol": {"split": "existing tag-disjoint split; no rows or tags are moved", "estimator": "same HistGradientBoostingRegressor family and serving random seed for each target; M0 is retrained for the ablation", "M0": "current serving feature families", "M1": "M0 plus deterministic mechanism-inspired composition/process descriptors", "CALPHAD": "not included: no validated Ni thermodynamic database is configured"},
        "targets": {
            "ultimate_tensile_strength_MPa": ablate([row for row in strength_rows if row["property"] == "ultimate_tensile_strength"], "value_MPa", creep=False, seeds=(20260831,)),
            "proof_strength_0p2_MPa": ablate([row for row in strength_rows if row["property"] == "proof_strength_0p2"], "value_MPa", creep=False, seeds=(20260831,)),
            "log10_rupture_life_h": ablate(creep_rows, "log10_rupture_life_h", creep=True, seeds=(20260901,)),
        },
    }
    if args.leave_one_tag_out:
        report["leave_one_tag_out"] = {
            "ultimate_tensile_strength_MPa": leave_one_tag_out([row for row in strength_rows if row["property"] == "ultimate_tensile_strength"], "value_MPa", creep=False, seed=20260831),
            "proof_strength_0p2_MPa": leave_one_tag_out([row for row in strength_rows if row["property"] == "proof_strength_0p2"], "value_MPa", creep=False, seed=20260831),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
