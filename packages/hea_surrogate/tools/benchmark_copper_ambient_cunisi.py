"""Grouped diagnostic for the CC-BY room-temperature Cu-Ni-Si auxiliary layer.

It is deliberately reported separately from the hot-end release gate: the
released dataset labels are room-temperature values and do not contain tensile
test temperature.  The result therefore measures only composition/process
coverage for the auxiliary layer.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/processed/copper_ambient_cunisi_ccby_v1/candidate_records.csv"
REPORT = ROOT / "reports/copper_hot_end/20260910_cunisi_auxiliary_grouped_baseline.json"
ELEMENTS = ("Cu", "Al", "Co", "Cr", "Mg", "Ni", "Si", "Sn", "Zn")
PROCESS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Tag (K)", "tag (h)")


def _feature(row: dict[str, str]) -> list[float]:
    numbers = [float(row[f"{element}_wt_percent"]) for element in ELEMENTS]
    numbers += [float(row[name]) for name in PROCESS]
    numbers += [float(row["Aging"] == "Y"), float(row["Secondary thermo-mechanical process"] == "Y")]
    return numbers


def _composition_group(row: dict[str, str]) -> str:
    return ";".join(f"{element}={float(row[f'{element}_wt_percent']):.6f}" for element in ELEMENTS)


def _evaluate(rows: list[dict[str, str]], target: str, group_key: str) -> dict[str, float | int | str]:
    x = np.asarray([_feature(row) for row in rows], dtype=float)
    y = np.asarray([float(row[target]) for row in rows], dtype=float)
    groups = np.asarray([row[group_key] for row in rows])
    folds = min(5, len(set(groups)))
    predicted = np.empty(len(rows), dtype=float)
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        model = ExtraTreesRegressor(
            n_estimators=500, min_samples_leaf=2, max_features=0.9,
            n_jobs=1, random_state=20260910,
        ).fit(x[train], y[train])
        predicted[test] = model.predict(x[test])
    return {
        "target": target,
        "rows": len(rows),
        "groups": len(set(groups)),
        "folds": folds,
        "r2": float(r2_score(y, predicted)),
        "mae_MPa": float(mean_absolute_error(y, predicted)),
        "rmse_MPa": float(mean_squared_error(y, predicted) ** 0.5),
    }


def main() -> None:
    with DATASET.open(encoding="utf8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["composition_group"] = _composition_group(row)
    report = {
        "status": "ambient_auxiliary_diagnostic_not_hot_end_release_model",
        "dataset": str(DATASET.relative_to(ROOT)),
        "rows": len(rows),
        "features": [*(f"{element}_wt_percent" for element in ELEMENTS), *PROCESS, "Aging", "Secondary thermo-mechanical process"],
        "composition_held_out": [_evaluate(rows, target, "composition_group") for target in ("Yield strength (MPa)", "Ultimate tensile strength (MPa)")],
        "source_doi_held_out": [_evaluate(rows, target, "source_doi") for target in ("Yield strength (MPa)", "Ultimate tensile strength (MPa)")],
        "interpretation": "Neither split validates high-temperature generalization. Composition-held-out is included only to expose auxiliary-layer chemical extrapolation error; source-DOI-held-out exposes document-level transfer.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
