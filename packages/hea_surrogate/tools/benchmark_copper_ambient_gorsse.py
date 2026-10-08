"""Grouped diagnostic for the released Gorsse copper-alloy dataset.

This is intentionally an ambient, source-separated diagnostic.  The released
CSV labels strength at room temperature but does not supply a numeric tensile
test temperature; it must never be folded into the temperature-labelled
hot-end release gate.
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
DATASET = ROOT / "data/processed/copper_hot_end_ccby_gorsse_v1/candidate_records.csv"
REPORT = ROOT / "reports/copper_hot_end/20260911_gorsse_ambient_grouped_baseline.json"
ELEMENTS = ("Cu", "Al", "Ag", "B", "Be", "Ca", "Co", "Ce", "Cr", "Fe", "Hf", "La", "Mg", "Mn", "Mo", "Nb", "Nd", "Ni", "P", "Pb", "Pr", "Si", "Sn", "Ti", "V", "Zn", "Zr")
NUMERIC_PROCESS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Tag (K)", "tag (h)")
TARGETS = ("Yield strength (MPa)", "Ultimate tensile strength (MPa)")


def _number(value: str) -> float:
    return float(value) if value.strip() else -1.0


def _feature(row: dict[str, str]) -> list[float]:
    values = [float(row[f"{element}_wt_percent"]) for element in ELEMENTS]
    values += [_number(row[name]) for name in NUMERIC_PROCESS]
    values += [float(row["Aging"] == "Y"), float(row["Secondary thermo-mechanical process"] == "Y")]
    return values


def _composition_group(row: dict[str, str]) -> str:
    return ";".join(f"{element}={float(row[f'{element}_wt_percent']):.6f}" for element in ELEMENTS)


def _evaluate(rows: list[dict[str, str]], target: str, group_key: str) -> dict[str, float | int | str]:
    eligible = [row for row in rows if row[target].strip()]
    x = np.asarray([_feature(row) for row in eligible], dtype=float)
    y = np.asarray([float(row[target]) for row in eligible], dtype=float)
    groups = np.asarray([row[group_key] for row in eligible])
    folds = min(5, len(set(groups)))
    if folds < 2:
        raise ValueError(f"not enough independent groups for {target}: {folds}")
    predicted = np.empty(len(eligible), dtype=float)
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        model = ExtraTreesRegressor(
            n_estimators=500, min_samples_leaf=2, max_features=0.9,
            n_jobs=1, random_state=20260911,
        ).fit(x[train], y[train])
        predicted[test] = model.predict(x[test])
    return {
        "target": target,
        "rows": len(eligible),
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
        "source_scope": "Gorsse et al. released CC0 dataset; room-temperature strength labels",
        "composition_held_out": [_evaluate(rows, target, "composition_group") for target in TARGETS],
        "source_doi_held_out": [_evaluate(rows, target, "source_doi") for target in TARGETS],
        "interpretation": "A passing ambient score does not validate 538 C or other hot-end extrapolation. The source's processing fields are retained but no missing tensile-test temperature is invented.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
