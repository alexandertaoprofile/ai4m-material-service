"""Isolated runtime for the Cambridge Ni-superalloy rupture-life baseline.

This is a screening evaluator, deliberately separate from the HEA at.% model.
Its input composition is wt.% and its result is conditional on temperature and
applied stress.
"""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models/ni_superalloy_cast_ds_sc_creep_rupture_v0/baseline_20260901_serving_v1"
PACKAGE_DIR = ROOT / "data/processed/ni_superalloy_training/20260901_cambridge_creep_rupture_v1"
ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTE_CODES = {"cast": "cc", "cc": "cc", "directionally_solidified": "ds", "ds": "ds", "single_crystal": "sc", "sc": "sc"}
_MODEL: dict | None = None
_REFERENCE: np.ndarray | None = None


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric") from None
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _model() -> dict:
    global _MODEL
    if _MODEL is None:
        _MODEL = joblib.load(MODEL_DIR / "rupture_life_model.joblib")
    return _MODEL


def _reference() -> np.ndarray:
    global _REFERENCE
    if _REFERENCE is None:
        with (PACKAGE_DIR / "rupture_life_admitted.csv").open(encoding="utf8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _REFERENCE = np.asarray([[float(row[f"{element}_wt_percent"]) for element in ELEMENTS] for row in rows], dtype=float)
    return _REFERENCE


def _composition(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ValueError("composition_wt_percent must be a non-empty wt.% object")
    raw = {str(element): _number(amount, f"composition_wt_percent.{element}") for element, amount in value.items()}
    unknown = sorted(set(raw) - set(ELEMENTS))
    if unknown:
        raise ValueError(f"unsupported elements for this model: {unknown}")
    if any(amount < 0 for amount in raw.values()):
        raise ValueError("composition_wt_percent must be non-negative")
    total = sum(raw.values())
    if 99.0 <= total <= 101.0:
        fractions = {element: amount / 100.0 for element, amount in raw.items()}
    elif .97 <= total <= 1.03:
        fractions = raw
    else:
        raise ValueError("composition_wt_percent must sum to about 100 wt.% (or 1.0 source fraction); values are not auto-normalized")
    return {element: fractions.get(element, 0.0) for element in ELEMENTS}


def _heat_treatment_features(value: str) -> list[float]:
    output: list[float] = []
    for family, stages in (("solution", 3), ("precipitation", 4)):
        for stage in range(1, stages + 1):
            for suffix in ("temp_C", "time_h", "heating_C_per_h"):
                match = re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)", value or "")
                output.append(float(match.group(1)) if match else float("nan"))
    return output


def _features(scope: dict, composition: dict[str, float], route: str) -> np.ndarray:
    temperature_C = _number(scope.get("test_temperature_C"), "test_temperature_C")
    stress_MPa = _number(scope.get("applied_stress_MPa"), "applied_stress_MPa")
    if temperature_C <= 0 or stress_MPa <= 0:
        raise ValueError("test_temperature_C and applied_stress_MPa must be positive")
    gradient = scope.get("casting_gradient_K_per_mm")
    gradient_value = float("nan") if gradient in (None, "") else _number(gradient, "casting_gradient_K_per_mm")
    heat_treatment = scope.get("heat_treatment")
    if not isinstance(heat_treatment, str) or not heat_treatment.strip():
        raise ValueError("heat_treatment is required; use the stage string format emitted by the model package")
    return np.asarray([[
        temperature_C + 273.15, math.log10(stress_MPa), *[composition[element] for element in ELEMENTS],
        *[float(route == item) for item in ("cc", "ds", "sc")], gradient_value,
        *_heat_treatment_features(heat_treatment),
    ]], dtype=float)


def _applicability(composition: dict[str, float], temperature_C: float, stress_MPa: float) -> dict:
    reference = _reference()
    vector = np.asarray([composition[element] for element in ELEMENTS])
    scale = np.maximum(reference.std(axis=0), .003)
    distance = float(np.sqrt((((reference - vector) / scale) ** 2).sum(axis=1)).min())
    level = "inside" if distance <= 2.0 else "boundary" if distance <= 4.0 else "outside"
    return {
        "level": level, "nearest_training_composition_distance": distance,
        "temperature_C": temperature_C, "applied_stress_MPa": stress_MPa,
        "meaning": "Composition proximity only; temperature, stress, route and heat-treatment conditions must also be engineering-feasible.",
    }


def evaluate(scope: dict) -> dict:
    composition = _composition(scope.get("composition_wt_percent"))
    route_key = str(scope.get("manufacturing_route", "")).strip().casefold()
    if route_key not in ROUTE_CODES:
        raise ValueError("manufacturing_route must be cast, directionally_solidified, or single_crystal")
    route = ROUTE_CODES[route_key]
    vector = _features(scope, composition, route)
    artifact = _model()
    mask = np.asarray(artifact["feature_mask"], dtype=bool)
    log10_life = float(artifact["model"].predict(vector[:, mask])[0])
    temperature_C = float(scope["test_temperature_C"])
    stress_MPa = float(scope["applied_stress_MPa"])
    return {
        "model_domain": "ni_superalloy_creep_rupture",
        "model_version": "ni_superalloy_cast_ds_sc_creep_rupture_v0 / baseline_20260901_serving_v1",
        "inputs": {
            "composition_wt_percent": {element: round(amount * 100.0, 6) for element, amount in composition.items() if amount > 0},
            "manufacturing_route": {"cc": "cast", "ds": "directionally_solidified", "sc": "single_crystal"}[route],
            "test_temperature_C": temperature_C, "applied_stress_MPa": stress_MPa,
            "heat_treatment": scope["heat_treatment"],
        },
        "rupture_life": {
            "predicted_log10_hours": log10_life,
            "predicted_hours": float(10 ** log10_life),
            "screening_error_factor": 1.8141,
            "error_basis": "independent Tag test MAE = 0.259 log10(h)",
        },
        "applicability_domain": _applicability(composition, temperature_C, stress_MPa),
        "scope": "Early screening of rupture-life priority under the stated temperature, stress, route and heat-treatment assumptions.",
    }


def dispatch(request: dict) -> dict:
    operation = request.get("operation")
    scope = request.get("constraints") or {}
    if operation == "evaluate":
        return evaluate(scope)
    if operation == "evaluate_batch":
        return {"results": [evaluate({**scope, **candidate}) for candidate in request.get("candidates", [])]}
    raise ValueError("ni_superalloy_creep_rupture currently supports evaluate and evaluate_batch")
