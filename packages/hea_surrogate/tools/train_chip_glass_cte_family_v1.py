#!/usr/bin/env python3
"""Fit the traceable US20250026678A1 0--300 C CTE family model.

This is a deliberately narrow first service lane: oxide mol% compositions in
the disclosed alkali-free alumino-borosilicate family, evaluated only for the
same 0--300 C linear-CTE definition.  Validation holds out whole patent tables
to avoid testing a row against close variants from its own table.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold


CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
OTHER = re.compile(r"([A-Za-z][A-Za-z0-9]*)=([0-9]+(?:\.[0-9]+)?)\s+mol%")


def rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    raw = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    names = set(CORE)
    for row in raw:
        names.update(name for name, _ in OTHER.findall(row.get("other_oxides_text", "")))
    return raw, sorted(names)


def vector(row: dict[str, str], features: list[str]) -> list[float]:
    values = {feature: float(row.get(feature, "0") or 0.0) for feature in CORE}
    values.update({name: float(value) for name, value in OTHER.findall(row.get("other_oxides_text", ""))})
    return [values.get(feature, 0.0) for feature in features]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--property-name", default="CTE_linear_0_to_300C")
    parser.add_argument("--temperature-range", default="0-300 C")
    parser.add_argument("--model-version", default="chip_glass_cte_family_v1_20260902")
    parser.add_argument("--family-description", default="disclosed alkali-free alumino-borosilicate family")
    parser.add_argument("--evidence-description", default="US20250026678A1 Tables 2-12; CTE definition and method stated in the publication; retained PDF and HTML table snapshot.")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    source, features = rows(args.input)
    data = [row for row in source if row.get("property_name") == args.property_name and row.get("composition_basis") == "mol_percent"]
    if len(data) < 50:
        raise SystemExit(f"need at least 50 admitted rows for {args.property_name}, got {len(data)}")
    x = np.asarray([vector(row, features) for row in data])
    y = np.asarray([float(row["property_value"]) * 1e6 for row in data])
    table_groups = [re.search(r"Table (\d+)", row["source_locator"]).group(1) for row in data]
    # Some patents publish one wide table in serial 8-example blocks.  When
    # there is only one source table, retain those blocks as the independent
    # validation units rather than falling back to random row splitting.
    if len(set(table_groups)) == 1:
        groups = np.asarray([f"block_{(int(re.search(r'example (\d+)', row['source_locator']).group(1)) - 1) // 8 + 1}" for row in data])
    else:
        groups = np.asarray(table_groups)
    cv = GroupKFold(n_splits=len(set(groups)))
    prediction = np.full(len(data), np.nan)
    for train, test in cv.split(x, y, groups):
        model = ExtraTreesRegressor(n_estimators=600, min_samples_leaf=2, max_features=0.9, random_state=20260902, n_jobs=-1)
        model.fit(x[train], y[train]); prediction[test] = model.predict(x[test])
    final = ExtraTreesRegressor(n_estimators=1000, min_samples_leaf=2, max_features=0.9, random_state=20260902, n_jobs=-1).fit(x, y)
    errors = np.abs(y - prediction)
    for row, pred, error in zip(data, prediction, errors):
        row["cv_prediction_ppm_per_K"] = f"{pred:.6f}"; row["absolute_error_ppm_per_K"] = f"{error:.6f}"
    args.output_dir.mkdir(parents=True)
    fields = list(data[0]) + ["cv_prediction_ppm_per_K", "absolute_error_ppm_per_K"]
    with (args.output_dir / "training_rows.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(data)
    report = {"model_version": args.model_version, "status": "service_pilot_narrow_family", "model": "ExtraTreesRegressor",
              "task": f"oxide mol% to linear CTE at {args.temperature_range}", "property_name": args.property_name, "unit": "ppm/K", "training_rows": len(data), "independent_compositions": len({row['glass_id'] for row in data}),
              "source_tables": sorted(set(groups)), "validation": "leave-one-patent-table-out GroupKFold",
              "metrics": {"mae_ppm_per_K": mean_absolute_error(y, prediction), "rmse_ppm_per_K": mean_squared_error(y, prediction) ** 0.5,
                          "r2": r2_score(y, prediction), "max_absolute_error_ppm_per_K": float(errors.max())},
              "features_mol_percent": features, "training_min_max_mol_percent": {feature: [float(x[:, i].min()), float(x[:, i].max())] for i, feature in enumerate(features)},
              "scope": f"Only the {args.family_description} and the same {args.temperature_range} ASTM-E228 CTE definition. It is not a residual-stress, TGV, RF, Tg, modulus, or package-reliability model.",
              "evidence": args.evidence_description}
    (args.output_dir / "training_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "MODEL_CARD.md").write_text(f"# 芯片玻璃基板 CTE 家族模型 v1\n\n该模型仅用于已公开的{args.family_description}，在 **{args.temperature_range}、ASTM E228 定义** 下对线性 CTE 作候选筛选。输入为氧化物 mol%。输出不是残余内应力，也不替代封装翘曲、TGV 制程、射频或可靠性验证。\n\n验证按专利表整体留出，避免将同一表内的近邻变体同时置于训练与测试。模型的可用范围和误差见 `training_report.json`。\n", encoding="utf-8")
    joblib.dump({"model": final, "features": features, "training_vectors": x, "training_targets_ppm_per_K": y, "report": report}, args.output_dir / "model.joblib")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
