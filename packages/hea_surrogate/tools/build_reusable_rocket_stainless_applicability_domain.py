#!/usr/bin/env python3
"""Record a conservative, traceable applicability-domain gate for v2."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from train_reusable_rocket_stainless_design_v2 import DATA, ELS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees/applicability_domain.json"


def main() -> None:
    data = pd.read_csv(DATA)
    # Composition distance only.  Processing descriptors are too incomplete to
    # support a numerical distance gate and remain explicit required inputs.
    # The table has repeated tensile tests for the same composition.  Collapse
    # those first: otherwise a duplicate is every row's zero-distance neighbour
    # and the gate becomes meaningless.
    composition_rows = data[ELS].drop_duplicates().reset_index(drop=True)
    x = SimpleImputer(strategy="median").fit_transform(composition_rows)
    z = StandardScaler().fit_transform(x)
    # Exclude a row itself when measuring its nearest historical neighbour.
    squared = ((z[:, None, :] - z[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(squared, np.inf)
    nn_distance = np.sqrt(squared.min(axis=1))
    nonzero_bounds = {}
    for col in ELS:
        values = data.loc[data[col].fillna(0).ne(0), col]
        if not values.empty:
            nonzero_bounds[col] = {
                "minimum_wt_pct": float(values.min()), "maximum_wt_pct": float(values.max()),
                "p01_wt_pct": float(values.quantile(.01)), "p99_wt_pct": float(values.quantile(.99)),
            }
    report = {
        "model_version": "reusable_rocket_austenitic_design_v2_extratrees",
        "temperature_K": {"minimum": 293.0, "maximum": 1273.0},
        "composition_nonzero_bounds_wt_pct": nonzero_bounds,
        "composition_nearest_neighbour_distance": {
            "definition": "Euclidean distance after median imputation and z-score scaling over recorded elemental wt.% fields",
            "unique_compositions": int(len(composition_rows)),
            "review_threshold_p95": float(np.quantile(nn_distance, .95)),
            "block_threshold_p99": float(np.quantile(nn_distance, .99)),
        },
        "rules": [
            "Block automated strength predictions outside 293–1273 K.",
            "Require expert review above the p95 composition-neighbour distance; do not rank above the p99 threshold.",
            "A passing composition-distance check does not compensate for missing or unspecified cold work, heat treatment, thickness, weld state, strain rate, fatigue, fracture toughness, or LOX compatibility.",
            "Below 293 K use only the separately labelled 301/304L reference layer; never present it as a free-composition or 30X prediction.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"p95": report["composition_nearest_neighbour_distance"]["review_threshold_p95"], "p99": report["composition_nearest_neighbour_distance"]["block_threshold_p99"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
