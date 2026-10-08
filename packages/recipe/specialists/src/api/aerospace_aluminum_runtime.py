"""Restricted wt.% aerospace-aluminium composition/property screening runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/aerospace_aluminum_composition_v1_20260914/model.joblib"
_VARIABLES = ("Cu", "Mg", "Zn", "Si", "Mn", "Li", "Zr", "Sc", "Cr")

def _artifact() -> dict[str, Any]: return joblib.load(ARTIFACT)
def _num(value: Any, default: float) -> float:
    try: return float(value)
    except (TypeError, ValueError): return default

def _predict(artifact: dict[str, Any], composition: dict[str, float], processing: str) -> dict[str, float]:
    frame = pd.DataFrame([{**{e: float(composition.get(e, 0.0)) for e in artifact["elements"]}, "processing_category": processing}])
    return {target: float(model.predict(frame)[0]) for target, model in artifact["models"].items()}

def _composition(anchor: dict[str, Any], bounds: dict[str, Any], rng: np.random.Generator, elements: list[str]) -> dict[str, float] | None:
    c = {e: float(anchor.get(e, 0.0)) * 100 for e in elements}
    for element in _VARIABLES:
        if element not in c: continue
        lo, hi = bounds.get(element, [max(0, c[element] - 0.5), c[element] + 0.5])
        c[element] = float(np.clip(c[element] + rng.normal(0, max(0.01, (float(hi)-float(lo))*0.10)), float(lo), float(hi)))
    c["Al"] = 100 - sum(value for element, value in c.items() if element != "Al")
    if c["Al"] < 0: return None
    if "Al" in bounds and not (float(bounds["Al"][0]) <= c["Al"] <= float(bounds["Al"][1])): return None
    return {e: round(max(0.0, c[e]), 5) for e in elements}

def propose(scope: dict[str, Any]) -> dict[str, Any]:
    a = _artifact(); elements = list(a["elements"]); bounds = scope.get("element_bounds_wt_percent") or {}
    processing = str(scope.get("processing_category") or "Solutionised + Artificially peak aged")
    if processing not in a["processing_categories"]: raise ValueError(f"未记录的铝合金工艺类别：{processing}")
    n = max(3, min(30, int(_num(scope.get("num_candidates"), 12)))); rng = np.random.default_rng(int(_num(scope.get("random_seed"), 20260914)))
    anchors = [x for x in a["anchors"] if x["processing_category"] == processing] or list(a["anchors"])
    rows: list[dict[str, Any]] = []; attempted = 0
    for i in range(n * 20):
        attempted += 1; anchor = anchors[i % len(anchors)]
        c = _composition(anchor, bounds, rng, elements)
        if not c: continue
        p = _predict(a, {e: c[e] / 100 for e in elements}, processing)
        distance = float(np.sqrt(sum((c[e] / 100 - float(anchor.get(e, 0))) ** 2 for e in elements)))
        rows.append({"candidate_id": f"AlAero-v1-{len(rows)+1:02d}", "rank": 0, "composition_wt_percent": c, "processing_category": processing, "source_anchor": anchor["composition_id"], "predicted_properties": p, "applicability_domain": {"level": "inside" if distance <= .025 else "composition_boundary", "nearest_anchor_distance_fraction": distance}})
        if len(rows) >= n: break
    rows.sort(key=lambda x: (x["predicted_properties"]["uts_MPa"] + x["predicted_properties"]["yield_strength_MPa"], x["predicted_properties"]["elongation_pct"]), reverse=True)
    for rank, row in enumerate(rows, 1): row["rank"] = rank; row["candidate_id"] = f"AlAero-v1-{rank:02d}"
    return {"model_domain": "aerospace_aluminum_composition_v1", "model_version": a["model_version"], "screening_conditions": {"composition_basis": "wt.%", "processing_category": processing, "element_bounds_wt_percent": bounds}, "sampling": {"generated": attempted, "feasible": len(rows), "funnel_stages": [{"label":"局部 wt.% 候选", "count":attempted}, {"label":"成分闭合且工艺类别已记录", "count":len(rows)}, {"label":"训练成分邻域内/边界", "count":len(rows)}]}, "initial_candidates": rows, "all_candidates": rows, "validation": a["validation"], "limitations": "模型仅预测名义室温下、25 元素 wt.% 成分与十类已记录工艺类别联合条件下的 UTS、屈服强度和延伸率。它不含具体固溶/时效温度与时间，不能用于热处理制度优化；Al-Li 子域仅作局部参考。"}

def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("operation") not in {"propose", "propose_space"}: raise ValueError("aerospace aluminum service supports propose only")
    return propose(request.get("constraints") or {})
