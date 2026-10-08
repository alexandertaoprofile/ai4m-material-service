"""Traceable copper-alloy hot-wall evidence screening runtime."""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/copper_hot_end_evidence_v1_20260910/catalog_artifact.json"

def _number(value: Any, name: str) -> float:
    try: return float(value)
    except (TypeError, ValueError): raise ValueError(f"{name} must be numeric")

def _linear(x: list[float], y: list[float], at: float) -> tuple[float | None, str]:
    if at < x[0] or at > x[-1]: return None, "outside_source_temperature_range"
    for a, b, c, d in zip(x, x[1:], y, y[1:]):
        if a <= at <= b:
            value = c + (d-c) * (at-a) / (b-a)
            return value, "measured" if at in x else "engineering_interpolation"
    return y[-1], "measured"

def _property(grade: dict[str, Any], name: str, temp_K: float) -> dict[str, Any]:
    data = (grade.get("properties") or {}).get(name)
    if not data: return {"status":"not_recorded"}
    if data.get("kind") == "formula":
        lo, hi = data["temperature_K"]
        if not lo <= temp_K <= hi: return {"status":"outside_source_temperature_range", "source":data["source"]}
        lnt = math.log(temp_K); value = 6893 - 3466*lnt + 599.5*lnt**2 - 34.18*lnt**3
        return {"status":"source_curve", "value":value, "unit":"W/(m·K)", "source":data["source"], "temperature_coverage_K":[lo,hi]}
    value, status = _linear(data["temperature_C"], data["values"], temp_K-273.15)
    return {"status":status, "value":value, "unit":"MPa", "source":data["source"], "temperature_coverage_C":[data["temperature_C"][0],data["temperature_C"][-1]]}

def propose(scope: dict[str, Any]) -> dict[str, Any]:
    artifact=json.loads(ARTIFACT.read_text(encoding="utf-8")); temp_K=_number(scope.get("test_temperature_K",900), "test_temperature_K")
    thresholds=scope.get("screening_thresholds") or {}; rows=[]
    for grade in artifact["grades"]:
        k=_property(grade,"thermal_conductivity_W_mK",temp_K); ys=_property(grade,"yield_strength_MPa",temp_K)
        fatigue=(grade.get("properties") or {}).get("low_cycle_fatigue_cycles")
        fatigue_card={"status":"not_recorded"} if not fatigue else {"status":"measured_at_other_temperature", "temperature_C":fatigue["temperature_C"], "strain_percent":fatigue["strain_percent"], "cycles":fatigue["cycles"], "source":fatigue["source"]}
        pass_k = k.get("value") is not None and k["value"] >= float(thresholds.get("thermal_conductivity_min_W_mK", -math.inf))
        pass_ys = ys.get("value") is not None and ys["value"] >= float(thresholds.get("yield_strength_min_MPa", -math.inf))
        # Missing properties never pass a mandatory criterion, rather than being rejected as a different material class.
        required = [key for key in ("thermal_conductivity_min_W_mK","yield_strength_min_MPa") if key in thresholds]
        eligible = (pass_k if "thermal_conductivity_min_W_mK" in required else True) and (pass_ys if "yield_strength_min_MPa" in required else True)
        score=sum(float(v.get("value") or 0) for v in (k,ys))
        rows.append({"candidate_id":grade["id"],"alloy_name":grade["id"],"composition_wt_percent":grade["composition_wt_percent"],"material_state":grade["state"],"thermal_conductivity":k,"yield_strength":ys,"low_cycle_fatigue":fatigue_card,"screening":{"eligible_for_recorded_criteria":eligible,"unavailable_required_properties":[x for x in required if (k if x.startswith("thermal") else ys).get("value") is None],"score":score}})
    rows.sort(key=lambda item:(not item["screening"]["eligible_for_recorded_criteria"],-item["screening"]["score"]))
    return {"model_domain":"copper_hot_end_evidence_v1","model_version":artifact["model_version"],"screening_conditions":{"test_temperature_K":temp_K,"screening_thresholds":thresholds},"sampling":{"generated":len(rows),"feasible":sum(x["screening"]["eligible_for_recorded_criteria"] for x in rows),"funnel_stages":[{"label":"可追溯铜基牌号/状态","count":len(rows)},{"label":"已记录指标满足门槛","count":sum(x["screening"]["eligible_for_recorded_criteria"] for x in rows)}]},"initial_candidates":rows,"all_candidates":rows,"catalog_sources":artifact["sources"],"limitations":"仅在来源已覆盖的牌号、状态和温度内筛选；线性插值明确标为工程插值，不用于缺失指标放行。"}

def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("operation") not in {"propose","propose_space"}: raise ValueError("copper hot-end service supports propose only")
    return propose(request.get("constraints") or {})
