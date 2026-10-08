#!/usr/bin/env python3
"""Train a Tag-disjoint rupture-life baseline for Ni superalloy screening."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline

ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = ["cc", "ds", "sc"]


def numeric(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def heat_treatment_features(value: str) -> list[float]:
    output: list[float] = []
    for family, stages in (("solution", 3), ("precipitation", 4)):
        for stage in range(1, stages + 1):
            for suffix in ("temp_C", "time_h", "heating_C_per_h"):
                match = re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)", value or "")
                output.append(numeric(match.group(1)) if match else float("nan"))
    return output


def features(row: dict) -> list[float]:
    route = row["casting_code"]
    return [
        numeric(row["temperature_K"]), math.log10(numeric(row["stress_MPa"])),
        *[numeric(row.get(f"{element}_wt_percent")) for element in ELEMENTS],
        *[float(route == item) for item in ROUTES], numeric(row.get("casting_gradient_K_per_mm")),
        *heat_treatment_features(row.get("heat_treatment", "")),
    ]


def metrics(rows: list[dict]) -> dict:
    observed = np.array([row["observed_log10_h"] for row in rows])
    predicted = np.array([row["predicted_log10_h"] for row in rows])
    mae = float(mean_absolute_error(observed, predicted))
    return {
        "n": len(rows), "mae_log10_h": mae,
        "rmse_log10_h": float(math.sqrt(mean_squared_error(observed, predicted))),
        "r2_log10_h": float(r2_score(observed, predicted)) if len(rows) > 1 and len(set(observed)) > 1 else None,
        "typical_multiplicative_error_factor": float(10 ** mae),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    rows = read_csv(args.package / "rupture_life_admitted.csv")
    train = [row for row in rows if row["split"] == "train"]
    if len({row["tag"] for row in train}) < 20:
        raise SystemExit("insufficient independent training tags")
    x = np.array([features(row) for row in train])
    y = np.array([numeric(row["log10_rupture_life_h"]) for row in train])
    keep = np.any(~np.isnan(x), axis=0)
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("regressor", HistGradientBoostingRegressor(learning_rate=.05, max_leaf_nodes=12, min_samples_leaf=10, l2_regularization=4, max_iter=400, random_state=20260901)),
    ])
    model.fit(x[:, keep], y)
    predictions: list[dict] = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        predicted = float(model.predict(np.array([features(row)])[:, keep])[0])
        result = {
            "tag": row["tag"], "alloy_name": row["alloy_name"], "split": row["split"],
            "manufacturing_route": row["manufacturing_route"], "temperature_C": numeric(row["temperature_C"]),
            "stress_MPa": numeric(row["stress_MPa"]), "observed_rupture_life_h": numeric(row["rupture_life_h"]),
            "observed_log10_h": numeric(row["log10_rupture_life_h"]), "predicted_log10_h": predicted,
            "predicted_rupture_life_h": float(10 ** predicted), "source_row": row["source_row"],
            "reference_1_doi": row["reference_1_doi"],
        }
        predictions.append(result); grouped[result["split"]].append(result)
    report = {split: metrics(group) for split, group in sorted(grouped.items())}
    report["independent_tags"] = {split: len({row["tag"] for row in group}) for split, group in grouped.items()}
    args.output_dir.mkdir(parents=True)
    with (args.output_dir / "predictions.csv").open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(predictions[0])); writer.writeheader(); writer.writerows(predictions)
    feature_names = ["temperature_K", "log10_stress_MPa", *[f"{item}_wt_percent" for item in ELEMENTS], *[f"route__{item}" for item in ROUTES], "casting_gradient_K_per_mm", *[f"{family}_stage_{stage}_{suffix}" for family, count in (("solution", 3), ("precipitation", 4)) for stage in range(1, count + 1) for suffix in ("temp_C", "time_h", "heating_C_per_h")]]
    joblib.dump({"model": model, "feature_mask": keep.tolist(), "feature_names": feature_names}, args.output_dir / "rupture_life_model.joblib")
    summary = {
        "model": "HistGradientBoostingRegressor for log10 rupture life hours",
        "research_status": "exploratory screening baseline; not approved for engineering life prediction",
        "training_package": str(args.package),
        "target": "log10(rupture_life_h)",
        "inputs": "test temperature, applied stress, source wt.% composition, cast/DS/SC route, casting gradient and recorded heat-treatment stages",
        "split": "Tag-disjoint v1", "metrics": report,
        "boundaries": [
            "The prediction is conditional on temperature and applied stress; it is not a universal material life rating.",
            "Predicted rupture life is a screening estimate. The multiplicative error factor is derived from held-out Tag MAE in log space.",
            "No oxidation, thermo-mechanical fatigue, coating, geometry, environment or certification effect is represented.",
        ],
    }
    (args.output_dir / "training_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    test = report.get("test", {})
    (args.output_dir / "training_report.md").write_text(
        "# 镍基高温合金蠕变断裂寿命研究基线\n\n"
        f"独立 Tag 测试：n={test.get('n', 0)}，MAE={test.get('mae_log10_h', float('nan')):.3f} log10(h)，"
        f"典型误差倍率={test.get('typical_multiplicative_error_factor', float('nan')):.2f}×，R²={test.get('r2_log10_h')}。\n\n"
        "该模型用于在相同温度、载荷、路线和热处理假设下比较候选优先级，不用于寿命放行。\n",
        encoding="utf8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
