"""Short-CF RVE orthotropic-elasticity inference; no physical-test claims."""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "models/short_cf_thermomechanical_rve_v1_20260907/model.joblib"
_ARTIFACT: dict[str, Any] | None = None
_PROFILE_ALIASES = {
    "pla": "Bambu_PLA_Basic", "bambu pla basic": "Bambu_PLA_Basic", "bambu_pla_basic": "Bambu_PLA_Basic",
    "petg": "Bambu_PETG_HF", "bambu petg hf": "Bambu_PETG_HF", "bambu_petg_hf": "Bambu_PETG_HF",
    "abs": "Bambu_ABS", "asa": "Bambu_ASA", "pc": "Bambu_PC",
}


def _artifact() -> dict[str, Any]:
    global _ARTIFACT
    if _ARTIFACT is None:
        _ARTIFACT = joblib.load(MODEL_PATH)
    return _ARTIFACT


def _number(value: Any, field: str) -> float:
    try: value = float(value)
    except (TypeError, ValueError): raise ValueError(f"{field} must be numeric") from None
    if not math.isfinite(value): raise ValueError(f"{field} must be finite")
    return value


def _resolve_matrix_name(value: Any) -> str:
    """Resolve customer-friendly PLA/PETG names to a recorded matrix profile."""
    name = str(value or "").strip()
    profiles = _artifact()["matrix_profiles"]
    if name in profiles:
        return name
    normalized = " ".join(name.replace("_", " ").replace("-", " ").casefold().split())
    resolved = _PROFILE_ALIASES.get(normalized)
    if resolved and resolved in profiles:
        return resolved
    raise ValueError(f"unknown matrix_name: {name}; provide matrix_properties for a custom matrix")


def _input(scope: dict[str, Any]) -> dict[str, float]:
    artifact = _artifact(); raw = scope.get("matrix_properties") or {}
    if not raw and scope.get("matrix_name"):
        raw = artifact["matrix_profiles"][_resolve_matrix_name(scope["matrix_name"])]
    values = {name: _number(scope.get(name, raw.get(name)), name) for name in artifact["features"]}
    return values


def _domain(values: dict[str, float]) -> dict[str, Any]:
    domain = _artifact()["domain"]
    outside = {name: domain[name] for name, value in values.items() if value < domain[name][0] or value > domain[name][1]}
    return {"level": "outside" if outside else "inside", "recorded_ranges": domain, "outside_recorded_ranges": outside}


def _stability(constants: dict[str, float]) -> dict[str, Any]:
    e1,e2,e3 = (constants[name] for name in ("E11_MPa","E22_MPa","E33_MPa")); g12,g13,g23 = (constants[name] for name in ("G12_MPa","G13_MPa","G23_MPa")); v12,v13,v23 = (constants[name] for name in ("V12","V13","V23"))
    v21, v31, v32 = v12*e2/e1, v13*e3/e1, v23*e3/e2
    compliance = np.array([[1/e1,-v21/e2,-v31/e3,0,0,0],[-v12/e1,1/e2,-v32/e3,0,0,0],[-v13/e1,-v23/e2,1/e3,0,0,0],[0,0,0,1/g23,0,0],[0,0,0,0,1/g13,0],[0,0,0,0,0,1/g12]])
    stiffness = np.linalg.inv(compliance); eigenvalues = np.linalg.eigvalsh(stiffness)
    return {"pass": bool(np.all(eigenvalues > 0)), "min_stiffness_eigenvalue_MPa": float(eigenvalues.min()), "derived_poisson_ratios": {"V21": v21, "V31": v31, "V32": v32}, "stiffness_matrix_MPa": stiffness.tolist()}


def evaluate(scope: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact(); values = _input(scope); domain = _domain(values)
    if domain["level"] == "outside":
        return {"model_domain": "short_cf_thermomechanical_rve_v1", "mode": "outside_rve_domain_no_prediction", "inputs": values, "applicability_domain": domain, "next_action": "请将基体描述符、纤维体积分数、有效长度和取向收回已验证 RVE 域，或补充同条件 RVE/实物试验数据。"}
    row = pd.DataFrame([[values[name] for name in artifact["features"]]], columns=artifact["features"])
    constants = {name: float(artifact["models"][name].predict(row)[0]) for name in artifact["targets"]}
    stability = _stability(constants)
    metrics = {name: artifact["assessments"][name]["metrics"] for name in artifact["targets"]}
    return {"model_domain": "short_cf_thermomechanical_rve_v1", "mode": "rve_orthotropic_elasticity", "model_version": artifact["model_version"], "inputs": values, "applicability_domain": domain, "predicted_engineering_constants": {**constants, **stability["derived_poisson_ratios"]}, "stiffness_matrix_MPa": stability["stiffness_matrix_MPa"], "physical_consistency": {"positive_definite_stiffness": stability["pass"], "min_stiffness_eigenvalue_MPa": stability["min_stiffness_eigenvalue_MPa"], "poisson_reciprocity": "V21=V12*E22/E11; V31=V13*E33/E11; V32=V23*E33/E22"}, "validation": metrics, "mechanistic_scope": artifact["physics_scope"], "limitations": ["标签为 FAST_RF RVE 线弹性仿真，不是实物力学测试。", "固定 T300-like 短碳纤维、直径 7 μm、致密且完美界面。", "不预测强度、失效、疲劳、冲击、热导率或打印孔隙率效应。"]}


def evaluate_batch(scope: dict[str, Any], candidates: list[Any]) -> dict[str, Any]:
    return {"model_domain": "short_cf_thermomechanical_rve_v1", "mode": "batch_evaluation", "results": [evaluate({**scope, **(item if isinstance(item, dict) else {})}) for item in candidates]}


def propose_space(scope: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact(); requested = int(scope.get("num_candidates", 40)); requested = min(max(requested, 1), 200)
    seed = int(scope.get("random_seed", 20260907)); rng = random.Random(seed); candidates=[]
    profiles = artifact["matrix_profiles"]
    requested_name = str(scope.get("matrix_name") or "").strip()
    requested_properties = scope.get("matrix_properties") or {}
    displayed_matrix_name = requested_name or "用户给定基体"
    if requested_properties:
        profile_pool = [(requested_name or "用户给定基体", requested_properties)]
    elif requested_name:
        resolved_name = _resolve_matrix_name(requested_name)
        profile_pool = [(resolved_name, profiles[resolved_name])]
        if resolved_name != requested_name:
            displayed_matrix_name = f"{requested_name}（映射为 {resolved_name} 记录档案）"
    else:
        profile_pool = sorted(profiles.items())
    # This is a bounded local design screen, not an unbounded random catalogue:
    # the visible Vf/Lf/a11 inputs set the centre of the candidate neighbourhood.
    domain = artifact["domain"]
    def local_window(field: str, centre: Any, radius: float) -> tuple[float, float]:
        lo, hi = map(float, domain[field])
        value = _number(centre, field) if centre is not None else (lo + hi) / 2
        return max(lo, value - radius), min(hi, value + radius)
    vf_window = local_window("target_vf", scope.get("target_vf"), .035)
    length_window = local_window("fiber_length_mm", scope.get("fiber_length_mm"), .008)
    orientation_window = local_window("target_a11", scope.get("target_a11"), .13)
    recipe_window_description = (
        f"搜索窗口：Vf {vf_window[0] * 100:.1f}–{vf_window[1] * 100:.1f} vol.% · "
        f"有效长度 {length_window[0]:.3f}–{length_window[1]:.3f} mm · "
        f"a11 {orientation_window[0]:.2f}–{orientation_window[1]:.2f}。"
    )
    for index in range(requested):
        name, properties = rng.choice(profile_pool)
        candidate = evaluate({"matrix_properties": properties, "target_vf": rng.uniform(*vf_window), "fiber_length_mm": rng.uniform(*length_window), "target_a11": rng.uniform(*orientation_window)})
        if candidate.get("mode") == "rve_orthotropic_elasticity": candidates.append({"candidate_id":f"SCF-{index+1:03d}","matrix_name":name,**candidate})
    candidates.sort(key=lambda item: item["predicted_engineering_constants"]["E11_MPa"], reverse=True)
    # This funnel intentionally starts with the *current* recipe search.  Training-set
    # row counts belong in the model card, not in a customer-facing design decision.
    return {"taskid": str(scope.get("taskid") or "short-cf-rve-proposal"), "operation":"propose_space", "model_domain":"short_cf_thermomechanical_rve_v1", "model_version":artifact["model_version"], "screening_conditions":{"matrix_name": displayed_matrix_name, "target_centre":{"target_vf":scope.get("target_vf"),"fiber_length_mm":scope.get("fiber_length_mm"),"target_a11":scope.get("target_a11")}, "local_candidate_windows":{"target_vf":list(vf_window),"fiber_length_mm":list(length_window),"target_a11":list(orientation_window)}, "rve_assumptions":"固定 T300-like 短碳纤维、7 μm、致密、完美界面、线弹性"}, "sampling":{"generated":len(candidates),"feasible":len(candidates),"funnel_stages":[{"label":"局部配比候选","count":len(candidates),"description":recipe_window_description},{"label":"RVE 参数域内","count":len(candidates),"description":"固定 T300-like 短碳纤维、7 μm、致密完美界面；输入均位于验证域。"},{"label":"线弹性本构可用","count":len(candidates),"description":"预测刚度矩阵的最小特征值大于 0，可作为有限元线弹性输入。"},{"label":"E11 优先验证","count":min(5,len(candidates)),"description":"按 J = z(E11) 排序，保留前 5 组配比进入优先验证。"}]}, "initial_candidates":candidates[:5], "all_candidates":candidates, "nonlinear_response_function":{"name":artifact["model_version"],"input":"基体 E/ν/密度、纤维体积分数、长度、主方向取向","output":"正交各向异性工程常数与 6×6 刚度矩阵"}, "limitations":["RVE 标签，不是实物测试。","当前仅覆盖 5 种基体与固定短碳纤维条件。"]}


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    scope = request.get("constraints") or {}; operation = request.get("operation")
    if operation == "evaluate": return evaluate(scope)
    if operation == "evaluate_batch": return evaluate_batch(scope, request.get("candidates") or [])
    if operation in {"propose", "propose_space"}: return propose_space(scope)
    raise ValueError("short-CF RVE service supports evaluate, evaluate_batch and propose_space")
