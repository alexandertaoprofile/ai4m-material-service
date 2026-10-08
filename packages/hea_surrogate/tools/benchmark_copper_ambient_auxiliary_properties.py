"""Audit whether Gorsse ambient properties support a display-only Cu estimate."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed/copper_hot_end_ccby_gorsse_v1/candidate_records.csv"
REPORT = ROOT / "reports/copper_hot_end/20260911_ambient_auxiliary_property_baseline.json"
ELEMENTS = ("Cu", "Al", "Ag", "B", "Be", "Ca", "Co", "Ce", "Cr", "Fe", "Hf", "La", "Mg", "Mn", "Mo", "Nb", "Nd", "Ni", "P", "Pb", "Pr", "Si", "Sn", "Ti", "V", "Zn", "Zr")
PROCESS = ("Tss (K)", "tss (h)", "CR reduction (%)", "Tag (K)", "tag (h)")
TARGETS = {"hardness_HV": "Hardness (HV)", "electrical_conductivity_percent_IACS": "Electrical conductivity (%IACS)"}


def _number(value: str) -> float: return float(value) if value.strip() else -1.0
def _feature(row: dict[str, str], *, with_process: bool) -> list[float]:
    values = [float(row[f"{element}_wt_percent"]) for element in ELEMENTS]
    return values + ([_number(row[key]) for key in PROCESS] + [float(row["Aging"] == "Y"), float(row["Secondary thermo-mechanical process"] == "Y")] if with_process else [])
def _group(row: dict[str, str]) -> str: return ";".join(f"{element}={float(row[f'{element}_wt_percent']):.6f}" for element in ELEMENTS)


def _score(rows: list[dict[str, str]], target: str, *, with_process: bool) -> dict[str, float | int]:
    usable = [row for row in rows if row[target].strip()]; x = np.asarray([_feature(row, with_process=with_process) for row in usable]); y = np.asarray([float(row[target]) for row in usable]); groups = np.asarray([_group(row) for row in usable]); predicted = np.empty(len(usable)); folds = min(5, len(set(groups)))
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        predicted[test] = ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=.9, n_jobs=1, random_state=20260911).fit(x[train], y[train]).predict(x[test])
    return {"rows": len(usable), "composition_groups": len(set(groups)), "folds": folds, "r2": float(r2_score(y, predicted)), "mae": float(mean_absolute_error(y, predicted))}


def main() -> None:
    with DATA.open(encoding="utf8", newline="") as handle: rows = list(csv.DictReader(handle))
    report = {"status": "ambient_auxiliary_diagnostic_only", "source_scope": "Gorsse CC0 room-temperature dataset; not a hot-end or temperature-extrapolation model", "validation": {key: {"composition_only": _score(rows, column, with_process=False), "composition_plus_complete_process_fields": _score(rows, column, with_process=True)} for key, column in TARGETS.items()}, "decision_rule": "A result may be considered only as a D-level room-temperature estimate after a separate service-scope review; it cannot rank or hard-filter hot-wall candidates."}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
