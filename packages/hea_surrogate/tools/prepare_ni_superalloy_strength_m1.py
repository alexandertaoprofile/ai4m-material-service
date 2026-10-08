#!/usr/bin/env python3
"""Train and verify an M1 candidate with the exact serving M0 feature schema.

M0 is the deployed compact 31-feature tensile schema. M1 appends only
deterministic composition descriptors. Both models use the same Tag-disjoint
data and a compact HGBR candidate configuration. The output is a candidate
artifact; callers must explicitly opt in before it can affect service results.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline

from src.api.ni_superalloy_strength_features import ELEMENTS, feature_vector


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data/processed/ni_superalloy_training/20260831_cambridge_cast_ds_sc_v2"
TARGETS = ("ultimate_tensile_strength", "proof_strength_0p2")
SEED = 20260831


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def composition(row: dict[str, str]) -> dict[str, float]:
    return {element: number(row.get(f"{element}_wt_percent")) if math.isfinite(number(row.get(f"{element}_wt_percent"))) else 0.0 for element in ELEMENTS}


def vector(row: dict[str, str], *, include_m1: bool) -> tuple[list[str], np.ndarray]:
    return feature_vector(composition(row), row["casting_code"], number(row["temperature_K"]), row.get("heat_treatment") or "", include_m1=include_m1)[0], np.asarray(feature_vector(composition(row), row["casting_code"], number(row["temperature_K"]), row.get("heat_treatment") or "", include_m1=include_m1)[1], dtype=float)


def make_model() -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        # The serving M0 has a longer historical fit. This fixed 50-iteration
        # candidate setting keeps the M0/M1 comparison internally identical
        # and makes the release-candidate build reproducible on the service host.
        ("regressor", HistGradientBoostingRegressor(learning_rate=.05, max_leaf_nodes=10, min_samples_leaf=8, l2_regularization=3, max_iter=50, random_state=SEED)),
    ])


def score(y: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {"MAE": float(mean_absolute_error(y, prediction)), "RMSE": float(mean_squared_error(y, prediction) ** .5), "R2": float(r2_score(y, prediction))}


def fit_predict(train: list[dict[str, str]], test: list[dict[str, str]], *, include_m1: bool) -> tuple[np.ndarray, np.ndarray, list[str], list[bool], Pipeline]:
    names, _ = vector(train[0], include_m1=include_m1)
    x_train = np.asarray([vector(row, include_m1=include_m1)[1] for row in train])
    x_test = np.asarray([vector(row, include_m1=include_m1)[1] for row in test])
    y_train = np.asarray([number(row["value_MPa"]) for row in train])
    y_test = np.asarray([number(row["value_MPa"]) for row in test])
    mask = np.any(~np.isnan(x_train), axis=0)
    model = make_model(); model.fit(x_train[:, mask], y_train)
    return y_test, model.predict(x_test[:, mask]), names, mask.tolist(), model


def fixed_split(records: list[dict[str, str]], *, include_m1: bool) -> tuple[dict[str, object], dict[str, object]]:
    train = [row for row in records if row["split"] == "train"]
    report: dict[str, object] = {"records": {name: sum(row["split"] == name for row in records) for name in ("train", "validation", "test")}, "tags": {name: len({row["tag"] for row in records if row["split"] == name}) for name in ("train", "validation", "test")}}
    predictions: list[dict[str, object]] = []
    artifact: dict[str, object] = {}
    for name in ("test",):
        test = [row for row in records if row["split"] == name]
        if not test:
            continue
        y, prediction, feature_names, mask, model = fit_predict(train, test, include_m1=include_m1)
        report[name] = score(y, prediction)
        predictions.extend({"tag": row["tag"], "split": name, "observed_MPa": float(actual), "predicted_MPa": float(pred), "residual_MPa": float(pred - actual)} for row, actual, pred in zip(test, y, prediction))
        artifact = {"feature_names": feature_names, "feature_mask": mask, "model": model}
    # `model` above is already fitted only on the frozen serving train split.
    return report | {"predictions": predictions}, artifact


def leave_one_tag_out(records: list[dict[str, str]], *, include_m1: bool) -> dict[str, object]:
    tags = sorted({row["tag"] for row in records})
    observed: list[float] = []; predicted: list[float] = []; per_tag: list[dict[str, object]] = []
    for held_out in tags:
        train = [row for row in records if row["tag"] != held_out]
        test = [row for row in records if row["tag"] == held_out]
        y, output, _, _, _ = fit_predict(train, test, include_m1=include_m1)
        observed.extend(y.tolist()); predicted.extend(output.tolist())
        per_tag.append({"tag": held_out, "records": len(test), "MAE": float(mean_absolute_error(y, output))})
    return {"tags": len(tags), "records": len(records), "pooled": score(np.asarray(observed), np.asarray(predicted)), "tag_MAE_mean": float(np.mean([item["MAE"] for item in per_tag])), "tag_MAE_median": float(np.median([item["MAE"] for item in per_tag])), "per_tag": per_tag}


def repeated_tag_splits(records: list[dict[str, str]], *, include_m1: bool, count: int) -> dict[str, object]:
    """Additional held-out-tag checks without changing the frozen serving split."""
    groups = np.asarray([row["tag"] for row in records])
    splitter = GroupShuffleSplit(n_splits=count, test_size=.20, random_state=20260902)
    folds: list[dict[str, object]] = []
    for index, (train_idx, test_idx) in enumerate(splitter.split(np.zeros(len(records)), groups=groups), 1):
        train = [records[item] for item in train_idx]; test = [records[item] for item in test_idx]
        y, prediction, _, _, _ = fit_predict(train, test, include_m1=include_m1)
        folds.append({"fold": index, "test_records": len(test), "test_tags": len({row["tag"] for row in test}), **score(y, prediction)})
    return {"folds": folds, "mean_MAE": float(np.mean([item["MAE"] for item in folds])), "mean_R2": float(np.mean([item["R2"] for item in folds]))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models/ni_superalloy_cast_ds_sc_strength_v0/m1_20260902_candidate")
    parser.add_argument("--leave-one-tag-out", action="store_true", help="run the slower full Tag-wise robustness benchmark before writing the artifact")
    parser.add_argument("--repeated-tag-splits", type=int, default=0, help="run N additional deterministic Tag-disjoint 80/20 checks")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    split = {row["tag"]: row["split"] for row in rows(PACKAGE / "splits/tag_disjoint_split_v1.csv")}
    source = [{**row, "split": split[row["tag"]]} for row in rows(PACKAGE / "cast_ds_sc_short_time_strength_admitted.csv") if row["property"] in TARGETS]
    report: dict[str, object] = {
        "model_status": "M1 candidate only; not selected by the service runtime",
        "data": {"package": str(PACKAGE), "split": "frozen Cambridge Tag-disjoint v1"},
        "features": {"M0": "current serving compact 31-feature schema", "M1": "M0 plus 14 deterministic composition descriptors; no CALPHAD or fitted external feature"},
        "targets": {},
    }
    artifacts: dict[str, object] = {}
    for target in TARGETS:
        records = [row for row in source if row["property"] == target]
        m0_fixed, _ = fixed_split(records, include_m1=False)
        m1_fixed, artifact = fixed_split(records, include_m1=True)
        target_report: dict[str, object] = {"fixed_split": {"M0": {key: value for key, value in m0_fixed.items() if key != "predictions"}, "M1": {key: value for key, value in m1_fixed.items() if key != "predictions"}}}
        if args.leave_one_tag_out:
            m0_loto = leave_one_tag_out(records, include_m1=False)
            m1_loto = leave_one_tag_out(records, include_m1=True)
            m0_tags = {item["tag"]: item["MAE"] for item in m0_loto["per_tag"]}  # type: ignore[index]
            m1_tags = {item["tag"]: item["MAE"] for item in m1_loto["per_tag"]}  # type: ignore[index]
            deltas = {tag: float(m1_tags[tag] - m0_tags[tag]) for tag in m0_tags}
            target_report["leave_one_tag_out"] = {"M0": m0_loto, "M1": m1_loto, "M1_minus_M0": {"pooled": {key: float(m1_loto["pooled"][key] - m0_loto["pooled"][key]) for key in ("MAE", "RMSE", "R2")}, "better_tags": sum(value < 0 for value in deltas.values()), "worse_tags": sum(value > 0 for value in deltas.values()), "largest_improvement_MAE": min(deltas.values()), "largest_regression_MAE": max(deltas.values())}}
        if args.repeated_tag_splits:
            m0_repeats = repeated_tag_splits(records, include_m1=False, count=args.repeated_tag_splits)
            m1_repeats = repeated_tag_splits(records, include_m1=True, count=args.repeated_tag_splits)
            target_report["repeated_tag_splits"] = {"M0": m0_repeats, "M1": m1_repeats, "M1_minus_M0": {"mean_MAE": float(m1_repeats["mean_MAE"] - m0_repeats["mean_MAE"]), "mean_R2": float(m1_repeats["mean_R2"] - m0_repeats["mean_R2"])}}
        report["targets"][target] = target_report
        artifacts[target] = artifact
    args.output_dir.mkdir(parents=True)
    joblib.dump({"model_version": "hot_end_ni_superalloy_strength_m1_candidate_20260902", "models": artifacts, "feature_names": artifacts[TARGETS[0]]["feature_names"], "screening_MAE_MPa": {target: report["targets"][target]["fixed_split"]["M1"]["test"]["MAE"] for target in TARGETS}}, args.output_dir / "m1_models.joblib")
    (args.output_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"M1 candidate written to {args.output_dir}")
    for target in TARGETS:
        value = report["targets"][target]
        print(target, "fixed_test_MAE=", value["fixed_split"]["M1"]["test"]["MAE"])


if __name__ == "__main__":
    main()
