"""Honest, reproducible diagnostic for the admitted copper tensile records.

This is deliberately a release gate *diagnostic*, not a training command.  The
features contain only composition and test temperature: a held-out fabrication
state cannot be represented truthfully unless its process descriptors have been
measured for every candidate.  It reports chemistry-, grade-name-, and
grade--state-held-out OOF results.  In particular, different grade labels that
share an identical composition cannot be allowed to leak into a chemistry
extrapolation fold.
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
DATASET = ROOT / "data/processed/copper_hot_end_training_v0_20260910/nasa_tm_2007_tensile_records.csv"
REPORT = ROOT / "reports/copper_hot_end/20260910_grouped_baseline.json"
ELEMENTS = ("Cu", "Cr", "Nb", "Zr", "Ag", "Al", "O", "Ni", "Fe", "Ti", "Hf", "Co", "V", "Be", "B", "C", "N", "S")
# Match the minimal independent-group R² bar used by the existing local
# composition-screening service.  This is deliberately a necessary, not
# sufficient, release condition: process/state coverage and domain review are
# assessed separately before a runtime artifact may be made.
MIN_COMPOSITION_HELD_OUT_R2 = 0.60


def _composition_group(row: dict[str, str]) -> str:
    """Return a stable, label-independent key for the recorded chemistry."""
    return ";".join(
        f"{element}={float(row.get(f'{element}_wt_percent') or 0.0):.6f}"
        for element in ELEMENTS
    )


def _evaluate(rows: list[dict[str, str]], target: str, group_key: str) -> dict[str, float | int | str]:
    usable = [row for row in rows if row[target]]
    x = np.array(
        [[float(row.get(f"{element}_wt_percent") or 0.0) for element in ELEMENTS] + [float(row["test_temperature_C"])] for row in usable],
        dtype=float,
    )
    y = np.array([float(row[target]) for row in usable], dtype=float)
    groups = np.array([row[group_key] for row in usable])
    folds = min(5, len(set(groups)))
    predicted = np.empty(len(usable), dtype=float)
    for train, test in GroupKFold(n_splits=folds).split(x, y, groups):
        model = ExtraTreesRegressor(
            n_estimators=500, min_samples_leaf=2, max_features=0.9,
            # Keep this diagnostic single-process: nested worker pools can
            # terminate a CI/PTY job before it flushes the audit report.
            n_jobs=1, random_state=20260910,
        ).fit(x[train], y[train])
        predicted[test] = model.predict(x[test])
    group_errors = []
    for group in sorted(set(groups)):
        mask = groups == group
        group_errors.append({
            "group": str(group),
            "rows": int(mask.sum()),
            "mae_MPa": float(mean_absolute_error(y[mask], predicted[mask])),
            "mean_signed_error_MPa": float(np.mean(predicted[mask] - y[mask])),
        })
    group_errors.sort(key=lambda item: item["mae_MPa"], reverse=True)
    return {
        "target": target,
        "rows": int(len(usable)),
        "groups": int(len(set(groups))),
        "folds": int(folds),
        "r2": float(r2_score(y, predicted)),
        "mae_MPa": float(mean_absolute_error(y, predicted)),
        "rmse_MPa": float(mean_squared_error(y, predicted) ** 0.5),
        # These are diagnostic provenance groups, not a second release metric.
        # They make the next data-acquisition decision reproducible: add records
        # where chemistry held-out error is largest, rather than duplicating an
        # already well-covered grade.
        "worst_chemistry_groups_by_mae": group_errors[:10] if group_key == "composition_group" else [],
    }


def main() -> None:
    with DATASET.open(encoding="utf8", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    # Approximate/anisotropic cells are retained in the source audit dataset,
    # but cannot influence a release diagnostic.
    rows = [row for row in all_rows if not row["label_type"].startswith("review_only_")]
    for row in rows:
        row["composition_group"] = _composition_group(row)
        row["alloy_state_group"] = f"{row['alloy_name']}::{row['alloy_state']}"
    composition_held_out = [_evaluate(rows, target, "composition_group") for target in ("uts_MPa", "yield_0p2_MPa")]
    release_checks = {
        "composition_held_out_uts_r2_at_least_0p60": composition_held_out[0]["r2"] >= MIN_COMPOSITION_HELD_OUT_R2,
        "composition_held_out_yield_r2_at_least_0p60": composition_held_out[1]["r2"] >= MIN_COMPOSITION_HELD_OUT_R2,
    }
    report = {
        "status": "diagnostic_not_release_model",
        "dataset": str(DATASET.relative_to(ROOT)),
        "raw_rows": len(all_rows),
        "admitted_rows": len(rows),
        "features": [*(f"{element}_wt_percent" for element in ELEMENTS), "test_temperature_C"],
        "excludes": ["alloy_state", "test_strain_rate_per_s"],
        "exclusion_reason": "Neither field is complete, standardized process metadata for arbitrary proposed compositions; imputing it would inflate a release claim.",
        "composition_held_out": composition_held_out,
        "alloy_name_held_out": [_evaluate(rows, target, "alloy_name") for target in ("uts_MPa", "yield_0p2_MPa")],
        "alloy_state_held_out": [_evaluate(rows, target, "alloy_state_group") for target in ("uts_MPa", "yield_0p2_MPa")],
        "release_gate": {
            "minimum_composition_held_out_r2": MIN_COMPOSITION_HELD_OUT_R2,
            "checks": release_checks,
            "formal_release_status": "data_or_validation_gate_not_met" if not all(release_checks.values()) else "requires_process_domain_review",
            "scope": "Necessary quality gate for any future local-composition UTS/YS surrogate. Passing does not release fatigue, creep, CTE, conductivity, welding, or a manufacturing recommendation.",
        },
        "interpretation": "Composition-held-out is the chemical extrapolation diagnostic. Grade-name-held-out can still share a chemistry with another named grade; grade-state-held-out can still share chemistry with other states. Neither is sufficient by itself for release.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
