#!/usr/bin/env python3
"""Benchmark composition-only vs stoichiometric glass descriptors for CTE.

The added descriptors are deterministic transformations of the declared oxide
mol% (molar mass, oxide oxygen inventory, network-former and modifier charge
proxies).  They are not measured structure, fictive temperature, CALPHAD, or
residual-stress data.  The report therefore separates descriptor signal from
any claim of a thermodynamic constraint.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold


CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
OTHER = re.compile(r"([A-Za-z][A-Za-z0-9]*)=([0-9]+(?:\.[0-9]+)?)\s+mol%")
# Formula mass (g/mol), oxide oxygen atoms, cation count.  These are exact
# formula bookkeeping constants, not fitted material-property assumptions.
OXIDE = {
    "SiO2": (60.0843, 2, 1), "Al2O3": (101.9613, 3, 2), "B2O3": (69.6202, 3, 2),
    "Na2O": (61.9789, 1, 2), "K2O": (94.196, 1, 2), "Li2O": (29.8814, 1, 2),
    "MgO": (40.3044, 1, 1), "CaO": (56.0774, 1, 1), "BaO": (153.326, 1, 1),
    "SrO": (103.619, 1, 1), "ZnO": (81.379, 1, 1), "TiO2": (79.866, 2, 1),
    "ZrO2": (123.222, 2, 1), "P2O5": (141.9445, 5, 2), "SnO2": (150.708, 2, 1), "Fe2O3": (159.687, 3, 2),
}
CHARGE = {"Li2O": 1, "Na2O": 1, "K2O": 1, "MgO": 2, "CaO": 2, "BaO": 2, "SrO": 2, "ZnO": 2}
NETWORK = {"SiO2", "Al2O3", "B2O3", "P2O5"}


def load(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    features = set(CORE)
    for row in rows: features.update(name for name, _ in OTHER.findall(row.get("other_oxides_text", "")))
    unknown = sorted(features - set(OXIDE))
    if unknown: raise SystemExit(f"missing formula bookkeeping constants: {unknown}")
    return rows, sorted(features)


def composition(row: dict[str, str]) -> dict[str, float]:
    result = {name: float(row.get(name, "0") or 0) for name in CORE}
    result.update({name: float(value) for name, value in OTHER.findall(row.get("other_oxides_text", ""))})
    return result


def descriptor(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    molar_mass = sum(values.get(name, 0.0) * OXIDE[name][0] for name in values) / total
    oxygen = sum(values.get(name, 0.0) * OXIDE[name][1] for name in values) / total
    cations = sum(values.get(name, 0.0) * OXIDE[name][2] for name in values) / total
    network = sum(values.get(name, 0.0) for name in NETWORK) / total
    modifier = sum(values.get(name, 0.0) for name in CHARGE) / total
    modifier_charge = sum(values.get(name, 0.0) * CHARGE[name] for name in CHARGE) / total
    al_charge = values.get("Al2O3", 0.0) / total
    return {"mean_oxide_molar_mass_g_per_mol": molar_mass, "oxygen_moles_per_oxide_mole": oxygen,
            "cations_per_oxide_mole": cations, "network_former_oxide_fraction": network,
            "modifier_oxide_fraction": modifier, "modifier_charge_per_oxide_mole": modifier_charge,
            "modifier_charge_minus_Al_proxy": modifier_charge - al_charge}


def groups(rows: list[dict[str, str]]) -> np.ndarray:
    tables = [re.search(r"Table (\d+)", row["source_locator"]).group(1) for row in rows]
    if len(set(tables)) > 1: return np.asarray(tables)
    return np.asarray([f"block_{(int(re.search(r'example (\d+)', row['source_locator']).group(1)) - 1) // 8 + 1}" for row in rows])


def score(x: np.ndarray, y: np.ndarray, group: np.ndarray) -> tuple[dict[str, float], np.ndarray]:
    predicted = np.full(len(y), np.nan)
    for train, test in GroupKFold(n_splits=len(set(group))).split(x, y, group):
        model = ExtraTreesRegressor(n_estimators=800, min_samples_leaf=2, max_features=0.9, random_state=20260903, n_jobs=-1)
        predicted[test] = model.fit(x[train], y[train]).predict(x[test])
    error = np.abs(y - predicted)
    return {"mae_ppm_per_K": mean_absolute_error(y, predicted), "rmse_ppm_per_K": mean_squared_error(y, predicted) ** .5,
            "r2": r2_score(y, predicted), "max_absolute_error_ppm_per_K": float(error.max())}, predicted


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--input", type=Path, required=True); p.add_argument("--external", type=Path, required=True); p.add_argument("--output", type=Path, required=True); a = p.parse_args()
    if a.output.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output}")
    train, features = load(a.input); external, _ = load(a.external)
    comp = [composition(row) for row in train]; desc = [descriptor(row) for row in comp]; descriptor_names = list(desc[0])
    x_base = np.asarray([[item.get(name, 0.0) for name in features] for item in comp])
    x_augmented = np.column_stack([x_base, np.asarray([[item[name] for name in descriptor_names] for item in desc])])
    y = np.asarray([float(row["property_value"]) * 1e6 for row in train]); group = groups(train)
    base_metrics, _ = score(x_base, y, group); augmented_metrics, _ = score(x_augmented, y, group)
    lo, hi = x_base.min(axis=0), x_base.max(axis=0)
    ext_x = np.asarray([[composition(row).get(name, 0.0) for name in features] for row in external])
    inside = np.all((ext_x >= lo) & (ext_x <= hi), axis=1)
    report = {"input_rows": len(train), "external_rows": len(external), "validation": "whole patent table or serial 8-example block held out",
              "composition_features": features, "derived_descriptors": {name: "deterministic oxide-stoichiometry proxy" for name in descriptor_names},
              "comparison": {"composition_only": base_metrics, "composition_plus_descriptors": augmented_metrics,
                             "mae_change_ppm_per_K": augmented_metrics["mae_ppm_per_K"] - base_metrics["mae_ppm_per_K"]},
              "cross_source_check": {"external_records_inside_all_training_component_bounds": int(inside.sum()), "external_records": len(external),
                                     "status": "no_cross_source_error_reported because the two retained sources use different CTE temperature definitions; composition-overlap is reported instead."},
              "thermodynamic_constraint_status": "not_applied: no validated glass free-energy, viscosity, fictive-temperature or residual-stress model is available for these records."}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
