"""Runtime for the narrow, traceable 0--300 C chip-glass CTE pilot."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
VARIANTS = {
    "chip_glass_cte_family_v1": {
        "path": ROOT / "models/chip_glass_cte_family_v1_20260902/model.joblib",
        "label": "0-300°C",
    },
    "chip_glass_cte_25_300_family_v1": {
        "path": ROOT / "models/chip_glass_cte_25_300_family_v1_20260903/model.joblib",
        "label": "25-300°C",
    },
}
_ARTIFACTS: dict[str, dict[str, Any]] = {}


def _artifact(variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported chip-glass model domain: {variant}")
    if variant not in _ARTIFACTS:
        _ARTIFACTS[variant] = joblib.load(VARIANTS[variant]["path"])
    return _ARTIFACTS[variant]


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def evaluate(scope: dict[str, Any], variant: str) -> dict[str, Any]:
    if scope.get("composition_basis") != "mol_percent":
        raise ValueError("chip_glass_cte_family_v1 requires composition_basis='mol_percent'")
    raw = scope.get("composition_mol_percent")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("composition_mol_percent must be a non-empty oxide mol% object")
    artifact = _artifact(variant); features: list[str] = artifact["features"]
    unknown = sorted(set(map(str, raw)) - set(features))
    if unknown:
        raise ValueError(f"unsupported oxides for the current family model: {unknown}")
    values = {feature: _number(raw.get(feature, 0.0), f"composition_mol_percent.{feature}") for feature in features}
    total = sum(values.values())
    if not 99.0 <= total <= 101.0:
        raise ValueError("composition_mol_percent must sum to 99-101 mol%")
    bounds = artifact["report"]["training_min_max_mol_percent"]
    outside = [feature for feature, value in values.items() if value < bounds[feature][0] or value > bounds[feature][1]]
    if outside:
        return {"model_domain": variant, "mode": "outside_family_no_prediction",
                "inputs": {"composition_basis": "mol_percent", "composition_mol_percent": values, "total_mol_percent": total},
                "applicability_domain": {"level": "outside", "outside_recorded_bounds": outside,
                    "recorded_bounds_mol_percent": {feature: bounds[feature] for feature in outside}},
                "next_action": "将该配方作为新玻璃族进入带 0–300°C CTE、状态和热历史的实测验证；当前模型不对族外配方外推。"}
    vector = np.asarray([[values[feature] for feature in features]])
    prediction = float(artifact["model"].predict(vector)[0])
    report = artifact["report"]
    return {"model_domain": variant, "mode": "cte_family_screening",
            "model_version": report["model_version"],
            "inputs": {"composition_basis": "mol_percent", "composition_mol_percent": values, "total_mol_percent": total,
                       "property_definition": f"linear CTE, {VARIANTS[variant]['label']}"},
            f"cte_{report['property_name'].replace('CTE_linear_', '')}": {"prediction": prediction * 1e-6, "prediction_ppm_per_K": prediction, "unit": "1/K",
                                "screening_MAE_ppm_per_K": report["metrics"]["mae_ppm_per_K"],
                                "validation": report["validation"]},
            "applicability_domain": {"level": "inside", "family": "disclosed alkali-free alumino-borosilicate family",
                                       "source_tables": report["source_tables"]},
            "stress_interpretation": "该输出仅用于热失配的一项输入；制造残余应力还需退火/应变点、冷却曲线和应力测量，封装热应力还需 Cu/Si/RDL 堆叠、厚度和温度循环。",
            "not_predicted": ["residual stress", "Tg", "Young's modulus", "dielectric properties", "TGV manufacturability", "package reliability"]}


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("operation") == "evaluate":
        scope = request.get("constraints") or {}
        return evaluate(scope, scope.get("model_domain", "chip_glass_cte_family_v1"))
    raise ValueError("chip-glass CTE family models support evaluate only")
