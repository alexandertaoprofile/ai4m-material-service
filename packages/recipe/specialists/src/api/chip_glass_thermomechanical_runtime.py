"""Standalone low-B alkali-free chip-glass thermo-mechanical service."""
from __future__ import annotations

import math
import csv
import random
import re
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CTE_PATH = ROOT / "models/chip_glass_cte_family_v1_20260902/model.joblib"
MECH_PATH = ROOT / "models/chip_glass_family_thermomechanical_candidate_v2_20260904/model.joblib"
ANCHOR_PATH = ROOT / "data/processed/chip_glass_substrate_us20250026678_gate_v1/glass_thermal_admitted.csv"
CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")
MASS = np.asarray((60.0843, 101.9613, 69.6202, 61.9789, 94.196, 29.8814, 40.3044, 56.0774, 153.326, 81.379, 79.866, 123.222, 141.9445))
D_ESTIMATE_TEMPERATURES_C = (25.0, 150.0, 260.0)
_CTE: dict[str, Any] | None = None
_MECH: dict[str, Any] | None = None
_ANCHORS: list[dict[str, Any]] | None = None

# These defaults are deliberately limited to the supported model definition.
# Package dimensions, stack materials and thermal history are not inferred.
DEFAULT_SERVICE_OPTIONS = {
    "report_style": "standard",
    "include_model_details": True,
    "include_simulation_input_card": True,
    "application": "配方筛选与封装热失配初筛",
    "cte_definition_temperature_range_C": [0, 300],
    "poisson_ratio": None,
    "package_stack": {
        "glass_thickness_mm": None,
        "temperature_cycle_C": None,
        "layer_stack_description": None,
        "constraint_boundary": None,
        "cooling_or_anneal_history": None,
    },
}


def _artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    global _CTE, _MECH
    if _CTE is None:
        _CTE = joblib.load(CTE_PATH)
    if _MECH is None:
        _MECH = joblib.load(MECH_PATH)
    return _CTE, _MECH


def _number(value: Any, name: str) -> float:
    try:
        answer = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(answer) or answer < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return answer


def _mechanical_vector(values: dict[str, float]) -> np.ndarray:
    c = np.asarray([values[name] for name in CORE]); si, al, b, na, k, li, mg, ca, ba, zn, ti, zr, p = c
    modifier = na + k + li + mg + ca + ba + zn
    charge = na + k + li + 2 * (mg + ca + ba + zn)
    network = si + al + b + p + ti + zr
    descriptors = np.asarray((np.dot(c, MASS) / 100, modifier, charge, network, charge - 2 * al, (charge - 2 * al) / max(network, 1e-9), (mg + ca + ba + zn) / max(al, 1e-9), b / max(si, 1e-9)))
    return np.r_[c, descriptors]


def _predict_properties_many(values_list: list[dict[str, float]]) -> list[dict[str, Any]]:
    """Batch-equivalent of the verified property-prediction portion of evaluate.

    Proposal generation previously called every estimator once per composition.
    The saved estimators support 2-D batches, so this keeps all predictions and
    ranking inputs identical while avoiding 80 repeated Python/model calls.
    """
    cte, mechanical = _artifacts(); features: list[str] = cte["features"]
    cte_values = cte["model"].predict(np.asarray([[values[name] for name in features] for values in values_list]))
    mechanical_vectors = np.asarray([_mechanical_vector({name: values.get(name, 0.0) for name in CORE}) for values in values_list])
    mechanical_values = {name: item["estimator"].predict(mechanical_vectors) for name, item in mechanical["models"].items()}
    output: list[dict[str, Any]] = []
    for index, prediction in enumerate(cte_values):
        properties: dict[str, Any] = {"CTE_linear_0_to_300C": {"prediction_ppm_per_K": float(prediction), "prediction": float(prediction) * 1e-6, "unit": "1/K", "validation_MAE_ppm_per_K": cte["report"]["metrics"]["mae_ppm_per_K"], "definition": "linear CTE averaged over 0–300°C"}}
        for name, item in mechanical["models"].items():
            assessment = item["summary"]
            properties[name] = {"prediction": float(mechanical_values[name][index]), "unit": item["unit"], "validation_MAE": assessment["metrics"]["mae"], "validation_RMSE": assessment["metrics"]["rmse"], "validation": "complete patent-table holdout within the low-B alkali-free family"}
        output.append(properties)
    return output


def _service_options(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("service_options must be an object")
    unknown = sorted(set(raw) - set(DEFAULT_SERVICE_OPTIONS))
    if unknown:
        raise ValueError(f"unsupported service_options: {unknown}")
    options = {**DEFAULT_SERVICE_OPTIONS, **raw}
    if options["report_style"] not in {"standard", "compact"}:
        raise ValueError("service_options.report_style must be 'standard' or 'compact'")
    if not isinstance(options["include_model_details"], bool) or not isinstance(options["include_simulation_input_card"], bool):
        raise ValueError("service_options include flags must be boolean")
    if not isinstance(options["application"], str) or not options["application"].strip():
        raise ValueError("service_options.application must be a non-empty string")
    if options["cte_definition_temperature_range_C"] != [0, 300]:
        raise ValueError("the current CTE prediction is defined only over 0–300°C; use [0, 300]")
    nu = options["poisson_ratio"]
    if nu is not None:
        nu = _number(nu, "service_options.poisson_ratio")
        if not 0 < nu < 0.5:
            raise ValueError("service_options.poisson_ratio must be between 0 and 0.5")
        options["poisson_ratio"] = nu
    stack = options["package_stack"]
    if not isinstance(stack, dict):
        raise ValueError("service_options.package_stack must be an object")
    unknown_stack = sorted(set(stack) - set(DEFAULT_SERVICE_OPTIONS["package_stack"]))
    if unknown_stack:
        raise ValueError(f"unsupported package_stack options: {unknown_stack}")
    options["package_stack"] = {**DEFAULT_SERVICE_OPTIONS["package_stack"], **stack}
    return options


def _display_or_pending(value: Any, unit: str = "") -> str:
    if value is None or value == "":
        return "待用户输入"
    if isinstance(value, list):
        return "–".join(str(item) for item in value) + unit
    return f"{value}{unit}"


def _engineering_estimates(values: dict[str, float], user_poisson_ratio: float | None) -> dict[str, Any]:
    """Return transparent D-level initial values; they are never ranking features.

    k(T) and Cp(T) have no retained same-identity labels.  The rules below are
    deliberately bounded composition-indexed reference rules, not fitted models:
    they make the initial finite-element card executable while preserving a
    visible uncertainty band for fictive-temperature, OH/defect and process
    effects absent from the composition-only service.
    """
    modifier = sum(values.get(name, 0.0) for name in ("Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "SrO"))
    k_25 = float(np.clip(1.17 + .003 * (values.get("SiO2", 0.0) - 70.0) + .004 * (values.get("Al2O3", 0.0) - 12.0) - .006 * (modifier - 15.0) - .002 * (values.get("B2O3", 0.0) - 2.0), .85, 1.55))
    cp_25 = float(np.clip(.775 + .0010 * (modifier - 15.0) + .0015 * (values.get("B2O3", 0.0) - 2.0) - .0009 * (values.get("Al2O3", 0.0) - 12.0), .68, .92))
    k_curve = [{"temperature_C": temperature, "value": k_25 * (1.0 - .00035 * (temperature - 25.0)), "uncertainty": k_25 * (1.0 - .00035 * (temperature - 25.0)) * .25} for temperature in D_ESTIMATE_TEMPERATURES_C]
    cp_curve = [{"temperature_C": temperature, "value": cp_25 * (1.0 + .00035 * (temperature - 25.0)), "uncertainty": cp_25 * (1.0 + .00035 * (temperature - 25.0)) * .15} for temperature in D_ESTIMATE_TEMPERATURES_C]
    return {
        "poisson_ratio": {
            "tier": "D_engineering_estimate", "value": .230, "uncertainty": .008, "unit": "1",
            "display": "0.230 ± 0.008", "basis": "同家族 36 条实测值的 0.222–0.237 范围；组成模型未优于留出均值基线。",
            "simulation_value": user_poisson_ratio if user_poisson_ratio is not None else .230,
            "simulation_value_source": "user_input" if user_poisson_ratio is not None else "D_engineering_estimate",
        },
        "thermal_conductivity_k_T": {
            "tier": "D_engineering_estimate", "unit": "W/(m·K)", "curve": k_curve,
            "basis": "组成索引参考规则：SiO2/Al2O3 网络骨架与改性氧化物含量修正；25–260°C 采用弱降温趋势。无同身份近域训练标签。",
            "uncertainty_basis": "±25%，覆盖虚温、OH/缺陷、密度和退火状态未显式建模的影响。",
        },
        "specific_heat_Cp_T": {
            "tier": "D_engineering_estimate", "unit": "J/(g·K)", "curve": cp_curve,
            "basis": "组成索引参考规则：改性氧化物、B2O3 与 Al2O3 修正 25°C 基准值；25–260°C 采用平滑升温趋势。无同身份近域训练标签。",
            "uncertainty_basis": "±15%，覆盖虚温、含水/杂质和热史未显式建模的影响。",
        },
    }


def _curve_display(estimate: dict[str, Any], digits: int = 3) -> str:
    return "；".join(f"{point['temperature_C']:.0f}°C：{point['value']:.{digits}f} ± {point['uncertainty']:.{digits}f}" for point in estimate["curve"])


def _customer_markdown(properties: dict[str, Any], engineering: dict[str, Any], inputs: dict[str, Any], options: dict[str, Any], applicability: dict[str, Any]) -> str:
    cte = properties["CTE_linear_0_to_300C"]
    stack = options["package_stack"]
    nu_value = _display_or_pending(options["poisson_ratio"])
    lines = [
        "# 芯片封装玻璃基板配方—热机械初筛结果",
        "",
        "## 1. 需求与已知工况",
        "",
        "| 项目 | 本轮值 |", "|---|---|",
        f"| 应用目标 | {options['application']} |",
        "| 组分基准 | mol%（16 个已登记氧化物） |",
        f"| 配方总和 | {inputs['total_mol_percent']:.3f} mol% |",
        "| CTE 输出定义 | 线膨胀系数，0–300°C 平均值（当前固定定义） |",
        f"| 玻璃厚度 | {_display_or_pending(stack['glass_thickness_mm'], ' mm')} |",
        f"| 温度循环 | {_display_or_pending(stack['temperature_cycle_C'], ' °C')} |",
        f"| 层堆描述 | {_display_or_pending(stack['layer_stack_description'])} |",
        f"| 约束边界 | {_display_or_pending(stack['constraint_boundary'])} |",
        f"| 冷却/退火历史 | {_display_or_pending(stack['cooling_or_anneal_history'])} |",
        "",
        "## 2. 本轮筛选与比较口径",
        "",
        "- 已通过：配方总和 99–101 mol% 校验，以及低硼无碱铝硼硅酸盐家族的逐氧化物记录边界校验。",
        "- 已验证模型输出：CTE、密度、杨氏模量、应力光学系数（SOC）和两项黏度特征温度；泊松比、k(T)、Cp(T) 作为带不确定度的 D 级仿真初值，不参与候选排序。",
        "- 默认报告为 `standard`；可改为 `compact`。CTE 温区目前只能使用 0–300°C，不能改写为其他温区的预测。",
        "",
        "## 3. 证据覆盖与候选核验",
        "",
        "| 核验漏斗 | 本轮结果 |", "|---|---|",
        "| 输入候选配方 | 1 |", "| 组成总和通过 | 1 |", "| 家族逐氧化物边界通过 | 1 |", "| 已验证模型输出 | 6 |", "| D 级仿真初值 | 泊松比、k(T)、Cp(T) |", "| 仍需项目输入 | 层堆与热历史 |",
        "",
        f"候选属于 `{applicability['family']}`；CTE 训练记录来自 {len(applicability['source_tables'])} 个来源表，热机械属性仅在同家族专利表内完成留出验证。",
        "",
        "## 4. 结论",
        "",
        "该配方在当前低硼无碱玻璃家族内，可作为封装热失配、板挠曲和 SOC 敏感性的第一轮材料侧输入。残余内应力与翘曲必须由材料、层堆、几何、边界和热历史共同计算，不能从配方单独给出。",
        "",
        "## 5. 材料性质汇总与仿真输入",
        "",
        "### 候选玻璃性质卡",
        "",
        "| 玻璃侧性质 | 预测值 | 家族内留出验证 MAE | 用途 |", "|---|---:|---:|---|",
        f"| CTE（0–300°C） | {cte['prediction_ppm_per_K']:.3f} ppm/K | {cte['validation_MAE_ppm_per_K']:.3f} ppm/K | 热失配应变 |",
        f"| 密度 | {properties['density_g_per_cm3']['prediction']:.4f} g/cm³ | {properties['density_g_per_cm3']['validation_MAE']:.4f} g/cm³ | 质量/惯性 |",
        f"| 杨氏模量 E | {properties['young_modulus_GPa']['prediction']:.2f} GPa | {properties['young_modulus_GPa']['validation_MAE']:.3f} GPa | 热弹性/挠曲 |",
        f"| 应力光学系数 SOC | {properties['stress_optical_coefficient_nm_cm_per_MPa']['prediction']:.2f} nm/cm/MPa | {properties['stress_optical_coefficient_nm_cm_per_MPa']['validation_MAE']:.3f} nm/cm/MPa | 光学应力敏感性 |",
        f"| 200 poise 温度 | {properties['viscosity_temperature_200_poise_C']['prediction']:.1f} °C | {properties['viscosity_temperature_200_poise_C']['validation_MAE']:.2f} °C | 成形窗口参照 |",
        f"| 35 kpoise 温度 | {properties['viscosity_temperature_35kpoise_C']['prediction']:.1f} °C | {properties['viscosity_temperature_35kpoise_C']['validation_MAE']:.2f} °C | 成形窗口参照 |",
    ]
    if options["include_simulation_input_card"]:
        lines += [
            "", "### 封装仿真输入卡", "",
            "| 输入项 | 当前值/默认 | 使用说明 |", "|---|---|---|",
            f"| 泊松比 ν | {nu_value if options['poisson_ratio'] is not None else engineering['poisson_ratio']['display']} | {'用户提供值' if options['poisson_ratio'] is not None else 'D 级估算；同批实测优先'} |",
            f"| 热导率 k(T) | {_curve_display(engineering['thermal_conductivity_k_T'])} W/(m·K) | D 级估算，单点 ±25%；同批测量后替换 |",
            f"| 比热 Cp(T) | {_curve_display(engineering['specific_heat_Cp_T'])} J/(g·K) | D 级估算，单点 ±15%；同批测量后替换 |",
            "| 层堆及厚度 | 待用户输入 | Si/Cu/RDL/树脂的温度相关性质、层厚和边界 |",
            "", "$$\\Delta\\varepsilon=[\\alpha_{glass}-\\alpha_{stack}]\\Delta T$$",
        ]
    if options["include_model_details"] and options["report_style"] == "standard":
        lines += [
            "", "### 模型与适用性说明", "",
            "- CTE 是 0–300°C 的定义值；其余性质为当前玻璃家族内的配方筛选预测，并附家族内留出误差。",
            "- D 级 k(T)/Cp(T) 是透明的组成索引参考规则，并非由同身份标签训练的模型；其 ± 区间覆盖未输入的虚温、退火状态与缺陷影响。",
            "- 当任一氧化物超出记录边界时，服务拒绝给出数值预测，改为提示补充同配方实测。",
        ]
    if options["report_style"] == "standard":
        lines += [
            "", "## 用户可调整项与默认值", "",
            "| 参数 | 默认值 | 可调整内容 |", "|---|---|---|",
            "| `application` | 配方筛选与封装热失配初筛 | 项目/比较目标文字；不改变模型预测 |",
            "| `report_style` | `standard` | `standard` 或 `compact` |",
            "| `include_model_details` | `true` | 显示或隐藏模型适用性说明 |",
            "| `include_simulation_input_card` | `true` | 显示或隐藏仿真待补输入卡 |",
            "| `cte_definition_temperature_range_C` | `[0, 300]` | 当前不可改为其他温区 |",
            "| `poisson_ratio` | 未填（历史范围 0.222–0.237） | 填写同批实测值，供仿真使用 |",
            "| `package_stack` | 厚度、循环、层堆、边界、热历史均未填 | 填写实际项目条件，供后续热—力求解使用 |",
            "", "`k(T)` 与 `Cp(T)` 默认给出 25/150/260°C 的 D 级估算；同批玻璃测量值会优先替换估算，不参与当前配方筛选。",
        ]
    return "\n".join(lines)


def _outside_customer_markdown(inputs: dict[str, Any], applicability: dict[str, Any], options: dict[str, Any]) -> str:
    names = "、".join(applicability["outside_recorded_bounds"])
    return "\n".join([
        "# 芯片封装玻璃基板配方—热机械初筛结果", "",
        "## 1. 需求与已知工况", "", f"应用目标：{options['application']}。配方总和：{inputs['total_mol_percent']:.3f} mol%。",
        "", "## 2. 本轮筛选与比较口径", "", "服务只对低硼无碱铝硼硅酸盐家族、CTE 定义为 0–300°C 的配方给出数值预测。",
        "", "## 3. 证据覆盖与候选核验", "", f"候选未通过逐氧化物边界：{names}。因此没有输出任何材料性质预测。",
        "", "## 4. 结论", "", "当前配方超出已验证家族；不能将本服务结果用于热失配、内应力或翘曲计算。",
        "", "## 5. 建议补充的证据", "", "优先补充同配方的 CTE（0–300°C）、杨氏模量、泊松比、k(T)、Cp(T)，以及层堆、几何与退火/冷却历史，再进行封装仿真或扩域建模。",
    ])


def _family_anchors(features: list[str], bounds: dict[str, list[float]]) -> list[dict[str, Any]]:
    """Load only traceable same-family compositions as local proposal anchors."""
    global _ANCHORS
    if _ANCHORS is not None:
        return _ANCHORS
    anchors: list[dict[str, Any]] = []
    with ANCHOR_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("property_name") != "CTE_linear_0_to_300C":
                continue
            values = {name: float(row.get(name) or 0.0) for name in features}
            for oxide, value in re.findall(r"([A-Za-z0-9]+)=([0-9.]+) mol%", row.get("other_oxides_text") or ""):
                if oxide in values:
                    values[oxide] = float(value)
            total = sum(values.values())
            if not 99.0 <= total <= 101.0:
                continue
            if any(values[name] < bounds[name][0] or values[name] > bounds[name][1] for name in features):
                continue
            anchors.append({"glass_id": row["glass_id"], "source_locator": row["source_locator"], "composition_mol_percent": values})
    if not anchors:
        raise RuntimeError("no traceable same-family glass anchors were available for proposal")
    _ANCHORS = anchors
    return anchors


def _proposal_constraints(scope: dict[str, Any], features: list[str], bounds: dict[str, list[float]]) -> tuple[dict[str, list[float]], dict[str, Any], dict[str, Any], int, int]:
    raw_bounds = scope.get("oxide_bounds_mol_percent") or {}
    if not isinstance(raw_bounds, dict):
        raise ValueError("oxide_bounds_mol_percent must be an object")
    selected = {name: [float(bounds[name][0]), float(bounds[name][1])] for name in features}
    for name, pair in raw_bounds.items():
        if name not in selected or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"invalid oxide bound for {name}")
        low, high = _number(pair[0], f"oxide_bounds_mol_percent.{name}[0]"), _number(pair[1], f"oxide_bounds_mol_percent.{name}[1]")
        if low > high or low < bounds[name][0] or high > bounds[name][1]:
            raise ValueError(f"oxide bound for {name} must stay within the recorded family range {bounds[name]}")
        selected[name] = [low, high]
    objectives = scope.get("objectives") or {
        "CTE_linear_0_to_300C": {"goal": "minimize"},
        "young_modulus_GPa": {"goal": "maximize"},
        "stress_optical_coefficient_nm_cm_per_MPa": {"goal": "minimize"},
    }
    if not isinstance(objectives, dict) or not objectives:
        raise ValueError("objectives must be a non-empty object")
    allowed_objectives = {"CTE_linear_0_to_300C", "density_g_per_cm3", "young_modulus_GPa", "stress_optical_coefficient_nm_cm_per_MPa", "viscosity_temperature_200_poise_C", "viscosity_temperature_35kpoise_C"}
    for name, setting in objectives.items():
        if name not in allowed_objectives or not isinstance(setting, dict) or setting.get("goal") not in {"maximize", "minimize"}:
            raise ValueError(f"unsupported objective: {name}")
    thresholds = scope.get("screening_thresholds") or {}
    if not isinstance(thresholds, dict):
        raise ValueError("screening_thresholds must be an object")
    for name, value in thresholds.items():
        if name not in {"CTE_linear_0_to_300C_max_ppm_per_K", "density_g_per_cm3_max", "young_modulus_GPa_min", "stress_optical_coefficient_nm_cm_per_MPa_max"}:
            raise ValueError(f"unsupported screening threshold: {name}")
        _number(value, f"screening_thresholds.{name}")
    num_candidates = int(scope.get("num_candidates", 80))
    if not 1 <= num_candidates <= 500:
        raise ValueError("num_candidates must be 1–500")
    seed = int(scope.get("random_seed", 20260904))
    return selected, objectives, thresholds, num_candidates, seed


def _property_value(properties: dict[str, Any], name: str) -> float:
    if name == "CTE_linear_0_to_300C":
        return float(properties[name]["prediction_ppm_per_K"])
    return float(properties[name]["prediction"])


def _passes_thresholds(properties: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    values = {
        "CTE_linear_0_to_300C_max_ppm_per_K": _property_value(properties, "CTE_linear_0_to_300C"),
        "density_g_per_cm3_max": _property_value(properties, "density_g_per_cm3"),
        "young_modulus_GPa_min": _property_value(properties, "young_modulus_GPa"),
        "stress_optical_coefficient_nm_cm_per_MPa_max": _property_value(properties, "stress_optical_coefficient_nm_cm_per_MPa"),
    }
    for name, limit in thresholds.items():
        if name.endswith("_min") and values[name] < float(limit):
            return False
        if name.endswith("_max") and values[name] > float(limit):
            return False
    return True


def _proposal_markdown(result: dict[str, Any]) -> str:
    scope = result["search_space"]
    sampling = result["sampling"]
    candidates = result["initial_candidates"]
    lines = [
        "# 玻璃基板配方探索结果", "",
        "## 需求解读", "",
        f"- 模型域：`{result['model_domain']}`（低硼无碱铝硼硅酸盐玻璃）。",
        "- 成分基准：氧化物 mol%，总和固定为 100 mol%。",
        "- 目标：" + "；".join(
            f"{key} {value['goal']}" for key, value in result["screening_criteria"].items()
        ) + "。",
        "", "## 模型与响应", "",
        "| 项目 | 当前定义 |", "|---|---|",
        f"| 评价器 | `{result['nonlinear_response_function']['name']}` |",
        "| 输入 | 16 个氧化物 mol%，总和 100 mol%；仅同家族局部邻域 |",
        "| 输出 | 六项已验证预测；另附 ν、k(T)、Cp(T) 的 D 级带不确定度仿真初值 |",
        "| 候选生成 | 可追溯专利玻璃锚点；Al2O3/B2O3/MgO/CaO/BaO/SrO 小幅扰动，SiO2 平衡 |",
        "", "## 搜索与筛选", "",
        f"- 生成候选：{sampling['generated']}；通过性质门槛：{sampling['property_threshold_passed']}；展示初始种群：{len(candidates)}。",
        "- 候选只在具有可追溯来源的同家族玻璃配方附近作小幅氧化物扰动，未把远域高碱或高硼玻璃混入搜索空间。",
        "", "| 阶段 | 保留数量 | 说明 |", "|---|---:|---|",
        *[f"| {item['label']} | {item['count']} | {item['description']} |" for item in sampling['funnel_stages']],
        "", "## 初始候选（非最终最优）", "",
        "| 排名 | 配方（mol%） | CTE（0–300°C） | 杨氏模量 E | SOC | 适用域 |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for index, item in enumerate(candidates, 1):
        composition = "；".join(f"{name} {value:.2f}" for name, value in item["composition_mol_percent"].items() if value > 0)
        props = item["predicted_properties"]
        lines.append(f"| {index} | {composition} | {_property_value(props, 'CTE_linear_0_to_300C'):.3f} ppm/K | {_property_value(props, 'young_modulus_GPa'):.2f} GPa | {_property_value(props, 'stress_optical_coefficient_nm_cm_per_MPa'):.2f} nm/cm/MPa | 同家族局部邻域 |")
    if candidates:
        top = candidates[0]
        props = top["predicted_properties"]
        anchor = top["source_anchor"]
        lines += [
            "", "## 优先候选的完整性质卡", "",
            "| 项目 | 当前结果 |", "|---|---|",
            f"| 候选编号 | {top['candidate_id']} |",
            f"| 来源锚点 | {anchor['glass_id']}；{anchor['source_locator']} |",
            f"| CTE（0–300°C） | {_property_value(props, 'CTE_linear_0_to_300C'):.3f} ppm/K；家族内留出验证 MAE {props['CTE_linear_0_to_300C']['validation_MAE_ppm_per_K']:.3f} ppm/K |",
            f"| 密度 | {_property_value(props, 'density_g_per_cm3'):.4f} g/cm³；MAE {props['density_g_per_cm3']['validation_MAE']:.4f} g/cm³ |",
            f"| 杨氏模量 E | {_property_value(props, 'young_modulus_GPa'):.2f} GPa；MAE {props['young_modulus_GPa']['validation_MAE']:.3f} GPa |",
            f"| 应力光学系数 SOC | {_property_value(props, 'stress_optical_coefficient_nm_cm_per_MPa'):.2f} nm/cm/MPa；MAE {props['stress_optical_coefficient_nm_cm_per_MPa']['validation_MAE']:.3f} nm/cm/MPa |",
            f"| 200 poise 温度 | {_property_value(props, 'viscosity_temperature_200_poise_C'):.1f} °C；MAE {props['viscosity_temperature_200_poise_C']['validation_MAE']:.2f} °C |",
            f"| 35 kpoise 温度 | {_property_value(props, 'viscosity_temperature_35kpoise_C'):.1f} °C；MAE {props['viscosity_temperature_35kpoise_C']['validation_MAE']:.2f} °C |",
            f"| 泊松比 ν（D 级） | {top['engineering_estimates']['poisson_ratio']['display']}；同批实测优先 |",
            f"| 热导率 k(T)（D 级） | {_curve_display(top['engineering_estimates']['thermal_conductivity_k_T'])} W/(m·K)；各点 ±25% |",
            f"| 比热 Cp(T)（D 级） | {_curve_display(top['engineering_estimates']['specific_heat_Cp_T'])} J/(g·K)；各点 ±15% |",
            "| 待项目输入 | 层堆、厚度、约束和热历史；同批实测可替换所有 D 级估算 |",
        ]
    lines += [
        "", "## 优化交接与用户说明", "",
        "| 交接项目 | 结构化字段/接口 | 用户解读 |", "|---|---|---|",
        "| 搜索变量 | `search_space` | 氧化物边界、总和=100 mol%、来源锚点约束。 |",
        "| 筛选目标 | `screening_criteria` | 可修改 CTE、E、密度、SOC 与黏度特征温度的最大化/最小化方向。 |",
        "| 初始种群 | `initial_candidates` | 返回前五个性质门槛通过的候选，作为下一轮实验或优化起点。 |",
        "| 非线性评价器 | `evaluate` / `evaluate_batch` | 返回六项已验证预测及三项 D 级带不确定度仿真初值。 |",
        "", "## 默认假设（可修改）", "",
        "| 字段 | 当前默认值 | 用户可调整范围 |", "|---|---|---|",
        f"| `oxide_bounds_mol_percent` | 当前记录家族边界（{len(scope['oxide_bounds_mol_percent'])} 个氧化物） | 只能在已验证边界内收窄。 |",
        f"| `num_candidates` | {scope['num_candidates']} | 1–500。 |",
        f"| `random_seed` | {scope['random_seed']} | 固定后可复现同一候选池。 |",
        "| `screening_thresholds` | 默认无硬阈值 | 可输入 CTE/SOC 上限、密度上限和 E 下限。 |",
        "", "## 结论与下一步", "",
        result["user_conclusion"], "",
        "将优先候选与来源锚点并列制样，实测 CTE、E、泊松比、k(T)、Cp(T)；带入实际层堆、厚度和热历史后计算残余应力与翘曲。",
    ]
    return "\n".join(lines)


def propose_space(scope: dict[str, Any]) -> dict[str, Any]:
    """1111-style constrained initial population, restricted to traceable local anchors."""
    cte, _ = _artifacts(); features: list[str] = cte["features"]
    bounds = cte["report"]["training_min_max_mol_percent"]
    selected_bounds, objectives, thresholds, requested, seed = _proposal_constraints(scope, features, bounds)
    anchors = _family_anchors(features, bounds)
    randomizer = random.Random(seed)
    raw_candidates: list[tuple[int, dict[str, Any], dict[str, float]]] = []
    # Preserve the deterministic proposal stream, but defer model inference to
    # one 2-D batch.  This leaves anchor choice, local perturbation, bounds,
    # thresholds and returned candidate order unchanged.
    for attempts in range(1, requested * 80 + 1):
        anchor = randomizer.choice(anchors)
        values = dict(anchor["composition_mol_percent"])
        # Preserve 100 mol% by balancing SiO2 after small, local modifier changes.
        changed = randomizer.sample(["Al2O3", "B2O3", "MgO", "CaO", "BaO", "SrO"], k=2)
        for name in changed:
            delta = randomizer.uniform(-0.12, 0.12)
            values[name] += delta
            values["SiO2"] -= delta
        if any(values[name] < selected_bounds[name][0] or values[name] > selected_bounds[name][1] for name in features):
            continue
        raw_candidates.append((attempts, anchor, values))
        # With no hard property gate (the platform default), every valid local
        # perturbation is retained, so there is no reason to evaluate the
        # remaining random stream.
        if not thresholds and len(raw_candidates) == requested:
            break
    properties_batch = _predict_properties_many([values for _, _, values in raw_candidates])
    all_candidates: list[dict[str, Any]] = []
    attempts = 0
    for attempt, anchor, values, properties in zip((item[0] for item in raw_candidates), (item[1] for item in raw_candidates), (item[2] for item in raw_candidates), properties_batch):
        if not _passes_thresholds(properties, thresholds):
            continue
        all_candidates.append({"candidate_id": f"CGS-{len(all_candidates)+1:03d}", "source_anchor": {"glass_id": anchor["glass_id"], "source_locator": anchor["source_locator"], "composition_mol_percent": anchor["composition_mol_percent"]}, "composition_mol_percent": values, "predicted_properties": properties, "engineering_estimates": _engineering_estimates(values, None), "applicability_domain": {"level": "inside", "meaning": "同家族来源锚点的局部成分邻域"}})
        if len(all_candidates) == requested:
            attempts = attempt
            break
    if len(all_candidates) < requested:
        attempts = raw_candidates[-1][0] if raw_candidates else requested * 80
    for name, setting in objectives.items():
        ordered = sorted(range(len(all_candidates)), key=lambda i: _property_value(all_candidates[i]["predicted_properties"], name), reverse=setting["goal"] == "maximize")
        for rank, index in enumerate(ordered):
            all_candidates[index].setdefault("_scores", []).append(1.0 - rank / max(len(ordered) - 1, 1))
    for candidate in all_candidates:
        candidate["selection_score"] = sum(candidate.pop("_scores", [0.0])) / len(objectives)
    all_candidates.sort(key=lambda item: item["selection_score"], reverse=True)
    initial = all_candidates[:5]
    percentiles = {
        name: {"p5": float(np.percentile([item["composition_mol_percent"][name] for item in all_candidates], 5)),
               "p50": float(np.percentile([item["composition_mol_percent"][name] for item in all_candidates], 50)),
               "p95": float(np.percentile([item["composition_mol_percent"][name] for item in all_candidates], 95))}
        for name in features if any(item["composition_mol_percent"][name] > 0 for item in all_candidates)
    } if all_candidates else {}
    result = {
        "taskid": str(scope.get("taskid") or "chip-glass-local-proposal"), "operation": "propose_space",
        "model_domain": "chip_glass_thermomechanical_family_v1", "mode": "constrained_local_proposal", "model_version": "chip_glass_family_thermomechanical_v1_20260904",
        "raw_requirement": str(scope.get("raw_requirement") or scope.get("application") or "玻璃基板配方与封装热失配初筛"),
        "requirement_interpretation": {"template": "chip_glass_thermomechanical_local_screening", "effective_model_input": {"model_domain": "chip_glass_thermomechanical_family_v1", "composition_basis": "mol_percent", "objectives": objectives, "screening_thresholds": thresholds, "num_candidates": requested}, "field_provenance": {"model_domain": "platform_fixed", "composition_basis": "platform_fixed", "objectives": "user" if scope.get("objectives") else "platform_default", "screening_thresholds": "user" if scope.get("screening_thresholds") else "platform_default", "num_candidates": "user" if scope.get("num_candidates") else "platform_default"}},
        "screening_conditions": {"composition_basis": "mol_percent", "composition_total_mol_percent": 100.0, "CTE_definition": "linear CTE averaged over 0–300°C", "candidate_generation": "traceable same-family local perturbation"},
        "search_space": {"composition_basis": "mol_percent", "composition_total": 100.0, "oxide_bounds_mol_percent": selected_bounds, "anchor_policy": "traceable_same_family_local_perturbation", "num_candidates": requested, "random_seed": seed},
        "screening_criteria": objectives, "screening_thresholds": thresholds,
        "sampling": {"generated": len(all_candidates), "property_threshold_passed": len(all_candidates), "attempts": attempts, "funnel_stages": [
            {"label": "同家族来源锚点", "count": len(anchors), "description": "只使用有可追溯组分记录的低硼无碱玻璃实例。"},
            {"label": "局部氧化物边界扰动", "count": len(all_candidates), "description": "扰动后仍满足 100 mol% 与逐氧化物记录边界。"},
            {"label": "性质门槛通过", "count": len(all_candidates), "description": "按用户提供的硬阈值过滤；默认不设硬阈值。"},
            {"label": "综合优先短名单", "count": len(initial), "description": "按用户目标方向的等权排序。"},
        ]},
        "all_candidates": all_candidates, "initial_candidates": initial,
        "derived_candidate_percentiles_mol_percent": percentiles,
        "nonlinear_response_function": {"name": "chip_glass_thermomechanical_family_v1", "meaning": "氧化物 mol% 到 CTE、密度、E、SOC 和黏度特征温度的家族内筛选预测；附泊松比、k(T)、Cp(T) 的 D 级仿真初值。", "input": "16 氧化物 mol%，总和 100 mol%", "output": "六项已验证预测、三项 D 级估算、家族内验证误差与适用域"},
        "downstream_handoff": {"decision_variables": "search_space.oxide_bounds_mol_percent", "initial_population": "initial_candidates", "evaluation_contract": "chip-glass evaluate/evaluate_batch", "do_not_treat_as_hard_bounds": "initial_candidates composition percentiles"},
        "assumptions": [{"field": "composition_basis", "value": "mol_percent", "status": "platform_fixed"}, {"field": "CTE_definition", "value": "0–300°C", "status": "platform_fixed"}, {"field": "anchor_policy", "value": "traceable_same_family_local_perturbation", "status": "platform_fixed"}, {"field": "objectives", "value": objectives, "status": "user" if scope.get("objectives") else "platform_default"}],
        "limitations": ["候选仅限 US20250026678 同家族局部组成邻域。", "泊松比、k(T) 与 Cp(T) 为 D 级带不确定度的仿真初值，不参与配方排序；残余内应力与翘曲仍需层堆和热历史求解。", "筛选排序用于制样和验证优先级，不构成工程放行。"],
        "presentation": {"report_contract": "1111_composition_optimization_v1", "summary_sections": ["需求解读", "模型与响应", "搜索与筛选", "初始候选", "完整性质卡", "优化交接", "默认假设", "结论与下一步"], "visual_assets": []},
    }
    result["user_conclusion"] = (f"在低硼无碱玻璃基板家族内，已生成 {len(all_candidates)} 个满足当前边界与性质门槛的局部候选；优先候选用于下一轮制样和封装仿真输入收敛，不代表残余应力或翘曲已被直接优化。" if initial else "当前边界与性质门槛下未形成可展示候选；请放宽用户设定的家族内氧化物边界或性质门槛后重试。")
    result["customer_markdown"] = _proposal_markdown(result)
    return result


def evaluate(scope: dict[str, Any]) -> dict[str, Any]:
    if scope.get("composition_basis") != "mol_percent":
        raise ValueError("chip_glass_thermomechanical_family_v1 requires composition_basis='mol_percent'")
    raw = scope.get("composition_mol_percent")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("composition_mol_percent must be a non-empty oxide mol% object")
    options = _service_options(scope.get("service_options"))
    cte, mechanical = _artifacts(); features: list[str] = cte["features"]
    unknown = sorted(set(map(str, raw)) - set(features))
    if unknown:
        raise ValueError(f"unsupported oxides for the low-B glass family: {unknown}")
    values = {name: _number(raw.get(name, 0.0), f"composition_mol_percent.{name}") for name in features}
    total = sum(values.values())
    if not 99.0 <= total <= 101.0:
        raise ValueError("composition_mol_percent must sum to 99-101 mol%")
    bounds = cte["report"]["training_min_max_mol_percent"]
    outside = [name for name, value in values.items() if value < bounds[name][0] or value > bounds[name][1]]
    inputs = {"composition_basis": "mol_percent", "composition_mol_percent": values, "total_mol_percent": total}
    if outside:
        applicability = {"level": "outside", "outside_recorded_bounds": outside, "recorded_bounds_mol_percent": {name: bounds[name] for name in outside}}
        return {"model_domain": "chip_glass_thermomechanical_family_v1", "mode": "outside_family_no_prediction", "inputs": inputs, "service_options": options,
                "applicability_domain": applicability, "next_action": "该配方超出低硼无碱玻璃族的记录边界；请补充同配方 CTE、E、泊松比和热导/比热实测，再进入封装仿真。",
                "customer_markdown": _outside_customer_markdown(inputs, applicability, options)}
    cte_prediction = float(cte["model"].predict(np.asarray([[values[name] for name in features]]))[0])
    vector = _mechanical_vector({name: values.get(name, 0.0) for name in CORE})[None, :]
    properties: dict[str, Any] = {
        "CTE_linear_0_to_300C": {"prediction_ppm_per_K": cte_prediction, "prediction": cte_prediction * 1e-6, "unit": "1/K", "validation_MAE_ppm_per_K": cte["report"]["metrics"]["mae_ppm_per_K"], "definition": "linear CTE averaged over 0–300°C"}
    }
    for name, item in mechanical["models"].items():
        assessment = item["summary"]
        properties[name] = {"prediction": float(item["estimator"].predict(vector)[0]), "unit": item["unit"], "validation_MAE": assessment["metrics"]["mae"], "validation_RMSE": assessment["metrics"]["rmse"], "validation": "complete patent-table holdout within the low-B alkali-free family"}
    engineering = _engineering_estimates(values, options["poisson_ratio"])
    applicability = {"level": "inside", "family": "US20250026678 low-B alkali-free alumino(boro)silicate family", "source_tables": cte["report"]["source_tables"]}
    result = {"model_domain": "chip_glass_thermomechanical_family_v1", "mode": "thermomechanical_family_screening", "model_version": mechanical["model_version"], "inputs": inputs, "service_options": options,
            "applicability_domain": applicability,
            "predicted_properties": properties, "engineering_estimates": engineering,
            "simulation_inputs_to_measure": {"poisson_ratio": {"status": "user_input" if options["poisson_ratio"] is not None else "D_engineering_estimate", "same_family_observed_range": [0.222, 0.237], "unit": "1", "display": engineering["poisson_ratio"]["display"]}, "thermal_conductivity_k_T": {"status": "D_engineering_estimate", "unit": "W/(m·K)", "display": _curve_display(engineering["thermal_conductivity_k_T"])}, "specific_heat_Cp_T": {"status": "D_engineering_estimate", "unit": "J/(g·K)", "display": _curve_display(engineering["specific_heat_Cp_T"])}, "package_stack": {"status": "required", "fields": ["Si/Cu/RDL/polymer temperature-dependent properties", "layer thicknesses", "constraint boundary", "temperature cycle", "glass cooling/anneal history"]}},
            "stress_interpretation": "CTE、ρ、E 和 SOC 可用于热失配、板挠曲与应力光学敏感性初筛；残余应力与翘曲由层堆、几何和热历史共同求解。",
            "mechanistic_scope": "composition plus deterministic oxide stoichiometric descriptors; no CALPHAD, fictive-temperature, cooling-rate or residual-stress constitutive constraint."}
    result["customer_markdown"] = _customer_markdown(properties, engineering, inputs, options, applicability)
    return result


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "evaluate":
        return evaluate(request.get("constraints") or {})
    if operation == "evaluate_batch":
        base = request.get("constraints") or {}
        candidates = request.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("evaluate_batch requires a non-empty candidates list")
        results = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("each batch candidate must be an object")
            composition = candidate.get("composition_mol_percent", candidate)
            results.append(evaluate({**base, "composition_basis": "mol_percent", "composition_mol_percent": composition}))
        return {"model_domain": "chip_glass_thermomechanical_family_v1", "mode": "batch_evaluation", "results": results,
                "nonlinear_response_function": "chip_glass_thermomechanical_family_v1"}
    if operation == "propose_space":
        return propose_space(request.get("constraints") or {})
    raise ValueError("chip-glass thermo-mechanical family service supports evaluate, evaluate_batch and propose_space")
