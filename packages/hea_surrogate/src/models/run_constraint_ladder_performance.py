"""Fair M0/M1/M3 performance benchmarks on the traceable HEA core cohort.

M0: composition + recorded context; M1: M0 plus deterministic elemental
statistics; M3: M1 with a shared MLP and an auxiliary *experimental coarse
phase* head.  The input tables have no heat-treatment state, so this program
intentionally contains no CALPHAD-derived performance feature.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from ase.data import atomic_masses, atomic_numbers, covalent_radii
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.models.train_baselines import R, VEC, _features

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "constraint_ladder" / "performance_m0_m1_m3.json"
SEEDS = (11, 23, 37, 53, 71)


def split_70_15_15(groups: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    index = np.arange(len(groups))
    train, held = next(GroupShuffleSplit(n_splits=1, train_size=.70, random_state=2026).split(index, groups=groups))
    valid_inner, test_inner = next(GroupShuffleSplit(n_splits=1, train_size=.50, random_state=2027).split(held, groups=groups.iloc[held]))
    return train, held[valid_inner], held[test_inner]


def physical_descriptors(frame: pd.DataFrame) -> pd.DataFrame:
    """Composition-only elemental moments from ASE's packaged element table."""
    compositions = frame.composition_at_pct.map(json.loads)
    values: list[dict[str, float]] = []
    for composition in compositions:
        fractions = np.array(list(composition.values()), dtype=float) / 100.0
        symbols = list(composition)
        z = np.array([atomic_numbers[s] for s in symbols], dtype=float)
        mass = np.array([atomic_masses[atomic_numbers[s]] for s in symbols], dtype=float)
        radius = np.array([covalent_radii[atomic_numbers[s]] for s in symbols], dtype=float)
        vec = np.array([VEC.get(s, np.nan) for s in symbols], dtype=float)
        def mean_and_std(name: str, array: np.ndarray) -> dict[str, float]:
            known = ~np.isnan(array)
            weight = fractions[known]
            if not len(weight) or weight.sum() == 0:
                return {f"{name}_mean": np.nan, f"{name}_std": np.nan}
            weight = weight / weight.sum(); x = array[known]
            mean = float(np.dot(weight, x))
            return {f"{name}_mean": mean, f"{name}_std": float(np.sqrt(np.dot(weight, (x - mean) ** 2)))}
        row: dict[str, float] = {}
        for name, array in (("atomic_number", z), ("atomic_mass", mass), ("covalent_radius_pm", radius), ("VEC", vec)):
            row.update(mean_and_std(name, array))
        rbar = row["covalent_radius_pm_mean"]
        row["atomic_size_delta"] = float(np.sqrt(np.dot(fractions, (1 - radius / rbar) ** 2))) if rbar else np.nan
        row["max_element_fraction"] = float(fractions.max())
        row["mixing_entropy_J_molK_recomputed"] = float(-R * np.sum(fractions * np.log(np.clip(fractions, 1e-12, None))))
        values.append(row)
    return pd.DataFrame(values, index=frame.index)


def feature_sets(frame: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, list[str], list[str]]]:
    base, _, _ = _features(frame)
    composition = [c for c in base if c.startswith("x_")]
    context_n = ["test_temperature_C", "temperature_missing"]
    context_c = ["processing_method"]
    m0 = composition + context_n + context_c
    descriptor = physical_descriptors(frame)
    combined = pd.concat([base, descriptor], axis=1)
    m1 = composition + context_n + list(descriptor) + context_c
    return {"M0_data": (base[m0], composition + context_n, context_c), "M1_descriptor": (combined[m1], composition + context_n + list(descriptor), context_c)}


def pipeline(kind: str, numeric: list[str], categorical: list[str], seed: int) -> Pipeline:
    preprocess = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical),
    ])
    if kind == "extra_trees":
        model = ExtraTreesRegressor(n_estimators=240, min_samples_leaf=2, max_features=.8, n_jobs=-1, random_state=seed)
    elif kind == "mlp":
        model = MLPRegressor(hidden_layer_sizes=(64, 32), activation="relu", alpha=1e-4, early_stopping=True, validation_fraction=.15, n_iter_no_change=16, max_iter=350, random_state=seed)
    else:
        raise ValueError(kind)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {"MAE": float(mean_absolute_error(y, p)), "RMSE": float(mean_squared_error(y, p) ** .5), "R2": float(r2_score(y, p))}


def m3_multitask(X: pd.DataFrame, numeric: list[str], categorical: list[str], y: pd.Series, phases: pd.Series, split: tuple[np.ndarray, np.ndarray, np.ndarray]) -> dict:
    train, valid, test = split
    transformer = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical),
    ])
    x_train = transformer.fit_transform(X.iloc[train]); x_valid = transformer.transform(X.iloc[valid]); x_test = transformer.transform(X.iloc[test])
    classes = sorted(phases.unique()); class_id = {label: i for i, label in enumerate(classes)}
    phase_ids = phases.map(class_id).to_numpy()
    y_train = y.iloc[train].to_numpy(dtype=np.float32); ym, ys = float(y_train.mean()), float(y_train.std() or 1)
    predictions = []
    for seed in SEEDS:
        torch.manual_seed(seed)
        class Net(torch.nn.Module):
            def __init__(self):
                super().__init__(); self.body = torch.nn.Sequential(torch.nn.Linear(x_train.shape[1], 64), torch.nn.ReLU(), torch.nn.Linear(64, 32), torch.nn.ReLU()); self.property = torch.nn.Linear(32, 1); self.phase = torch.nn.Linear(32, len(classes))
            def forward(self, x):
                h = self.body(x); return self.property(h).squeeze(1), self.phase(h)
        net = Net(); opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
        xt = torch.tensor(x_train, dtype=torch.float32); yt = torch.tensor((y_train - ym) / ys, dtype=torch.float32); pt = torch.tensor(phase_ids[train], dtype=torch.long)
        xv = torch.tensor(x_valid, dtype=torch.float32); yv = torch.tensor((y.iloc[valid].to_numpy(dtype=np.float32) - ym) / ys, dtype=torch.float32); pv = torch.tensor(phase_ids[valid], dtype=torch.long)
        best, state, stale = float("inf"), None, 0
        for _ in range(500):
            net.train(); prop, phase = net(xt); loss = torch.nn.functional.mse_loss(prop, yt) + .20 * torch.nn.functional.cross_entropy(phase, pt); opt.zero_grad(); loss.backward(); opt.step()
            net.eval()
            with torch.no_grad(): vp, vc = net(xv); score = float(torch.nn.functional.mse_loss(vp, yv) + .20 * torch.nn.functional.cross_entropy(vc, pv))
            if score < best - 1e-5: best, state, stale = score, {k: v.detach().clone() for k, v in net.state_dict().items()}, 0
            else: stale += 1
            if stale >= 45: break
        net.load_state_dict(state); net.eval()
        with torch.no_grad(): prediction = net(torch.tensor(x_test, dtype=torch.float32))[0].numpy() * ys + ym
        predictions.append(prediction)
    stacked = np.asarray(predictions)
    return {"test_metrics": metrics(y.iloc[test], stacked.mean(axis=0)), "ensemble_prediction_std_mean": float(stacked.std(axis=0).mean()), "phase_classes": classes, "phase_loss_weight": .20}


def run_task(task: str, filename: str, target: str) -> dict:
    raw = pd.read_csv(DATA / filename)
    # Core keeps literature DOI plus explicit recorded processing and test temperature.
    frame = raw[(raw.source_dataset == "MPEA_dataset.csv") & raw.source_doi.notna() & raw.processing_method.notna() & raw.test_temperature_C.notna() & raw.phase_standardized.notna() & raw[target].notna()].reset_index(drop=True)
    if task == "hardness":
        temp = pd.to_numeric(frame.test_temperature_C, errors="coerce")
        frame = frame[(temp - 25).abs() <= 75].reset_index(drop=True)
    sets = feature_sets(frame)
    result = {"records": len(frame), "compositions": int(frame.canonical_formula.nunique()), "dois": int(frame.source_doi.nunique()), "core_rule": "MPEA source + DOI + processing + test temperature + phase label"}
    for split_name, group in {"composition_group": frame.canonical_formula, "doi_group": frame.source_doi}.items():
        split = split_70_15_15(group)
        block = {"split_records": {"train": len(split[0]), "validation": len(split[1]), "test": len(split[2])}, "models": {}}
        for variant, (X, numeric, categorical) in sets.items():
            for model_name in ("extra_trees", "mlp"):
                pred = []
                for seed in SEEDS:
                    model = pipeline(model_name, numeric, categorical, seed)
                    model.fit(X.iloc[np.r_[split[0], split[1]]], frame[target].iloc[np.r_[split[0], split[1]]])
                    pred.append(model.predict(X.iloc[split[2]]))
                stacked = np.asarray(pred)
                block["models"][f"{variant}_{model_name}"] = {"test_metrics": metrics(frame[target].iloc[split[2]], stacked.mean(axis=0)), "ensemble_prediction_std_mean": float(stacked.std(axis=0).mean())}
        X, numeric, categorical = sets["M1_descriptor"]
        block["models"]["M3_multitask_mlp"] = m3_multitask(X, numeric, categorical, frame[target], frame.phase_standardized.astype(str), split)
        result[split_name] = block
    return result


def main() -> None:
    report = {"method": {"M0": "composition + recorded process/test context", "M1": "M0 plus atomic mass/radius/Z/VEC moments from ASE", "M2": "not run on performance labels: heat-treatment field is absent", "M3": "M1 shared MLP with auxiliary experimental coarse-phase head", "seeds": list(SEEDS), "splits": "fixed 70/15/15 GroupShuffleSplit by exact composition and separately by DOI"}, "tasks": {"yield_strength": run_task("yield_strength", "experimental_yield_strength.csv", "yield_strength_MPa"), "hardness": run_task("hardness", "experimental_hardness.csv", "hardness_HV")}}
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
