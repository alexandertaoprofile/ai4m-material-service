"""Model-only runtime. Keep FastAPI/LLM concerns out of this module."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.composition.parser import parse_composition
from src.models.train_baselines import _features

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
_ARTIFACTS: dict[str, dict] = {}

# Kept with the deployed model rather than inferred from a single request.
# Yield-strength records have substantial temperature coverage; hardness is
# overwhelmingly ambient-temperature data and must not drive high-temperature
# composition ranking.
_TEMPERATURE_EVIDENCE = {
    "yield_strength": {
        "known_records": 1065,
        "range_C": [-268.8, 1600.0],
        "status": "covered",
        "message": "实验屈服强度数据覆盖多个温度点；结果仍应结合成分和工艺适用域解读。",
    },
    "hardness": {
        "known_records": 414,
        "ambient_records": 385,
        "range_C": [25.0, 1000.0],
    },
}


def _temperature_support(task: str, temperature_C: float) -> dict:
    """Expose property-specific temperature evidence without changing fields.

    The numeric hardness ensemble remains in the response for API backwards
    compatibility, but it is intentionally not a high-temperature screening
    criterion unless a future data refresh supplies enough such labels.
    """
    temperature = float(temperature_C)
    if task == "yield_strength":
        return {**_TEMPERATURE_EVIDENCE[task], "requested_temperature_C": temperature}
    ambient = 15.0 <= temperature <= 40.0
    if ambient:
        return {
            **_TEMPERATURE_EVIDENCE[task],
            "requested_temperature_C": temperature,
            "status": "ambient_supported",
            "usable_for_ranking": True,
            "message": "硬度数据以室温测试为主，可作为室温候选比较指标。",
        }
    return {
        **_TEMPERATURE_EVIDENCE[task],
        "requested_temperature_C": temperature,
        "status": "high_temperature_limited",
        "usable_for_ranking": False,
        "message": "现有硬度数据以 25°C 为主；该温度下的硬度数值仅作参考，不参与高温候选排序。",
    }


def _artifact(name: str) -> dict:
    if name not in _ARTIFACTS:
        _ARTIFACTS[name] = joblib.load(MODELS / f"{name}_ensemble.joblib")
    return _ARTIFACTS[name]


def _composition(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        return parse_composition(value).atomic_percent
    if not isinstance(value, dict) or not value:
        raise ValueError("composition must be a formula string or an at.% object")
    result = {str(key): float(item) for key, item in value.items()}
    total = sum(result.values())
    if abs(total - 1.0) < 1e-6:
        result = {key: value * 100 for key, value in result.items()}; total = 100.0
    if any(value < 0 for value in result.values()) or abs(total - 100.0) > .01:
        raise ValueError("composition_at_pct must be non-negative and sum to 100")
    return {key: value for key, value in sorted(result.items()) if value > 0}


def _features_for(raw_features: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    features = raw_features.copy()
    for column in artifact["feature_columns"]:
        if column not in features:
            features[column] = 0.0 if column.startswith("x_") else np.nan
    return features[artifact["feature_columns"]]


def _domain(composition: dict[str, float], artifact: dict) -> dict:
    unseen = sorted(set(composition) - set(artifact["elements"]))
    vector = np.array([composition.get(column[2:], 0.0) / 100 for column in artifact["composition_columns"]])
    distance = float(np.sqrt(((artifact["reference_compositions"] - vector) ** 2).sum(axis=1)).min())
    violations = [element for element, value in composition.items() if element in artifact["element_ranges_at_pct"] and (value < artifact["element_ranges_at_pct"][element]["min_observed_at_pct"] or value > artifact["element_ranges_at_pct"][element]["max_observed_at_pct"])]
    thresholds = artifact["distance_thresholds"]
    level = "outside" if unseen or violations or distance > thresholds["boundary"] else ("inside" if distance <= thresholds["inside"] else "boundary")
    return {"level":level,"nearest_training_composition_distance":distance,"distance_thresholds":thresholds,"unseen_elements":unseen,"element_range_violations":violations}


def _evaluate_many(constraints: dict, compositions: list[dict[str, float]]) -> list[dict]:
    temperature_C = float(constraints.get("test_temperature_C",25))
    rows = pd.DataFrame([{"composition_at_pct":json.dumps(item),"test_temperature_C":temperature_C,"processing_method":constraints.get("processing_method")} for item in compositions])
    raw_features, _, _ = _features(rows)
    outputs = [{"composition_at_pct": item} for item in compositions]
    for task, output_name in (("yield_strength","yield_strength_MPa"),("hardness","hardness_HV")):
        artifact = _artifact(task); values = np.vstack([model.predict(_features_for(raw_features,artifact)) for model in artifact["models"]])
        for index, output in enumerate(outputs): output[output_name] = {"mean":float(values[:,index].mean()),"std":float(values[:,index].std())}
    phase = _artifact("phase"); probabilities = np.stack([model.predict_proba(_features_for(raw_features,phase)) for model in phase["models"]])
    domain_artifact = _artifact("yield_strength")
    for index, output in enumerate(outputs):
        output["phase_probabilities"] = {label:float(probabilities[:,index,label_index].mean()) for label_index,label in enumerate(phase["classes"])}
        output["applicability_domain"] = _domain(compositions[index], domain_artifact)
        output["phase_risk"] = "high" if output["phase_probabilities"].get("IM",0)+output["phase_probabilities"].get("SS+IM",0) >= .5 else "low"
        output["property_temperature_support"] = {
            "yield_strength_MPa": _temperature_support("yield_strength", temperature_C),
            "hardness_HV": _temperature_support("hardness", temperature_C),
        }
    return outputs


def evaluate(constraints: dict) -> dict:
    if constraints.get("model_domain", "hea_mpea") != "hea_mpea":
        raise ValueError("only hea_mpea is registered in this runner")
    composition = _composition(constraints.get("composition"))
    output = _evaluate_many(constraints,[composition])[0]
    output["descriptors"] = {"composition_at_pct":composition,"n_elements":len(composition),"test_temperature_C":constraints.get("test_temperature_C",25),"processing_method":constraints.get("processing_method")}
    output["uncertainty"] = {"yield_strength_MPa_std":output["yield_strength_MPa"]["std"],"hardness_HV_std":output["hardness_HV"]["std"]}
    output["constraints"] = {"composition_sum_valid":True,"element_bounds_valid":not output["applicability_domain"]["unseen_elements"] and not output["applicability_domain"]["element_range_violations"],"phase_risk":output["phase_risk"],"overall_feasible":output["applicability_domain"]["level"] != "outside" and output["phase_risk"] != "high"}
    output["model_version"] = "hea_mpea_baseline_v0.1"
    return output


def _sample(elements: list[str], bounds: dict[str,list[float]], count: int, seed: int) -> list[dict[str,float]]:
    lower = np.array([float(bounds[element][0]) for element in elements]); upper = np.array([float(bounds[element][1]) for element in elements])
    if np.any(lower < 0) or np.any(upper < lower) or lower.sum() > 100 or upper.sum() < 100: raise ValueError("element_bounds_at_pct must permit a 100 at.% composition")
    rng = np.random.default_rng(seed); accepted=[]
    for _ in range(40):
        trial=lower+rng.dirichlet(np.ones(len(elements)),size=max(512,count*3))*(100-lower.sum())
        for row in trial:
            if np.all(row <= upper+1e-9):
                accepted.append({element:float(value) for element,value in zip(elements,row) if value>1e-8})
                if len(accepted)>=count:return accepted
    raise ValueError("too few valid compositions for requested bounds")


def propose(constraints: dict) -> dict:
    if constraints.get("model_domain", "hea_mpea") != "hea_mpea": raise ValueError("candidate proposal is currently available only for hea_mpea")
    scope = constraints.get("raw_scope") or constraints; assumptions=[]; elements=list(constraints.get("allowed_elements") or [])
    if not elements:
        elements=["Co","Cr","Fe","Mn","Ni"]; assumptions.append("allowed_elements defaulted to the Co-Cr-Fe-Mn-Ni exploratory HEA template; user confirmation required")
    artifact=_artifact("yield_strength"); unknown=sorted(set(elements)-set(artifact["elements"]))
    if unknown: raise ValueError(f"unsupported HEA model elements: {unknown}")
    requested=scope.get("element_bounds_at_pct") or {}; bounds={element:list(requested.get(element,[0.0,artifact["element_ranges_at_pct"][element]["max_observed_at_pct"]])) for element in elements}
    count=min(max(int(scope.get("candidate_count",1000)),50),5000); maximum_return=min(max(int(scope.get("max_return",40)),1),100)
    candidates=_evaluate_many(constraints,_sample(elements,bounds,count,int(scope.get("random_seed",2026))))
    objectives=dict(constraints.get("objectives") or {})
    hardness_support = _temperature_support("hardness", float(constraints.get("test_temperature_C", 25)))
    hardness_usable = bool(hardness_support["usable_for_ranking"])
    if not hardness_usable and isinstance(objectives.get("hardness_HV"), dict):
        # Do not silently accept a high-temperature hardness threshold and
        # then use it as though it had a validated response curve.
        objectives.pop("hardness_HV", None)
    # A vague request must still be a useful screening exercise.  Derive
    # conservative thresholds from this exact sampled design space instead of
    # pretending a fixed room-temperature threshold fits every temperature.
    # Explicit user minima always take precedence over these defaults.
    adaptive = str(scope.get("screening_mode") or "").lower() == "conservative_adaptive"
    auto_thresholds = {}
    if adaptive and not any(isinstance(objectives.get(k), dict) and objectives[k].get("min") is not None for k in (("yield_strength_MPa", "hardness_HV") if hardness_usable else ("yield_strength_MPa",))):
        ys = np.array([item["yield_strength_MPa"]["mean"] for item in candidates])
        us = np.array([item["yield_strength_MPa"]["std"] for item in candidates])
        # These quantiles retain a practical review set (roughly tens, not all
        # samples) once combined with the in-domain requirement below.
        auto_thresholds = {
            "yield_strength_MPa_min": float(np.quantile(ys, .40)),
            "yield_strength_MPa_std_max": float(np.quantile(us, .80)),
        }
        objectives.setdefault("yield_strength_MPa", {}).update({"goal":"maximize", "min":auto_thresholds["yield_strength_MPa_min"]})
        if hardness_usable:
            hv = np.array([item["hardness_HV"]["mean"] for item in candidates])
            auto_thresholds["hardness_HV_min"] = float(np.quantile(hv, .40))
            objectives.setdefault("hardness_HV", {}).update({"goal":"maximize", "min":auto_thresholds["hardness_HV_min"]})
    yield_min=(objectives.get("yield_strength_MPa") or {}).get("min") if isinstance(objectives.get("yield_strength_MPa"),dict) else None
    hardness_min=(objectives.get("hardness_HV") or {}).get("min") if isinstance(objectives.get("hardness_HV"),dict) else None
    uncertainty=(scope.get("uncertainty") or {}).get("yield_strength_MPa_std_max")
    if uncertainty is None and auto_thresholds:
        uncertainty=auto_thresholds["yield_strength_MPa_std_max"]
    domain_requirement = str(scope.get("applicability_requirement") or ("inside_preferred" if adaptive else "inside_or_boundary"))
    property_qualified=[item for item in candidates if item["phase_risk"] != "high" and (yield_min is None or item["yield_strength_MPa"]["mean"]>=float(yield_min)) and (hardness_min is None or item["hardness_HV"]["mean"]>=float(hardness_min)) and (uncertainty is None or item["yield_strength_MPa"]["std"]<=float(uncertainty))]
    inside_qualified=[item for item in property_qualified if item["applicability_domain"]["level"] == "inside"]
    boundary_fallback_used = False
    if domain_requirement == "inside":
        feasible = inside_qualified
    elif domain_requirement == "inside_preferred":
        # Do not silently return an empty page merely because a useful but
        # extrapolative element system has no exact training neighbour.  Keep
        # boundary candidates only as an explicitly flagged fallback.
        feasible = inside_qualified
        if not feasible:
            feasible=[item for item in property_qualified if item["applicability_domain"]["level"] != "outside"]
            boundary_fallback_used = bool(feasible)
    else:
        feasible=[item for item in property_qualified if item["applicability_domain"]["level"] != "outside"]
    weights = {"strength": .55, "hardness": .25, "phase": .10, "reliability": .10} if hardness_usable else {"strength": .70, "hardness": .0, "phase": .15, "reliability": .15}
    requested_weights = scope.get("selection_weights") or {}
    if isinstance(requested_weights, dict):
        for key in weights:
            try:
                if key in requested_weights and float(requested_weights[key]) >= 0:
                    weights[key] = float(requested_weights[key])
            except (TypeError, ValueError):
                pass
    total_weight = sum(weights.values())
    weights = {key: value / total_weight for key, value in weights.items()} if total_weight > 0 else weights
    def _unit(values: np.ndarray) -> np.ndarray:
        span = float(values.max() - values.min())
        return np.full(len(values), .5) if span < 1e-12 else (values - values.min()) / span
    if feasible:
        ys = np.array([item["yield_strength_MPa"]["mean"] for item in feasible])
        ystd = np.array([item["yield_strength_MPa"]["std"] for item in feasible])
        hv = np.array([item["hardness_HV"]["mean"] for item in feasible])
        ss = np.array([item["phase_probabilities"].get("SS", 0.0) for item in feasible])
        distance = np.array([item["applicability_domain"]["nearest_training_composition_distance"] for item in feasible])
        # The terms are all normalized in the *current feasible candidate
        # space*, so no dimensional quantity is silently given an arbitrary
        # numerical advantage.  The phase and distance terms remain useful
        # even when a boundary fallback is required.
        strength_score = _unit(ys - ystd)
        hardness_score = _unit(hv) if hardness_usable else np.zeros(len(feasible))
        phase_score = _unit(ss)
        reliability_score = .5 * (1.0 - _unit(ystd)) + .5 * (1.0 - _unit(distance))
        for index, item in enumerate(feasible):
            item["selection_score"] = float(
                weights["strength"] * strength_score[index]
                + weights["hardness"] * hardness_score[index]
                + weights["phase"] * phase_score[index]
                + weights["reliability"] * reliability_score[index]
            )
    feasible.sort(key=lambda item:item.get("selection_score", 0.0),reverse=True)
    percentiles={element:{"p05":float(np.quantile([item["composition_at_pct"].get(element,0) for item in feasible],.05)),"p50":float(np.quantile([item["composition_at_pct"].get(element,0) for item in feasible],.5)),"p95":float(np.quantile([item["composition_at_pct"].get(element,0) for item in feasible],.95))} for element in elements} if feasible else {}
    applied_uncertainty = {"yield_strength_MPa_std_max": float(uncertainty)} if uncertainty is not None else {}
    domain_rule = "inside only" if domain_requirement == "inside" else ("inside preferred; boundary fallback only when no in-domain candidate qualifies" if domain_requirement == "inside_preferred" else "inside or boundary")
    diagnostics={"generated":count,"inside_domain":sum(item["applicability_domain"]["level"] == "inside" for item in candidates),"boundary_domain":sum(item["applicability_domain"]["level"] == "boundary" for item in candidates),"low_phase_risk":sum(item["phase_risk"] == "low" for item in candidates),"property_qualified_before_domain":len(property_qualified),"inside_qualified":len(inside_qualified),"boundary_fallback_used":boundary_fallback_used,"feasible":len(feasible)}
    selection_formula={"expression":"J=wy*z(YS-sigmaYS)+wh*z(HV)+wp*z(P_SS)+wr*[0.5*(1-z(sigmaYS))+0.5*(1-z(d_AD))]","weights":weights,"normalization":"z() is min-max normalization within the current feasible candidate set","meaning":"ranking score only; hard constraints are evaluated before ranking"}
    return {"model_domain":"hea_mpea","search_space":{"elements_at_pct":bounds,"sum_constraint_at_pct":100,"processing_method":constraints.get("processing_method"),"test_temperature_C":constraints.get("test_temperature_C",25)},"screening_criteria":{"mode":"conservative_adaptive" if adaptive else "user_specified_or_exploratory","objectives":objectives,"uncertainty":applied_uncertainty,"auto_thresholds":auto_thresholds,"phase_rule":"IM probability + SS+IM probability < 0.5","applicability_rule":domain_rule,"selection_formula":selection_formula,"property_temperature_support":{"yield_strength_MPa":_temperature_support("yield_strength", float(constraints.get("test_temperature_C",25))),"hardness_HV":hardness_support}},"sampling":{"method":"Dirichlet composition-simplex sampling + batched surrogate evaluation","generated":count,"feasible":len(feasible),"diagnostics":diagnostics},"derived_candidate_percentiles_at_pct":percentiles,"initial_candidates":feasible[:maximum_return],"_presentation_candidates":feasible,"assumptions":assumptions,"model_version":"hea_mpea_baseline_v0.1"}


def dispatch(request: dict) -> dict:
    operation=request.get("operation"); constraints=request.get("constraints") or {}
    if operation=="evaluate": return evaluate(constraints)
    if operation=="evaluate_batch": return {"results":[evaluate({**constraints,**candidate}) for candidate in request.get("candidates",[])]}
    if operation=="propose": return propose(constraints)
    raise ValueError(f"unsupported operation: {operation}")
