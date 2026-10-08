"""Serving adapter for the documented Cs/FA/MA–Pb(I,Br)3 transport model.

This is deliberately a transport-stability *screening* service.  Its training
response is ln[sigma(T)T], not device lifetime, PCE, or decomposition data.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = ROOT / "pure_zero_shot_v2"
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
from predict_joint import load_artifact, predict  # type: ignore

_ARTIFACT: dict[str, Any] | None = None


def _artifact() -> dict[str, Any]:
    global _ARTIFACT
    if _ARTIFACT is None:
        _ARTIFACT = load_artifact(MODEL_ROOT / "model_artifact.json")
    return _ARTIFACT


def _formula(c: dict[str, float]) -> str:
    def n(value: float) -> str:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    names = {"Cs": "cs_fraction", "FA": "fa_fraction", "MA": "ma_fraction"}
    a = "".join(f"{key}{n(c[value])}" for key, value in names.items() if c[value] > 1e-9)
    i, br = c["iodide_fraction"], c["bromide_fraction"]
    x = f"I{n(3*i)}" if br < 1e-9 else (f"Br{n(3*br)}" if i < 1e-9 else f"(I{n(i)}Br{n(br)})3")
    return f"{a}Pb{x}"


def _curve_value(prediction: dict[str, Any], temperature: float) -> float:
    return float(min(prediction["curve"], key=lambda x: abs(float(x["temperature_K"]) - temperature))["predicted_ln_sigmaT"])


def _candidate(composition: Any, temperatures: list[float]) -> dict[str, Any]:
    request = {"composition": composition, "temperatures_K": temperatures} if isinstance(composition, str) else {"fractions": composition, "temperatures_K": temperatures}
    prediction = predict(request, _artifact())
    p = prediction["parameters"]
    return {
        "formula": _formula(prediction["composition"]), "composition": prediction["composition"],
        "predicted_transport": prediction, "Ea_low_eV": float(p["Ea_low_eV"]),
        "Ea_high_eV": float(p["Ea_high_eV"]),
        "ln_sigmaT_220K": _curve_value(prediction, 220.0), "ln_sigmaT_300K": _curve_value(prediction, 300.0),
        "in_training_domain": bool(prediction["domain"]["inside_training_box"]),
    }


def _score(candidates: list[dict[str, Any]]) -> None:
    for key in ("ln_sigmaT_220K", "ln_sigmaT_300K"):
        values = np.array([item[key] for item in candidates], dtype=float)
        scale = float(values.std()) or 1.0
        for item, value in zip(candidates, values):
            item.setdefault("_score", 0.0)
            item["_score"] += -0.5 * float((value - values.mean()) / scale)


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    constraints = dict(request.get("constraints") or {})
    operation = request.get("operation", "propose_space")
    temperatures = [float(x) for x in constraints.get("temperatures_K", [170, 220, 300, 330])]
    if operation == "evaluate":
        raw = constraints.get("fractions") or constraints.get("composition")
        if raw is None:
            raise ValueError("evaluate requires fractions or composition")
        pred = predict({"fractions": raw, "temperatures_K": temperatures}, _artifact())
        return {"model_domain": "perovskite_transport_stability_v2", "mode": "transport_stability_evaluation", "predicted_transport": pred,
                "stability_scope": "仅为 ln[sigma(T)T] 电输运代理；不预测器件寿命、PCE 或湿热/光照分解。"}
    grid = _artifact().get("training_compositions") or []
    candidates = [_candidate(item, temperatures) for item in grid]
    _score(candidates)
    candidates.sort(key=lambda x: (-x["_score"], x["ln_sigmaT_300K"]))
    for index, item in enumerate(candidates, 1):
        item["rank"] = index
        item["transport_stability_score"] = round(float(item.pop("_score")), 4)
    n = min(max(1, int(constraints.get("num_candidates", 5))), len(candidates))
    pool = candidates[: max(n, int(np.ceil(len(candidates) * .25)))]
    return {
        "model_domain": "perovskite_transport_stability_v2", "model_version": _artifact().get("model_version"),
        "mode": "documented_composition_grid_screening", "target_temperatures_K": temperatures,
        "stability_scope": {"proxy": "低 ln[sigma(T)T] 的电输运响应", "not_predicted": ["器件寿命", "PCE", "湿热/光照分解", "相分离动力学"]},
        "sampling": {"generated": len(candidates), "feasible": len(candidates), "funnel_stages": [
            {"label": "可追溯训练配比网格", "count": len(candidates), "description": "只使用模型文档中列出的 Cs/FA/MA–Pb(I,Br)3 组分。"},
            {"label": "170–330 K 可比较范围", "count": len(candidates), "description": "在模型训练温区内比较温度响应。"},
            {"label": "低电输运响应候选池", "count": len(pool), "description": "按 220 K 与 300 K 的 ln[sigmaT] 联合排序。"},
            {"label": "优先验证短名单", "count": n, "description": "用于后续离子输运与器件稳定性实验验证。"}]},
        "initial_candidates": candidates[:n], "all_candidates": candidates, "customer_markdown": "该结果仅用于电输运稳定性代理初筛。",
    }
