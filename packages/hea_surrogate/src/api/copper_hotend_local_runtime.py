"""Bounded, state-conditioned Cu hot-wall local composition screening."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import math

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/copper_hot_end_local_composition_v1_20260911/model.joblib"

_DENSITY_G_CM3 = {"Cu": 8.96, "Cr": 7.19, "Nb": 8.57, "Zr": 6.52, "Ag": 10.49, "Al": 2.70, "Ni": 8.90, "Fe": 7.87, "Ti": 4.51}
_CTE_PPM_PER_K = {"Cu": 16.5, "Cr": 4.9, "Nb": 7.3, "Zr": 5.7, "Ag": 19.1, "Al": 23.1, "Ni": 13.4, "Fe": 11.8, "Ti": 8.6}
_DEFAULT_AMBIENT_PROCESS = {"solution_temperature_K": 1233.0, "solution_time_h": 2.0, "cold_reduction_pct": 50.0, "aged": True, "aging_temperature_K": 723.0, "aging_time_h": 2.0, "secondary_thermomechanical_process": False}


def _artifact() -> dict[str, Any]:
    return joblib.load(ARTIFACT)


def _number(value: Any, default: float) -> float:
    try: return float(value)
    except (TypeError, ValueError): return default


def _state_features(state: str, tokens: tuple[str, ...]) -> list[float]:
    lower = state.casefold()
    return [float(token in lower) for token in tokens]


def _context_distance(anchor: dict[str, Any], state: str, temperature: float, tokens: tuple[str, ...]) -> tuple[int, float]:
    """Return explicit state and temperature distance to a training anchor."""
    requested = np.asarray(_state_features(state, tokens))
    observed = np.asarray(_state_features(str(anchor.get("alloy_state") or ""), tokens))
    return int(np.abs(requested - observed).sum()), abs(float(anchor.get("test_temperature_C") or 0) - temperature)


def _contextual_references(artifact: dict[str, Any], family: str, state: str, temperature: float) -> list[dict[str, Any]]:
    references = list(artifact["references"]); tokens = tuple(artifact["process_tokens"])
    if family == "GRCop_type_Cu_Cr_Nb":
        references = [row for row in references if str(row.get("alloy_name") or "").casefold().startswith("grcop")]
    if not references:
        raise ValueError("当前成分家族没有可追溯训练锚点；请改用 custom 或提供受支持的铜基家族。")
    exact = [row for row in references if _context_distance(row, state, temperature, tokens) == (0, 0)]
    # Only fall back when the requested state/temperature is absent.  The
    # fallback is retained for transparency but can never be labelled inside.
    return exact or sorted(references, key=lambda row: _context_distance(row, state, temperature, tokens))


def _predict(artifact: dict[str, Any], composition: dict[str, float], temperature: float, state: str) -> tuple[float, float]:
    elements = tuple(artifact["elements"]); tokens = tuple(artifact["process_tokens"])
    x = np.asarray([[*(float(composition.get(item, 0)) for item in elements), temperature, *_state_features(state, tokens)]])
    def blended(key: str) -> float:
        model = artifact["models"][key]
        return float(.3 * model["extra_trees"].predict(x)[0] + .7 * model["random_forest"].predict(x)[0])
    return blended("uts"), blended("yield")


def _range(mean: float, fraction: float, *, lower: float = 0.0) -> dict[str, float]:
    return {"mean": mean, "low": max(lower, mean * (1 - fraction)), "high": mean * (1 + fraction)}


def _ambient_process(scope: dict[str, Any]) -> dict[str, Any]:
    supplied = scope.get("ambient_process") or {}
    return {**_DEFAULT_AMBIENT_PROCESS, **(supplied if isinstance(supplied, dict) else {})}


def _additional_properties(artifact: dict[str, Any], composition: dict[str, float], ys: float, ambient_process: dict[str, Any]) -> dict[str, Any]:
    auxiliary = artifact.get("ambient_auxiliary", {}).get("electrical_conductivity_percent_IACS")
    if not auxiliary:
        return {}
    process_values = [
        _number(ambient_process.get("solution_temperature_K"), 1233), _number(ambient_process.get("solution_time_h"), 2),
        _number(ambient_process.get("cold_reduction_pct"), 50), _number(ambient_process.get("aging_temperature_K"), 723),
        _number(ambient_process.get("aging_time_h"), 2), float(bool(ambient_process.get("aged", True))),
        float(bool(ambient_process.get("secondary_thermomechanical_process", False))),
    ]
    x = np.asarray([[*(float(composition.get(item, 0)) for item in auxiliary["elements"]), *process_values]])
    iacs = float(np.clip(auxiliary["model"].predict(x)[0], 0, 100)); metrics = auxiliary["validation"]; mae = float(metrics["mae_percent_IACS"])
    conductivity = {"evidence_level": "C", **_range(iacs, mae / max(iacs, 1.0)), "unit": "%IACS", "screening_MAE": mae, "condition": "室温；采用本轮可见的铜合金默认/用户覆盖加工参数", "basis": "Gorsse CC0 数据集全工艺字段辅助回归；不参与候选排序或硬筛选", "validation": metrics}
    sigma_low = max(0, conductivity["low"]) / 100 * 58e6; sigma_high = conductivity["high"] / 100 * 58e6
    k_mean = 2.44e-8 * (iacs / 100 * 58e6) * 298.15
    thermal = {"evidence_level": "D", "mean": k_mean, "low": .75 * 2.44e-8 * sigma_low * 298.15, "high": 1.25 * 2.44e-8 * sigma_high * 298.15, "unit": "W/(m·K)", "condition": "约 25 °C；不适用于热壁服役温度", "basis": "由 C 级 %IACS 预测按 Wiedemann–Franz 关系换算；含导电率误差和 Lorenz 数近似的不确定度", "not_for_ranking": True}
    known_mass = sum(float(composition.get(item, 0)) for item in _DENSITY_G_CM3)
    density = 1 / sum((float(composition.get(item, 0)) / known_mass) / value for item, value in _DENSITY_G_CM3.items() if float(composition.get(item, 0)) > 0) if known_mass else 0
    cte = sum((float(composition.get(item, 0)) / known_mass) * value for item, value in _CTE_PPM_PER_K.items()) if known_mass else 0
    return {
        "electrical_conductivity_percent_IACS": conductivity,
        "thermal_conductivity_room_temperature_W_mK": thermal,
        "hardness_HV": {"evidence_level": "D", **_range(3 * ys / 9.807, .50), "unit": "HV", "condition": "与本轮 0.2% 屈服同状态、约 25 °C", "basis": "Tabor 型 H≈3σy 工程换算；未用硬度标签校准，不参与排序或硬筛选", "not_for_ranking": True},
        "density_g_cm3": {"evidence_level": "D", **_range(density, .10), "unit": "g/cm³", "condition": "名义成分、室温", "basis": "纯元素密度倒数混合法；忽略孔隙、析出相和氧化物，不参与排序或硬筛选", "not_for_ranking": True},
        "CTE_room_temperature_ppm_per_K": {"evidence_level": "D", **_range(cte, .35), "unit": "10⁻⁶/K", "condition": "约 20–100 °C", "basis": "纯元素线膨胀系数成分加权代理；未作合金/组织校准，不参与排序或硬筛选", "not_for_ranking": True},
    }


def _composition_from_anchor(anchor: dict[str, Any], bounds: dict[str, Any], rng: np.random.Generator, elements: tuple[str, ...]) -> dict[str, float] | None:
    comp = dict(anchor["composition_wt_percent"])
    for item in elements:
        if item == "Cu": continue
        raw = bounds.get(item, [0, max(0.2, comp.get(item, 0) + 1.0)])
        lo, hi = max(0., _number(raw[0] if isinstance(raw, list) and raw else 0, 0)), _number(raw[1] if isinstance(raw, list) and len(raw) > 1 else comp.get(item, 0) + 1, comp.get(item, 0) + 1)
        comp[item] = float(np.clip(comp.get(item, 0) + rng.normal(0, max(.02, (hi-lo)*.14)), lo, hi))
    comp["Cu"] = 100. - sum(comp.get(item, 0) for item in elements if item != "Cu")
    copper_bounds = bounds.get("Cu")
    if copper_bounds and (comp["Cu"] < _number(copper_bounds[0], 0) or comp["Cu"] > _number(copper_bounds[1], 100)):
        return None
    return {item: round(max(0., comp.get(item, 0)), 5) for item in elements}


def _thresholds(scope: dict[str, Any]) -> dict[str, float]:
    thresholds = scope.get("screening_thresholds") or {}
    if not isinstance(thresholds, dict) or set(thresholds) - {"uts_min_MPa", "proof_strength_min_MPa"}:
        raise ValueError("铜基性能要求仅支持抗拉强度和0.2%屈服强度下限。")
    for key, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    return thresholds


def evaluate(scope: dict[str, Any], *, artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate this exact recipe, including its own ancillary properties.

    No sampling or nearest-reference substitution is performed. Reference
    records define support only; all predictors use the supplied composition.
    """
    artifact = _artifact() if artifact is None else artifact
    elements = tuple(artifact["elements"])
    raw = scope.get("composition_wt_percent")
    if not isinstance(raw, dict) or not raw or set(raw) - set(elements):
        raise ValueError("composition_wt_percent must contain supported copper elements")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in raw.values()):
        raise ValueError("composition must contain finite nonnegative mass percentages")
    composition = {e: float(raw.get(e, 0)) for e in elements}
    if not math.isclose(sum(composition.values()), 100., rel_tol=0., abs_tol=1e-3):
        raise ValueError("composition must sum to 100 wt.%")
    raw_temperature = scope.get("test_temperature_C", 25.)
    if isinstance(raw_temperature, bool):
        raise ValueError("test_temperature_C must be finite and above absolute zero")
    temperature = float(raw_temperature)
    if not math.isfinite(temperature) or temperature <= -273.15:
        raise ValueError("test_temperature_C must be finite and above absolute zero")
    state = str(scope.get("processing_state") or "solution_aged")
    family = str(scope.get("composition_family") or "GRCop_type_Cu_Cr_Nb")
    thresholds = _thresholds(scope)
    ambient_process = _ambient_process(scope)
    for key in ("solution_temperature_K", "solution_time_h", "cold_reduction_pct", "aging_temperature_K", "aging_time_h"):
        value = ambient_process[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("ambient_process requires finite nonnegative parameters: " + key)
    if ambient_process["cold_reduction_pct"] > 100:
        raise ValueError("cold_reduction_pct must be between 0 and 100")
    for key in ("aged", "secondary_thermomechanical_process"):
        if not isinstance(ambient_process[key], bool):
            raise ValueError("ambient_process requires boolean flags: " + key)
    references = _contextual_references(artifact, family, state, temperature)
    anchor = min(references, key=lambda row: sum((composition[e] - row["composition_wt_percent"].get(e, 0)) ** 2 for e in elements))
    distance = float(np.sqrt(sum((composition[e] - anchor["composition_wt_percent"].get(e, 0)) ** 2 for e in elements)))
    state_distance, temperature_distance = _context_distance(anchor, state, temperature, tuple(artifact["process_tokens"]))
    if state_distance:
        level = "state_extrapolation"
    elif temperature_distance:
        level = "temperature_extrapolation"
    else:
        level = "inside" if distance <= 2.5 else "outside"
    uts, ys = _predict(artifact, composition, temperature, state)
    if not math.isfinite(uts) or not math.isfinite(ys) or not uts >= ys >= 0:
        raise ValueError("copper prediction violates finite UTS >= YS >= 0")
    observed = {"uts_min_MPa": uts, "proof_strength_min_MPa": ys}
    gaps = {key: {"target": float(target), "observed": observed[key], "shortfall": max(0., target - observed[key]),
                  "relative_shortfall": max(0., target - observed[key]) / max(abs(target), 1.), "unit": "MPa"}
            for key, target in thresholds.items()}
    conditions = {"composition_family": family, "test_temperature_C": temperature, "processing_state": state,
                  "ambient_process": ambient_process, "screening_thresholds": thresholds}
    return {"model_domain": "copper_hot_end_local_composition_v1", "model_version": artifact["model_version"],
        "composition_wt_percent": composition, "material_state": state, "screening_conditions": conditions,
        "source_anchor": {"alloy_name": anchor["alloy_name"], "alloy_state": anchor["alloy_state"],
            "test_temperature_C": anchor["test_temperature_C"], "source_type": "training_record_composition_anchor",
            "anchor_role": "support_reference_only; predictions use the supplied recipe and conditions"},
        "short_time_tensile": {"ultimate_tensile_strength_MPa": {"mean": uts, "unit": "MPa", "source": "specialist",
            "screening_MAE_MPa": artifact["validation"]["uts"]["composition_held_out"]["mae_MPa"]},
            "yield_0p2_MPa": {"mean": ys, "unit": "MPa", "source": "specialist",
            "screening_MAE_MPa": artifact["validation"]["yield"]["composition_held_out"]["mae_MPa"]}},
        "additional_properties": _additional_properties(artifact, composition, ys, ambient_process),
        "applicability_domain": {"level": level, "nearest_anchor_distance_wt_percent": distance,
            "state_distance": state_distance, "temperature_distance_C": temperature_distance},
        "strict_screening": {"passed": level == "inside" and all(g["shortfall"] == 0 for g in gaps.values()),
            "constraint_gaps": gaps, "total_relative_gap": sum(g["relative_shortfall"] for g in gaps.values())}}


def evaluate_batch(scope: dict[str, Any], candidates: Any) -> dict[str, Any]:
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 256:
        raise ValueError("evaluate_batch requires 1 to 256 candidates")
    # A recipe cannot silently override the batch's fixed temperature/process.
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) - {"composition_wt_percent", "candidate_id"}:
            raise ValueError("batch candidates may contain only composition_wt_percent and candidate_id")
        if "composition_wt_percent" not in candidate:
            raise ValueError("each batch candidate requires composition_wt_percent")
    artifact = _artifact()
    results = []
    for candidate in candidates:
        row = evaluate({**scope, "composition_wt_percent": candidate["composition_wt_percent"]}, artifact=artifact)
        if "candidate_id" in candidate:
            row["candidate_id"] = str(candidate["candidate_id"])
        results.append(row)
    return {"results": results}


def propose(scope: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact(); elements = tuple(artifact["elements"]); state = str(scope.get("processing_state") or "solution_aged")
    temperature = _number(scope.get("test_temperature_C"), 25.); count = max(3, min(40, int(_number(scope.get("num_candidates"), 12))))
    bounds = scope.get("element_bounds_wt_percent") or {}; family = str(scope.get("composition_family") or "GRCop_type_Cu_Cr_Nb")
    ambient_process = _ambient_process(scope); seed = int(_number(scope.get("random_seed"), 20260911)); rng = np.random.default_rng(seed)
    thresholds = _thresholds(scope)
    candidates: list[dict[str, Any]] = []; attempted = 0; within_bounds = 0; physical_ordered = 0
    references = _contextual_references(artifact, family, state, temperature)
    for index in range(count * 15):
        attempted += 1
        anchor = references[index % len(references)] if index < len(references) else references[int(rng.integers(len(references)))]
        composition = _composition_from_anchor(anchor, bounds, rng, elements)
        if not composition: continue
        within_bounds += 1
        uts, ys = _predict(artifact, composition, temperature, state)
        if not math.isfinite(uts) or not math.isfinite(ys) or not uts >= ys >= 0: continue
        physical_ordered += 1
        distance = float(np.sqrt(sum((composition[item] - anchor["composition_wt_percent"].get(item, 0)) ** 2 for item in elements)))
        state_distance, temperature_distance = _context_distance(anchor, state, temperature, tuple(artifact["process_tokens"]))
        if state_distance == 0 and temperature_distance == 0:
            level = "inside" if distance <= 2.5 else "composition_boundary"
        elif state_distance:
            level = "state_extrapolation"
        else:
            level = "temperature_extrapolation"
        evidence_level = "B" if level in {"inside", "composition_boundary"} else "C"
        candidates.append({"candidate_id": f"CuHW-v1-{len(candidates)+1:02d}", "alloy_name": "铜基热壁局部配比候选", "composition_wt_percent": composition, "material_state": state, "source_anchor": {"alloy_name": anchor["alloy_name"], "alloy_state": anchor["alloy_state"], "test_temperature_C": anchor["test_temperature_C"], "source_type": "training_record_composition_anchor", "anchor_role": "composition_neighborhood_only; prediction processing_state is taken from this request"}, "short_time_tensile": {"ultimate_tensile_strength_MPa": {"mean": uts, "evidence_level": evidence_level, "screening_MAE_MPa": artifact["validation"]["uts"]["composition_held_out"]["mae_MPa"]}, "yield_0p2_MPa": {"mean": ys, "evidence_level": evidence_level, "screening_MAE_MPa": artifact["validation"]["yield"]["composition_held_out"]["mae_MPa"]}}, "additional_properties": _additional_properties(artifact, composition, ys, ambient_process), "applicability_domain": {"level": level, "nearest_anchor_distance_wt_percent": distance, "state_distance": state_distance, "temperature_distance_C": temperature_distance}})
        if len(candidates) >= count: break
    candidates.sort(key=lambda row: (row["applicability_domain"]["level"] in {"inside", "composition_boundary"}, row["short_time_tensile"]["ultimate_tensile_strength_MPa"]["mean"] + row["short_time_tensile"]["yield_0p2_MPa"]["mean"], -row["applicability_domain"]["nearest_anchor_distance_wt_percent"]), reverse=True)
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
        row["candidate_id"] = f"CuHW-v1-{rank:02d}"
    for row in candidates:
        tensile = row["short_time_tensile"]
        values = {"uts_min_MPa": tensile["ultimate_tensile_strength_MPa"]["mean"], "proof_strength_min_MPa": tensile["yield_0p2_MPa"]["mean"]}
        gaps = {key: {"target": float(target), "observed": values[key], "shortfall": max(0., float(target) - values[key]), "relative_shortfall": max(0., float(target) - values[key]) / max(abs(float(target)), 1.), "unit": "MPa"} for key, target in thresholds.items()}
        supported = row["applicability_domain"]["level"] in {"inside", "composition_boundary"}
        row["strict_screening"] = {"passed": supported and all(gap["shortfall"] == 0 for gap in gaps.values()), "constraint_gaps": gaps, "total_relative_gap": sum(gap["relative_shortfall"] for gap in gaps.values())}
    passed = [row for row in candidates if row["strict_screening"]["passed"]]
    nearest = [] if passed or not thresholds else sorted([row for row in candidates if row["applicability_domain"]["level"] in {"inside", "composition_boundary"}], key=lambda row: (row["strict_screening"]["total_relative_gap"], row["rank"]))[:3]
    funnel = [{"label": "局部成分候选", "count": attempted}, {"label": "满足元素 wt.% 边界", "count": within_bounds}, {"label": "物理有序的有效强度预测", "count": physical_ordered}, {"label": "组成、状态与温度有支持的配方", "count": sum(row["applicability_domain"]["level"] in {"inside", "composition_boundary"} for row in candidates)}, {"label": "满足本次性能要求", "count": len(passed)}]
    return {"model_domain": "copper_hot_end_local_composition_v1", "model_version": artifact["model_version"], "screening_conditions": {"composition_family": family, "test_temperature_C": temperature, "processing_state": state, "element_bounds_wt_percent": bounds, "ambient_process": ambient_process, "screening_thresholds": thresholds}, "sampling": {"generated": attempted, "feasible": len(passed), "funnel_stages": funnel}, "initial_candidates": passed, "nearest_candidates": nearest, "all_candidates": candidates, "validation": artifact["validation"], "auxiliary_validation": artifact.get("ambient_auxiliary", {}).get("electrical_conductivity_percent_IACS", {}).get("validation", {}), "limitations": "B 级为成分、工艺状态和温度均在联合适用域内的短时 UTS、0.2% 屈服直接预测；状态或温度外推时降为 C 级。C 级仅为室温 %IACS 辅助模型预测；D 级热导、硬度、密度与 CTE 均是带区间的工程估算，均不参与候选排序或硬筛选。疲劳、蠕变、焊接和冷却通道结构性能仍需明确载荷/温度边界及独立模型或试验。"}


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    scope = request.get("constraints") or {}
    if operation in {"propose", "propose_space"}:
        return propose(scope)
    if operation == "evaluate":
        return evaluate(scope)
    if operation == "evaluate_batch":
        return evaluate_batch(scope, request.get("candidates"))
    raise ValueError("unsupported copper operation")
