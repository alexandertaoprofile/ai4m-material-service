"""Requirement mapping and deterministic ranking for 3D-printing filaments."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from alpha.logs import logger

from .filament_profiles import starter_profiles


JsonDict = Dict[str, Any]


@dataclass
class FilamentScore:
    candidate: JsonDict
    score: float
    requirement_scores: JsonDict = field(default_factory=dict)
    data_coverage: float = 0.0
    hard_constraint_status: JsonDict = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "candidate": self.candidate,
            "score": round(float(self.score), 3),
            "requirement_scores": dict(self.requirement_scores),
            "data_coverage": round(float(self.data_coverage), 3),
            "hard_constraint_status": dict(self.hard_constraint_status),
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
        }


@dataclass
class FilamentSelectionResult:
    taskid: str
    scenario: JsonDict
    requirements: List[str]
    constraints: JsonDict
    ranked: List[FilamentScore]
    source_payload_path: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "taskid": self.taskid,
            "scenario": dict(self.scenario),
            "requirements": list(self.requirements),
            "constraints": dict(self.constraints),
            "ranked": [item.to_dict() for item in self.ranked],
            "source_payload_path": self.source_payload_path,
            "notes": list(self.notes),
        }


FILAMENT_KEYWORDS = [
    "3d打印", "3D打印", "增材制造", "耗材", "filament", "打印耗材", "喷嘴",
    "热床", "层间", "封箱", "pla", "petg", "abs", "asa", "pa6", "pa12",
    "pa-cf", "cf-pa", "pc-cf", "peek", "pekk", "碳纤维", "玻纤",
    "3d_printing_filament", "additive_manufacturing",
]

REQUIREMENT_MAP: List[Tuple[str, List[str], float]] = [
    ("thermal", ["高导热", "散热", "热管理", "导热", "thermal"], 1.35),
    ("strength", ["强度", "承载", "弯曲强度", "拉伸强度", "strength"], 1.05),
    ("stiffness", ["刚度", "模量", "刚性", "stiff", "modulus"], 1.05),
    ("layer_adhesion", ["层间", "z向", "Z向", "粘接", "结合力", "剪切", "interlayer"], 1.15),
    ("heat_resistance", ["耐热", "热变形", "长期热", "交变热", "hdt", "tg"], 1.15),
    ("dimensional_stability", ["尺寸稳定", "低吸水", "吸水", "翘曲", "稳定性", "cte", "热胀冷缩", "热循环"], 0.95),
    ("printability", ["可打印", "加工性", "打印窗口", "易打印", "喷嘴", "热床"], 0.75),
    ("electrical_insulation", ["电绝缘", "绝缘", "介电", "dielectric", "insulation"], 0.8),
]


def repo_in_ls_dir(repo_root: str) -> Path:
    return Path(repo_root) / "src" / "MNS_CaseHub" / "cases" / "material_discovery_demo" / "results" / "in-LS"


def latest_in_ls_payload(repo_root: str) -> Tuple[Optional[JsonDict], Optional[str]]:
    in_ls = repo_in_ls_dir(repo_root)
    if not in_ls.is_dir():
        return None, None
    try:
        paths = [p for p in in_ls.iterdir() if p.suffix.lower() == ".json"]
        if not paths:
            return None, None
        latest = max(paths, key=_payload_sort_key)
        return json.loads(latest.read_text(encoding="utf-8")), str(latest)
    except Exception as exc:
        logger.warning(f"[FILAMENT] failed to read latest in-LS payload: {exc!s}")
        return None, None


def _payload_sort_key(path: Path) -> Tuple[int, float]:
    m = re.search(r"simulation_task_(\d{4})_(\d{6})", path.name)
    if m:
        return int(m.group(1) + m.group(2)), path.stat().st_mtime
    return 0, path.stat().st_mtime


def detect_filament_task(text: str, payload: Optional[JsonDict] = None) -> bool:
    hay = f"{text or ''}\n{json.dumps(payload or {}, ensure_ascii=False)}".lower()
    return any(str(k).lower() in hay for k in FILAMENT_KEYWORDS)


def _first_scenario(payload: Optional[JsonDict]) -> JsonDict:
    if not isinstance(payload, dict):
        return {}
    scenarios = payload.get("scenario_tasks")
    if isinstance(scenarios, list) and scenarios:
        first = scenarios[0]
        return first if isinstance(first, dict) else {}
    st = payload.get("simulation_task")
    if isinstance(st, dict):
        return st
    return {}


def _payload_texts(payload: Optional[JsonDict]) -> List[str]:
    if not isinstance(payload, dict):
        return []
    texts: List[str] = []
    for key in ("user_instruction", "instruction", "query", "task_description"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip())
    scenarios = payload.get("scenario_tasks")
    if isinstance(scenarios, list):
        for item in scenarios:
            if not isinstance(item, dict):
                continue
            for key in ("scenario_name", "application", "requirements", "baseline_reason", "advanced_reason"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    texts.append(val.strip())
            sim_params = item.get("simulation_parameters")
            if isinstance(sim_params, dict):
                vals = sim_params.get("key_requirements")
                if isinstance(vals, list):
                    texts.extend(str(v).strip() for v in vals if str(v).strip())
    return texts


def parse_requirements(text: str, payload: Optional[JsonDict]) -> List[str]:
    scenario = _first_scenario(payload)
    raw: List[str] = []
    raw.extend(_payload_texts(payload))
    for key in ("requirements", "application", "scenario_name", "baseline_reason", "advanced_reason"):
        val = scenario.get(key)
        if isinstance(val, str) and val.strip():
            raw.append(val.strip())
    sim_params = scenario.get("simulation_parameters")
    if isinstance(sim_params, dict):
        vals = sim_params.get("key_requirements")
        if isinstance(vals, list):
            raw.extend(str(v).strip() for v in vals if str(v).strip())
    if text:
        raw.append(str(text))

    joined = "；".join(raw)
    found: List[str] = []
    for canonical, keywords, _weight in REQUIREMENT_MAP:
        if any(k.lower() in joined.lower() for k in keywords):
            found.append(canonical)
    if not found:
        found = ["strength", "stiffness", "printability"]
    return list(dict.fromkeys(found))


def parse_constraints(text: str, payload: Optional[JsonDict]) -> JsonDict:
    joined = "；".join([str(text or "")] + _payload_texts(payload))
    scenario = _first_scenario(payload)
    constraints: JsonDict = {}
    for holder in (payload or {}, scenario):
        if not isinstance(holder, dict):
            continue
        for key in ("target_constraints", "constraints", "hard_constraints"):
            raw = holder.get(key)
            if isinstance(raw, dict):
                constraints.update(_normalize_constraint_keys(raw))

    def _num_near(patterns: List[str]) -> Optional[float]:
        for pattern in patterns:
            m = re.search(pattern, joined, flags=re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except Exception:
                    continue
        return None

    constraints.setdefault("thermal_conductivity_min_w_mk", _num_near([
        r"(?:导热系数|热导率|体积热导率|导热)[^0-9]{0,16}(?:>=|≥|不低于|大于等于)?\s*(\d+(?:\.\d+)?)\s*W",
        r"(?:>=|≥|不低于|大于等于)\s*(\d+(?:\.\d+)?)\s*W/\(?m",
    ]))
    constraints.setdefault("hdt_min_c", _num_near([
        r"(?:HDT|热变形温度)[^0-9]{0,16}(?:>=|≥|不低于|大于等于)?\s*(\d+(?:\.\d+)?)\s*(?:℃|C|°C)",
    ]))
    constraints.setdefault("layer_shear_min_mpa", _num_near([
        r"(?:层间剪切强度|层间剪切|剪切强度)[^0-9]{0,16}(?:>=|≥|不低于|大于等于)?\s*(\d+(?:\.\d+)?)\s*MPa",
    ]))
    constraints.setdefault("elongation_min_pct", _num_near([
        r"(?:断裂伸长率|伸长率)[^0-9]{0,16}(?:>=|≥|不低于|大于等于)?\s*(\d+(?:\.\d+)?)\s*%",
    ]))
    if "electrical_insulation_required" not in constraints:
        constraints["electrical_insulation_required"] = bool(re.search(r"电绝缘|绝缘|介电", joined, flags=re.IGNORECASE))
    return {k: v for k, v in constraints.items() if v not in (None, False)}


def _normalize_constraint_keys(raw: JsonDict) -> JsonDict:
    aliases = {
        "thermal_conductivity_min_w_mk": ["thermal_conductivity_min_w_mk", "导热系数下限", "导热系数", "热导率下限"],
        "hdt_min_c": ["hdt_min_c", "热变形温度下限", "HDT下限", "hdt"],
        "layer_shear_min_mpa": ["layer_shear_min_mpa", "interlayer_shear_min_mpa", "interlayer_shear_strength_min_mpa", "层间剪切强度下限"],
        "elongation_min_pct": ["elongation_min_pct", "elongation_break_min_pct", "断裂伸长率下限"],
        "density_max_g_cm3": ["density_max_g_cm3", "density_max", "密度上限"],
        "tensile_strength_min_mpa": ["tensile_strength_min_mpa", "拉伸强度下限"],
        "flexural_strength_min_mpa": ["flexural_strength_min_mpa", "弯曲强度下限"],
        "continuous_use_temp_min_c": ["continuous_use_temp_min_c", "continuous_use_temp_c", "连续使用温度下限"],
        "cte_max_um_m_c": ["cte_max_um_m_c", "ctE_max_um_m_c", "CTE上限", "热膨胀系数上限"],
        "volume_resistivity_min_ohm_cm": ["volume_resistivity_min_ohm_cm", "体积电阻率下限"],
        "electrical_insulation_required": ["electrical_insulation_required", "电绝缘要求"],
        "fatigue_required": ["fatigue_required", "疲劳要求"],
        "printability_constraints": ["printability_constraints", "打印约束"],
    }
    out = _normalize_aliases(raw, aliases)
    normalized: JsonDict = {}
    for key in aliases:
        val = out.get(key)
        if val is not None:
            normalized[key] = val
    return normalized


def _numeric(props: JsonDict, key: str) -> Optional[float]:
    value = props.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        if m:
            return float(m.group(0))
    return None


def _norm(value: Optional[float], low: float, high: float, inverse: bool = False) -> float:
    if value is None:
        return 0.45
    if high <= low:
        return 0.0
    x = (float(value) - low) / (high - low)
    x = max(0.0, min(1.0, x))
    return 1.0 - x if inverse else x


def _tag_bonus(candidate: JsonDict, tags: Iterable[str]) -> float:
    have = set(_normalize_tags(candidate.get("tags") or []))
    want = set(tags)
    if not want:
        return 0.0
    return min(1.0, len(have.intersection(want)) / max(1, len(want)))


def _score_requirement(candidate: JsonDict, requirement: str, constraints: Optional[JsonDict] = None) -> Tuple[float, str, Optional[str]]:
    constraints = constraints or {}
    props = candidate.get("properties") or {}
    name = str(candidate.get("name") or "")
    tags = set(_normalize_tags(candidate.get("tags") or []))

    if requirement == "thermal":
        tc = _numeric(props, "thermal_conductivity_w_mk")
        target = constraints.get("thermal_conductivity_min_w_mk")
        if tc is not None:
            high = max(3.0, float(target or 3.0))
            score = _norm(tc, 0.2, high)
            gap = None if not target or tc >= float(target) else f"导热系数未达到目标 ≥{target:g} W/(m·K)"
            return score, f"导热系数可用，按 {tc:g} W/(m·K) 计入", gap
        bonus = _tag_bonus(candidate, ["carbon_fiber", "thermal_path_candidate"]) * 0.22
        if tags.intersection({"bn_filled", "aln_filled", "ceramic_filled", "ceramic_filler", "thermal_filler"}):
            bonus += 0.18
        score = 0.18 + bonus
        if target and float(target) >= 10:
            score = min(score, 0.42)
            gap = f"缺少实测导热系数，无法证明满足 ≥{float(target):g} W/(m·K)；当前仅为导热路径候选"
        else:
            gap = "缺少实测导热系数，当前仅用填料/体系标签做代理判断"
        return min(score, 0.64), "含增强相或工程体系标签，可作为散热路径候选" if bonus else "未见专门导热增强信息", gap

    if requirement == "strength":
        flex = _numeric(props, "flexural_strength_mpa")
        tensile = _numeric(props, "tensile_strength_mpa")
        val = flex if flex is not None else tensile
        target = constraints.get("flexural_strength_min_mpa") if flex is not None else constraints.get("tensile_strength_min_mpa")
        high = max(90.0, float(target or 90.0))
        score = _norm(val, 45.0, high)
        if val is None and tags.intersection({"carbon_fiber", "glass_fiber", "engineering"}):
            score = 0.42
        if val is not None and target and val < float(target):
            gap = f"强度未达到目标 ≥{float(target):g} MPa"
        else:
            gap = None if val is not None else "缺少强度实测值，不能闭合动态载荷强度校核"
        label = "弯曲强度" if flex is not None else "拉伸强度"
        return score, f"{label}代理值 {val:g} MPa" if val is not None else "按工程体系默认强度潜力评估", gap

    if requirement == "stiffness":
        flex = _numeric(props, "flexural_modulus_mpa")
        tensile = _numeric(props, "tensile_modulus_mpa")
        val = flex if flex is not None else tensile
        score = _norm(val, 1500.0, 3200.0) + 0.15 * _tag_bonus(candidate, ["carbon_fiber", "glass_fiber", "stiff"])
        score = min(1.0, score)
        gap = None if val is not None else "缺少刚度/模量实测值"
        label = "弯曲模量" if flex is not None else "拉伸模量"
        return score, f"{label}代理值 {val:g} MPa" if val is not None else "按增强相和工程塑料体系评估刚度潜力", gap

    if requirement == "layer_adhesion":
        val = _numeric(props, "z_impact_kj_m2")
        score = _norm(val, 4.0, 15.0)
        target = constraints.get("layer_shear_min_mpa")
        gap = None if val is not None and score >= 0.55 else "Z 向层间性能需要打印参数和试样验证"
        if target and val is None:
            gap = f"缺少层间剪切强度实测值，无法证明满足 ≥{float(target):g} MPa"
        return score, f"Z向冲击代理值 {val:g} kJ/m2" if val is not None else "缺少Z向层间代理指标", gap

    if requirement == "heat_resistance":
        val = _numeric(props, "hdt_045_mpa_c")
        target = constraints.get("hdt_min_c")
        high = max(120.0, float(target or 120.0))
        score = _norm(val, 55.0, high) + 0.08 * _tag_bonus(candidate, ["heat_resistant", "engineering"])
        score = min(1.0, score)
        if val is not None and target and val < float(target):
            gap = f"HDT 未达到目标 ≥{float(target):g} C"
        else:
            gap = None if val is not None and score >= 0.6 else "耐热或热循环性能仍需 HDT/Tg/热循环数据确认"
        return score, f"HDT(0.45MPa) 代理值 {val:g} C" if val is not None else "缺少HDT/Tg实测值", gap

    if requirement == "dimensional_stability":
        water = _numeric(props, "water_absorption_pct")
        score = _norm(water, 0.25, 0.85, inverse=True)
        if "lower_moisture_than_pa6" in (candidate.get("tags") or []):
            score = min(1.0, score + 0.18)
        gap = None if water is not None and score >= 0.55 else "吸水/翘曲/CTE数据不足，尺寸稳定性需补充"
        return score, f"饱和吸水率代理值 {water:g}%" if water is not None else "按吸湿敏感性标签评估尺寸稳定性", gap

    if requirement == "printability":
        proc = candidate.get("process") or {}
        enclosure = str(proc.get("enclosure") or "")
        score = 0.75
        if "必需" in enclosure:
            score -= 0.18
        if "硬化钢" in str(proc.get("nozzle") or ""):
            score -= 0.08
        if "easy_print" in (candidate.get("tags") or []):
            score += 0.15
        score = max(0.2, min(1.0, score))
        gap = None if score >= 0.55 else "打印设备门槛较高，需要封箱/硬化喷嘴/干燥策略"
        return score, f"{name} 的工艺窗口：喷嘴 {proc.get('nozzle', '待补充')}，封箱 {proc.get('enclosure', '待补充')}", gap

    if requirement == "electrical_insulation":
        if "metal" in tags or str(candidate.get("family") or "").lower() in {"metal", "al", "ti"}:
            return 0.05, "金属/导电体系不满足电绝缘约束", "电绝缘约束与金属/高导电连续相冲突"
        if tags.intersection({"carbon_fiber", "carbon_nanotube", "cnt"}):
            return 0.34, "碳纤维增强体系存在导电/漏电风险，需要体积电阻率实测", "缺少体积/表面电阻率与击穿强度数据"
        if tags.intersection({"insulating", "electrical_insulating", "dielectric"}):
            return 0.74, "绝缘填料/体系标签表明具备电绝缘潜力，但仍需体积电阻率和击穿强度闭合", "缺少体积/表面电阻率与击穿强度数据"
        return 0.62, "聚合物基体通常具备电绝缘潜力，但仍需牌号级电性能确认", "缺少体积/表面电阻率与击穿强度数据"

    return 0.5, "未映射需求，按中性分处理", None


def _coverage_for_requirements(candidate: JsonDict, requirements: List[str]) -> float:
    props = candidate.get("properties") or {}
    needed = {
        "thermal": ["thermal_conductivity_w_mk"],
        "strength": ["flexural_strength_mpa", "tensile_strength_mpa"],
        "stiffness": ["flexural_modulus_mpa", "tensile_modulus_mpa"],
        "layer_adhesion": ["z_impact_kj_m2", "interlayer_shear_strength_mpa"],
        "heat_resistance": ["hdt_045_mpa_c"],
        "dimensional_stability": ["water_absorption_pct", "cte_um_m_c", "ctE_um_m_c"],
        "electrical_insulation": ["volume_resistivity_ohm_cm", "dielectric_strength_kv_mm"],
    }
    checks = []
    for req in requirements:
        keys = needed.get(req)
        if not keys:
            continue
        checks.append(any(_numeric(props, key) is not None for key in keys))
    if not checks:
        return 0.0
    return sum(1 for ok in checks if ok) / len(checks)


def _hard_constraint_status(candidate: JsonDict, constraints: JsonDict) -> JsonDict:
    props = candidate.get("properties") or {}
    tags = set(_normalize_tags(candidate.get("tags") or []))
    status: JsonDict = {}

    def _proxy(label: str, target: Any, basis: str, value: Any = None):
        status[label] = {"status": "proxy", "target": target, "value": value, "basis": basis}

    def _cmp_min(key: str, prop_keys: Iterable[str], label: str, proxy_keys: Iterable[str] = (), proxy_basis: str = ""):
        target = constraints.get(key)
        if target is None:
            return
        val = _first_numeric(props, prop_keys)
        if val is None:
            proxy_val = _first_numeric(props, proxy_keys)
            if proxy_val is not None and proxy_basis:
                _proxy(label, target, proxy_basis, proxy_val)
            else:
                status[label] = {"status": "unknown", "target": target, "value": None}
        elif val >= float(target):
            status[label] = {"status": "pass", "target": target, "value": val}
        else:
            status[label] = {"status": "fail", "target": target, "value": val}

    def _cmp_max(key: str, prop_keys: Iterable[str], label: str, proxy_keys: Iterable[str] = (), proxy_basis: str = ""):
        target = constraints.get(key)
        if target is None:
            return
        val = _first_numeric(props, prop_keys)
        if val is None:
            proxy_val = _first_numeric(props, proxy_keys)
            if proxy_val is not None and proxy_basis:
                _proxy(label, target, proxy_basis, proxy_val)
            else:
                status[label] = {"status": "unknown", "target": target, "value": None}
        elif val <= float(target):
            status[label] = {"status": "pass", "target": target, "value": val}
        else:
            status[label] = {"status": "fail", "target": target, "value": val}

    _cmp_min("thermal_conductivity_min_w_mk", ["thermal_conductivity_w_mk", "thermal_conductivity_xy_w_mk", "thermal_conductivity_z_w_mk"], "导热系数")
    if "导热系数" in status and status["导热系数"].get("status") == "unknown":
        if tags.intersection({"thermal_conductive", "thermal_filler", "thermal_path_candidate", "ceramic_filler", "ceramic_filled", "bn_filled", "aln_filled"}):
            _proxy("导热系数", constraints.get("thermal_conductivity_min_w_mk"), "填料/体系标签支持导热路径，但缺少牌号实测导热系数")
        elif tags.intersection({"carbon_fiber", "carbon_nanotube", "cnt"}):
            _proxy("导热系数", constraints.get("thermal_conductivity_min_w_mk"), "碳基增强相可作为导热路径线索，但不能等同于体积导热率达标")
    _cmp_min("hdt_min_c", ["hdt_045_mpa_c", "hdt_18_mpa_c"], "HDT")
    _cmp_min("layer_shear_min_mpa", ["interlayer_shear_strength_mpa"], "层间剪切强度", ["z_impact_kj_m2"], "Z向冲击强度可作为层间结合韧性的代理线索")
    _cmp_min("elongation_min_pct", ["elongation_break_pct"], "断裂伸长率")
    _cmp_max("density_max_g_cm3", ["density_g_cm3"], "密度")
    _cmp_min("tensile_strength_min_mpa", ["tensile_strength_mpa"], "拉伸强度", ["flexural_strength_mpa"], "弯曲强度可作为结构承载能力代理，但不能替代拉伸实测")
    _cmp_min("flexural_strength_min_mpa", ["flexural_strength_mpa"], "弯曲强度")
    _cmp_min("continuous_use_temp_min_c", ["continuous_use_temp_c"], "连续使用温度")
    _cmp_max("cte_max_um_m_c", ["cte_um_m_c", "ctE_um_m_c"], "CTE", ["water_absorption_pct"], "吸水率可辅助判断尺寸稳定风险，但不能替代 CTE")
    _cmp_min("volume_resistivity_min_ohm_cm", ["volume_resistivity_ohm_cm"], "体积电阻率")
    if constraints.get("electrical_insulation_required"):
        vr = _numeric(props, "volume_resistivity_ohm_cm")
        target = float(constraints.get("volume_resistivity_min_ohm_cm") or 1e8)
        if vr is None:
            if tags.intersection({"insulating", "electrical_insulating", "dielectric", "ceramic_filler", "bn_filled", "aln_filled"}):
                _proxy("电绝缘", "required", "绝缘填料/聚合物体系提供电绝缘线索，但仍需体积电阻率或击穿强度闭合")
            elif tags.intersection({"carbon_fiber", "carbon_nanotube", "cnt"}):
                status["电绝缘"] = {
                    "status": "risk",
                    "target": "required",
                    "value": None,
                    "basis": "碳基增强相可能引入导电网络，需用体积/表面电阻率确认",
                }
            else:
                _proxy("电绝缘", "required", "聚合物基体通常具备绝缘潜力，但缺少牌号级电性能实测")
        else:
            status["电绝缘"] = {"status": "pass" if vr >= target else "fail", "target": f">={target:g} ohm·cm", "value": vr}
    if constraints.get("fatigue_required"):
        val = _first_numeric(props, ["fatigue_life_cycles", "fatigue_strength_mpa"])
        if val is None:
            proxy_val = _first_numeric(props, ["z_impact_kj_m2", "impact_xy_kj_m2", "flexural_strength_mpa"])
            if proxy_val is not None:
                _proxy("疲劳", "required", "冲击/弯曲数据可辅助判断韧性和承载潜力，但不能替代疲劳寿命", proxy_val)
            else:
                status["疲劳"] = {"status": "unknown", "target": "required", "value": None}
    return status


def _first_numeric(props: JsonDict, keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        val = _numeric(props, key)
        if val is not None:
            return val
    return None


def _candidate_from_upstream(raw: JsonDict) -> Optional[JsonDict]:
    name = raw.get("name") or raw.get("material") or raw.get("material_name") or raw.get("display_name")
    if not isinstance(name, str) or not name.strip():
        return None
    props = _normalize_property_keys(_merge_dict_fields(
        raw,
        ["properties", "mechanical_properties", "thermal_properties", "physical_properties", "耗材特性", "物理性能", "机械性能"],
    ))
    process = _normalize_process_keys(_merge_dict_fields(
        raw,
        ["process", "print_settings", "printing_settings", "recommended_print_settings", "打印设置", "推荐打印设置", "打印前准备"],
    ))
    return {
        "name": name.strip(),
        "display_name": str(raw.get("display_name") or name).strip(),
        "family": raw.get("family") or "",
        "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        "_source": "upstream_payload",
        "properties": dict(props),
        "process": dict(process),
        "advantages": raw.get("advantages") if isinstance(raw.get("advantages"), list) else [],
        "limitations": raw.get("limitations") if isinstance(raw.get("limitations"), list) else [],
        "evidence": raw.get("evidence") or raw.get("source") or "upstream_payload",
    }


def _merge_dict_fields(raw: JsonDict, field_names: Iterable[str]) -> JsonDict:
    merged: JsonDict = {}
    for field in field_names:
        val = raw.get(field)
        if isinstance(val, dict):
            merged.update(val)
    return merged


def _normalize_property_keys(props: JsonDict) -> JsonDict:
    aliases = {
        "impact_xy_kj_m2": ["impact_xy_kj_m2", "冲击强度-XY方向", "冲击强度_XY方向", "冲击强度 XY方向", "xy_impact", "impact_strength_xy"],
        "flexural_strength_mpa": ["flexural_strength_mpa", "弯曲强度-XY方向", "弯曲强度_XY方向", "弯曲强度", "flexural_strength"],
        "flexural_modulus_mpa": ["flexural_modulus_mpa", "弯曲模量-XY方向", "弯曲模量_XY方向", "弯曲模量", "flexural_modulus"],
        "z_impact_kj_m2": ["z_impact_kj_m2", "冲击强度-Z方向", "冲击强度_Z方向", "层间粘接", "Z向冲击强度", "z_impact", "interlayer_impact"],
        "hdt_045_mpa_c": ["hdt_045_mpa_c", "热变形温度", "热变形温度0.45MPa", "HDT", "hdt"],
        "water_absorption_pct": ["water_absorption_pct", "饱和吸水率", "吸水率", "water_absorption"],
        "thermal_conductivity_w_mk": ["thermal_conductivity_w_mk", "导热系数", "热导率", "thermal_conductivity"],
        "thermal_conductivity_xy_w_mk": ["thermal_conductivity_xy_w_mk", "面内导热系数", "XY导热系数"],
        "thermal_conductivity_z_w_mk": ["thermal_conductivity_z_w_mk", "Z向导热系数", "through_plane_thermal_conductivity"],
        "density_g_cm3": ["density_g_cm3", "密度", "density"],
        "cte_um_m_c": ["cte_um_m_c", "ctE_um_m_c", "CTE", "线膨胀系数", "热膨胀系数"],
        "ctE_um_m_c": ["ctE_um_m_c", "cte_um_m_c", "CTE", "线膨胀系数", "热膨胀系数"],
        "tensile_strength_mpa": ["tensile_strength_mpa", "拉伸强度", "tensile_strength"],
        "tensile_modulus_mpa": ["tensile_modulus_mpa", "拉伸模量", "tensile_modulus"],
        "continuous_use_temp_c": ["continuous_use_temp_c", "连续使用温度", "continuous_use_temp"],
        "interlayer_shear_strength_mpa": ["interlayer_shear_strength_mpa", "层间剪切强度", "层间剪切", "interlayer_shear_strength"],
        "elongation_break_pct": ["elongation_break_pct", "断裂伸长率", "伸长率", "elongation_at_break"],
        "volume_resistivity_ohm_cm": ["volume_resistivity_ohm_cm", "体积电阻率", "volume_resistivity"],
        "dielectric_strength_kv_mm": ["dielectric_strength_kv_mm", "击穿强度", "介电强度", "dielectric_strength"],
    }
    return _normalize_aliases(props, aliases)


def _normalize_process_keys(process: JsonDict) -> JsonDict:
    aliases = {
        "drying": ["drying", "使用前是否干燥", "干燥条件", "干燥"],
        "nozzle": ["nozzle", "喷嘴尺寸/材质", "喷嘴", "喷嘴材质"],
        "bed": ["bed", "适用的打印面板和床温", "热床", "床温"],
        "nozzle_temp": ["nozzle_temp", "喷嘴温度", "打印温度"],
        "speed": ["speed", "打印速度"],
        "enclosure": ["enclosure", "是否需要封箱打印", "封箱"],
        "fan": ["fan", "部件冷却风扇", "冷却风扇"],
        "annealing": ["annealing", "退火", "打印后工艺处理"],
    }
    return _normalize_aliases(process, aliases)


def _normalize_aliases(data: JsonDict, aliases: Dict[str, List[str]]) -> JsonDict:
    out = dict(data or {})
    lowered = {str(k).strip().lower(): v for k, v in (data or {}).items()}
    for canonical, names in aliases.items():
        if canonical in out:
            continue
        for name in names:
            if name in data:
                out[canonical] = data[name]
                break
            lowered_value = lowered.get(str(name).strip().lower())
            if lowered_value is not None:
                out[canonical] = lowered_value
                break
    return out


def _normalize_tags(tags: Iterable[Any]) -> List[str]:
    aliases = {
        "ceramic_filler": ["ceramic_filled", "thermal_filler"],
        "ceramic_filled": ["ceramic_filler", "thermal_filler"],
        "thermal_path": ["thermal_path_candidate"],
        "high_conductivity": ["thermal_path_candidate", "thermal_filler"],
        "oriented": ["oriented_structure", "thermal_path_candidate"],
        "bn_filled": ["thermal_filler", "ceramic_filler"],
        "aln_filled": ["thermal_filler", "ceramic_filler"],
        "carbon_nanotube": ["cnt", "thermal_path_candidate"],
        "insulating": ["electrical_insulating", "dielectric"],
    }
    out: List[str] = []
    for raw in tags or []:
        tag = str(raw).strip()
        if not tag:
            continue
        out.append(tag)
        out.extend(aliases.get(tag, []))
    return list(dict.fromkeys(out))


def collect_candidates(payload: Optional[JsonDict]) -> List[JsonDict]:
    candidates = starter_profiles()
    scenario = _first_scenario(payload)
    upstream_candidates: List[JsonDict] = []
    for holder in (payload or {}, scenario):
        if not isinstance(holder, dict):
            continue
        for field in ("candidate_materials", "candidate_filaments", "filaments", "materials", "material_candidates"):
            vals = holder.get(field)
            if isinstance(vals, list):
                for item in vals:
                    if isinstance(item, dict):
                        c = _candidate_from_upstream(item)
                        if c:
                            upstream_candidates.append(c)

    if upstream_candidates:
        starter_by_name = {str(c.get("name", "")).lower(): c for c in candidates}
        by_name: Dict[str, JsonDict] = {}
        for c in upstream_candidates:
            by_name[str(c.get("name", "")).lower()] = c
        for c in candidates:
            key = str(c.get("name", "")).lower()
            if key not in by_name:
                c = dict(c)
                c["_source"] = "starter_profile"
                by_name[key] = c
        candidates = list(by_name.values())
    return candidates


def rank_filaments(taskid: str, text: str, payload: Optional[JsonDict], payload_path: Optional[str] = None) -> FilamentSelectionResult:
    requirements = parse_requirements(text, payload)
    constraints = parse_constraints(text, payload)
    requirements = _requirements_from_constraints(requirements, constraints)
    scenario = _first_scenario(payload)
    candidates = collect_candidates(payload)

    weights = {name: weight for name, _keywords, weight in REQUIREMENT_MAP}
    ranked: List[FilamentScore] = []
    for candidate in candidates:
        total = 0.0
        total_w = 0.0
        detail: JsonDict = {}
        reasons: List[str] = []
        gaps: List[str] = []
        for req in requirements:
            score, reason, gap = _score_requirement(candidate, req, constraints=constraints)
            weight = weights.get(req, 1.0)
            total += score * weight
            total_w += weight
            detail[req] = round(float(score), 3)
            if reason:
                reasons.append(f"{_requirement_label(req)}: {reason}")
            if gap:
                gaps.append(gap)
        final = total / total_w if total_w else 0.0
        tags = set(_normalize_tags(candidate.get("tags") or []))
        props = candidate.get("properties") or {}
        hdt = _numeric(props, "hdt_045_mpa_c")
        if "thermal" in requirements and not tags.intersection({"carbon_fiber", "glass_fiber", "thermal_path_candidate", "thermal_filler", "ceramic_filler"}):
            final *= 0.88
        if candidate.get("_source") == "upstream_payload":
            final *= 1.18
        elif any((c.get("_source") == "upstream_payload") for c in candidates):
            final *= 0.82
        if "heat_resistance" in requirements and hdt is not None and hdt < 70:
            final *= 0.82
        coverage = _coverage_for_requirements(candidate, requirements)
        hard_status = _hard_constraint_status(candidate, constraints)
        unknown_hard = sum(1 for item in hard_status.values() if item.get("status") == "unknown")
        proxy_hard = sum(1 for item in hard_status.values() if item.get("status") == "proxy")
        risk_hard = sum(1 for item in hard_status.values() if item.get("status") == "risk")
        failed_hard = sum(1 for item in hard_status.values() if item.get("status") == "fail")
        if unknown_hard:
            final *= max(0.55, 1.0 - 0.06 * unknown_hard)
        if proxy_hard:
            final *= max(0.75, 1.0 - 0.025 * proxy_hard)
        if risk_hard:
            final *= max(0.55, 1.0 - 0.08 * risk_hard)
        if failed_hard:
            final *= max(0.38, 1.0 - 0.16 * failed_hard)
        if coverage < 0.35:
            gaps.append("关键数据覆盖率偏低，当前排名只能作为候选优先级而非达标结论")
        ranked.append(FilamentScore(
            candidate=candidate,
            score=final,
            requirement_scores=detail,
            data_coverage=coverage,
            hard_constraint_status=hard_status,
            reasons=reasons,
            gaps=list(dict.fromkeys(gaps)),
        ))

    ranked.sort(key=lambda item: (-item.score, -item.data_coverage, str(item.candidate.get("name") or "")))
    notes = [
        "当前版本为第一阶段已有耗材推荐框架，使用上游 candidate_materials 时会优先覆盖内置 starter profiles。",
        "硬约束缺失时不再按满足处理，只保留候选优先级；导热、层间剪切、HDT、电绝缘等必须以牌号 TDS/试样实测闭合。",
    ]
    return FilamentSelectionResult(
        taskid=taskid,
        scenario=scenario,
        requirements=requirements,
        constraints=constraints,
        ranked=ranked,
        source_payload_path=payload_path,
        notes=notes,
    )


def _requirements_from_constraints(requirements: List[str], constraints: JsonDict) -> List[str]:
    out = list(requirements)
    implied = [
        ("thermal_conductivity_min_w_mk", "thermal"),
        ("hdt_min_c", "heat_resistance"),
        ("continuous_use_temp_min_c", "heat_resistance"),
        ("layer_shear_min_mpa", "layer_adhesion"),
        ("elongation_min_pct", "strength"),
        ("tensile_strength_min_mpa", "strength"),
        ("flexural_strength_min_mpa", "strength"),
        ("density_max_g_cm3", "dimensional_stability"),
        ("cte_max_um_m_c", "dimensional_stability"),
        ("electrical_insulation_required", "electrical_insulation"),
    ]
    for key, req in implied:
        if constraints.get(key) not in (None, False) and req not in out:
            out.append(req)
    return out


def write_selection_manifest(repo_root: str, result: FilamentSelectionResult) -> str:
    out_dir = Path(repo_root) / "src" / "MNS_CaseHub" / "cases" / "material_discovery_demo" / "results" / "filament_selection" / str(result.taskid)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "filament_selection_manifest.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def build_optimization_plan(result: FilamentSelectionResult) -> List[JsonDict]:
    """Build material modification suggestions from unmet or uncertain indicators."""

    if not result.ranked:
        return []

    top = result.ranked[0]
    status = top.hard_constraint_status or {}
    requirements = set(result.requirements or [])
    rows: List[JsonDict] = []

    def add(issue: str, strategy: str, materials: str, caution: str, priority: int) -> None:
        if any(row.get("issue") == issue for row in rows):
            return
        rows.append({
            "issue": issue,
            "strategy": strategy,
            "materials": materials,
            "caution": caution,
            "priority": priority,
        })

    unresolved = {
        name: item
        for name, item in status.items()
        if item.get("status") in {"unknown", "fail", "proxy", "risk"}
    }
    scores = top.requirement_scores or {}

    def weak(req: str, threshold: float = 0.75) -> bool:
        val = scores.get(req)
        try:
            return val is not None and float(val) < threshold
        except Exception:
            return False

    if "导热系数" in unresolved or weak("thermal"):
        if status.get("电绝缘", {}).get("status") in {"risk", "unknown", "proxy"} or weak("electrical_insulation"):
            add(
                "导热与绝缘同时不足",
                "优先构建绝缘导热网络",
                "BN、AlN、Al2O3、表面包覆 SiC",
                "少用未包覆碳系填料，避免形成导电通路",
                1,
            )
        else:
            add(
                "导热不足",
                "提高面内/厚向导热通路连续性",
                "石墨、石墨烯、CNT、短切碳纤维、BN/AlN",
                "碳系填料提升导热快，但会提高导电风险",
                1,
            )

    if "电绝缘" in unresolved or weak("electrical_insulation"):
        add(
            "电绝缘风险",
            "用绝缘填料替代或包覆导电填料",
            "BN、AlN、Al2O3、硅烷包覆碳纤维",
            "需要补体积/表面电阻率和击穿强度",
            2,
        )

    if "层间剪切强度" in unresolved or weak("layer_adhesion"):
        add(
            "层间结合不足",
            "提升基体韧性和纤维/基体界面结合",
            "PA12/PA6 共混、增韧剂、马来酸酐接枝相容剂、硅烷偶联剂",
            "不建议只增加刚性填料，否则层间可能更脆",
            3,
        )

    if any(key in unresolved for key in ("拉伸强度", "弯曲强度")) or weak("strength"):
        add(
            "强度承载不足",
            "增强承载骨架并控制纤维取向",
            "短切碳纤维、玻纤、连续纤维局部增强",
            "碳纤维增强可能牺牲绝缘和层间韧性",
            4,
        )

    if "CTE" in unresolved or "密度" in unresolved or weak("dimensional_stability", 0.7):
        add(
            "尺寸稳定性不足",
            "降低吸水率和热膨胀，优先选低吸湿基体",
            "PA12、PPS、PEEK、玻纤、低吸湿矿物填料",
            "尼龙体系需重点关注吸湿后的尺寸和强度保持率",
            5,
        )

    if "HDT" in unresolved or "连续使用温度" in unresolved or weak("heat_resistance"):
        add(
            "耐热边界不足",
            "提高基体 Tg/HDT 或引入耐热增强体系",
            "PC、PPS、PEEK、PEI、PPA",
            "耐热基体通常提高成本和打印门槛",
            6,
        )

    if "疲劳" in unresolved:
        add(
            "疲劳寿命未知",
            "提高韧性并降低应力集中",
            "增韧 PA12、弹性体增韧剂、界面偶联剂",
            "需要用循环载荷和热循环后的强度保持率验证",
            7,
        )

    if not rows:
        add(
            "指标基本匹配",
            "保持当前体系，进入参数和结构优化",
            "同基体微调填料比例、纤维取向和热通路布局",
            "仍建议用实测数据确认关键边界",
            9,
        )

    rows.sort(key=lambda row: int(row.get("priority", 99)))
    return rows[:5]


def build_markdown_report(result: FilamentSelectionResult, top_n: int = 5) -> str:
    ranked = result.ranked[:top_n]
    top = ranked[0] if ranked else None
    scenario = result.scenario or {}
    lines: List[str] = []
    visible_requirements = [r for r in result.requirements if r != "printability"]
    lines.append("### 应用场景和性质需求")
    lines.append("")
    if scenario:
        lines.append(f"应用场景：{scenario.get('scenario_name') or scenario.get('application') or '未指定'}")
        if scenario.get("application"):
            lines.append(f"使用位置：{scenario.get('application')}")
        lines.append("")
    if visible_requirements:
        lines.append("性质需求：" + "、".join(_requirement_label(r) for r in visible_requirements))
        lines.append("")
        lines.append("以下排序会同时参考直接数据和相近性质线索；间接线索只用于选型预判，最终仍以实测为准。")
    else:
        lines.append("以下排序会同时参考直接数据和相近性质线索；间接线索只用于选型预判，最终仍以实测为准。")
    lines.append("")

    if result.constraints:
        constraint_labels = {
            "thermal_conductivity_min_w_mk": "导热系数下限",
            "hdt_min_c": "HDT 下限",
            "layer_shear_min_mpa": "层间剪切强度下限",
            "elongation_min_pct": "断裂伸长率下限",
            "density_max_g_cm3": "密度上限",
            "tensile_strength_min_mpa": "拉伸强度下限",
            "flexural_strength_min_mpa": "弯曲强度下限",
            "continuous_use_temp_min_c": "连续使用温度下限",
            "cte_max_um_m_c": "CTE 上限",
            "volume_resistivity_min_ohm_cm": "体积电阻率下限",
            "electrical_insulation_required": "电绝缘要求",
            "fatigue_required": "疲劳要求",
        }
        units = {
            "thermal_conductivity_min_w_mk": "W/(m·K)",
            "hdt_min_c": "C",
            "layer_shear_min_mpa": "MPa",
            "elongation_min_pct": "%",
            "density_max_g_cm3": "g/cm3",
            "tensile_strength_min_mpa": "MPa",
            "flexural_strength_min_mpa": "MPa",
            "continuous_use_temp_min_c": "C",
            "cte_max_um_m_c": "um/(m·C)",
            "volume_resistivity_min_ohm_cm": "ohm·cm",
            "electrical_insulation_required": "",
            "fatigue_required": "",
        }
        max_keys = {"density_max_g_cm3", "cte_max_um_m_c"}
        lines.append("#### 材料需求目标")
        lines.append("")
        lines.append("| 需求项 | 目标 |")
        lines.append("|---|---|")
        for key, val in result.constraints.items():
            if key == "printability_constraints":
                continue
            label = constraint_labels.get(key, key)
            if isinstance(val, bool):
                target = "需要" if val else "不要求"
            elif isinstance(val, list):
                target = "；".join(str(x) for x in val)
            else:
                op = "≤" if key in max_keys else "≥"
                target = f"{op} {val:g} {units.get(key, '')}".strip()
            lines.append(f"| {label} | {target} |")
        lines.append("")

    lines.append("### 候选耗材排序")
    lines.append("")
    lines.append("| 排名 | 候选耗材 | 匹配度 | 证据覆盖 | 主要优势 | 待补数据 |")
    lines.append("|---|---|---:|---:|---|---|")
    for idx, item in enumerate(ranked, start=1):
        c = item.candidate
        advantages = "；".join(_visible_material_phrases(c.get("advantages") or [], limit=2)) or "待补充"
        gaps = "；".join(_short_gap_phrases(item.gaps, limit=2)) or "暂无明显缺口"
        lines.append(
            f"| {idx} | {c.get('display_name') or c.get('name')} | {item.score * 100:.0f}/100 | "
            f"{item.data_coverage:.0%} | {advantages} | {gaps} |"
        )
    lines.append("")

    if top:
        c = top.candidate
        name = c.get("display_name") or c.get("name")
        lines.append("### 推荐材料依据")
        lines.append("")
        all_hard = top.hard_constraint_status or {}
        unresolved = sum(1 for item in all_hard.values() if item.get("status") in {"unknown", "fail", "proxy", "risk"})
        if all_hard and unresolved > 0:
            pending_labels = [
                str(key)
                for key, item in all_hard.items()
                if item.get("status") in {"unknown", "fail", "proxy", "risk"}
            ][:3]
            pending_text = "、".join(pending_labels) if pending_labels else "关键性能"
            lines.append(f"综合当前数据，**{name}** 更适合作为优先验证对象：力学和耐热表现较突出，{pending_text}还需要进一步确认。")
        else:
            lines.append(f"**{name}** 在本轮约束下匹配度最高，可作为当前推荐耗材。")
        lines.append("")
        lines.append("已有支撑：")
        visible_reasons = [r for r in top.reasons if not r.startswith(_requirement_label("printability") + ":")]
        for reason in visible_reasons[:4]:
            lines.append(f"- {reason}")
        if top.gaps:
            lines.append("")
            lines.append("需要补充：")
            for gap in _short_gap_phrases(top.gaps, limit=3):
                lines.append(f"- {gap}")
        lines.append("")
        lines.append("关键证据：")
        lines.append("")
        lines.append("| 项目 | 判断 | 证据 | 来源 |")
        lines.append("|---|---|---|---|")
        for row in _evidence_rows(top):
            lines.append(f"| {row['项目']} | {row['状态']} | {row['本轮证据']} | {row['来源']} |")

    lines.append("")
    optimization_rows = build_optimization_plan(result)
    lines.append("### 后续优化建议")
    lines.append("")
    lines.append("| 待优化项 | 改性方向 | 可选材料/填料 | 注意事项 |")
    lines.append("|---|---|---|---|")
    for row in optimization_rows:
        lines.append(f"| {row['issue']} | {row['strategy']} | {row['materials']} | {row['caution']} |")
    lines.append("")
    lines.append("建议先用排名靠前的耗材做基准样条，再围绕上表选择填料体系、填料比例、界面偶联和取向结构；每轮改性后优先复测导热 XY/Z、CTE、层间剪切、电阻率和热循环后强度保持率。")
    lines.append("")
    lines.append("### 材料性能判读")
    lines.append("")
    lines.append("下图给出基准候选在各项材料维度上的相对表现，用于快速观察优势和短板。")
    return "\n".join(lines) + "\n"


def _evidence_rows(item: FilamentScore) -> List[JsonDict]:
    c = item.candidate or {}
    ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
    source = str(ev.get("source") or ev.get("source_type") or "候选材料数据").strip()
    confidence = _confidence_label(ev.get("confidence"))
    rows: List[JsonDict] = []
    for name, status_item in (item.hard_constraint_status or {}).items():
        status = _status_label(status_item.get("status"))
        value = status_item.get("value")
        basis = str(status_item.get("basis") or "").strip()
        evidence = _evidence_text(name, value, basis, status_item.get("status"))
        rows.append({
            "项目": name,
            "状态": status,
            "本轮证据": evidence,
            "来源": source,
            "可信度": confidence if status_item.get("status") in {"pass", "fail"} else _proxy_confidence(status_item.get("status"), confidence),
            "作用": _evidence_role(name, status_item),
        })
    return rows


def _evidence_text(name: str, value: Any, basis: str, status: Any = None) -> str:
    if value is not None:
        if status == "proxy":
            return _proxy_value_text(value, basis)
        units = {
            "导热系数": "W/(m·K)",
            "HDT": "C",
            "层间剪切强度": "MPa",
            "断裂伸长率": "%",
            "密度": "g/cm3",
            "拉伸强度": "MPa",
            "弯曲强度": "MPa",
            "连续使用温度": "C",
            "CTE": "um/(m·C)",
            "体积电阻率": "ohm·cm",
        }
        return f"{value:g} {units.get(name, '')}".strip() if isinstance(value, (int, float)) else str(value)
    if basis:
        return basis
    return "待补充直接实测数据"


def _proxy_value_text(value: Any, basis: str) -> str:
    unit = ""
    if "Z向冲击" in basis or "冲击" in basis:
        unit = "kJ/m2"
    elif "弯曲强度" in basis or "拉伸" in basis:
        unit = "MPa"
    elif "吸水率" in basis:
        unit = "%"
    value_text = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    suffix = f" {unit}" if unit else ""
    return f"{value_text}{suffix}（{basis}）"


def _evidence_role(name: str, status_item: JsonDict) -> str:
    if status_item.get("status") == "proxy":
        return "用于选型预判，不等同于直接实测"
    if status_item.get("status") == "risk":
        return "提示潜在失效模式，需实测排除风险"
    roles = {
        "导热系数": "判断热管理能力",
        "层间剪切强度": "判断层间承载和动态载荷风险",
        "拉伸强度": "判断结构承载能力",
        "弯曲强度": "判断结构承载能力",
        "HDT": "判断热载荷下的形变风险",
        "连续使用温度": "判断长期服役温度边界",
        "密度": "判断轻量化约束",
        "CTE": "判断热循环尺寸稳定性",
        "电绝缘": "判断电气安全边界",
        "疲劳": "判断循环载荷可靠性",
    }
    return roles.get(name, "用于候选排序和验证优先级判断")


def _confidence_label(value: Any) -> str:
    try:
        score = float(value)
    except Exception:
        return "中"
    if score >= 0.85:
        return "高"
    if score >= 0.55:
        return "中"
    return "低"


def _proxy_confidence(status: Any, base: str) -> str:
    if status == "unknown":
        return "低"
    if status in {"proxy", "risk"} and base == "高":
        return "中"
    return base


def _visible_material_phrases(items: Iterable[Any], limit: int) -> List[str]:
    hidden = re.compile(r"打印|喷嘴|封箱|热床|干燥|退火|FDM|工艺窗口|打印参数|设备门槛", re.IGNORECASE)
    out: List[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or hidden.search(text):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _short_gap_phrases(items: Iterable[Any], limit: int) -> List[str]:
    replacements = [
        (r"缺少实测导热系数.*", "导热系数待测"),
        (r".*无法证明满足\s*[≥>=]?\s*\d+.*W/\(m·K\).*", "导热系数待测"),
        (r".*当前仅为导热路径候选.*", "导热实测待补"),
        (r"吸水/翘曲/CTE数据不足.*", "尺寸稳定数据不足"),
        (r"缺少强度实测值.*", "强度数据不足"),
        (r".*动态载荷强度校核.*", "动态强度待验证"),
        (r"缺少体积/表面电阻率.*", "电绝缘待测"),
        (r".*击穿强度数据.*", "电绝缘待测"),
        (r".*层间.*验证.*", "层间性能待验证"),
        (r".*疲劳.*", "疲劳数据待补"),
        (r".*关键数据覆盖率偏低.*", "数据覆盖偏低"),
    ]
    out: List[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        short = text
        for pattern, repl in replacements:
            if re.search(pattern, short, flags=re.IGNORECASE):
                short = repl
                break
        if short == text:
            short = re.split(r"[；;。]", short, maxsplit=1)[0].strip()
            short = short[:28]
        if short and short not in out:
            out.append(short)
        if len(out) >= limit:
            break
    return out


def _format_hard_status(status: JsonDict) -> str:
    if not status:
        return "无明确硬指标"
    counts = {"pass": 0, "proxy": 0, "risk": 0, "unknown": 0, "fail": 0}
    for name, item in status.items():
        key = str(item.get("status"))
        counts[key] = counts.get(key, 0) + 1
    parts = []
    if counts.get("pass"):
        parts.append(f"实测支持 {counts['pass']} 项")
    if counts.get("proxy"):
        parts.append(f"间接参考 {counts['proxy']} 项")
    if counts.get("risk"):
        parts.append(f"风险待测 {counts['risk']} 项")
    if counts.get("unknown"):
        parts.append(f"数据待补 {counts['unknown']} 项")
    if counts.get("fail"):
        parts.append(f"低于目标 {counts['fail']} 项")
    return "；".join(parts) if parts else "无明确硬指标"


def _status_label(status: Any) -> str:
    symbols = {"pass": "实测支持", "fail": "低于目标", "unknown": "数据待补", "proxy": "间接参考", "risk": "风险待测"}
    return symbols.get(str(status), str(status))


def _target_label(target: Any) -> str:
    if target == "required":
        return "需要"
    return str(target)


def _requirement_label(req: str) -> str:
    labels = {
        "thermal": "导热/热管理",
        "strength": "强度",
        "stiffness": "刚度",
        "layer_adhesion": "层间结合",
        "heat_resistance": "耐热/热循环",
        "dimensional_stability": "尺寸稳定",
        "printability": "可打印性",
        "electrical_insulation": "电绝缘",
    }
    return labels.get(req, req)


def build_selection_from_latest(repo_root: str, taskid: str, text: str) -> Tuple[FilamentSelectionResult, str]:
    payload, path = latest_in_ls_payload(repo_root)
    result = rank_filaments(taskid=taskid, text=text, payload=payload, payload_path=path)
    manifest = write_selection_manifest(repo_root, result)
    return result, manifest
