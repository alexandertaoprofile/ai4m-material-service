"""M0--M3 coarse phase benchmark on records with actual annealing conditions.

This is deliberately a *phase* validation set, separate from the performance
tables whose heat-treatment field is empty. M2 is direct CALPHAD equilibrium.
M3 is a soft, validation-tuned fusion of the data-model phase probability and
the CALPHAD phase prior; it is not a claim of a differentiable free-energy loss.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline

from src.models.run_constraint_ladder_performance import physical_descriptors, split_70_15_15
from src.models.train_baselines import _features

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "reports" / "calphad" / "machaka_2021_tdb_domain_700_900c_calphad.csv"
REPORT = ROOT / "reports" / "constraint_ladder" / "thermal_phase_m0_m1_m2_m3.json"
LABELS = ["FCC_SS", "BCC_SS", "FCC_PLUS_BCC", "Im"]
SEEDS = (11, 23, 37, 53, 71)


def calphad_coarse(value: str) -> str:
    phases = {entry.split("=", 1)[0] for entry in value.split(";") if entry}
    fcc = bool(phases & {"FCC_A1", "FCC_4SL"})
    bcc = bool(phases & {"BCC_A2", "B2_BCC"})
    intermetallic = bool(phases - {"FCC_A1", "FCC_4SL", "BCC_A2", "B2_BCC"})
    if intermetallic: return "Im"
    if fcc and bcc: return "FCC_PLUS_BCC"
    if fcc: return "FCC_SS"
    if bcc: return "BCC_SS"
    return "Im"


def calphad_flags(value: str) -> dict[str, int]:
    phases = {entry.split("=", 1)[0] for entry in value.split(";") if entry}
    return {
        "calphad_has_fcc": int(bool(phases & {"FCC_A1", "FCC_4SL"})),
        "calphad_has_bcc": int(bool(phases & {"BCC_A2", "B2_BCC"})),
        "calphad_has_intermetallic": int(bool(phases - {"FCC_A1", "FCC_4SL", "BCC_A2", "B2_BCC"})),
        "calphad_phase_count": len(phases),
    }


def composition_json(value: str) -> str:
    """Convert the batch runner's explicit at.% string to canonical JSON."""
    return json.dumps({part.split("=", 1)[0]: float(part.split("=", 1)[1]) for part in value.split(";") if part}, sort_keys=True)


def metric(y: pd.Series, p: np.ndarray) -> dict:
    return {"accuracy": float(accuracy_score(y, p)), "macro_F1": float(f1_score(y, p, labels=LABELS, average="macro", zero_division=0)), "confusion_matrix": confusion_matrix(y, p, labels=LABELS).tolist(), "label_order": LABELS}


def make_model(seed: int) -> Pipeline:
    return Pipeline([("impute", SimpleImputer(strategy="median")), ("model", ExtraTreesClassifier(n_estimators=240, min_samples_leaf=2, max_features=.8, class_weight="balanced", n_jobs=-1, random_state=seed))])


def ensemble_predict_proba(X: pd.DataFrame, y: pd.Series, fit: np.ndarray, evaluate: np.ndarray) -> np.ndarray:
    """Return probability columns aligned to the fixed coarse-phase label order."""
    probability = []
    for seed in SEEDS:
        model = make_model(seed)
        model.fit(X.iloc[fit], y.iloc[fit])
        raw = model.predict_proba(X.iloc[evaluate])
        aligned = np.zeros((len(evaluate), len(LABELS)), dtype=float)
        for column, label in enumerate(model.named_steps["model"].classes_):
            aligned[:, LABELS.index(label)] = raw[:, column]
        probability.append(aligned)
    return np.mean(probability, axis=0)


def ensemble_predict(X: pd.DataFrame, y: pd.Series, split: tuple[np.ndarray, np.ndarray, np.ndarray]) -> dict:
    train, valid, test = split
    probability = ensemble_predict_proba(X, y, np.r_[train, valid], test)
    return metric(y.iloc[test], np.asarray(LABELS)[probability.argmax(axis=1)])


def thermo_prior(stable_phases: pd.Series) -> np.ndarray:
    """Smoothed direct-CALPHAD prior in the same coarse label vocabulary."""
    prior = np.full((len(stable_phases), len(LABELS)), 0.05, dtype=float)
    for i, value in enumerate(stable_phases):
        prior[i, LABELS.index(calphad_coarse(value))] = 0.85
    return prior


def soft_constrained_m3(X: pd.DataFrame, y: pd.Series, stable_phases: pd.Series, split: tuple[np.ndarray, np.ndarray, np.ndarray]) -> dict:
    """Tune CALPHAD-prior weight exclusively on validation, then freeze for test."""
    train, valid, test = split
    validation_data = ensemble_predict_proba(X, y, train, valid)
    validation_thermo = thermo_prior(stable_phases.iloc[valid])
    candidates = np.linspace(0.0, 1.0, 21)
    validation_scores = {}
    for weight in candidates:
        predicted = np.asarray(LABELS)[((1 - weight) * validation_data + weight * validation_thermo).argmax(axis=1)]
        validation_scores[f"{weight:.2f}"] = float(f1_score(y.iloc[valid], predicted, labels=LABELS, average="macro", zero_division=0))
    # Stable tie-breaker prefers the less intrusive constraint.
    selected = min(candidates, key=lambda weight: (-validation_scores[f"{weight:.2f}"], weight))
    test_data = ensemble_predict_proba(X, y, np.r_[train, valid], test)
    test_thermo = thermo_prior(stable_phases.iloc[test])
    prediction = np.asarray(LABELS)[((1 - selected) * test_data + selected * test_thermo).argmax(axis=1)]
    output = metric(y.iloc[test], prediction)
    output.update({"selected_calphad_prior_weight": float(selected), "validation_macro_F1_by_weight": validation_scores})
    return output


def main() -> None:
    frame = pd.read_csv(INPUT)
    # Reuse the canonical feature builder by exposing test temperature as the
    # actual anneal temperature. This is legitimate only in this thermal set.
    frame["test_temperature_C"] = frame["anneal_temperature_C"].astype(float)
    frame["processing_method"] = frame["Sythesis_Route"].fillna("UNKNOWN")
    frame["composition_at_pct"] = frame["composition_at_pct"].map(composition_json)
    base, _, _ = _features(frame)
    comp = [c for c in base if c.startswith("x_")]
    m0 = pd.concat([base[comp + ["test_temperature_C", "temperature_missing"]], pd.DataFrame({"log_anneal_time_min": np.log1p(pd.to_numeric(frame["Annealing_Time_(min)"], errors="coerce"))})], axis=1)
    m1 = pd.concat([m0, physical_descriptors(frame)], axis=1)
    flags = pd.DataFrame([calphad_flags(x) for x in frame.stable_phases], index=frame.index)
    # `flags` is retained as an auditable diagnostic, but never fit as an
    # ordinary feature.  M3 below uses the full CALPHAD phase result only via a
    # validation-tuned prior fusion.
    y = frame.observed_phase_coarse.astype(str)
    report = {"method": {"M0": "ExtraTrees on composition + actual anneal temperature + hold time", "M1": "M0 + ASE elemental moments", "M2": "direct CALPHAD equilibrium mapped to four coarse labels", "M3": "M1 probability softly fused with a smoothed direct-CALPHAD prior; prior weight selected only on validation", "warning": "discovery-pool diagnostic only: 105 secondary-compilation records and 3 BCC_SS labels are insufficient for a release decision"}, "records": len(frame), "compositions": int(frame["Alloy "].nunique()), "dois": int(frame.References.nunique()), "results": {}}
    for name, groups in {"composition_group": frame["Alloy "].astype(str), "doi_group": frame.References.astype(str)}.items():
        split = split_70_15_15(groups)
        test = split[2]
        report["results"][name] = {
            "split_records": {"train": len(split[0]), "validation": len(split[1]), "test": len(test)},
            "M0_data": ensemble_predict(m0, y, split),
            "M1_descriptor": ensemble_predict(m1, y, split),
            "M2_direct_calphad": metric(y.iloc[test], np.array([calphad_coarse(x) for x in frame.stable_phases.iloc[test]])),
            "M3_soft_thermo_constraint": soft_constrained_m3(m1, y, frame.stable_phases, split),
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
