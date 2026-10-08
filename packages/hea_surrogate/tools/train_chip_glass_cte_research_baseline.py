#!/usr/bin/env python3
"""Train a provenance-grouped exploratory CTE baseline from SciGlass+.

The SciGlass+ summary sheet does not declare wt.% versus mol.% per row.  This
script is deliberately a research diagnostic only: it reports whether the
reported oxide fractions contain predictive signal, but never creates a
customer-facing service model or catalogue fact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold


OXIDES = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
COLUMN_TO_NAME = {
    "BZ": "SiO2", "CD": "Al2O3", "CQ": "B2O3", "CC": "Na2O", "CH": "K2O",
    "CK": "Li2O", "CG": "MgO", "CF": "CaO", "EA": "BaO", "CM": "ZnO",
    "CU": "TiO2", "CB": "ZrO2", "CA": "P2O5", "ZP": "CTEbelowTg", "AAW": "Doi",
}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def col_from_reference(reference: str) -> str:
    return re.match(r"[A-Z]+", reference).group(0)


def shared_strings(book: zipfile.ZipFile) -> list[str]:
    return ["".join(node.itertext()) for _, node in ET.iterparse(book.open("xl/sharedStrings.xml"), events=("end",)) if node.tag == NS + "si"]


def source_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as book:
        strings = shared_strings(book)
        for _, row in ET.iterparse(book.open("xl/worksheets/sheet1.xml"), events=("end",)):
            if row.tag != NS + "row" or row.attrib.get("r") == "1":
                continue
            record: dict[str, str] = {}
            for cell in row:
                name = COLUMN_TO_NAME.get(col_from_reference(cell.attrib.get("r", "")))
                value = cell.find(NS + "v")
                if name and value is not None:
                    record[name] = strings[int(value.text)] if cell.attrib.get("t") == "s" else value.text
            if record:
                rows.append(record)
            row.clear()
    return rows


def as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    accepted: list[dict[str, object]] = []
    rejected = Counter()
    for raw in source_rows(args.input):
        values = {oxide: as_float(raw.get(oxide)) or 0.0 for oxide in OXIDES}
        total = sum(values.values())
        cte = as_float(raw.get("CTEbelowTg"))
        doi = raw.get("Doi", "").strip()
        if not 90.0 <= total <= 101.0:
            rejected["reported_oxide_total_outside_90_101"] += 1
            continue
        if not doi:
            rejected["missing_doi"] += 1
            continue
        if cte is None or not 0.0 < cte < 30e-6:
            rejected["invalid_ctebelowtg"] += 1
            continue
        accepted.append({
            "doi": doi,
            "reported_total": total,
            "cte_below_tg_per_K": cte,
            "cte_below_tg_ppm_per_K": cte * 1e6,
            **{oxide: values[oxide] / total * 100.0 for oxide in OXIDES},
        })

    # Repeated records are retained only once when both composition and label
    # match.  DOI is the group, avoiding same-paper leakage across folds.
    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for row in accepted:
        key = tuple(round(float(row[oxide]), 8) for oxide in OXIDES) + (round(float(row["cte_below_tg_ppm_per_K"]), 8), str(row["doi"]))
        unique[key] = row
    rows = list(unique.values())
    groups = np.array([str(row["doi"]) for row in rows])
    x = np.array([[float(row[oxide]) for oxide in OXIDES] for row in rows])
    y = np.array([float(row["cte_below_tg_ppm_per_K"]) for row in rows])
    group_count = len(set(groups))
    if len(rows) < 50 or group_count < 5:
        raise SystemExit(f"not enough provenance-grouped CTE rows: rows={len(rows)}, DOI groups={group_count}")
    folds = min(5, group_count)
    cv = GroupKFold(n_splits=folds)
    predictions = np.full(len(rows), np.nan)
    for train, test in cv.split(x, y, groups):
        model = ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=0.9, random_state=20260902, n_jobs=-1)
        model.fit(x[train], y[train])
        predictions[test] = model.predict(x[test])
    final_model = ExtraTreesRegressor(n_estimators=600, min_samples_leaf=2, max_features=0.9, random_state=20260902, n_jobs=-1)
    final_model.fit(x, y)
    baseline = np.full(len(y), y.mean())
    for row, prediction in zip(rows, predictions):
        row["cv_prediction_ppm_per_K"] = prediction
        row["absolute_error_ppm_per_K"] = abs(float(row["cte_below_tg_ppm_per_K"]) - prediction)
        row["composition_basis_status"] = "unresolved_in_summary_sheet_research_only"
        row["source_id"] = "SRC-SCIGLASS-PLUS-2025"
        row["source_locator"] = f"DOI:{row['doi']}"
    args.output_dir.mkdir(parents=True)
    fields = ["doi", "reported_total", *OXIDES, "cte_below_tg_per_K", "cte_below_tg_ppm_per_K", "cv_prediction_ppm_per_K", "absolute_error_ppm_per_K", "composition_basis_status", "source_id", "source_locator"]
    write_csv(args.output_dir / "cte_research_rows.csv", fields, rows)
    joblib.dump({"model": final_model, "features": list(OXIDES), "unit": "ppm_per_K", "composition_basis_status": "unresolved_in_summary_sheet_research_only"}, args.output_dir / "cte_research_model.joblib")
    report = {
        "model": "ExtraTreesRegressor",
        "task": "reported oxide fractions to CTEbelowTg",
        "model_status": "research_baseline_not_customer_service_release",
        "source": "SRC-SCIGLASS-PLUS-2025",
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "input_rows_with_cte": len(accepted),
        "deduplicated_rows": len(rows),
        "doi_groups": group_count,
        "cv_scheme": f"GroupKFold(n_splits={folds}, group=DOI)",
        "metrics": {
            "mae_ppm_per_K": mean_absolute_error(y, predictions),
            "rmse_ppm_per_K": mean_squared_error(y, predictions) ** 0.5,
            "r2": r2_score(y, predictions),
            "mean_baseline_mae_ppm_per_K": mean_absolute_error(y, baseline),
        },
        "feature_importance": dict(sorted(zip(OXIDES, final_model.feature_importances_), key=lambda item: item[1], reverse=True)),
        "rejected_rows": dict(rejected),
        "limitations": [
            "The source summary sheet does not state wt.% or mol.% per row; normalized reported fractions are exploratory only.",
            "CTEbelowTg lacks a per-row temperature interval in this summary sheet; this model cannot substitute for CTE(T).",
            "No residual-stress, cooling-rate, TGV or package-reliability prediction is created.",
        ],
    }
    (args.output_dir / "training_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
