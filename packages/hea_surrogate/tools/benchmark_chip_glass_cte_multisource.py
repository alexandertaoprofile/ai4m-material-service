#!/usr/bin/env python3
"""Audit added 0--300 C sources and composition-derived glass descriptors.

The descriptor block is deliberately limited to deterministic oxide chemistry:
average oxide mass, modifier and network-former contents, cation-charge
balance, an NBO/T-like proxy, alkaline-earth/Al balance and B/Si ratio.  It is
not a molecular-dynamics, CALPHAD, fictive-temperature or residual-stress
model.  Every score holds an entire document source out.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

OXIDES = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
MASS = np.asarray((60.0843, 101.9613, 69.6202, 61.9789, 94.1960, 29.8814, 40.3044, 56.0774, 153.3260, 81.3790, 79.8660, 123.2220, 141.9445))


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle: return list(csv.DictReader(handle))


def features(row: dict[str, str]) -> tuple[np.ndarray, np.ndarray]:
    c = np.asarray([float(row.get(oxide, "") or 0) for oxide in OXIDES])
    si, al, b, na, k, li, mg, ca, ba, zn, ti, zr, p = c
    modifiers = na + k + li + mg + ca + ba + zn
    modifier_charge = na + k + li + 2 * (mg + ca + ba + zn)
    network_formers = si + al + b + p + ti + zr
    physical = np.asarray((np.dot(c, MASS) / 100, modifiers, modifier_charge, network_formers, modifier_charge - 2 * al, (modifier_charge - 2 * al) / max(network_formers, 1e-9), (mg + ca + ba + zn) / max(al, 1e-9), b / max(si, 1e-9)))
    return c, np.r_[c, physical]


def score(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray, test_y: np.ndarray) -> dict[str, float]:
    model = ExtraTreesRegressor(n_estimators=1000, min_samples_leaf=2, max_features=0.9, random_state=20260903, n_jobs=-1)
    pred = model.fit(train_x, train_y).predict(test_x)
    return {"mae_ppm_per_K": float(mean_absolute_error(test_y, pred)), "rmse_ppm_per_K": float(mean_squared_error(test_y, pred) ** .5), "r2": float(r2_score(test_y, pred))}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", type=Path, nargs="+", required=True, help="admitted glass_thermal CSV files")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output}")
    rows = [row for path in a.inputs for row in read(path)]
    comp, descriptor = zip(*(features(row) for row in rows))
    x_comp, x_desc = np.asarray(comp), np.asarray(descriptor)
    y = np.asarray([float(row["property_value"]) * 1e6 for row in rows])
    source = np.asarray([row["source_id"] for row in rows])
    held_out = {}
    for identity in sorted(set(source)):
        test = source == identity
        held_out[identity] = {"n_test": int(test.sum()), "composition_only": score(x_comp[~test], y[~test], x_comp[test], y[test]), "composition_plus_oxide_chemistry_descriptors": score(x_desc[~test], y[~test], x_desc[test], y[test])}
    names = sorted(set(source))
    added_source_effect = {}
    if {"SRC-PATENT-US20250026678", "SRC-PATENT-US20110082022", "SRC-PATENT-CN1275889C"}.issubset(names):
        target = source == "SRC-PATENT-CN1275889C"
        base = source == "SRC-PATENT-US20250026678"
        augmented = base | (source == "SRC-PATENT-US20110082022")
        added_source_effect = {"target": "SRC-PATENT-CN1275889C", "baseline_train_source": "SRC-PATENT-US20250026678", "augmented_train_sources": ["SRC-PATENT-US20250026678", "SRC-PATENT-US20110082022"], "composition_only": {"baseline": score(x_comp[base], y[base], x_comp[target], y[target]), "after_adding_near_domain_source": score(x_comp[augmented], y[augmented], x_comp[target], y[target])}}
    mae_positive = all(item["composition_plus_oxide_chemistry_descriptors"]["mae_ppm_per_K"] < item["composition_only"]["mae_ppm_per_K"] for item in held_out.values())
    report = {"target": "linear CTE 0-300 C, ppm/K", "rows": len(rows), "sources": {key: int((source == key).sum()) for key in names}, "validation": "entire document source held out; no parameter tuning on held-out source", "descriptors": ["average oxide molar mass", "modifier mol%", "modifier charge", "network-former mol%", "charge-minus-2Al", "NBO/T-like charge/network proxy", "alkaline-earth-to-Al proxy", "B2O3-to-SiO2 ratio"], "held_out_source_results": held_out, "near_domain_data_addition_effect": added_source_effect, "descriptor_mae_improved_for_every_held_source": mae_positive, "interpretation": "Descriptors are composition transformations only, not thermodynamic or residual-stress constraints. They may be advanced to a separately versioned candidate model only when every held-source MAE improves; service release still requires a model-domain and uncertainty review."}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
