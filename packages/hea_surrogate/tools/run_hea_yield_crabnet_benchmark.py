"""Minimal, leakage-aware HEA yield-strength benchmark for raw CrabNet.

This script intentionally evaluates only the experimental HEA/MPEA yield-strength
task.  It keeps the deployed composition-group split, compares raw composition
CrabNet with both the current ExtraTrees feature set and a composition-only
ExtraTrees control, and writes a compact reproducible report.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from crabnet.crabnet_ import CrabNet
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.models.train_baselines import _features, _pipeline, _split

DATA_PATH = ROOT / "data" / "processed" / "experimental_yield_strength.csv"
REPORT_DIR = ROOT / "reports" / "crabnet"


def metrics(y_true: pd.Series, prediction: np.ndarray) -> dict[str, float]:
    return {
        "MAE": float(mean_absolute_error(y_true, prediction)),
        "RMSE": float(mean_squared_error(y_true, prediction) ** 0.5),
        "R2": float(r2_score(y_true, prediction)),
    }


def crabnet_frame(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"formula": frame["canonical_formula"].astype(str), "target": frame[target].astype(float)},
        index=frame.index,
    )


def run_crabnet(train: pd.DataFrame, validation: pd.DataFrame | None) -> CrabNet:
    return_model = CrabNet(
        model_name="hea_yield_raw_composition_benchmark",
        compute_device="cuda",
        batch_size=128,
        epochs=200,
        checkin=20,
        random_state=2026,
        verbose=True,
        losscurve=False,
        learningcurve=False,
        save=False,
    )
    return_model.fit(train, val_df=validation)
    return return_model


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("This benchmark requires CUDA; no GPU was detected by PyTorch.")

    target = "yield_strength_MPa"
    frame = pd.read_csv(DATA_PATH)
    frame = frame[frame[target].notna()].reset_index(drop=True)
    train_idx, validation_idx, test_idx = _split(frame["canonical_formula"])
    train = frame.iloc[train_idx].copy()
    validation = frame.iloc[validation_idx].copy()
    test = frame.iloc[test_idx].copy()

    # ExtraTrees controls: the current deployed feature set and raw fractions only.
    feature_frame, numeric, categorical = _features(frame)
    y = frame[target]
    et_context = _pipeline("extra_trees", "regression", numeric, categorical, 2026)
    et_context.fit(feature_frame.iloc[train_idx], y.iloc[train_idx])
    et_context_validation = et_context.predict(feature_frame.iloc[validation_idx])
    et_context_test = et_context.predict(feature_frame.iloc[test_idx])

    composition_columns = [column for column in feature_frame.columns if column.startswith("x_")]
    et_composition = ExtraTreesRegressor(
        n_estimators=160, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=2026
    )
    et_composition.fit(feature_frame.iloc[train_idx][composition_columns], y.iloc[train_idx])
    et_composition_validation = et_composition.predict(feature_frame.iloc[validation_idx][composition_columns])
    et_composition_test = et_composition.predict(feature_frame.iloc[test_idx][composition_columns])

    start = time.monotonic()
    crabnet_validation_model = run_crabnet(crabnet_frame(train, target), crabnet_frame(validation, target))
    crabnet_validation = crabnet_validation_model.predict(crabnet_frame(validation, target))
    validation_seconds = time.monotonic() - start

    # Keep this first gate deliberately single-fit: every model sees only the
    # train split, while validation and test remain unseen readouts.  This also
    # avoids CrabNet's incompatible implicit-validation path under torch 2.5.
    crabnet_test = crabnet_validation_model.predict(crabnet_frame(test, target))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "hea_yield_raw_composition_crabnet_vs_extratrees_v1",
        "scope": "Experimental HEA/MPEA yield strength only; no calculated NbCrVWZr records.",
        "split": {
            "method": "GroupShuffleSplit by canonical_formula",
            "train_random_state": 2026,
            "validation_test_random_state": 2027,
            "records": {"train": len(train), "validation": len(validation), "test": len(test)},
            "unique_compositions": {
                "train": int(train.canonical_formula.nunique()),
                "validation": int(validation.canonical_formula.nunique()),
                "test": int(test.canonical_formula.nunique()),
            },
        },
        "models": {
            "extra_trees_current_features": "atom fractions + simple descriptors + processing method + test temperature",
            "extra_trees_composition_only": "atom fractions only",
            "crabnet_raw_composition": "canonical formula / atomic ratios only",
        },
        "crabnet_config": {
            "epochs": 200,
            "batch_size": 128,
            "random_state": 2026,
            "device": str(crabnet_validation_model.compute_device),
            "gpu": torch.cuda.get_device_name(0),
        },
        "validation_metrics": {
            "extra_trees_current_features": metrics(y.iloc[validation_idx], et_context_validation),
            "extra_trees_composition_only": metrics(y.iloc[validation_idx], et_composition_validation),
            "crabnet_raw_composition": metrics(y.iloc[validation_idx], crabnet_validation),
        },
        "test_metrics": {
            "extra_trees_current_features": metrics(y.iloc[test_idx], et_context_test),
            "extra_trees_composition_only": metrics(y.iloc[test_idx], et_composition_test),
            "crabnet_raw_composition": metrics(y.iloc[test_idx], crabnet_test),
        },
        "elapsed_seconds": {"crabnet_single_fit": validation_seconds},
        "interpretation_rule": "All three models fit the train split only. Use validation MAE as the go/no-go gate; test metrics are a single frozen-configuration readout, not a hyperparameter-search target.",
    }
    (REPORT_DIR / "hea_yield_raw_composition_benchmark.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    predictions = pd.DataFrame(
        {
            "record_id": test["record_id"].to_numpy(),
            "canonical_formula": test["canonical_formula"].to_numpy(),
            "yield_strength_MPa": y.iloc[test_idx].to_numpy(),
            "extra_trees_current_features_prediction": et_context_test,
            "extra_trees_composition_only_prediction": et_composition_test,
            "crabnet_raw_composition_prediction": crabnet_test,
        }
    )
    predictions.to_csv(REPORT_DIR / "hea_yield_raw_composition_test_predictions.csv", index=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
