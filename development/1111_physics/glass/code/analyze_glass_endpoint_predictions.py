#!/usr/bin/env python3
"""Paired bootstrap audit for the held-out highest-temperature glass endpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--bootstrap", type=int, default=20_000)
    args = parser.parse_args()

    frame = pd.read_csv(args.input)
    keys = [
        "source_kod",
        "canonical_composition",
        "task",
        "condition_temperature_C",
        "target",
        "anchor_row_index",
    ]
    wide = frame.pivot(index=keys, columns="method", values="prediction").reset_index()
    baseline = "baseline_full_multitask"
    adapter = "cte_adapter_pair_response"
    if wide[[baseline, adapter]].isna().any().any():
        raise RuntimeError("paired endpoint rows are incomplete")

    base_error = np.abs(wide[baseline].to_numpy(float) - wide.target.to_numpy(float))
    adapter_error = np.abs(wide[adapter].to_numpy(float) - wide.target.to_numpy(float))
    improvement = base_error - adapter_error
    rng = np.random.default_rng(args.seed)
    samples = rng.integers(0, len(wide), size=(args.bootstrap, len(wide)))
    boot = improvement[samples].mean(axis=1)

    report = {
        "rows": int(len(wide)),
        "duplicate_pair_keys": int(wide.duplicated(keys).sum()),
        "baseline_MAE": float(base_error.mean()),
        "adapter_MAE": float(adapter_error.mean()),
        "absolute_MAE_reduction": float(improvement.mean()),
        "relative_MAE_reduction_pct": float(100.0 * improvement.mean() / base_error.mean()),
        "endpoint_fraction_improved": float(np.mean(improvement > 0)),
        "paired_bootstrap": {
            "replicates": int(args.bootstrap),
            "seed": int(args.seed),
            "mean_MAE_reduction_95pct_CI": [float(x) for x in np.quantile(boot, [0.025, 0.975])],
            "probability_positive_MAE_reduction": float(np.mean(boot > 0)),
        },
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
