#!/usr/bin/env python3
"""Stress-test v2 against temperatures absent from fitting.

This is an interpolation diagnostic, not the release validation metric.  It
answers whether the high group-CV score simply comes from seeing the exact test
temperature in the training fold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score

from train_reusable_rocket_stainless_design_v2 import (
    CAT, DATA, NUM, TARGETS, dataset_for, make_model,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/reusable_rocket_stainless/20260902_v2_temperature_interpolation_audit.json"


def main() -> None:
    raw = pd.read_csv(DATA)
    report = {
        "method": "3-fold held-out temperature values; sorted unique temperatures are assigned round-robin to folds",
        "interpretation": "Tests interpolation across temperatures not present in each fit. It is deliberately distinct from the release GroupKFold metric.",
        "targets": {},
    }
    for target in TARGETS:
        d = dataset_for(raw, target)
        temperatures = sorted(d["Temperature (K)"].unique())
        folds = {temp: index % 3 for index, temp in enumerate(temperatures)}
        prediction = pd.Series(index=d.index, dtype=float)
        for fold in range(3):
            test = d["Temperature (K)"].map(folds).eq(fold)
            train = ~test
            model = make_model().fit(d.loc[train, NUM + CAT], d.loc[train, target])
            prediction.loc[d.index[test]] = model.predict(d.loc[test, NUM + CAT])
        y = d[target]
        report["targets"][target] = {
            "n": int(len(d)),
            "held_out_temperature_values": [float(t) for t in temperatures],
            "MAE": float(mean_absolute_error(y, prediction)),
            "R2": float(r2_score(y, prediction)),
        }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["targets"], ensure_ascii=False))


if __name__ == "__main__":
    main()
