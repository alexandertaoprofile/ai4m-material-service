"""Serving runtime for reusable-rocket austenitic stainless candidate screening.

The numerical model is deliberately limited to short-time tensile properties
between 293 and 1273 K.  Cryogenic reference records are returned separately,
never used to manufacture a free-composition low-temperature prediction.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees"
MODEL_PATH = MODEL_DIR / "austenitic_design_v2_extratrees.joblib"
DOMAIN_PATH = MODEL_DIR / "applicability_domain.json"
TRAINING = ROOT / "data/processed/reusable_rocket_stainless_design/20260902_v1/map_model_training_observations.csv"
CRYO_REFERENCE = ROOT / "data/processed/reusable_rocket_stainless_reference_baseline/20260902_v0/admitted_tensile_observations.csv"
ELS = ["Cr","Ni","Mo","Mn","Si","Nb","Ti","Zr","Ta","V","W","Cu","N","C","B","P","S","Co","Al","Sn","Pb"]
_ARTIFACT: dict | None = None
_DOMAIN: dict | None = None
_REFERENCE_VECTOR: np.ndarray | None = None
_IMPUTER: SimpleImputer | None = None
_SCALER: StandardScaler | None = None

# Visible platform defaults. They define the condition of a first comparison,
# not a material specification and not a substitute for a user's process card.
DEFAULT_PROCESS = {
    "material_state": "solution_annealed",
    "solution_treatment_temperature_K": 1323.0,
    "solution_treatment_time_s": 3600.0,
    "quench": "water",
    "product_form_code": 1,
    "melting_route_code": 1,
}
DEFAULT_BOUNDS = {
    "Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0],
    "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08],
}
VALIDATION_MAE = {"yield_0p2_MPa": 16.2076, "uts_MPa": 16.2633, "elongation_pct": 3.2922}


def _number(value: Any, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _artifact() -> dict:
    global _ARTIFACT
    if _ARTIFACT is None:
        _ARTIFACT = joblib.load(MODEL_PATH)
    return _ARTIFACT


def _domain() -> dict:
    global _DOMAIN
    if _DOMAIN is None:
        _DOMAIN = json.loads(DOMAIN_PATH.read_text(encoding="utf-8"))
    return _DOMAIN


def _rows(compositions: list[dict[str, float]], temperature_K: float, process: dict[str, Any]) -> pd.DataFrame:
    """Build the saved estimator schema once for a candidate batch."""
    artifact = _artifact()
    records = [{
        **composition,
        "Solution_treatment_temperature": process["solution_treatment_temperature_K"],
        "Solution_treatment_time(s)": process["solution_treatment_time_s"],
        "Water_Quenched_after_s.t.": float(process["quench"] == "water"),
        "Air_Quenched_after_s.t.": float(process["quench"] == "air"),
        "Grains mm-2": np.nan, "Size of ingot": np.nan, "Temperature (K)": temperature_K,
        "Type of melting": process["melting_route_code"], "Product form": process["product_form_code"],
    } for composition in compositions]
    return pd.DataFrame(records)[artifact["features"]["numeric"] + artifact["features"]["categorical"]]


def _applicability_many(compositions: list[dict[str, float]], temperature_K: float) -> list[dict[str, Any]]:
    """Vectorized equivalent of the per-candidate empirical domain gate."""
    reference, imputer, scaler = _distance_state(); domain = _domain()
    raw = pd.DataFrame(compositions, columns=ELS)
    vectors = scaler.transform(imputer.transform(raw))
    distances = np.sqrt(((vectors[:, None, :] - reference[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
    limits = domain["composition_nearest_neighbour_distance"]
    result: list[dict[str, Any]] = []
    for composition, distance in zip(compositions, distances):
        level = "inside" if distance <= limits["review_threshold_p95"] else "boundary" if distance <= limits["block_threshold_p99"] else "outside"
        out_of_range = [element for element, bounds in domain["composition_nonzero_bounds_wt_pct"].items() if composition[element] and not (bounds["minimum_wt_pct"] <= composition[element] <= bounds["maximum_wt_pct"])]
        if out_of_range: level = "outside"
        result.append({"level": level, "nearest_training_composition_distance": round(float(distance), 4), "review_threshold": limits["review_threshold_p95"], "block_threshold": limits["block_threshold_p99"], "out_of_recorded_nonzero_bounds": out_of_range, "temperature_K": temperature_K, "meaning": "Composition-neighbour screening over recorded elemental wt.% fields; it does not establish weld, fatigue, fracture or LOX suitability."})
    return result


def _predict_many(compositions: list[dict[str, float]], temperature_K: float, process: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    """Run the three saved ExtraTrees models in three batch calls, not 3*N calls."""
    artifact = _artifact(); rows = _rows(compositions, temperature_K, process)
    columns = {target: model.predict(rows) for target, model in artifact["models"].items()}
    return [{target: {"mean": float(values[index]), "screening_MAE": VALIDATION_MAE[target], "unit": "%" if target == "elongation_pct" else "MPa"} for target, values in columns.items()} for index in range(len(compositions))]


def _distance_state() -> tuple[np.ndarray, SimpleImputer, StandardScaler]:
    global _REFERENCE_VECTOR, _IMPUTER, _SCALER
    if _REFERENCE_VECTOR is None:
        raw = pd.read_csv(TRAINING)[ELS].drop_duplicates().reset_index(drop=True)
        _IMPUTER = SimpleImputer(strategy="median").fit(raw)
        _SCALER = StandardScaler().fit(_IMPUTER.transform(raw))
        _REFERENCE_VECTOR = _SCALER.transform(_IMPUTER.transform(raw))
    return _REFERENCE_VECTOR, _IMPUTER, _SCALER  # type: ignore[return-value]


def _composition(value: Any) -> tuple[dict[str, float], float]:
    if not isinstance(value, dict) or not value:
        raise ValueError("composition_wt_percent must be a non-empty wt.% object")
    raw = {str(k): _number(v, f"composition_wt_percent.{k}") for k, v in value.items()}
    unknown = sorted(set(raw) - set(ELS) - {"Fe"})
    if unknown:
        raise ValueError(f"unsupported elements for reusable_rocket_stainless: {unknown}")
    if any(v < 0 for v in raw.values()):
        raise ValueError("composition_wt_percent must be non-negative")
    solutes = sum(v for k, v in raw.items() if k != "Fe")
    stated_fe = raw.get("Fe")
    if stated_fe is not None and abs(stated_fe - (100.0 - solutes)) > .15:
        raise ValueError("Fe must be the balance: 100 minus the listed solutes (±0.15 wt.%)")
    if not (10.0 <= solutes <= 60.0):
        raise ValueError("listed solutes must total 10–60 wt.% for the current austenitic-stainless model")
    if raw.get("Cr", 0) <= 0 or raw.get("Ni", 0) <= 0:
        raise ValueError("Cr and Ni must be specified for this austenitic-stainless service")
    return {element: raw.get(element, 0.0) for element in ELS}, 100.0 - solutes


def _process(scope: dict) -> tuple[dict[str, Any], list[str]]:
    supplied = scope.get("processing") or {}
    if supplied and not isinstance(supplied, dict):
        raise ValueError("processing must be an object")
    supplied = supplied or {}
    values = dict(DEFAULT_PROCESS)
    values.update({k: v for k, v in supplied.items() if v not in (None, "")})
    for key in ("solution_treatment_temperature_K", "solution_treatment_time_s"):
        values[key] = _number(values[key], key)
    if values["solution_treatment_temperature_K"] <= 0 or values["solution_treatment_time_s"] < 0:
        raise ValueError("solution treatment temperature must be positive and time non-negative")
    quench = str(values.get("quench", "")).casefold()
    if quench not in {"water", "air"}:
        raise ValueError("processing.quench must be water or air")
    values["quench"] = quench
    for key in ("product_form_code", "melting_route_code"):
        values[key] = int(_number(values[key], key))
    defaults = [key for key in DEFAULT_PROCESS if key not in supplied or supplied.get(key) in (None, "")]
    return values, defaults


def _row(composition: dict[str, float], temperature_K: float, process: dict[str, Any]) -> pd.DataFrame:
    record: dict[str, Any] = {**composition, "Solution_treatment_temperature": process["solution_treatment_temperature_K"],
        "Solution_treatment_time(s)": process["solution_treatment_time_s"],
        "Water_Quenched_after_s.t.": float(process["quench"] == "water"),
        "Air_Quenched_after_s.t.": float(process["quench"] == "air"), "Grains mm-2": np.nan,
        "Size of ingot": np.nan, "Temperature (K)": temperature_K,
        "Type of melting": process["melting_route_code"], "Product form": process["product_form_code"]}
    artifact = _artifact()
    return pd.DataFrame([record])[artifact["features"]["numeric"] + artifact["features"]["categorical"]]


def _applicability(composition: dict[str, float], temperature_K: float) -> dict[str, Any]:
    domain = _domain(); reference, imputer, scaler = _distance_state()
    vector = scaler.transform(imputer.transform(pd.DataFrame([composition], columns=ELS)))[0]
    distance = float(np.sqrt(((reference - vector) ** 2).sum(axis=1)).min())
    limits = domain["composition_nearest_neighbour_distance"]
    level = "inside" if distance <= limits["review_threshold_p95"] else "boundary" if distance <= limits["block_threshold_p99"] else "outside"
    out_of_range = []
    for element, bounds in domain["composition_nonzero_bounds_wt_pct"].items():
        value = composition[element]
        if value and not (bounds["minimum_wt_pct"] <= value <= bounds["maximum_wt_pct"]):
            out_of_range.append(element)
    if out_of_range:
        level = "outside"
    return {"level": level, "nearest_training_composition_distance": round(distance, 4),
        "review_threshold": limits["review_threshold_p95"], "block_threshold": limits["block_threshold_p99"],
        "out_of_recorded_nonzero_bounds": out_of_range, "temperature_K": temperature_K,
        "meaning": "Composition-neighbour screening over recorded elemental wt.% fields; it does not establish weld, fatigue, fracture or LOX suitability."}


def _cryo_reference(temperature_K: float) -> dict[str, Any]:
    rows = pd.read_csv(CRYO_REFERENCE)
    rows["distance"] = (rows["test_temperature_K"] - temperature_K).abs()
    nearest = rows.sort_values("distance").groupby("alloy_reference", as_index=False).first().head(6)
    return {"status": "reference_only", "target_temperature_K": temperature_K,
        "message": "当前温区使用 301/304L 可追溯参考与试验优先级，不生成自由配方强度预测。",
        "nearest_records": nearest[["alloy_reference","test_temperature_K","yield_0p2_MPa","uts_MPa","elongation_pct","source_locator"]].rename(columns={"alloy_reference":"material_id","test_temperature_K":"temperature_K","yield_0p2_MPa":"yield_MPa"}).to_dict("records"),
        "next_validation": ["目标温度母材拉伸", "焊缝与热影响区拉伸/低温韧性", "低周疲劳", "LOX 相容性"]}


def evaluate(scope: dict) -> dict[str, Any]:
    temperature_K = _number(scope.get("test_temperature_K"), "test_temperature_K")
    composition, fe = _composition(scope.get("composition_wt_percent"))
    if temperature_K < 293:
        return {"model_domain": "reusable_rocket_stainless", "mode": "cryogenic_reference", "inputs": {"composition_wt_percent": {**{k: v for k,v in composition.items() if v}, "Fe": round(fe, 6)}, "test_temperature_K": temperature_K}, "cryogenic_reference": _cryo_reference(temperature_K), "not_predicted": ["free-composition low-temperature tensile properties", "weld performance", "fatigue", "fracture toughness", "LOX compatibility"]}
    if temperature_K > 1273:
        raise ValueError("test_temperature_K must be within 293–1273 K for short-time tensile screening")
    process, defaults = _process(scope)
    row = _row(composition, temperature_K, process)
    artifact = _artifact(); predictions = {}
    for target, model in artifact["models"].items():
        predictions[target] = {"mean": float(model.predict(row)[0]), "screening_MAE": VALIDATION_MAE[target], "unit": "%" if target == "elongation_pct" else "MPa"}
    return {"model_domain": "reusable_rocket_stainless", "mode": "short_time_tensile_screening", "model_version": artifact["model_version"],
        "inputs": {"composition_wt_percent": {**{k: round(v,6) for k,v in composition.items() if v}, "Fe": round(fe, 6)}, "test_temperature_K": temperature_K, "processing": process, "platform_defaults_used": defaults},
        "short_time_tensile": predictions, "applicability_domain": _applicability(composition, temperature_K),
        "verification_focus": ["cold-work amount and grain size confirmation", "weld/HAZ properties when applicable", "fatigue and fracture toughness", "LOX compatibility"],
        "scope": "Candidate screening only: 293–1273 K short-time tensile prediction within the recorded austenitic-stainless neighbourhood."}


def _bounds(scope: dict) -> dict[str, tuple[float, float]]:
    raw = scope.get("element_bounds_wt_percent") or DEFAULT_BOUNDS
    if not isinstance(raw, dict):
        raise ValueError("element_bounds_wt_percent must be an object")
    bounds: dict[str, tuple[float, float]] = {}
    for element, pair in raw.items():
        if element == "Fe":
            continue
        if element not in ELS or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("each element bound must be a supported element with [min, max] wt.%")
        low, high = _number(pair[0], f"{element}.min"), _number(pair[1], f"{element}.max")
        if low < 0 or high < low:
            raise ValueError("element wt.% bounds must satisfy 0 <= min <= max")
        bounds[element] = (low, high)
    if not {"Cr", "Ni"}.issubset(bounds):
        raise ValueError("Cr and Ni bounds are required")
    if sum(low for low, _ in bounds.values()) > 60 or sum(high for _, high in bounds.values()) < 10:
        raise ValueError("element bounds do not overlap the current 10–60 wt.% solute support")
    return bounds


def propose(scope: dict) -> dict[str, Any]:
    temperature_K = _number(scope.get("test_temperature_K"), "test_temperature_K")
    if temperature_K < 293:
        return {"model_domain": "reusable_rocket_stainless", "operation": "propose", "mode": "cryogenic_reference", "sampling": {"generated": 0, "feasible": 0}, "cryogenic_reference": _cryo_reference(temperature_K), "user_action": "先以 301/304L 参考路线确定低温拉伸、焊接与韧性试验矩阵；补入可追溯低温成分—工艺标签后再开放自由配方筛选。"}
    if temperature_K > 1273:
        raise ValueError("test_temperature_K must be within 293–1273 K")
    bounds = _bounds(scope); process, defaults = _process(scope)
    requested = min(max(int(scope.get("num_candidates", 40)), 20), 80)
    rng = np.random.default_rng(int(scope.get("random_seed", 20260902)))
    sampled: list[tuple[int, dict[str, float]]] = []
    # Generate the deterministic attempt stream first.  We then evaluate only
    # the prefix that can enter the retained set, preserving the same seed,
    # attempt count, gate and candidate order as the former scalar loop.
    for attempt_number in range(1, requested * 30 + 1):
        composition = {e: float(rng.uniform(low, high)) for e, (low, high) in bounds.items()}
        total = sum(composition.values())
        if not (10 <= total <= 60):
            continue
        sampled.append((attempt_number, composition))
    # ``evaluate`` normalizes sparse user input to the complete recorded
    # element schema before both model and domain calls; retain that exact
    # convention for the batch path.
    sampled_compositions = [{element: composition.get(element, 0.0) for element in ELS} for _, composition in sampled]
    domains = _applicability_many(sampled_compositions, temperature_K)
    accepted_indices = [index for index, domain in enumerate(domains) if domain["level"] != "outside"][:requested]
    if not accepted_indices:
        raise ValueError("no sampled composition passed the current empirical applicability-domain gate")
    last_index = accepted_indices[-1]
    # Attempt count includes raw element-bound samples, just as before.  The
    # current bounds happen to accept all of them, but keep the accounting
    # explicit for future wider user-defined windows.
    attempts = sampled[last_index][0]
    element_bound_samples = attempts
    fe_balance_feasible = last_index + 1
    accepted = [sampled_compositions[index] for index in accepted_indices]
    predictions = _predict_many(accepted, temperature_K, process)
    candidates = []
    for composition, domain, tensile in zip(accepted, [domains[index] for index in accepted_indices], predictions):
        fe = 100.0 - sum(composition.values())
        candidates.append({"candidate_id": f"RSS-{len(candidates)+1:03d}", "composition_wt_percent": {**{element: round(value, 6) for element, value in composition.items() if value}, "Fe": round(fe, 6)}, "short_time_tensile": tensile, "applicability_domain": domain})
    values = np.asarray([[c["short_time_tensile"]["yield_0p2_MPa"]["mean"] - VALIDATION_MAE["yield_0p2_MPa"], c["short_time_tensile"]["uts_MPa"]["mean"] - VALIDATION_MAE["uts_MPa"], c["short_time_tensile"]["elongation_pct"]["mean"] - VALIDATION_MAE["elongation_pct"]] for c in candidates])
    normalised = (values - values.min(axis=0)) / np.maximum(values.max(axis=0) - values.min(axis=0), 1e-9)
    objectives = scope.get("objectives") or {}
    weights = np.asarray([float(objectives.get("yield_strength", 1)), float(objectives.get("uts", 1)), float(objectives.get("elongation", 1))])
    weights = weights / weights.sum()
    for item, score in zip(candidates, normalised @ weights):
        item["screening_score"] = round(float(score), 5)
        item["score_basis"] = "Normalised lower-confidence short-time tensile screening values; used only to rank this task's candidates."
    # A data-neighbourhood candidate is always more useful to the customer
    # than a marginally higher score at the empirical boundary.  Boundary
    # candidates remain in the returned shortlist as exploratory alternatives.
    candidates.sort(key=lambda item: (item["applicability_domain"]["level"] != "inside", -item["screening_score"]))
    inside_count = sum(item["applicability_domain"]["level"] == "inside" for item in candidates)
    priority_count = min(5, inside_count)
    return {"model_domain": "reusable_rocket_stainless", "operation": "propose", "mode": "short_time_tensile_screening", "model_version": _artifact()["model_version"],
        "screening_conditions": {"test_temperature_K": temperature_K, "processing": process, "platform_defaults_used": defaults, "low_temperature_verification_K": scope.get("low_temperature_verification_K", [90, 111])},
        "search_space": {"element_bounds_wt_percent": {e: list(v) for e,v in bounds.items()}, "Fe": "balance to 100 wt.%"},
        "sampling": {"generated": len(candidates), "feasible": len(candidates), "attempts": attempts, "method": "bounded random candidate screening with empirical composition-neighbour gate",
            "funnel_stages": [
                {"label": "元素 wt.% 边界采样", "count": element_bound_samples},
                {"label": "Fe 平衡可行（溶质总量 10–60 wt.%）", "count": fe_balance_feasible},
                {"label": "经验成分适用域内或边界", "count": len(candidates)},
                {"label": "训练成分邻域内", "count": inside_count},
                {"label": "强度—延性综合优先", "count": priority_count},
            ]},
        "initial_candidates": candidates[:12], "all_candidates": candidates,
        "limitations": ["Below 293 K the service switches to traceable 301/304L references and verification planning.", "Cold-work amount and grain size are not available as systematic model inputs.", "Weld, fatigue, fracture toughness and LOX compatibility require separate validation."],
        "verification_focus": ["confirm process state", "target-temperature tensile testing", "weld/HAZ testing if applicable", "cryogenic toughness, fatigue and LOX compatibility"]}


def dispatch(request: dict) -> dict:
    operation = request.get("operation"); scope = request.get("constraints") or {}
    if operation == "evaluate":
        return evaluate(scope)
    if operation == "evaluate_batch":
        return {"results": [evaluate({**scope, **candidate}) for candidate in request.get("candidates", [])]}
    if operation == "propose":
        return propose(scope)
    raise ValueError("reusable_rocket_stainless supports evaluate, evaluate_batch and propose")
