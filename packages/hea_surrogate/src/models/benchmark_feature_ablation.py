"""Benchmark the marginal value of the current composition descriptors.

The comparison deliberately reuses the grouped train/validation/test split used
by :mod:`train_baselines`: a canonical composition can never appear in both the
training set and the held-out set.  It is a feature ablation, not a claim about
thermodynamic or strengthening-mechanism descriptors.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.train_baselines import _features, _metrics, _pipeline, _split

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "models" / "feature_ablation_report.json"


def _variants(features: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, list[str], list[str]]]:
    composition = [column for column in features if column.startswith("x_")]
    context_numeric = ["test_temperature_C", "temperature_missing"]
    context_categorical = ["processing_method"]
    simple_statistics = ["n_elements", "mixing_entropy_J_molK", "average_atomic_number", "average_VEC"]
    return {
        "composition_only": (features[composition], composition, []),
        "composition_plus_context": (
            features[composition + context_numeric + context_categorical],
            composition + context_numeric,
            context_categorical,
        ),
        "composition_context_plus_simple_descriptors": (
            features[composition + simple_statistics + context_numeric + context_categorical],
            composition + simple_statistics + context_numeric,
            context_categorical,
        ),
    }


def _run_task(task: str, filename: str, target: str) -> dict:
    frame = pd.read_csv(DATA / filename)
    if task == "hardness":
        temperature = pd.to_numeric(frame.test_temperature_C, errors="coerce")
        frame = frame[temperature.isna() | ((temperature - 25).abs() <= 75)].copy()
    frame = frame[frame[target].notna()].reset_index(drop=True)
    features, _, _ = _features(frame)
    train, validation, test = _split(frame.canonical_formula)
    results = {}
    for variant, (X, numeric, categorical) in _variants(features).items():
        # ExtraTrees was selected for all three existing task reports.  Fixing
        # the estimator makes this an interpretable feature-only comparison.
        predictions = []
        for seed in (11, 23, 37, 53, 71):
            model = _pipeline("extra_trees", "regression", numeric, categorical, seed)
            model.fit(X.iloc[np.r_[train, validation]], frame[target].iloc[np.r_[train, validation]])
            predictions.append(model.predict(X.iloc[test]))
        predictions = np.asarray(predictions)
        results[variant] = {
            "feature_count_before_one_hot": len(X.columns),
            "test_metrics": _metrics(frame[target].iloc[test], predictions.mean(axis=0), "regression"),
            "ensemble_prediction_std_mean": float(predictions.std(axis=0).mean()),
        }
    simple = results["composition_context_plus_simple_descriptors"]["test_metrics"]
    for variant, result in results.items():
        metric = result["test_metrics"]
        result["delta_vs_composition_context_plus_simple_descriptors"] = {
            "MAE": float(metric["MAE"] - simple["MAE"]),
            "RMSE": float(metric["RMSE"] - simple["RMSE"]),
            "R2": float(metric["R2"] - simple["R2"]),
        }
    return {
        "records": len(frame),
        "unique_compositions": int(frame.canonical_formula.nunique()),
        "split_records": {"train": len(train), "validation": len(validation), "test": len(test)},
        "split_compositions": {
            "train": int(frame.canonical_formula.iloc[train].nunique()),
            "validation": int(frame.canonical_formula.iloc[validation].nunique()),
            "test": int(frame.canonical_formula.iloc[test].nunique()),
        },
        "results": results,
    }


def main() -> None:
    report = {
        "protocol": {
            "split": "70/15/15 GroupShuffleSplit by canonical_formula; fixed random states 2026/2027",
            "model": "ExtraTrees (same hyperparameters and 5-seed ensemble as the existing selected models)",
            "feature_sets": {
                "composition_only": "normalized elemental atomic fractions only",
                "composition_plus_context": "elemental fractions plus processing method, test temperature, and missing-temperature flag",
                "composition_context_plus_simple_descriptors": "previous set plus element count, ideal mixing entropy, average atomic number, and average VEC",
            },
            "scope": "This tests only the four current simple composition statistics. It does not test CALPHAD, mixing enthalpy/Omega, phase fractions, or explicit strengthening-mechanism descriptors.",
        },
        "tasks": {
            "yield_strength": _run_task("yield_strength", "experimental_yield_strength.csv", "yield_strength_MPa"),
            "hardness": _run_task("hardness", "experimental_hardness.csv", "hardness_HV"),
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
