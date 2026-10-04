"""Dependency-light, explicit CSP/DNG contract; no inferred element recipes."""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Any

from .schemas import GenerationConstraint

ELEMENTS = "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm".split()
SUPPORTED = frozenset(ELEMENTS)
PROPERTY_UNITS = {"band_gap": "eV", "bulk_modulus": "GPa", "shear_modulus": "GPa", "dielectric_constant_x": "1", "electron_effective_mass": "m0", "hole_effective_mass": "m0"}


def omatg_enabled() -> bool:
    return os.getenv("NEW_MATERIAL_GENERATOR", "mattergen").lower() == "omatg"


def integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{name} 必须是 {low}–{high} 的整数。")
    parsed = int(value)
    if not low <= parsed <= high:
        raise ValueError(f"{name} 必须是 {low}–{high} 的整数。")
    return parsed


def formula_counts(formula: str) -> dict[str, int]:
    """Exact integer formula with nested parentheses; reject disorder/percentages."""
    formula = formula.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
    tokens = re.findall(r"[A-Z][a-z]?|[0-9]+|[()]", formula)
    if not formula or "".join(tokens) != formula or len(formula) > 120:
        raise ValueError("化学式需使用明确的整数计量（例如 LiAlO2）；小数占位、wt.%/at.% 配方不能直接用于 CSP。")
    stack = [Counter()]
    position = 0
    while position < len(tokens):
        token = tokens[position]
        position += 1
        if token == "(":
            stack.append(Counter())
            continue
        if token == ")":
            if len(stack) == 1 or not stack[-1]:
                raise ValueError("化学式括号不完整。")
            group = stack.pop()
        elif token in SUPPORTED:
            group = Counter({token: 1})
        else:
            raise ValueError(f"化学式中包含当前模型不支持的元素或标记：{token}。")
        multiplier = 1
        if position < len(tokens) and tokens[position].isdigit():
            multiplier = integer(tokens[position], "化学式原子计数", 1, 10000)
            position += 1
        stack[-1].update({element: number * multiplier for element, number in group.items()})
    if len(stack) != 1 or not stack[0]:
        raise ValueError("化学式括号不完整。")
    return dict(stack[0])


def element_list(value: Any, name: str) -> list[str]:
    if isinstance(value, str):
        value = [item for item in re.split(r"[-,，、\s]+", value.strip()) if item]
    if not isinstance(value, list):
        raise ValueError(f"{name} 需要元素列表。")
    result = list(dict.fromkeys(str(item).strip().capitalize() for item in value))
    if any(item not in SUPPORTED for item in result):
        raise ValueError(f"{name} 存在当前 OMatG 检查点不支持的元素（原子序数范围 1–100）。")
    if len(result) > 8:
        raise ValueError(f"{name} 当前最多支持 8 种元素。")
    return result


def omatg_constraint(payload: dict, source: dict, taskid: str, raw: str, current: str) -> GenerationConstraint:
    from .constraints import _formula_from_text, _elements_from_context, _ehull_from_context, _validation_targets_from_context, _is_alloy_optimization_request
    if _is_alloy_optimization_request(current):
        raise ValueError("合金成分/比例优化请交给 alloy_composition_optimization；本服务生成周期晶体。")
    from .task_context import build_task_context
    task_context = build_task_context(payload, source, raw, current)
    normalized_text = current.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
    formula = str(source.get("target_formula") or _formula_from_text(normalized_text) or "").strip() or None
    # Current formula/elements take precedence. Do not mine old assistant prose.
    explicit_allowed = source.get("allowed_elements", source.get("elements", []))
    allowed = element_list(explicit_allowed, "allowed_elements")
    if not allowed and not formula:
        allowed = _elements_from_context(current)
    allowed = element_list(allowed, "allowed_elements")
    counts = formula_counts(formula) if formula else {}
    if counts:
        if explicit_allowed and allowed and not set(counts) <= set(allowed):
            raise ValueError("化学式元素不在允许元素集合内，请确认当前任务。")
        allowed = allowed or list(counts)
    mode = str(source.get("generation_mode", "auto")).lower()
    if mode not in {"auto", "csp", "dng"}:
        raise ValueError("generation_mode 需要 auto、csp 或 dng。")
    mode = ("csp" if formula else "dng") if mode == "auto" else mode
    if mode == "csp" and not formula:
        raise ValueError("CSP 需要 target_formula；仅有元素集合请使用 DNG。")
    if mode == "dng" and formula:
        raise ValueError("已给出固定化学式，建议使用 CSP；DNG 不会保留该计量比。")
    refine = source.get("refine_with_csp", False)
    if not isinstance(refine, bool):
        raise ValueError("refine_with_csp 需要布尔值。")
    if refine and mode != "dng":
        raise ValueError("refine_with_csp 仅用于 DNG 后追加固定组成探索；已有化学式直接使用 CSP。")
    opened = source.get("allow_open_chemical_space", False)
    if not isinstance(opened, bool):
        raise ValueError("allow_open_chemical_space 需要布尔值。")
    family = str(source.get("material_family") or "").strip()
    if not allowed and not opened:
        raise ValueError("无法确定待生成的元素体系。请提供化学式或元素集合；仅有材料大类/性质时，需要确认开放化学空间后才启动 DNG。")
    if not allowed and not (family or current.strip() or source.get("validation_targets") or task_context.get("scenario")):
        raise ValueError("开放 DNG 仍需要材料方向或希望筛选的性质。")
    if not counts and any(term in (family + current).lower() for term in ("聚合物", "树脂", "塑料", "橡胶", "复合材料", "非晶", "玻璃", "polymer")):
        raise ValueError("当前 OMatG 主链面向周期无机晶体，该材料对象需要专项模型。")
    excluded = element_list(source.get("excluded_elements", source.get("forbidden_elements", [])), "excluded_elements")
    required = element_list(source.get("required_elements", []), "required_elements")
    if allowed and (set(allowed) & set(excluded) or not set(required) <= set(allowed)):
        raise ValueError("允许、禁止或必含元素存在冲突。")
    if set(required) & set(excluded) or counts and set(counts) & set(excluded):
        raise ValueError("固定化学式/必含元素与禁止元素冲突。")
    if counts and not set(required) <= set(counts):
        raise ValueError("必含元素不在固定化学式中。")
    units = integer(source.get("formula_units", 1), "formula_units", 1, 20)
    atoms = sum(counts.values()) * units if counts else integer(source.get("num_atoms", 8), "num_atoms", 2, 20)
    if atoms > 20:
        raise ValueError(f"当前 MP-20 检查点每胞最多 20 原子；此输入为 {atoms}，需减少化学式单位或选择其他模型。")
    if len(required) > atoms:
        raise ValueError("每胞原子数小于必含元素种数。")
    minimum_default = 1 if counts or len(allowed) == 1 else max(2, len(required))
    minimum = integer(source.get("min_distinct_elements", minimum_default), "min_distinct_elements", 1, 8)
    available_count = len(counts) if counts else len(allowed) if allowed else len(SUPPORTED - set(excluded))
    if minimum > min(atoms, available_count):
        raise ValueError("最少元素种数超过化学空间或晶胞原子数，请确认组成条件。")
    strategy = str(source.get("dng_species_strategy", "auto")).lower()
    if strategy not in {"auto", "masked", "presence_anchors"}:
        raise ValueError("dng_species_strategy 需要 auto、masked 或 presence_anchors。")
    if mode == "csp":
        if strategy == "presence_anchors":
            raise ValueError("元素位点锚定仅用于 DNG；CSP 按固定化学式计数。")
        strategy = "masked"
    elif strategy == "auto":
        strategy = "presence_anchors" if required or allowed and minimum > 1 else "masked"
    if not isinstance(source.get("target_properties", {}), dict):
        raise ValueError("target_properties 需要对象。")
    properties = dict(source.get("target_properties") or {})
    hull = properties.get("energy_above_hull", _ehull_from_context(current))
    hull = 0.05 if hull is None else hull
    if isinstance(hull, bool) or not math.isfinite(float(hull)) or float(hull) < 0:
        raise ValueError("energy_above_hull 需要非负有限数值（eV/atom）。")
    properties["energy_above_hull"] = float(hull)
    stability_limit_mode = source.get("stability_limit_mode", "reference")
    if stability_limit_mode not in {"reference", "hard"}:
        raise ValueError("stability_limit_mode 仅支持 reference（参考线）或 hard（硬性上限）。")
    for key, value in properties.items():
        if key not in {"energy_above_hull", *PROPERTY_UNITS}:
            raise ValueError(f"当前没有可执行的 {key} 数值模型；请作为后续专项验证目标，而不是生成或已达标条件。")
        if isinstance(value, bool) or value is None or not math.isfinite(float(value)):
            raise ValueError(f"target_properties.{key} 需要有限数值。")
        properties[key] = float(value)
    limits = source.get("property_constraints") or {}
    if not isinstance(limits, dict):
        raise ValueError("property_constraints 需要对象。")
    for key, bound in limits.items():
        if key not in PROPERTY_UNITS or not isinstance(bound, dict) or not ({"min", "max"} & bound.keys()):
            raise ValueError(f"{key} 没有可执行的性质上下限；请使用已接入性质的 min/max。")
        if set(bound) - {"min", "max", "unit"} or bound.get("unit", PROPERTY_UNITS[key]) != PROPERTY_UNITS[key]:
            raise ValueError(f"{key} 请使用 {PROPERTY_UNITS[key]} 单位。")
        for name in ("min", "max"):
            if name in bound and (isinstance(bound[name], bool) or not math.isfinite(float(bound[name]))):
                raise ValueError(f"{key}.{name} 需要有限数值。")
        if float(bound.get("min", -math.inf)) > float(bound.get("max", math.inf)):
            raise ValueError(f"{key} 上下限冲突。")
        limits[key] = {**{name: float(bound[name]) for name in ("min", "max") if name in bound}, "unit": PROPERTY_UNITS[key]}
    symmetry = source.get("symmetry_constraints") or {}
    if not isinstance(symmetry, dict) or set(symmetry) - {"non_centrosymmetric", "space_group_number"}:
        raise ValueError("symmetry_constraints 仅支持 non_centrosymmetric 和 space_group_number。")
    if "non_centrosymmetric" in symmetry and not isinstance(symmetry["non_centrosymmetric"], bool):
        raise ValueError("non_centrosymmetric 需要布尔值。")
    if "space_group_number" in symmetry:
        symmetry = {**symmetry, "space_group_number": integer(symmetry["space_group_number"], "space_group_number", 1, 230)}
    if not isinstance(source.get("validation_targets", {}), dict):
        raise ValueError("validation_targets 需要对象；关注点值可为 null。")
    parameter_sources = {
        "target_formula": "用户确认字段" if source.get("target_formula") else "当前请求解析" if formula else "DNG 生成",
        "allowed_elements": "用户确认字段" if source.get("allowed_elements") or source.get("elements") else "由化学式解析" if counts else "当前请求解析" if allowed else "用户确认开放空间",
        "required_elements": "用户确认字段" if "required_elements" in source else "服务默认：不强制所有允许元素出现",
        "num_atoms": "化学式计数 × Z" if counts else "用户确认字段" if "num_atoms" in source else "服务默认",
        "formula_units": "用户确认字段" if "formula_units" in source else "服务默认",
        "seed": "用户确认字段" if "seed" in source else "服务默认",
        "max_candidates": "用户确认字段" if "max_candidates" in payload or "max_candidates" in source else "服务默认",
        "energy_above_hull": "用户确认字段" if "energy_above_hull" in (source.get("target_properties") or {}) else "当前请求解析" if _ehull_from_context(current) is not None else "服务后筛默认",
        "energy_above_hull_mode": "硬性上限" if stability_limit_mode == "hard" else "参考线",
        "refine_with_csp": "用户确认字段" if "refine_with_csp" in source else "服务默认",
        "min_distinct_elements": "用户确认字段" if "min_distinct_elements" in source else "服务默认：多元素空间至少两种元素；单元素/固定化学式按原组成",
        "dng_species_strategy": "用户确认字段" if "dng_species_strategy" in source else "服务默认：白名单多元素探索使用部分位点锚定",
    }
    return GenerationConstraint(taskid=taskid, raw_requirement=current or raw, target_formula=formula,
        allowed_elements=allowed, required_elements=required, excluded_elements=excluded,
        generation_mode=mode, formula_units=units, num_atoms=atoms, refine_with_csp=refine, parameter_sources=parameter_sources,
        seed=integer(source.get("seed", 42), "seed", 0, 2**32 - 1),
        allow_open_chemical_space=opened, material_family=family, task_context=task_context,
        min_distinct_elements=minimum, dng_species_strategy=strategy,
        target_properties=properties, property_constraints=limits, symmetry_constraints=symmetry,
        validation_targets=source.get("validation_targets") or _validation_targets_from_context(current),
        requested_candidate_count=integer(payload.get("max_candidates", source.get("max_candidates", os.getenv("OMATG_DEFAULT_CANDIDATES", "10"))), "max_candidates", 1, 10),
        notes=[str(note) for note in source.get("notes", [])] + [
            "当前请求优先；上文只作背景，固定组成请经弹窗确认后写入结构化字段。",
            "未填写 E_hull 数值时采用 0.05 eV/atom 的稳定性复核参考线；超过参考线仍继续评价，只有明确确认 hard 时才作硬性上限。基础 OMatG 不以它作为生成奖励。",
            "性质数值目标是对比目标；硬性范围请填写 property_constraints，定性目标不转换成数值门槛。"])
