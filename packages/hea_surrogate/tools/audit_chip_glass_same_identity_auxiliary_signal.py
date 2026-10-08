#!/usr/bin/env python3
"""Audit measured same-sample properties as optional CTE calibration inputs.

This intentionally does not treat density or annealing point as properties
available from a proposed composition.  It tests only their value after they
have been measured on the same glass identity.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold

OXIDES = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
FIELDS = ("density_g_per_cm3", "young_modulus_GPa", "strain_point_fibre_C", "strain_point_BBV_C", "annealing_point_fibre_C", "annealing_point_BBV_C", "poisson_ratio")


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cte", type=Path, required=True)
    parser.add_argument("--same-identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")

    aux = {row["glass_id"]: row for row in read(args.same_identity)}
    cte = [row for row in read(args.cte) if row["glass_id"] in aux]
    y = np.asarray([float(row["property_value"]) * 1e6 for row in cte])
    report: dict[str, object] = {
        "scope": "US20250026678 same-identity records, CTE 0-300 C",
        "important_interpretation": "auxiliary fields are measured same-sample calibration/QC inputs, not free-composition prediction features",
        "n_cte_records": len(cte),
        "spearman_with_cte": {},
    }
    for field in FIELDS:
        selected = [(float(row["property_value"]) * 1e6, float(aux[row["glass_id"]][field])) for row in cte if aux[row["glass_id"]].get(field, "")]
        if len(selected) >= 3:
            corr = spearmanr(*zip(*selected))
            report["spearman_with_cte"][field] = {"n": len(selected), "rho": float(corr.statistic), "p_value": float(corr.pvalue)}

    selected = [row for row in cte if aux[row["glass_id"]].get("density_g_per_cm3", "")]
    groups = np.asarray([re.search(r"Table (\d+)", row["source_locator"]).group(1) for row in selected])
    y_density = np.asarray([float(row["property_value"]) * 1e6 for row in selected])
    composition = np.asarray([[float(row.get(oxide, "") or 0) for oxide in OXIDES] for row in selected])
    with_density = np.column_stack([composition, [float(aux[row["glass_id"]]["density_g_per_cm3"]) for row in selected]])
    benchmark = {"n": len(selected), "validation": "GroupKFold by patent table; measured density supplied only to held-out sample", "mae_ppm_per_K": {}}
    for name, x in (("composition_only", composition), ("composition_plus_measured_density", with_density)):
        prediction = np.zeros(len(y_density))
        for train, test in GroupKFold(n_splits=len(set(groups))).split(x, y_density, groups):
            model = ExtraTreesRegressor(n_estimators=800, min_samples_leaf=2, max_features=0.9, n_jobs=-1, random_state=20260903)
            prediction[test] = model.fit(x[train], y_density[train]).predict(x[test])
        benchmark["mae_ppm_per_K"][name] = float(mean_absolute_error(y_density, prediction))
    benchmark["absolute_mae_change_ppm_per_K"] = benchmark["mae_ppm_per_K"]["composition_plus_measured_density"] - benchmark["mae_ppm_per_K"]["composition_only"]
    report["measured_density_calibration_benchmark"] = benchmark
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
