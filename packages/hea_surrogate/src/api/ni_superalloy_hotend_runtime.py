"""Combined hot-end Ni-superalloy screening runtime.

The four targets retain independent models and validation boundaries; the
combined response is only a shared candidate card, never a mixed-label model.
"""
from __future__ import annotations

import math
import csv
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from src.api.ni_superalloy_creep_runtime import (
    ELEMENTS, ROOT, ROUTE_CODES, _composition, _heat_treatment_features,
    _number, evaluate as evaluate_creep,
)
from src.api.ni_superalloy_strength_features import m1_descriptor_values

M0_STRENGTH_DIR = ROOT / "models/ni_superalloy_cast_ds_sc_strength_v0/baseline_20260831_cambridge_serving_v2"
M1_STRENGTH_DIR = ROOT / "models/ni_superalloy_cast_ds_sc_strength_v0/m1_20260902_candidate"
ELONGATION_DIR = ROOT / "models/ni_superalloy_cast_ds_sc_elongation_v0/baseline_20260901_serving_v1"
ANCHOR_CSV = ROOT / "data/processed/ni_superalloy_training/20260831_cambridge_cast_ds_sc_full_v3/cast_ds_sc_short_time_strength_admitted.csv"
_STRENGTH: dict[str, dict] = {}
_ELONGATION: dict | None = None


def _temperature_support(temperature_C: float) -> dict[str, Any]:
    """Classify temperature coverage for the combined hot-end response."""
    if temperature_C <= 1093:
        return {"level": "inside", "label": "短时强度训练温区内"}
    if temperature_C <= 1100:
        return {
            "level": "boundary",
            "label": "接近短时强度训练上限（1093 °C）",
            "reference_temperature_C": 1093.0,
        }
    return {
        "level": "extrapolated_reference",
        "label": "高于短时强度训练温区，作为工况外推参考",
        "reference_temperature_C": 1093.0,
    }


def _strength_variant() -> str:
    """M1 is explicit opt-in for shadow/canary verification; M0 stays default."""
    variant = os.getenv("NI_SUPERALLOY_STRENGTH_VARIANT", "m0").strip().casefold()
    if variant not in {"m0", "m1_candidate"}:
        raise ValueError("NI_SUPERALLOY_STRENGTH_VARIANT must be m0 or m1_candidate")
    return variant


def _strength() -> dict:
    variant = _strength_variant()
    if variant not in _STRENGTH:
        if variant == "m0":
            _STRENGTH[variant] = joblib.load(M0_STRENGTH_DIR / "baseline_models.joblib")
        else:
            _STRENGTH[variant] = joblib.load(M1_STRENGTH_DIR / "m1_models.joblib")
    return _STRENGTH[variant]


def _elongation() -> dict:
    global _ELONGATION
    if _ELONGATION is None:
        _ELONGATION = joblib.load(ELONGATION_DIR / "elongation_model.joblib")
    return _ELONGATION


def _common(scope: dict) -> tuple[dict[str, float], str, float, str, float]:
    composition = _composition(scope.get("composition_wt_percent"))
    key = str(scope.get("manufacturing_route", "")).strip().casefold()
    if key not in ROUTE_CODES:
        raise ValueError("manufacturing_route must be cast, directionally_solidified, or single_crystal")
    temperature_C = _number(scope.get("test_temperature_C"), "test_temperature_C")
    if temperature_C <= 0:
        raise ValueError("test_temperature_C must be positive")
    heat_treatment = scope.get("heat_treatment")
    if not isinstance(heat_treatment, str) or not heat_treatment.strip():
        raise ValueError("heat_treatment is required; use the stage string format emitted by the model package")
    gradient = scope.get("casting_gradient_K_per_mm")
    gradient_value = float("nan") if gradient in (None, "") else _number(gradient, "casting_gradient_K_per_mm")
    return composition, ROUTE_CODES[key], temperature_C, heat_treatment, gradient_value


def _tensile_features(composition: dict[str, float], route: str, temperature_C: float, heat_treatment: str, _gradient: float) -> np.ndarray:
    """Recreate the compact v2 strength feature schema by saved column name."""
    stages = _heat_treatment_features(heat_treatment)
    solution = [(stages[index], stages[index + 1]) for index in range(0, 9, 3)]
    precipitation = [(stages[index], stages[index + 1]) for index in range(9, 21, 3)]
    valid = lambda value: value if math.isfinite(value) else float("nan")
    values = {
        "temperature_K": temperature_C + 273.15,
        **{f"{element}_wt_percent": composition[element] for element in ELEMENTS},
        **{f"route__{item}": float(route == item) for item in ("cc", "ds", "sc", "am/sebm", "am/lc")},
        "max_solution_temp_C": max((valid(temp) for temp, _ in solution), default=float("nan")),
        "sum_solution_time_h": sum(time for _, time in solution if math.isfinite(time)),
        "max_precipitation_temp_C": max((valid(temp) for temp, _ in precipitation), default=float("nan")),
        "sum_precipitation_time_h": sum(time for _, time in precipitation if math.isfinite(time)),
    }
    # M1 is schema-compatible with M0 and appends deterministic descriptors.
    # Updating this mapping is inert for M0 because its saved feature list does
    # not request any M1 field.
    values.update(m1_descriptor_values(composition))
    names = _strength()["feature_names"]
    return np.asarray([[values.get(name, float("nan")) for name in names]], dtype=float)


def _elongation_features(composition: dict[str, float], route: str, temperature_C: float, heat_treatment: str, gradient: float) -> np.ndarray:
    return np.asarray([[
        temperature_C + 273.15, *[composition[element] for element in ELEMENTS],
        *[float(route == item) for item in ("cc", "ds", "sc")], gradient,
        *_heat_treatment_features(heat_treatment),
    ]], dtype=float)


def evaluate(scope: dict) -> dict:
    composition, route, temperature_C, heat_treatment, gradient = _common(scope)
    tensile = _tensile_features(composition, route, temperature_C, heat_treatment, gradient)
    artifact = _strength(); strength = artifact["models"]
    short_strength: dict[str, dict] = {}
    default_mae = {
        "ultimate_tensile_strength": 111.0157643473382,
        "proof_strength_0p2": 85.8476861599025,
    }
    for target, name in (
        ("ultimate_tensile_strength", "ultimate_tensile_strength_MPa"),
        ("proof_strength_0p2", "proof_strength_0p2_MPa"),
    ):
        item = strength[target]
        mask = np.asarray(item["feature_mask"], dtype=bool)
        mae = float((artifact.get("screening_MAE_MPa") or {}).get(target, default_mae[target]))
        short_strength[name] = {"mean": float(item["model"].predict(tensile[:, mask])[0]), "screening_MAE_MPa": mae}
    elongation = _elongation()
    mask = np.asarray(elongation["feature_mask"], dtype=bool)
    elongation_percent = float(elongation["model"].predict(_elongation_features(composition, route, temperature_C, heat_treatment, gradient)[:, mask])[0])
    creep = None
    if scope.get("applied_stress_MPa") not in (None, ""):
        creep = evaluate_creep({**scope, "composition_wt_percent": {element: amount * 100.0 for element, amount in composition.items() if amount > 0}})
    return {
        "model_domain": "ni_superalloy_hot_end",
        "model_version": ("hot_end_ni_superalloy_screening_m1_candidate / 20260902" if _strength_variant() == "m1_candidate" else "hot_end_ni_superalloy_screening_v0 / 20260901"),
        "inputs": {
            "composition_wt_percent": {element: round(amount * 100.0, 6) for element, amount in composition.items() if amount > 0},
            "manufacturing_route": {"cc": "cast", "ds": "directionally_solidified", "sc": "single_crystal"}[route],
            "test_temperature_C": temperature_C, "heat_treatment": heat_treatment,
            "applied_stress_MPa": scope.get("applied_stress_MPa"),
        },
        "short_time_strength": short_strength,
        "tensile_ductility": {
            "elongation_percent": elongation_percent,
            "screening_MAE_percent": 3.0567379244938775,
            "status": "auxiliary_indicator",
            "message": "延伸率用于辅助比较拉伸延性；其验证集波动较大，不作为单独放行门槛。",
        },
        "creep_rupture": ({**creep["rupture_life"], "applicability_domain": creep["applicability_domain"]} if creep else {"status": "stress_required", "message": "补充施加载荷（MPa）后计算断裂寿命初筛。"}),
        "hardness": {"status": "not_available", "message": "剑桥数据集没有可训练的硬度标签，当前服务不输出硬度预测。"},
        "scope": "在指定路线、热处理、温度和（如提供）载荷条件下，对热端镍基候选进行早期比较。",
    }


def _bounds(scope: dict) -> dict[str, tuple[float, float]]:
    raw = scope.get("element_bounds_wt_percent") or {}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("element_bounds_wt_percent is required for proposal; values are [min, max] in wt.%")
    result: dict[str, tuple[float, float]] = {}
    for element, limit in raw.items():
        if element not in ELEMENTS or not isinstance(limit, (list, tuple)) or len(limit) != 2:
            raise ValueError("each element bound must be a supported element with [min, max]")
        lo, hi = _number(limit[0], f"{element}.min"), _number(limit[1], f"{element}.max")
        if lo < 0 or hi < lo or hi > 100:
            raise ValueError("element wt.% bounds must satisfy 0 <= min <= max <= 100")
        result[element] = (lo, hi)
    if "Ni" not in result:
        raise ValueError("Ni bounds are required because Ni is balanced to 100 wt.%")
    return result


def _anchors(route: str, bounds: dict[str, tuple[float, float]]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    with ANCHOR_CSV.open(encoding="utf8", newline="") as handle:
        for row in csv.DictReader(handle):
            if ROUTE_CODES.get(str(row.get("manufacturing_route", "")).casefold()) != route:
                continue
            composition = {element: float(row.get(f"{element}_wt_percent") or 0) * 100 for element in ELEMENTS}
            # A proposal may only use the user-approved element system.  This is
            # deliberately stricter than merely bounding the named elements.
            if any(amount > 1e-8 and element not in bounds for element, amount in composition.items()):
                continue
            if any(not (bounds[element][0] <= composition[element] <= bounds[element][1]) for element in bounds):
                continue
            output.setdefault(row["tag"], {"tag": row["tag"], "alloy_name": row.get("alloy_name") or row["tag"], "composition": composition})
    return list(output.values())


def _candidate_from_anchor(anchor: dict[str, Any], bounds: dict[str, tuple[float, float]], rng: np.random.Generator) -> dict[str, float] | None:
    composition = dict(anchor["composition"])
    variable = [element for element in bounds if element != "Ni" and composition[element] > 0]
    if not variable:
        return None
    # Local perturbation only: ±0.35 wt.% per existing solute, then Ni balance.
    for element in variable:
        composition[element] = round(float(np.clip(composition[element] + rng.uniform(-.35, .35), *bounds[element])), 5)
    composition["Ni"] = round(100.0 - sum(composition[element] for element in composition if element != "Ni"), 5)
    if not (bounds["Ni"][0] <= composition["Ni"] <= bounds["Ni"][1]):
        return None
    if any(not (bounds[element][0] <= composition[element] <= bounds[element][1]) for element in bounds):
        return None
    return {element: amount for element, amount in composition.items() if amount > 0}


def propose(scope: dict) -> dict:
    route_key = str(scope.get("manufacturing_route", "")).strip().casefold()
    if route_key not in ROUTE_CODES:
        raise ValueError("manufacturing_route must be cast, directionally_solidified, or single_crystal")
    route = ROUTE_CODES[route_key]
    temperature_C = _number(scope.get("test_temperature_C"), "test_temperature_C")
    heat_treatment = scope.get("heat_treatment")
    if not isinstance(heat_treatment, str) or not heat_treatment.strip():
        raise ValueError("heat_treatment is required for proposal")
    stress = _number(scope.get("applied_stress_MPa"), "applied_stress_MPa")
    if not (700 <= temperature_C <= 1800 and 80 <= stress <= 740):
        raise ValueError("proposal is limited to 700–1800 °C and 80–740 MPa")
    bounds = _bounds(scope)
    anchors = _anchors(route, bounds)
    if not anchors:
        raise ValueError("no route-matched Cambridge anchor alloy lies inside the requested element bounds")
    requested = int(scope.get("num_candidates", 120))
    requested = min(max(requested, 20), 300)
    raw_thresholds = scope.get("screening_thresholds") or {}
    if not isinstance(raw_thresholds, dict):
        raise ValueError("screening_thresholds must be an object")
    allowed_thresholds = {"uts_min_MPa", "proof_strength_min_MPa", "rupture_life_min_h"}
    if set(raw_thresholds) - allowed_thresholds:
        raise ValueError("unsupported nickel screening threshold")
    if any(isinstance(value, bool) for value in raw_thresholds.values()):
        raise ValueError("screening thresholds must be finite positive numbers")
    thresholds = {key: float(value) for key, value in raw_thresholds.items()}
    if any(not math.isfinite(value) or value <= 0 for value in thresholds.values()):
        raise ValueError("screening thresholds must be finite positive numbers")
    rng = np.random.default_rng(int(scope.get("random_seed", 20260901)))
    candidates: list[dict[str, Any]] = []
    attempts = 0
    while len(candidates) < requested and attempts < requested * 30:
        attempts += 1
        anchor = anchors[attempts % len(anchors)]
        proposed = _candidate_from_anchor(anchor, bounds, rng)
        if proposed is None:
            continue
        result = evaluate({**scope, "composition_wt_percent": proposed})
        creep = result["creep_rupture"]
        strength_values = result["short_time_strength"]
        uts_value = strength_values["ultimate_tensile_strength_MPa"]["mean"]
        proof_value = strength_values["proof_strength_0p2_MPa"]["mean"]
        life_value = creep["predicted_hours"]
        if (not all(math.isfinite(v) for v in (uts_value, proof_value, life_value, creep["predicted_log10_hours"]))
                or not uts_value >= proof_value >= 0 or uts_value <= 0 or life_value <= 0
                or creep.get("applicability_domain", {}).get("level") == "outside"):
            continue
        uts = result["short_time_strength"]["ultimate_tensile_strength_MPa"]
        proof = result["short_time_strength"]["proof_strength_0p2_MPa"]
        log_life = creep["predicted_log10_hours"]
        candidates.append({
            "candidate_id": f"NIH-{len(candidates)+1:03d}", "source_anchor": {"tag": anchor["tag"], "alloy_name": anchor["alloy_name"], "composition_wt_percent": {element: round(amount, 5) for element, amount in anchor["composition"].items() if amount > 0}},
            "composition_wt_percent": result["inputs"]["composition_wt_percent"],
            "ultimate_tensile_strength_MPa": uts, "proof_strength_0p2_MPa": proof,
            "creep_rupture": creep, "elongation_percent_auxiliary": result["tensile_ductility"],
            "applicability_domain": creep["applicability_domain"],
        })
    if not candidates:
        raise ValueError("no local candidates passed the current empirical-composition support check")
    values = np.asarray([[item["ultimate_tensile_strength_MPa"]["mean"] - item["ultimate_tensile_strength_MPa"]["screening_MAE_MPa"], item["proof_strength_0p2_MPa"]["mean"] - item["proof_strength_0p2_MPa"]["screening_MAE_MPa"], item["creep_rupture"]["predicted_log10_hours"] - .2586568] for item in candidates])
    lo, hi = values.min(axis=0), values.max(axis=0)
    normalized = (values - lo) / np.maximum(hi - lo, 1e-9)
    weights = np.asarray([.35, .20, .45])
    for item, score in zip(candidates, normalized @ weights):
        item["screening_score"] = round(float(score), 5)
        item["score_basis"] = "UTS/proof strength and log10 rupture-life lower-confidence screening values; elongation is auxiliary only."
    candidates.sort(key=lambda item: item["screening_score"], reverse=True)
    uts_kept = [item for item in candidates if item["ultimate_tensile_strength_MPa"]["mean"] >= thresholds.get("uts_min_MPa", 0)]
    proof_kept = [item for item in uts_kept if item["proof_strength_0p2_MPa"]["mean"] >= thresholds.get("proof_strength_min_MPa", 0)]
    life_kept = [item for item in proof_kept if item["creep_rupture"]["predicted_hours"] >= thresholds.get("rupture_life_min_h", 0)]
    shortlist = life_kept[: min(12, len(life_kept))]

    # Keep the strict screen separate from a useful diagnostic fallback.  A
    # near-miss is never represented as a passed candidate: it records the
    # absolute and relative gap to every user-selected gate so that an R&D
    # user can decide whether a requirement is negotiable.
    def _gate_gap(item: dict[str, Any]) -> dict[str, dict[str, float]]:
        observed = {
            "uts_min_MPa": float(item["ultimate_tensile_strength_MPa"]["mean"]),
            "proof_strength_min_MPa": float(item["proof_strength_0p2_MPa"]["mean"]),
            "rupture_life_min_h": float(item["creep_rupture"]["predicted_hours"]),
        }
        return {
            key: {
                "target": thresholds[key],
                "observed": value,
                "shortfall": max(0.0, thresholds[key] - value),
                "relative_shortfall": max(0.0, thresholds[key] - value) / thresholds[key],
            }
            for key, value in observed.items() if key in thresholds
        }

    for item in candidates:
        gaps = _gate_gap(item)
        item["strict_screening"] = {
            "passed": all(value["shortfall"] <= 1e-9 for value in gaps.values()),
            "gaps": gaps,
            "total_relative_shortfall": round(sum(value["relative_shortfall"] for value in gaps.values()), 6),
        }
    nearest_candidates = sorted(
        candidates,
        key=lambda item: (item["strict_screening"]["total_relative_shortfall"], -item["screening_score"]),
    )[:3]
    temperature_support = _temperature_support(temperature_C)
    reference_candidates = candidates[:5] if not shortlist and temperature_support["level"] != "inside" else []
    return {
        "model_domain": "ni_superalloy_hot_end", "operation": "propose", "model_version": "hot_end_ni_superalloy_screening_v0 / 20260901",
        "screening_conditions": {"manufacturing_route": {"cc": "cast", "ds": "directionally_solidified", "sc": "single_crystal"}[route], "test_temperature_C": temperature_C, "applied_stress_MPa": stress, "heat_treatment": heat_treatment, "temperature_support": temperature_support},
        "sampling": {
            "source_anchors": len(anchors), "generated": len(candidates), "attempts": attempts,
            "method": "route-matched Cambridge anchors with local existing-solute perturbations and Ni balance; no new elements are introduced",
            "screening_thresholds": thresholds,
            "funnel_stages": [
                {"label": "满足成分、路线与工况约束", "count": len(candidates)},
                *([{"label": f"短时 UTS ≥ {thresholds['uts_min_MPa']:g} MPa", "count": len(uts_kept)}] if "uts_min_MPa" in thresholds else []),
                *([{"label": f"0.2% 屈服 ≥ {thresholds['proof_strength_min_MPa']:g} MPa", "count": len(proof_kept)}] if "proof_strength_min_MPa" in thresholds else []),
                *([{"label": f"蠕变寿命 ≥ {thresholds['rupture_life_min_h']:g} h", "count": len(life_kept)}] if "rupture_life_min_h" in thresholds else []),
                {"label": "综合排序优先短名单", "count": len(shortlist)},
            ],
        },
        "initial_candidates": shortlist,
        "nearest_candidates": nearest_candidates if not shortlist else [],
        "reference_candidates": reference_candidates,
        "all_candidates": candidates,
        "limitations": ["Screening results compare candidates under the stated route, heat treatment, temperature and stress only.", "Hardness is not output because the source set has no trainable hardness labels.", "Candidates require CALPHAD/oxidation assessment and targeted mechanical testing before engineering release."],
    }


def dispatch(request: dict) -> dict:
    operation = request.get("operation")
    scope = request.get("constraints") or {}
    if operation == "evaluate":
        return evaluate(scope)
    if operation == "evaluate_batch":
        return {"results": [evaluate({**scope, **candidate}) for candidate in request.get("candidates", [])]}
    if operation == "propose":
        return propose(scope)
    raise ValueError("ni_superalloy_hot_end currently supports evaluate, evaluate_batch and propose")
