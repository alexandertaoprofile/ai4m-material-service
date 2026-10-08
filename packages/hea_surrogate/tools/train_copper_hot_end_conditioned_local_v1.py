"""Train the bounded Cu hot-wall local-composition screening artifact.

This is intentionally not a broad copper-alloy model.  It trains only the
principal Cu-Cr-Nb-Zr-Ag-Al-O-Ni-Fe-Ti records with an explicitly supplied
processing-state descriptor and test temperature.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/processed/copper_hot_end_training_v0_20260910/nasa_tm_2007_tensile_records.csv"
AMBIENT_DATASET = ROOT / "data/processed/copper_hot_end_ccby_gorsse_v1/candidate_records.csv"
OUT = ROOT / "models/copper_hot_end_local_composition_v1_20260911"
ELEMENTS = ("Cu", "Cr", "Nb", "Zr", "Ag", "Al", "O", "Ni", "Fe", "Ti")
EXCLUDED = ("Hf", "Co", "V", "Be", "B", "C", "N", "S")
TOKENS = ("as_received", "braze", "cold", "aged", "solution", "anneal", "hot", "roll", "lpbf", "ded", "slm", "sps", "ecap", "sputter", "extrud", "laham", "laser", "hip", "vacuum", "quench", "water", "cast", "forge", "remelt")
AMBIENT_ELEMENTS = ("Cu", "Al", "Ag", "B", "Be", "Ca", "Co", "Ce", "Cr", "Fe", "Hf", "La", "Mg", "Mn", "Mo", "Nb", "Nd", "Ni", "P", "Pb", "Pr", "Si", "Sn", "Ti", "V", "Zn", "Zr")
AMBIENT_PROCESS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Tag (K)", "tag (h)")


def _state_features(value: str) -> list[float]:
    text = (value or "").casefold()
    return [float(token in text) for token in TOKENS]


def _core(rows: list[dict[str, str]], target: str) -> list[dict[str, str]]:
    return [row for row in rows if row.get(target) and not row["label_type"].startswith("review_only_") and not any(float(row.get(f"{item}_wt_percent") or 0) > 0 for item in EXCLUDED)]


def _x(rows: list[dict[str, str]]) -> np.ndarray:
    return np.asarray([[*(float(row.get(f"{item}_wt_percent") or 0) for item in ELEMENTS), float(row["test_temperature_C"]), *_state_features(row["alloy_state"])] for row in rows], dtype=float)


def _group(row: dict[str, str], mode: str) -> str:
    if mode == "composition":
        return ";".join(f"{item}={float(row.get(f'{item}_wt_percent') or 0):.6f}" for item in ELEMENTS)
    return f"{row['alloy_name']}::{row['alloy_state']}"


def _models() -> tuple[ExtraTreesRegressor, RandomForestRegressor]:
    return (
        ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2, max_features=.9, n_jobs=1, random_state=20260910),
        RandomForestRegressor(n_estimators=200, min_samples_leaf=2, max_features=.9, n_jobs=1, random_state=20260910),
    )


def _oof(rows: list[dict[str, str]], target: str, mode: str) -> dict[str, float | int]:
    x = _x(rows); y = np.asarray([float(row[target]) for row in rows]); groups = np.asarray([_group(row, mode) for row in rows])
    predicted = np.empty(len(rows)); folds = min(5, len(set(groups)))
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        et, rf = _models(); et.fit(x[train], y[train]); rf.fit(x[train], y[train])
        predicted[test] = .3 * et.predict(x[test]) + .7 * rf.predict(x[test])
    return {"rows": len(rows), "groups": len(set(groups)), "folds": folds, "r2": float(r2_score(y, predicted)), "mae_MPa": float(mean_absolute_error(y, predicted))}


def _ambient_number(value: str) -> float:
    return float(value) if value.strip() else -1.0


def _ambient_x(rows: list[dict[str, str]]) -> np.ndarray:
    return np.asarray([
        [*(float(row[f"{item}_wt_percent"]) for item in AMBIENT_ELEMENTS),
         *(_ambient_number(row[field]) for field in AMBIENT_PROCESS),
         float(row["Aging"] == "Y"), float(row["Secondary thermo-mechanical process"] == "Y")]
        for row in rows
    ], dtype=float)


def _ambient_group(row: dict[str, str]) -> str:
    return ";".join(f"{item}={float(row[f'{item}_wt_percent']):.6f}" for item in AMBIENT_ELEMENTS)


def _ambient_conductivity(rows: list[dict[str, str]]) -> tuple[ExtraTreesRegressor, dict[str, float | int]]:
    usable = [row for row in rows if row["Electrical conductivity (%IACS)"].strip()]
    x = _ambient_x(usable); y = np.asarray([float(row["Electrical conductivity (%IACS)"]) for row in usable])
    groups = np.asarray([_ambient_group(row) for row in usable]); predicted = np.empty(len(usable)); folds = min(5, len(set(groups)))
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        model = ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=.9, n_jobs=1, random_state=20260911)
        predicted[test] = model.fit(x[train], y[train]).predict(x[test])
    final = ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=.9, n_jobs=1, random_state=20260911).fit(x, y)
    return final, {"rows": len(usable), "composition_groups": len(set(groups)), "folds": folds, "r2": float(r2_score(y, predicted)), "mae_percent_IACS": float(mean_absolute_error(y, predicted))}


def main() -> None:
    with DATASET.open(encoding="utf-8", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    with AMBIENT_DATASET.open(encoding="utf-8", newline="") as handle:
        ambient_rows = list(csv.DictReader(handle))
    models: dict[str, object] = {}; validation: dict[str, object] = {}; references: list[dict[str, object]] = []
    for target, key in (("uts_MPa", "uts"), ("yield_0p2_MPa", "yield")):
        rows = _core(all_rows, target); x = _x(rows); y = np.asarray([float(row[target]) for row in rows])
        et, rf = _models(); et.fit(x, y); rf.fit(x, y)
        models[key] = {"extra_trees": et, "random_forest": rf}
        validation[key] = {"composition_held_out": _oof(rows, target, "composition"), "alloy_state_held_out": _oof(rows, target, "state")}
        if key == "uts":
            references = [{"alloy_name": row["alloy_name"], "alloy_state": row["alloy_state"], "test_temperature_C": float(row["test_temperature_C"]), "composition_wt_percent": {item: float(row.get(f"{item}_wt_percent") or 0) for item in ELEMENTS}} for row in rows]
    conductivity_model, conductivity_validation = _ambient_conductivity(ambient_rows)
    artifact = {
        "model_version": "copper_hot_end_conditioned_local_composition_v1_20260911",
        "elements": ELEMENTS, "process_tokens": TOKENS,
        "validation": validation,
        "scope": "Cu-Cr-Nb-Zr-Ag-Al-O-Ni-Fe-Ti principal domain; state-conditioned short-time UTS/0.2% yield screening only.",
        "sources": "NASA/TM-2007-214663 and source-audited copper tensile records; see processed CSV source_locator column.",
        "references": references,
        "models": models,
        "ambient_auxiliary": {
            "electrical_conductivity_percent_IACS": {
                "model": conductivity_model,
                "elements": AMBIENT_ELEMENTS,
                "process_fields": AMBIENT_PROCESS,
                "validation": conductivity_validation,
                "scope": "Gorsse CC0 copper-alloy dataset at room temperature; full processing fields required. It is a C-level auxiliary estimate only, not a hot-wall thermal-conductivity model.",
                "source": "Gorsse et al., Scientific Data 10, 504 (2023), CC0 dataset.",
            }
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, OUT / "model.joblib")
    public = {key: value for key, value in artifact.items() if key not in {"models", "references"}}
    public["ambient_auxiliary"] = {
        key: {field: value for field, value in record.items() if field != "model"}
        for key, record in artifact["ambient_auxiliary"].items()
    }
    (OUT / "model_card.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(OUT), "validation": validation}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
