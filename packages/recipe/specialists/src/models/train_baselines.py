"""Train minute-scale HEA baselines without composition leakage.

This is intentionally a baseline feature set.  It uses normalized composition,
simple composition-derived statistics, processing category and temperature. A
later descriptor release will add the fully curated thermodynamic feature set.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, balanced_accuracy_score, classification_report, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.preprocessing import LabelEncoder

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "processed"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports" / "models"
R = 8.314462618
ATOMIC_NUMBER = {"Al":13,"Co":27,"Cr":24,"Fe":26,"Mn":25,"Ni":28,"Cu":29,"Mo":42,"Nb":41,"Ta":73,"Ti":22,"V":23,"W":74,"Zr":40,"Hf":72,"Si":14,"C":6,"B":5}
VEC = {"Al":3,"Co":9,"Cr":6,"Fe":8,"Mn":7,"Ni":10,"Cu":11,"Mo":6,"Nb":5,"Ta":5,"Ti":4,"V":5,"W":6,"Zr":4,"Hf":4,"Si":4,"C":4,"B":3}


def _split(groups: pd.Series):
    indices = np.arange(len(groups))
    train_idx, holdout_idx = next(GroupShuffleSplit(n_splits=1, train_size=.70, random_state=2026).split(indices, groups=groups))
    validation_relative = .5
    validation_inner, test_inner = next(GroupShuffleSplit(n_splits=1, train_size=validation_relative, random_state=2027).split(holdout_idx, groups=groups.iloc[holdout_idx]))
    return train_idx, holdout_idx[validation_inner], holdout_idx[test_inner]


def _features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    compositions = frame.composition_at_pct.map(json.loads)
    elements = sorted({element for item in compositions for element in item})
    result = pd.DataFrame({f"x_{element}": compositions.map(lambda item: item.get(element, 0.0) / 100) for element in elements})
    fractions = result[[f"x_{item}" for item in elements]].to_numpy()
    nonzero = fractions > 0
    result["n_elements"] = nonzero.sum(axis=1)
    result["mixing_entropy_J_molK"] = -R * np.where(nonzero, fractions * np.log(np.clip(fractions, 1e-12, None)), 0).sum(axis=1)
    for name, values in (("average_atomic_number", ATOMIC_NUMBER), ("average_VEC", VEC)):
        known = np.array([values.get(element, np.nan) for element in elements])
        weighted = np.where(np.isnan(known), 0, fractions * np.nan_to_num(known))
        known_fraction = fractions[:, ~np.isnan(known)].sum(axis=1)
        result[name] = np.divide(weighted.sum(axis=1), known_fraction, out=np.full(len(frame), np.nan), where=known_fraction > 0)
    result["test_temperature_C"] = pd.to_numeric(frame.test_temperature_C, errors="coerce")
    result["temperature_missing"] = result.test_temperature_C.isna().astype(int)
    result["processing_method"] = frame.processing_method.fillna("UNKNOWN").astype(str)
    numeric = [column for column in result if column != "processing_method"]
    return result, numeric, ["processing_method"]


def _preprocessor(numeric: list[str], categorical: list[str], scaled: bool) -> ColumnTransformer:
    num_steps = [("impute", SimpleImputer(strategy="median"))]
    if scaled: num_steps.append(("scale", StandardScaler()))
    return ColumnTransformer([("num", Pipeline(num_steps), numeric), ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical)])


def _metrics(y, pred, kind: str) -> dict:
    if kind == "regression":
        return {"MAE": float(mean_absolute_error(y, pred)), "RMSE": float(mean_squared_error(y, pred) ** .5), "R2": float(r2_score(y, pred))}
    return {"accuracy": float(accuracy_score(y, pred)), "balanced_accuracy": float(balanced_accuracy_score(y, pred)), "macro_F1": float(f1_score(y, pred, average="macro", zero_division=0))}


def _pipeline(name: str, kind: str, numeric: list[str], categorical: list[str], seed: int):
    scaled = name in {"ridge", "mlp"}
    if name == "ridge": model = Ridge(alpha=10.0) if kind == "regression" else LogisticRegression(C=.5, max_iter=500, class_weight="balanced", random_state=seed)
    elif name == "extra_trees": model = ExtraTreesRegressor(n_estimators=160, min_samples_leaf=2, max_features=.8, n_jobs=-1, random_state=seed) if kind == "regression" else ExtraTreesClassifier(n_estimators=160, min_samples_leaf=2, max_features=.8, n_jobs=-1, class_weight="balanced", random_state=seed)
    elif name == "mlp": model = MLPRegressor(hidden_layer_sizes=(64,32), activation="relu", alpha=1e-4, early_stopping=True, validation_fraction=.15, n_iter_no_change=12, max_iter=160, random_state=seed) if kind == "regression" else MLPClassifier(hidden_layer_sizes=(64,32), activation="relu", alpha=1e-4, early_stopping=True, validation_fraction=.15, n_iter_no_change=12, max_iter=160, random_state=seed)
    else: raise ValueError(name)
    return Pipeline([("preprocess", _preprocessor(numeric, categorical, scaled)), ("model", model)])


def train_task(name: str, filename: str, target: str, kind: str) -> dict:
    frame = pd.read_csv(DATA / filename)
    if name == "hardness":
        temp = pd.to_numeric(frame.test_temperature_C, errors="coerce")
        # Missing-temperature legacy exports are retained but explicitly tagged
        # as ambient assumed; known elevated-temperature records are excluded.
        frame = frame[temp.isna() | ((temp - 25).abs() <= 75)].copy()
    frame = frame[frame[target].notna()].reset_index(drop=True)
    X, numeric, categorical = _features(frame); y = frame[target]
    label_encoder = None
    if kind == "classification":
        label_encoder = LabelEncoder(); y = pd.Series(label_encoder.fit_transform(y), index=y.index)
    train, validation, test = _split(frame.canonical_formula)
    candidate_metrics, candidates = {}, ["ridge", "extra_trees", "mlp"]
    for candidate in candidates:
        try:
            model = _pipeline(candidate, kind, numeric, categorical, 2026); model.fit(X.iloc[train], y.iloc[train])
            candidate_metrics[candidate] = _metrics(y.iloc[validation], model.predict(X.iloc[validation]), kind)
        except Exception as exc: candidate_metrics[candidate] = {"error": str(exc)}
    score = "MAE" if kind == "regression" else "macro_F1"
    viable = {key:value for key,value in candidate_metrics.items() if score in value}
    selected = min(viable, key=lambda key: viable[key][score]) if kind == "regression" else max(viable, key=lambda key: viable[key][score])
    ensemble, predictions = [], []
    for seed in (11, 23, 37, 53, 71):
        model = _pipeline(selected, kind, numeric, categorical, seed); model.fit(X.iloc[np.r_[train, validation]], y.iloc[np.r_[train, validation]])
        ensemble.append(model); predictions.append(model.predict(X.iloc[test]))
    stacked = np.asarray(predictions)
    if kind == "regression": test_metrics = _metrics(y.iloc[test], stacked.mean(axis=0), kind); uncertainty = {"ensemble_prediction_std_mean": float(stacked.std(axis=0).mean())}
    else:
        voted = pd.DataFrame(stacked).mode(axis=0).iloc[0]
        test_metrics = _metrics(y.iloc[test], voted, kind)
        test_metrics["confusion_matrix"] = confusion_matrix(y.iloc[test], voted).tolist()
        test_metrics["classes"] = label_encoder.classes_.tolist()
        test_metrics["per_class"] = classification_report(y.iloc[test], voted, target_names=label_encoder.classes_.tolist(), output_dict=True, zero_division=0)
        uncertainty = {"ensemble_disagreement_rate": float(np.mean(np.any(stacked != stacked[0], axis=0)))}
    MODELS.mkdir(parents=True, exist_ok=True); REPORTS.mkdir(parents=True, exist_ok=True)
    composition_columns = [c for c in X.columns if c.startswith("x_")]
    reference = X.iloc[np.r_[train, validation]][composition_columns].to_numpy(dtype=float)
    # Distance limits are learned from the training/validation cloud only.
    distances = np.sqrt(((reference[:, None, :] - reference[None, :, :]) ** 2).sum(axis=2))
    np.fill_diagonal(distances, np.inf)
    nearest = distances.min(axis=1)
    ranges = {}
    for column in composition_columns:
        observed = X.iloc[np.r_[train, validation]][column]
        positive = observed[observed > 0]
        ranges[column[2:]] = {"min_observed_at_pct": float(positive.min() * 100) if len(positive) else 0.0, "max_observed_at_pct": float(observed.max() * 100)}
    artifact = {"task":name, "kind":kind, "models":ensemble, "feature_columns":list(X.columns), "elements":[c[2:] for c in composition_columns], "composition_columns":composition_columns, "reference_compositions":reference, "element_ranges_at_pct":ranges, "distance_thresholds":{"inside":float(np.quantile(nearest,.95)),"boundary":float(nearest.max())}, "classes": label_encoder.classes_.tolist() if label_encoder else None, "model_version":"hea_mpea_baseline_v0.1", "feature_set":"baseline_composition_process_temperature_v0"}
    joblib.dump(artifact, MODELS / f"{name}_ensemble.joblib")
    report = {"task":name,"kind":kind,"n_records":len(frame),"n_compositions":int(frame.canonical_formula.nunique()),"split_records":{"train":len(train),"validation":len(validation),"test":len(test)},"split_compositions":{"train":int(frame.canonical_formula.iloc[train].nunique()),"validation":int(frame.canonical_formula.iloc[validation].nunique()),"test":int(frame.canonical_formula.iloc[test].nunique())},"selected_model":selected,"validation_models":candidate_metrics,"test_metrics":test_metrics,"uncertainty":uncertainty,"notes":"Hardness uses known near-room-temperature records plus legacy records with missing temperature flagged as ambient assumed." if name == "hardness" else ""}
    (REPORTS / f"{name}_training_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--task", choices=["all","yield_strength","hardness","phase"], default="all"); args = parser.parse_args()
    tasks = {"yield_strength":("experimental_yield_strength.csv","yield_strength_MPa","regression"),"hardness":("experimental_hardness.csv","hardness_HV","regression"),"phase":("experimental_phase.csv","phase_standardized","classification")}
    selected = tasks if args.task == "all" else {args.task:tasks[args.task]}
    print(json.dumps({name:train_task(name,*spec) for name,spec in selected.items()},indent=2))
