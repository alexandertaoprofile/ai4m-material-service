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
            "thermal_simulation_inputs": build_thermal_simulation_inputs(self),
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


def _load_printer_profiles() -> List[JsonDict]:
    path = Path(__file__).with_name("printer_profiles.json")
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[FILAMENT] failed to read printer profiles: {exc!s}")
        return []
    printers = data.get("printers") if isinstance(data, dict) else None
    return [item for item in printers or [] if isinstance(item, dict)]


def _match_printer_profile(text: str) -> Optional[JsonDict]:
    hay = str(text or "")
    for profile in _load_printer_profiles():
        names = [profile.get("name"), profile.get("id")]
        names.extend(profile.get("aliases") or [])
        for name in names:
            alias = str(name or "").strip()
            if alias and re.search(re.escape(alias), hay, flags=re.IGNORECASE):
                return profile
    return None


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


def _scenario_dicts(payload: Optional[JsonDict]) -> List[JsonDict]:
    if not isinstance(payload, dict):
        return []
    scenarios = payload.get("scenario_tasks")
    if isinstance(scenarios, list):
        return [item for item in scenarios if isinstance(item, dict)]
    st = payload.get("simulation_task")
    if isinstance(st, dict):
        return [st]
    return []


def _scenario_from_asset_context(text: str) -> JsonDict:
    text = str(text or "")
    if "已选择 STL 模型资产" not in text and "初步零件语义" not in text:
        return {}

    def pick(label: str) -> str:
        pattern = rf"{re.escape(label)}[：:]\s*([^\n\r]+)"
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""

    part_name = pick("初步零件语义")
    category = pick("零件类别")
    focus = pick("推断关注点")
    filename = ""
    match = re.search(r"文件名[：:]\s*([^\n\r]+)", text)
    if match:
        filename = match.group(1).strip()

    if not any([part_name, category, focus, filename]):
        return {}

    scenario_name = part_name or Path(filename).stem or "已选STL零件"
    if category and category != "结构件" and category not in scenario_name:
        scenario_name = f"{scenario_name}（{category}）"

    requirements = focus or "尺寸稳定、装配可靠性、刚度、轻量化、疲劳、层间结合"
    return {
        "scenario_name": scenario_name,
        "application": part_name or category or Path(filename).stem,
        "requirements": requirements,
        "asset_filename": filename,
        "asset_context_source": "stl_filename_metadata",
    }


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
    if _looks_like_motor_assembly(joined):
        found.extend(["strength", "stiffness", "heat_resistance", "dimensional_stability", "thermal", "printability"])
    if not found:
        found = ["strength", "stiffness", "printability"]
    return list(dict.fromkeys(found))


def parse_constraints(text: str, payload: Optional[JsonDict]) -> JsonDict:
    joined = "；".join([str(text or "")] + _payload_texts(payload))
    constraints: JsonDict = {}
    for holder in [payload or {}, *_scenario_dicts(payload)]:
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

    printability = constraints.get("printability_constraints")
    printability = dict(printability) if isinstance(printability, dict) else {}
    default_printed_stl = bool(re.search(r"默认制造方式.*FDM|默认制造方式.*FFF|待\s*FDM/FFF\s*丝材\s*3D\s*打印|商用\s*3D\s*打印耗材", joined, flags=re.IGNORECASE))
    if re.search(r"FDM|FFF|丝材打印|熔融沉积", joined, flags=re.IGNORECASE) or default_printed_stl:
        printability.setdefault("process", "FDM/FFF 丝材打印")
    matched_printer = _match_printer_profile(joined)
    if matched_printer:
        printability.setdefault("printer", str(matched_printer.get("name") or matched_printer.get("id") or "已识别打印机"))
        printability.setdefault("printer_profile_id", matched_printer.get("id"))
        printability.setdefault("printer_capabilities", matched_printer.get("capabilities") or {})
    elif re.search(r"拓竹\s*A1|Bambu\s*(?:Lab\s*)?A1", joined, flags=re.IGNORECASE):
        printability.setdefault("printer", "拓竹 A1 / Bambu Lab A1")
    if re.search(r"现有喷嘴|喷嘴", joined, flags=re.IGNORECASE) or default_printed_stl:
        printability.setdefault("nozzle", "按现有喷嘴条件约束")
    if re.search(r"现有.*热床|热床", joined, flags=re.IGNORECASE) or default_printed_stl:
        printability.setdefault("bed", "按现有热床条件约束")
    if re.search(r"封闭腔体|封箱|腔体", joined, flags=re.IGNORECASE) or default_printed_stl:
        printability.setdefault("chamber", "按现有封闭腔体/封箱条件约束")
    if printability:
        constraints["printability_constraints"] = printability

    material_boundary: List[str] = []
    if re.search(r"商用耗材优先|商业耗材优先|商用\s*3D\s*打印耗材", joined) or default_printed_stl:
        material_boundary.append("商用耗材优先")
    if re.search(r"允许.*复合改性|复合改性|可打印复合耗材", joined) or default_printed_stl:
        material_boundary.append("允许复合改性")
    if re.search(r"允许.*定向增强|定向增强", joined):
        material_boundary.append("允许定向增强")
    if material_boundary:
        constraints["material_boundary"] = list(dict.fromkeys(material_boundary))
    if _looks_like_motor_assembly(joined):
        constraints["component_model"] = {
            "type": "printed_shell_plus_metal_core",
            "shell_volume_fraction": 0.30,
            "core_volume_fraction": 0.70,
            "core_template": "servo_joint_motor_core",
            "note": "关节电机/舵机按外部可打印壳体加内部金属/机电核心的等效热模型处理",
        }
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
        "material_boundary": ["material_boundary", "材料边界"],
    }
    out = _normalize_aliases(raw, aliases)
    normalized: JsonDict = {}
    for key in aliases:
        val = out.get(key)
        if val is not None:
            normalized[key] = val
    return normalized


def _looks_like_motor_assembly(text: str) -> bool:
    return bool(re.search(r"HS-?225BB|hitec|舵机|关节电机|电机壳体|电机外壳|关节驱动|伺服电机|servo", str(text or ""), flags=re.IGNORECASE))


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
    proxy = _proxy_evidence(candidate)
    tags = set(_normalize_tags(candidate.get("tags") or []))
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
        if any(_numeric(props, key) is not None for key in keys):
            checks.append(1.0)
            continue
        proxy_item = proxy.get(req) or {}
        if proxy_item:
            status = str(proxy_item.get("status") or "proxy")
            confidence = _safe_float(proxy_item.get("confidence"), 0.35)
            if status == "risk":
                checks.append(min(0.3, max(0.15, confidence)))
            else:
                checks.append(min(0.55, max(0.25, confidence)))
            continue
        checks.append(_tag_proxy_coverage(req, tags))
    if not checks:
        return 0.0
    return sum(checks) / len(checks)


def _proxy_evidence(candidate: JsonDict) -> JsonDict:
    raw = candidate.get("proxy_evidence")
    return raw if isinstance(raw, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _tag_proxy_coverage(req: str, tags: set[str]) -> float:
    if req == "thermal" and tags.intersection({"thermal_path_candidate", "thermal_filler", "ceramic_filler", "bn_filled", "aln_filled", "carbon_fiber"}):
        return 0.35
    if req in {"strength", "stiffness"} and tags.intersection({"carbon_fiber", "glass_fiber", "engineering", "stiff"}):
        return 0.35
    if req == "heat_resistance" and tags.intersection({"heat_resistant", "engineering"}):
        return 0.4
    if req == "dimensional_stability" and tags.intersection({"lower_moisture_than_pa6", "low_warp", "engineering"}):
        return 0.25
    if req == "electrical_insulation":
        if tags.intersection({"insulating", "electrical_insulating", "dielectric", "ceramic_filler", "bn_filled", "aln_filled"}):
            return 0.4
        if tags.intersection({"carbon_fiber", "carbon_nanotube", "cnt"}):
            return 0.2
    if req == "fatigue" and tags.intersection({"carbon_fiber", "glass_fiber", "tough", "moderate_toughness"}):
        return 0.25
    return 0.0


def _hard_constraint_status(candidate: JsonDict, constraints: JsonDict) -> JsonDict:
    props = candidate.get("properties") or {}
    tags = set(_normalize_tags(candidate.get("tags") or []))
    proxy = _proxy_evidence(candidate)
    status: JsonDict = {}

    def _proxy(label: str, target: Any, basis: str, value: Any = None):
        status[label] = {"status": "proxy", "target": target, "value": value, "basis": basis}

    def _proxy_from_req(label: str, req: str, target: Any) -> bool:
        item = proxy.get(req)
        if not isinstance(item, dict):
            return False
        basis = str(item.get("basis") or "").strip()
        if not basis:
            return False
        status[label] = {
            "status": "risk" if item.get("status") == "risk" else "proxy",
            "target": target,
            "value": None,
            "basis": basis,
        }
        return True

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
        if _proxy_from_req("导热系数", "thermal", constraints.get("thermal_conductivity_min_w_mk")):
            pass
        elif tags.intersection({"thermal_conductive", "thermal_filler", "thermal_path_candidate", "ceramic_filler", "ceramic_filled", "bn_filled", "aln_filled"}):
            _proxy("导热系数", constraints.get("thermal_conductivity_min_w_mk"), "填料/体系标签支持导热路径，但缺少牌号实测导热系数")
        elif tags.intersection({"carbon_fiber", "carbon_nanotube", "cnt"}):
            _proxy("导热系数", constraints.get("thermal_conductivity_min_w_mk"), "碳基增强相可作为导热路径线索，但不能等同于体积导热率达标")
    _cmp_min("hdt_min_c", ["hdt_045_mpa_c", "hdt_18_mpa_c"], "HDT")
    if "HDT" in status and status["HDT"].get("status") == "unknown":
        _proxy_from_req("HDT", "heat_resistance", constraints.get("hdt_min_c"))
    _cmp_min("layer_shear_min_mpa", ["interlayer_shear_strength_mpa"], "层间剪切强度", ["z_impact_kj_m2"], "Z向冲击强度可作为层间结合韧性的代理线索")
    _cmp_min("elongation_min_pct", ["elongation_break_pct"], "断裂伸长率")
    _cmp_max("density_max_g_cm3", ["density_g_cm3"], "密度")
    _cmp_min("tensile_strength_min_mpa", ["tensile_strength_mpa"], "拉伸强度", ["flexural_strength_mpa"], "弯曲强度可作为结构承载能力代理，但不能替代拉伸实测")
    if "拉伸强度" in status and status["拉伸强度"].get("status") == "unknown":
        _proxy_from_req("拉伸强度", "strength", constraints.get("tensile_strength_min_mpa"))
    _cmp_min("flexural_strength_min_mpa", ["flexural_strength_mpa"], "弯曲强度")
    _cmp_min("continuous_use_temp_min_c", ["continuous_use_temp_c"], "连续使用温度")
    if "连续使用温度" in status and status["连续使用温度"].get("status") == "unknown":
        _proxy_from_req("连续使用温度", "heat_resistance", constraints.get("continuous_use_temp_min_c"))
    _cmp_max("cte_max_um_m_c", ["cte_um_m_c", "ctE_um_m_c"], "CTE", ["water_absorption_pct"], "吸水率可辅助判断尺寸稳定风险，但不能替代 CTE")
    _cmp_min("volume_resistivity_min_ohm_cm", ["volume_resistivity_ohm_cm"], "体积电阻率")
    if constraints.get("electrical_insulation_required"):
        vr = _numeric(props, "volume_resistivity_ohm_cm")
        target = float(constraints.get("volume_resistivity_min_ohm_cm") or 1e8)
        if vr is None:
            if _proxy_from_req("电绝缘", "electrical_insulation", "required"):
                pass
            elif tags.intersection({"insulating", "electrical_insulating", "dielectric", "ceramic_filler", "bn_filled", "aln_filled"}):
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
            elif _proxy_from_req("疲劳", "fatigue", "required"):
                pass
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
    display_name = str(raw.get("display_name") or name).strip()
    if _is_invalid_candidate_name(str(name), display_name):
        return None
    props = _normalize_property_keys(_merge_dict_fields(
        raw,
        ["properties", "mechanical_properties", "thermal_properties", "physical_properties", "耗材特性", "物理性能", "机械性能"],
    ))
    process = _normalize_process_keys(_merge_dict_fields(
        raw,
        ["process", "print_settings", "printing_settings", "recommended_print_settings", "打印设置", "推荐打印设置", "打印前准备"],
    ))
    evidence = raw.get("evidence") or raw.get("source") or "upstream_payload"
    evidence_text = ""
    if isinstance(evidence, dict):
        evidence_text = str(evidence.get("quote") or evidence.get("basis") or evidence.get("source") or "")
    else:
        evidence_text = str(evidence or "")
    proxy = raw.get("proxy_evidence") if isinstance(raw.get("proxy_evidence"), dict) else {}
    inferred = _infer_proxy_from_candidate_text(
        "；".join([
            str(name),
            str(raw.get("display_name") or ""),
            evidence_text,
            "；".join(str(x) for x in raw.get("advantages") or [] if isinstance(raw.get("advantages"), list)),
            "；".join(str(x) for x in raw.get("limitations") or [] if isinstance(raw.get("limitations"), list)),
        ])
    )
    merged_proxy = {**inferred.get("proxy_evidence", {}), **proxy}
    tags = []
    if isinstance(raw.get("tags"), list):
        tags.extend(raw.get("tags") or [])
    tags.extend(inferred.get("tags", []))
    tags = list(dict.fromkeys(str(t) for t in tags if t))
    props = _sanitize_upstream_properties(props, str(name), display_name, str(raw.get("family") or ""), tags, evidence_text)
    return {
        "name": name.strip(),
        "display_name": display_name,
        "family": raw.get("family") or "",
        "tags": tags,
        "_source": "upstream_payload",
        "properties": dict(props),
        "process": dict(process),
        "advantages": raw.get("advantages") if isinstance(raw.get("advantages"), list) else [],
        "limitations": raw.get("limitations") if isinstance(raw.get("limitations"), list) else [],
        "proxy_evidence": merged_proxy,
        "evidence": evidence,
    }


def _is_invalid_candidate_name(name: str, display_name: str = "") -> bool:
    text = (display_name or name or "").strip()
    compact = re.sub(r"\s+", "", text).lower()
    invalid_exact = {
        "共用",
        "通用",
        "共同",
        "共享",
        "场景共用",
        "场景通用",
        "共用材料",
        "通用材料",
        "共用材料体系",
        "通用材料体系",
        "baseline",
        "advanced",
        "candidate",
    }
    if compact in invalid_exact:
        return True
    if len(compact) <= 2 and not re.search(r"[a-z0-9]|碳|氮|氧|硼|铝|尼龙|peek|pa|pc|abs|pla", compact, flags=re.IGNORECASE):
        return True
    return False


def _sanitize_upstream_properties(props: JsonDict, name: str, display_name: str, family: str, tags: Iterable[str], evidence_text: str) -> JsonDict:
    cleaned = dict(props or {})
    k = _numeric(cleaned, "thermal_conductivity_w_mk")
    if k is None:
        return cleaned

    candidate = {"name": name, "display_name": display_name, "family": family, "tags": list(tags or [])}
    if not _is_unusually_high_polymer_conductivity(k, family or name, tags, candidate):
        return cleaned

    if _evidence_supports_thermal_conductivity(evidence_text, k):
        return cleaned

    cleaned["thermal_conductivity_w_mk"] = None
    cleaned.setdefault("_discarded_properties", {})["thermal_conductivity_w_mk"] = {
        "value": k,
        "reason": "上游高导热值缺少证据文本支撑，疑似把需求阈值写入材料属性",
    }
    return cleaned


def _evidence_supports_thermal_conductivity(text: str, value: float) -> bool:
    evidence = str(text or "")
    if not evidence:
        return False
    thermal_hit = re.search(r"导热|热导|thermal\s*conduct", evidence, flags=re.IGNORECASE)
    if not thermal_hit:
        return False
    value_text = f"{float(value):g}"
    pattern = rf"(导热|热导|thermal\s*conduct)[^。；;\n]{{0,80}}{re.escape(value_text)}|{re.escape(value_text)}[^。；;\n]{{0,80}}(导热|热导|thermal\s*conduct)"
    return bool(re.search(pattern, evidence, flags=re.IGNORECASE))


def _infer_proxy_from_candidate_text(text: str) -> JsonDict:
    text = str(text or "").lower()
    tags: List[str] = []
    proxy: JsonDict = {}

    def add_tag(*values: str):
        for value in values:
            if value and value not in tags:
                tags.append(value)

    def add_proxy(key: str, basis: str, confidence: float = 0.35, status: str = "proxy"):
        proxy.setdefault(key, {"status": status, "basis": basis, "confidence": confidence})

    if re.search(r"(碳纤|碳纤维|carbon\s*fiber|\bcf\b|cfrp)", text, flags=re.IGNORECASE):
        add_tag("carbon_fiber", "stiff", "thermal_path_candidate")
        add_proxy("strength", "碳纤维增强体系可作为强度/承载潜力线索，但不能替代牌号拉伸实测", 0.45)
        add_proxy("stiffness", "碳纤维增强体系可作为刚度潜力线索，但仍需模量实测", 0.45)
        add_proxy("thermal", "碳基增强相可形成导热路径，但不能等同于体积导热率达标", 0.35)
        add_proxy("electrical_insulation", "碳基增强相可能引入导电网络，需要体积/表面电阻率确认", 0.25, "risk")
    if re.search(r"(玻纤|玻璃纤维|glass\s*fiber|\bgf\b)", text, flags=re.IGNORECASE):
        add_tag("glass_fiber", "stiff")
        add_proxy("strength", "玻纤增强体系可作为承载潜力线索，但不能替代牌号强度实测", 0.42)
        add_proxy("stiffness", "玻纤增强体系可作为刚度潜力线索，但仍需模量实测", 0.42)
    if re.search(r"(peek|聚醚醚酮|pps|聚苯硫醚|pei|ppa)", text, flags=re.IGNORECASE):
        add_tag("engineering", "heat_resistant")
        add_proxy("heat_resistance", "高温工程塑料基体具备耐热潜力，但仍需具体牌号 HDT/Tg/连续使用温度", 0.5)
        add_proxy("dimensional_stability", "高温工程塑料体系可作为尺寸稳定线索，但仍需 CTE/吸水率/热循环数据", 0.35)
    if re.search(r"(pa12|尼龙\s*12|尼龙12)", text, flags=re.IGNORECASE):
        add_tag("engineering", "lower_moisture_than_pa6")
        add_proxy("dimensional_stability", "PA12 体系通常较 PA6 吸湿风险低，但仍需牌号吸水率和 CTE 数据", 0.38)
    if re.search(r"(bn|氮化硼|aln|氮化铝|al2o3|氧化铝|陶瓷|ceramic|sic)", text, flags=re.IGNORECASE):
        add_tag("thermal_filler", "ceramic_filler", "insulating")
        add_proxy("thermal", "陶瓷导热填料/体系可作为导热路径线索，但仍需复合材料实测导热系数", 0.45)
        add_proxy("electrical_insulation", "BN/AlN/Al2O3 等陶瓷填料通常具备绝缘潜力，但仍需电阻率和击穿强度闭合", 0.45)
    if re.search(r"(alsi10mg|铝合金|金属|metal|铸造|selective\s*laser|slm|lpbf|粉末床)", text, flags=re.IGNORECASE):
        add_tag("metal", "non_filament_material")
        add_proxy("printability", "金属/铸造/粉末床材料不是 FDM/FFF 丝材耗材，需要金属增材或铸造工艺", 0.7, "risk")
        add_proxy("electrical_insulation", "金属体系与电绝缘要求冲突", 0.8, "risk")
    if re.search(r"(取向|定向|oriented|流场)", text, flags=re.IGNORECASE):
        add_tag("oriented_structure", "thermal_path_candidate")
        add_proxy("thermal", "取向结构可提升定向导热通路连续性，但仍需 XY/Z 导热实测", 0.48)
    if re.search(r"(冲击|疲劳|循环|动态载荷|韧性)", text, flags=re.IGNORECASE):
        add_proxy("fatigue", "冲击/韧性/动态载荷描述可辅助判断疲劳风险，但不能替代疲劳寿命", 0.3)
    elif "carbon_fiber" in tags or "glass_fiber" in tags:
        add_proxy("fatigue", "纤维增强体系可作为承载潜力线索，但疲劳寿命仍需循环载荷实测", 0.25)

    return {"tags": tags, "proxy_evidence": proxy}


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
        "specific_heat_j_kg_k": ["specific_heat_j_kg_k", "比热容", "定压比热", "specific_heat", "heat_capacity", "cp"],
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


def _candidate_context_text(candidate: JsonDict, family: str = "") -> str:
    parts = [
        family,
        str(candidate.get("family") or ""),
        str(candidate.get("name") or ""),
        str(candidate.get("display_name") or ""),
    ]
    return " ".join(part for part in parts if part).lower()


def _is_unusually_high_polymer_conductivity(value: Any, family: str, tags: Iterable[str], candidate: Optional[JsonDict] = None) -> bool:
    try:
        k = float(value)
    except Exception:
        return False
    if k < 2.0:
        return False

    normalized_tags = set(_normalize_tags(tags or []))
    if normalized_tags.intersection({"thermal_filler", "ceramic_filler", "bn_filled", "aln_filled", "oriented_structure"}):
        return False

    context = _candidate_context_text(candidate or {}, family)
    polymer_hint = re.search(r"\b(pa6|pa12|pa|pc|abs|asa|petg|pla|nylon|peek|pei|pps|ppa)\b|尼龙|聚酰胺|碳纤维", context, flags=re.IGNORECASE)
    return bool(polymer_hint or normalized_tags.intersection({"carbon_fiber", "glass_fiber", "engineering", "thermal_path_candidate"}))


def collect_candidates(payload: Optional[JsonDict]) -> List[JsonDict]:
    candidates = starter_profiles()
    upstream_candidates: List[JsonDict] = []
    for holder in [payload or {}, *_scenario_dicts(payload)]:
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
    scenario = dict(_first_scenario(payload))
    asset_scenario = _scenario_from_asset_context(text)
    if asset_scenario:
        scenario.update(asset_scenario)
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
    """Build modification suggestions from the top candidate's unmet indicators."""

    if not result.ranked:
        return []

    top = result.ranked[0]
    top_name = str(top.candidate.get("display_name") or top.candidate.get("name") or "基准耗材").strip()
    status = top.hard_constraint_status or {}
    rows: List[JsonDict] = []

    def add(
        prop: str,
        current: str,
        strategy: str,
        materials: str,
        expected: str,
        interaction: str,
        verification: str,
        priority: int,
    ) -> None:
        if any(row.get("property") == prop for row in rows):
            return
        rows.append({
            "base_material": top_name,
            "property": prop,
            "current": current,
            "strategy": strategy,
            "materials": materials,
            "expected": expected,
            "interaction": interaction,
            "verification": verification,
            "priority": priority,
            # Kept for older visual helpers; the current report uses the fields above.
            "issue": prop,
            "caution": verification,
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

    def target_text(label: str, target: Any) -> str:
        if target in (None, ""):
            return ""
        if target == "required":
            return "需要"
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
        op = "≤" if label in {"密度", "CTE"} else "≥"
        if isinstance(target, (int, float)):
            return f"{op} {target:g} {units.get(label, '')}".strip()
        return str(target)

    def current_for(label: str, req: str = "") -> str:
        item = status.get(label) or {}
        if item:
            state = _status_label(item.get("status"))
            value = item.get("value")
            target = item.get("target")
            basis = str(item.get("basis") or "").strip()
            parts = [state]
            if value is not None:
                parts.append(f"当前 {value:g}" if isinstance(value, (int, float)) else f"当前 {value}")
            if target not in (None, ""):
                parts.append(f"目标 {target_text(label, target)}")
            if basis:
                parts.append(basis)
            return "；".join(parts)
        if req and req in scores:
            try:
                return f"相对评分 {float(scores[req]) * 10:.1f}/10，低于建议阈值"
            except Exception:
                pass
        return "当前证据不足"

    insulation_risky = status.get("电绝缘", {}).get("status") in {"risk", "unknown", "proxy"} or weak("electrical_insulation")
    thermal_insulation_combined = False

    if "导热系数" in unresolved or weak("thermal"):
        if insulation_risky:
            thermal_insulation_combined = True
            add(
                "导热/电绝缘协同",
                current_for("导热系数", "thermal"),
                "在保持绝缘的前提下建立连续导热网络，优先做面内与厚向双向导热设计",
                "BN、AlN、Al2O3、表面绝缘包覆 SiC，必要时少量包覆碳纤维",
                "提高导热通路连续性，同时降低形成导电网络的风险",
                "导热填料加多后可能抬高黏度、降低层间韧性；若使用碳系填料，还可能削弱电绝缘",
                "导热系数 XY/Z、体积/表面电阻率、击穿强度",
                1,
            )
        else:
            add(
                "导热系数",
                current_for("导热系数", "thermal"),
                "提高填料连通度和取向，让热通路沿主要散热方向连续",
                "石墨、石墨烯、CNT、短切碳纤维、BN、AlN",
                "提升面内或厚向导热能力，优先逼近用户设定的导热目标",
                "高导热填料通常会牺牲韧性和加工窗口；碳系方案还需额外确认绝缘风险",
                "导热系数 XY/Z、热循环后导热保持率",
                1,
            )

    if not thermal_insulation_combined and ("电绝缘" in unresolved or weak("electrical_insulation")):
        add(
            "电绝缘",
            current_for("电绝缘", "electrical_insulation"),
            "减少导电连续相，或对导电增强相做绝缘包覆/界面隔离",
            "BN、AlN、Al2O3、硅烷包覆碳纤维、绝缘陶瓷填料",
            "降低漏电和击穿风险，让热管理方案不破坏电安全边界",
            "绝缘填料的导热提升通常低于碳系填料；包覆处理也可能降低界面传热效率",
            "体积/表面电阻率、击穿强度、介电损耗",
            2,
        )

    if "层间剪切强度" in unresolved or weak("layer_adhesion"):
        add(
            "层间结合",
            current_for("层间剪切强度", "layer_adhesion"),
            "提升基体韧性和纤维/基体界面结合，避免只堆高刚性填料",
            "PA12/PA6 共混、增韧剂、马来酸酐接枝相容剂、硅烷偶联剂",
            "提高 Z 向承载和冲击韧性，降低层间剥离风险",
            "增韧会改善层间和疲劳，但可能降低模量、HDT 或尺寸稳定；需要控制添加量",
            "层间剪切强度、Z 向冲击、热循环后层间强度保持率",
            3,
        )

    if any(key in unresolved for key in ("拉伸强度", "弯曲强度")) or weak("strength"):
        add(
            "强度",
            current_for("拉伸强度", "strength") if "拉伸强度" in unresolved else current_for("弯曲强度", "strength"),
            "增强承载骨架并控制增强相取向，优先保证受力方向连续",
            "短切碳纤维、玻纤、连续纤维局部增强、界面偶联剂",
            "提高拉伸/弯曲承载能力，但需同步观察韧性和绝缘变化",
            "纤维增强会提高强度和刚度，但可能让材料更各向异性，并增加层间开裂风险",
            "拉伸强度、弯曲强度、断裂伸长率、动态载荷保持率",
            4,
        )

    if "CTE" in unresolved or "密度" in unresolved or weak("dimensional_stability", 0.7):
        add(
            "尺寸稳定",
            current_for("CTE", "dimensional_stability") if "CTE" in unresolved else current_for("密度", "dimensional_stability"),
            "降低吸水和热膨胀，优先选择低吸湿基体并加入低 CTE 增强相",
            "PA12、PPS、PEEK、玻纤、矿物填料、低吸湿陶瓷填料",
            "降低湿热环境下的尺寸漂移，并改善热循环后的强度保持",
            "低吸湿/高耐热基体能提升稳定性，但材料成本和打印门槛通常会上升",
            "CTE、吸水率、湿热后尺寸变化、热循环后强度保持率",
            5,
        )

    if "HDT" in unresolved or "连续使用温度" in unresolved or weak("heat_resistance"):
        add(
            "耐热边界",
            current_for("HDT", "heat_resistance") if "HDT" in unresolved else current_for("连续使用温度", "heat_resistance"),
            "提高基体 Tg/HDT，或切换到更高耐温工程塑料体系",
            "PC、PPS、PEEK、PEI、PPA、耐热增强填料",
            "提高连续热载荷下的形变边界和热循环稳定性",
            "耐热体系通常更难打印，层间融合和残余应力需要同步关注",
            "HDT、Tg、连续使用温度、热循环后强度保持率",
            6,
        )

    if "疲劳" in unresolved:
        add(
            "疲劳寿命",
            current_for("疲劳"),
            "提高韧性并降低界面和填料端部应力集中",
            "增韧 PA12、弹性体增韧剂、界面偶联剂、低缺陷纤维体系",
            "提升循环载荷下的裂纹扩展抗力和寿命稳定性",
            "疲劳优化常和强度、刚度存在取舍，需要避免过度增韧导致承载能力下降",
            "疲劳寿命、循环后拉伸/弯曲强度、热循环后保持率",
            7,
        )

    rows.sort(key=lambda row: int(row.get("priority", 99)))
    return rows[:5]


def _device_material_limit_rows(result: FilamentSelectionResult) -> List[Tuple[str, str]]:
    constraints = result.constraints or {}
    printability = constraints.get("printability_constraints")
    printability = printability if isinstance(printability, dict) else {}
    material_boundary = constraints.get("material_boundary")
    rows: List[Tuple[str, str]] = []

    def add(category: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        if isinstance(value, list):
            text = "；".join(str(item) for item in value if str(item).strip())
        else:
            text = str(value).strip()
        if text:
            rows.append((category, text))

    printer_name = str(printability.get("printer") or "")
    is_bambu_a1 = bool(re.search(r"拓竹\s*A1|Bambu\s*(?:Lab\s*)?A1", printer_name, flags=re.IGNORECASE))
    nozzle_value = "现有喷嘴条件"
    bed_value = "现有热床条件"
    chamber_value = "现有封闭腔体/封箱条件"
    if is_bambu_a1:
        nozzle_value = "默认喷嘴最高约 300 ℃，默认非硬化喷嘴"
        bed_value = "默认热床最高约 100 ℃"
        chamber_value = "默认无主动加热腔体，封闭环境需现场确认"

    add("打印工艺", printability.get("process"))
    add("打印设备", printability.get("printer"))
    add("喷嘴条件", nozzle_value if printability.get("nozzle") else None)
    add("热床条件", bed_value if printability.get("bed") else None)
    add("腔体条件", chamber_value if printability.get("chamber") else None)
    add("材料边界", material_boundary)
    return rows


def _md_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _temperature_max_from_text(text: str) -> Optional[float]:
    values = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:℃|C|°C)", str(text or ""), flags=re.IGNORECASE):
        try:
            values.append(float(match.group(1)))
        except Exception:
            continue
    return max(values) if values else None


def _temperature_max_near_label(text: str, labels: Iterable[str]) -> Optional[float]:
    src = str(text or "")
    values = []
    for label in labels:
        pattern = rf"{re.escape(label)}[^0-9]{{0,80}}(\d+(?:\.\d+)?)\s*(?:-|~|至|到)?\s*(\d+(?:\.\d+)?)?\s*(?:℃|C|°C)"
        for match in re.finditer(pattern, src, flags=re.IGNORECASE):
            for group in match.groups():
                if not group:
                    continue
                try:
                    values.append(float(group))
                except Exception:
                    continue
    return max(values) if values else None


def _printability_summary(item: FilamentScore, constraints: Optional[JsonDict] = None) -> str:
    candidate = item.candidate or {}
    process = candidate.get("process") if isinstance(candidate.get("process"), dict) else {}
    constraints = constraints or {}
    printability = constraints.get("printability_constraints")
    printability = printability if isinstance(printability, dict) else {}
    capabilities = printability.get("printer_capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    tags = set(_normalize_tags(candidate.get("tags") or []))
    candidate_text = " ".join(str(candidate.get(key) or "") for key in ("name", "display_name", "family"))
    process_name = str(printability.get("process") or "")
    if (
        ("FDM" in process_name.upper() or "FFF" in process_name.upper() or "丝材" in process_name)
        and (
            tags.intersection({"metal", "non_filament_material"})
            or re.search(r"AlSi10Mg|铝合金|金属|铸造|粉末床|SLM|LPBF", candidate_text, flags=re.IGNORECASE)
        )
    ):
        return "非FDM丝材耗材"
    text = " ".join(str(process.get(key) or "") for key in ("nozzle", "bed", "nozzle_temp", "enclosure"))
    notes: List[str] = []

    nozzle_caps = capabilities.get("nozzle") if isinstance(capabilities.get("nozzle"), dict) else {}
    bed_caps = capabilities.get("bed") if isinstance(capabilities.get("bed"), dict) else {}
    chamber_caps = capabilities.get("chamber") if isinstance(capabilities.get("chamber"), dict) else {}
    nozzle_text = " ".join(str(process.get(key) or "") for key in ("nozzle_temp", "nozzle"))
    bed_text = str(process.get("bed") or "")
    nozzle_temp_need = _temperature_max_near_label(nozzle_text, ["打印喷嘴温度", "喷嘴温度", "打印温度"]) or _temperature_max_from_text(str(process.get("nozzle_temp") or ""))
    bed_temp_need = _temperature_max_near_label(bed_text, ["打印面板温度", "热床温度", "热床", "床温"])
    nozzle_temp_max = nozzle_caps.get("max_temp_c")
    bed_temp_max = bed_caps.get("max_temp_c")
    if isinstance(nozzle_temp_need, (int, float)) and isinstance(nozzle_temp_max, (int, float)) and nozzle_temp_need > float(nozzle_temp_max):
        notes.append("喷嘴温度超限")
    if isinstance(bed_temp_need, (int, float)) and isinstance(bed_temp_max, (int, float)) and bed_temp_need > float(bed_temp_max):
        notes.append("热床温度超限")

    needs_hardened_nozzle = (
        "硬化钢" in text
        or bool(tags.intersection({"carbon_fiber", "glass_fiber", "abrasive", "ceramic_filler", "thermal_filler"}))
    )
    nozzle_hardened = nozzle_caps.get("hardened")
    if needs_hardened_nozzle and nozzle_hardened is not True:
        notes.append("需硬化喷嘴")

    enclosure = str(process.get("enclosure") or "")
    chamber_enclosed = chamber_caps.get("enclosed")
    if "必需" in enclosure and chamber_enclosed is not True:
        notes.append("需封箱")
    elif "推荐" in enclosure and chamber_enclosed is not True:
        notes.append("建议封箱")

    bed = str(process.get("bed") or "")
    if "高温热床" in bed:
        notes.append("热床待确认")

    if "按供应商 TDS" in text or not process:
        notes.append("工艺待确认")

    if not notes:
        return "当前设备直接适配"
    return "；".join(notes[:2])


def _is_directly_printable(item: FilamentScore, constraints: Optional[JsonDict] = None) -> bool:
    return _printability_summary(item, constraints=constraints) == "当前设备直接适配"


def _ranked_for_report(ranked: List[FilamentScore], top_n: int, constraints: Optional[JsonDict] = None) -> List[FilamentScore]:
    displayed = list(ranked[:top_n])
    if not displayed or any(_is_directly_printable(item, constraints=constraints) for item in displayed):
        return displayed

    printable = next((item for item in ranked[top_n:] if _is_directly_printable(item, constraints=constraints)), None)
    if printable is None:
        return displayed
    if len(displayed) < top_n:
        displayed.append(printable)
    else:
        displayed[-1] = printable
    return displayed


def _constraint_display_note(key: str, value: Any) -> str:
    if key == "thermal_conductivity_min_w_mk":
        try:
            target = float(value)
        except Exception:
            target = 0.0
        if target >= 50:
            return "该数值远高于常见 FDM 聚合物耗材，当前按上游目标保留，并作为数据缺口/改性方向校核。"
        return "按牌号导热系数或实测导热数据校核。"
    if key == "density_max_g_cm3":
        return "用于轻量化边界判断，缺少牌号密度时不默认满足。"
    if key == "electrical_insulation_required":
        return "需要体积电阻率、表面电阻率或击穿强度数据闭合。"
    if key == "fatigue_required":
        return "需要循环载荷或热循环后的强度保持数据验证。"
    if key in {"hdt_min_c", "continuous_use_temp_min_c"}:
        return "用于判断热载荷下的形变边界。"
    if key in {"layer_shear_min_mpa", "elongation_min_pct", "tensile_strength_min_mpa", "flexural_strength_min_mpa"}:
        return "用于结构承载和层间可靠性校核。"
    if key == "cte_max_um_m_c":
        return "用于热循环尺寸稳定性校核。"
    return "按上游输入保留，需结合牌号数据或实测确认。"


def build_markdown_report(result: FilamentSelectionResult, top_n: int = 5, include_conclusion: bool = True) -> str:
    ranked = _ranked_for_report(result.ranked, top_n, constraints=result.constraints)
    top = result.ranked[0] if result.ranked else None
    scenario = result.scenario or {}
    lines: List[str] = []
    visible_requirements = [r for r in result.requirements if r != "printability"]
    lines.append("### 耗材选型和计算优化")
    lines.append("")
    if scenario:
        lines.append(f"- **应用场景**：{scenario.get('scenario_name') or scenario.get('application') or '未指定'}")
        if scenario.get("application"):
            lines.append("")
            lines.append(f"- **使用位置**：{scenario.get('application')}")
        lines.append("")
    if visible_requirements:
        lines.append("- **关注性能**：" + "、".join(_requirement_label(r) for r in visible_requirements))
        lines.append("")
        lines.append("> 判读说明：直接数据优先；相近性质线索仅用于选型预判，最终以牌号 TDS、试样实测和打印验证为准。")
    else:
        lines.append("> 判读说明：直接数据优先；相近性质线索仅用于选型预判，最终以牌号 TDS、试样实测和打印验证为准。")
    lines.append("")

    limit_rows = _device_material_limit_rows(result)
    if limit_rows:
        lines.append("### 设备与材料限制")
        lines.append("")
        lines.append("| 限制条件 | 当前条件 |")
        lines.append("|---|---|")
        for category, value in limit_rows:
            lines.append(f"| {_md_cell(category)} | {_md_cell(value)} |")
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
            "hdt_min_c": "℃",
            "layer_shear_min_mpa": "MPa",
            "elongation_min_pct": "%",
            "density_max_g_cm3": "g/cm3",
            "tensile_strength_min_mpa": "MPa",
            "flexural_strength_min_mpa": "MPa",
            "continuous_use_temp_min_c": "℃",
            "cte_max_um_m_c": "um/(m·℃)",
            "volume_resistivity_min_ohm_cm": "ohm·cm",
            "electrical_insulation_required": "",
            "fatigue_required": "",
        }
        max_keys = {"density_max_g_cm3", "cte_max_um_m_c"}
        lines.append("#### 量化目标与校核口径")
        lines.append("")
        lines.append("| 需求项 | 上游目标 | 校核说明 |")
        lines.append("|---|---|---|")
        for key, val in result.constraints.items():
            if key in {"printability_constraints", "material_boundary", "component_model"}:
                continue
            label = constraint_labels.get(key, key)
            if isinstance(val, bool):
                target = "需要" if val else "不要求"
            elif isinstance(val, list):
                target = "；".join(str(x) for x in val)
            elif isinstance(val, dict):
                target = "；".join(f"{k}: {v}" for k, v in val.items())
            else:
                op = "≤" if key in max_keys else "≥"
                try:
                    target = f"{op} {float(val):g} {units.get(key, '')}".strip()
                except (TypeError, ValueError):
                    target = str(val)
            note = _constraint_display_note(key, val)
            lines.append(f"| {_md_cell(label)} | {_md_cell(target)} | {_md_cell(note)} |")
        lines.append("")

    lines.append("### 候选耗材排序")
    lines.append("")
    lines.append("> 说明：下表是当前约束下的优先验证顺序，不代表候选耗材已经完全满足所有材料指标；`当前设备直接适配` 只表示当前打印条件可先做样条验证。")
    lines.append("")
    lines.append("| 排名 | 候选耗材 | 匹配度 | 打印适配 | 证据覆盖 | 主要优势 |")
    lines.append("|---|---|---:|---|---:|---|")
    for idx, item in enumerate(ranked, start=1):
        c = item.candidate
        advantages = "；".join(_candidate_advantage_phrases(item, limit=2)) or "待补充"
        lines.append(
            f"| {idx} | {_md_cell(c.get('display_name') or c.get('name'))} | {item.score * 100:.0f}% | "
            f"{_md_cell(_printability_summary(item, constraints=result.constraints))} | {item.data_coverage:.0%} | "
            f"{_md_cell(advantages)} |"
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
    if optimization_rows:
        lines.append("| 基准耗材 | 待优化性质 | 当前判断 | 优化做法 | 预期提升 | 可能影响 | 验证指标 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in optimization_rows:
            lines.append(
                f"| {row['base_material']} | {row['property']} | {row['current']} | "
                f"{row['strategy']}；可选：{row['materials']} | {row['expected']} | {row['interaction']} | {row['verification']} |"
            )
        lines.append("")
        lines.append("建议先用第一名耗材做基准样条，再只针对上表未闭合的性质做小步改性。这里不建议把所有填料一次性叠加，因为导热、强度、层间结合、绝缘和尺寸稳定之间会互相牵制；每一轮只改一到两个变量，再复测对应验证指标。")
    else:
        lines.append("当前第一名耗材没有暴露出需要通过材料改性处理的未满足项，建议先进入样条验证和应用结构匹配；若后续实测出现偏差，再按具体失效项补充改性方案。")
    lines.append("")
    lines.append("### 材料性能判读")
    lines.append("")
    lines.append("下图给出基准候选在各项材料维度上的相对表现，用于快速观察优势和短板。")
    if include_conclusion:
        conclusion = build_final_conclusion(result, optimization_rows)
        if conclusion:
            lines.append("")
            lines.append("### 结论")
            lines.append("")
            lines.append(conclusion)
    return "\n".join(lines) + "\n"


def build_final_conclusion(result: FilamentSelectionResult, optimization_rows: Optional[List[JsonDict]] = None) -> str:
    if not result.ranked:
        return ""

    top = result.ranked[0]
    name = str(top.candidate.get("display_name") or top.candidate.get("name") or "当前第一候选").strip()
    printable = next((item for item in result.ranked if _is_directly_printable(item, constraints=result.constraints)), None)
    printable_name = ""
    if printable is not None:
        printable_name = str(printable.candidate.get("display_name") or printable.candidate.get("name") or "").strip()
    status = top.hard_constraint_status or {}
    scores = top.requirement_scores or {}
    optimization_rows = optimization_rows if optimization_rows is not None else build_optimization_plan(result)

    passed = [name_ for name_, item in status.items() if item.get("status") == "pass"]
    proxy = [name_ for name_, item in status.items() if item.get("status") == "proxy"]
    unknown = [name_ for name_, item in status.items() if item.get("status") == "unknown"]
    risk = [name_ for name_, item in status.items() if item.get("status") == "risk"]
    failed = [name_ for name_, item in status.items() if item.get("status") == "fail"]

    score_supported: List[str] = []
    for req, score in scores.items():
        if req == "printability":
            continue
        try:
            if float(score) >= 0.75:
                label = _requirement_label(req)
                if label not in score_supported:
                    score_supported.append(label)
        except Exception:
            continue

    supported = _dedupe_conclusion_labels(passed + score_supported)
    uncertain = list(dict.fromkeys(proxy + unknown + risk))
    unmet = list(dict.fromkeys(failed))

    paragraphs: List[str] = []
    if supported:
        paragraphs.append(
            f"- **性能优先候选**：建议先以 **{name}** 作为材料性能方向的第一候选。它在 "
            f"{'、'.join(supported[:4])} 方面有较好的直接数据或相近证据支撑，适合作为性能验证起点。"
        )
    else:
        paragraphs.append(
            f"- **性能优先候选**：现有耗材里，**{name}** 是当前相对最合适的第一候选，但它更像是优先验证对象，还不能直接当作完全达标材料。"
        )

    if printable_name:
        if printable_name == name:
            paragraphs.append(
                f"- **打印验证基准**：**{printable_name}** 同时具备 `当前设备直接适配` 条件，可优先用于现有设备的样条打印与工艺验证。"
            )
        else:
            paragraphs.append(
                f"- **打印验证基准**：如果要先验证当前打印机、默认喷嘴和热床流程，建议加入 **{printable_name}** 作为可直接打印的对照样条；它不代表性能最优，但能帮助区分“材料性能不足”和“打印工艺不可达”。"
            )

    if uncertain:
        paragraphs.append(
            f"- **需要补证据**：{'、'.join(uncertain[:5])} 仍需要牌号 TDS、试样实测或打印验证闭合；这些项目目前不能直接等同于已经满足应用要求。"
        )
    if unmet:
        paragraphs.append(
            f"- **当前不能闭合**：{'、'.join(unmet[:5])} 还不能闭合，不建议用文字判断替代实测，需要先补数据再决定是否继续推进。"
        )
    elif uncertain:
        paragraphs.append(
            f"- **当前不能证明达标**：还不能证明 {'、'.join(uncertain[:4])} 已经达标。它们不是一定不满足，而是还缺少能让结论站住的直接数据。"
        )

    if optimization_rows:
        focus = "、".join(str(row.get("property") or "") for row in optimization_rows[:3] if row.get("property"))
        difficulty = _optimization_difficulty(optimization_rows)
        paragraphs.append(
            f"- **后续优化方向**：建议围绕 {focus} 展开。整体难度判断为 **{difficulty}**：核心不是简单加填料，而是在导热、绝缘、层间韧性和尺寸稳定之间找平衡。"
        )
    else:
        paragraphs.append(
            "- **后续优化方向**：可以先不做材料改性，优先进入样条制备和应用结构匹配。只有当实测暴露出短板时，再针对具体失效项做小范围配方调整。"
        )

    return "\n".join(paragraphs)


def build_material_property_summary(result: FilamentSelectionResult) -> str:
    """Summarize measured, proxy and calculated material properties for the top candidate."""
    if not result.ranked:
        return ""

    top = result.ranked[0]
    candidate = top.candidate or {}
    name = str(candidate.get("display_name") or candidate.get("name") or "当前第一候选").strip()
    props = candidate.get("properties") if isinstance(candidate.get("properties"), dict) else {}
    process = candidate.get("process") if isinstance(candidate.get("process"), dict) else {}

    lines: List[str] = []
    lines.append("### 材料参数与性质汇总")
    lines.append("")
    lines.append(f"对象：**{name}**")
    lines.append("")
    lines.append("| 参数/性质 | 数值 | 状态 | 来源 |")
    lines.append("|---|---:|---|---|")

    used_labels: set[str] = set()
    for row in _evidence_rows(top):
        label = str(row.get("项目") or "").strip()
        if not label:
            continue
        status_item = (top.hard_constraint_status or {}).get(label, {})
        if status_item.get("status") not in {"pass", "fail"}:
            continue
        value_raw = status_item.get("value") if status_item else None
        if not isinstance(value_raw, (int, float)):
            continue
        value = _format_summary_numeric_value(label, value_raw)
        status_text = str(row.get("状态") or "待判断").strip()
        source = str(row.get("来源") or "候选材料数据").strip()
        lines.append(f"| {label} | {value} | {status_text} | {source} |")
        used_labels.add(label)

    for key, meta in _material_property_meta().items():
        if key not in props:
            continue
        value = props.get(key)
        if value in (None, "", []):
            continue
        label = str(meta.get("label") or key)
        if label in used_labels:
            continue
        unit = str(meta.get("unit") or "")
        formatted = _format_property_value(value, unit)
        lines.append(f"| {label} | {formatted} | 数据已给出 | 候选材料 properties 字段 |")
        used_labels.add(label)

    for req, score in (top.requirement_scores or {}).items():
        if req == "printability":
            continue
        try:
            score_value = float(score) * 10.0
        except Exception:
            continue
        label = f"{_requirement_label(req)}相对评分"
        lines.append(f"| {label} | {score_value:.1f}/10 | 计算得到 | 雷达图评分计算 |")

    numeric_process_rows = (
        ("nozzle_diameter_mm", "喷嘴直径", "mm"),
        ("bed_temp_c", "热床温度", "C"),
        ("nozzle_temp_c", "喷嘴温度", "C"),
        ("print_speed_mm_s", "打印速度", "mm/s"),
        ("drying_temp_c", "干燥温度", "C"),
        ("drying_time_h", "干燥时间", "h"),
    )
    for key, label, unit in numeric_process_rows:
        val = process.get(key)
        if not isinstance(val, (int, float)):
            continue
        lines.append(f"| {label} | {_format_property_value(val, unit)} | 工艺参数 | 候选材料 process 字段 |")

    lines.append("")
    lines.append("说明：本表只展示本轮已拿到或计算得到的数值型参数；没有数值的文字判断和工艺描述不在这里展开。")
    return "\n".join(lines) + "\n"


def build_thermal_simulation_inputs(result: FilamentSelectionResult) -> JsonDict:
    """Return mandatory thermal-field inputs with measured values or conservative estimates."""
    if not result.ranked:
        return {}

    top = result.ranked[0]
    candidate = top.candidate or {}
    props = candidate.get("properties") if isinstance(candidate.get("properties"), dict) else {}
    tags = set(_normalize_tags(candidate.get("tags") or []))
    family = str(candidate.get("family") or candidate.get("name") or "").lower()
    name = str(candidate.get("display_name") or candidate.get("name") or "当前第一候选").strip()

    density = _thermal_density_input(props, family, tags)
    conductivity = _thermal_conductivity_input(props, family, tags)
    specific_heat = _thermal_specific_heat_input(props, family, tags)
    component_model = result.constraints.get("component_model") if isinstance(result.constraints, dict) else None
    component_model = component_model if isinstance(component_model, dict) else {}

    data = {
        "material_name": name,
        "thermal_conductivity_w_mk": conductivity,
        "specific_heat_j_kg_k": specific_heat,
        "density_kg_m3": density,
        "usage_note": "下游热场仿真优先使用 recommended；range_min/range_max 用于敏感性分析。estimated 表示本轮未拿到牌号实测值。",
    }

    if component_model.get("type") == "printed_shell_plus_metal_core":
        data["component_model"] = _motor_component_thermal_model(
            shell_name=name,
            shell_k=conductivity,
            shell_cp=specific_heat,
            shell_rho=density,
            shell_vf=float(component_model.get("shell_volume_fraction") or 0.30),
            core_vf=float(component_model.get("core_volume_fraction") or 0.70),
        )
    return data


def build_thermal_simulation_input_markdown(result: FilamentSelectionResult) -> str:
    data = build_thermal_simulation_inputs(result)
    if not data:
        return ""

    lines = []
    lines.append("### 热场仿真输入参数")
    lines.append("")
    component_model = data.get("component_model") if isinstance(data.get("component_model"), dict) else {}
    if component_model:
        lines.append(f"对象：**{component_model.get('object_name', '关节电机等效热模型')}**")
        lines.append(f"外壳耗材：**{component_model.get('shell_name', data.get('material_name', '当前第一候选'))}**")
        lines.append(f"建模假设：外壳体积分数 {component_model.get('shell_volume_fraction', 0):.0%}，内部金属/机电核心体积分数 {component_model.get('core_volume_fraction', 0):.0%}。")
        lines.append("")
        lines.append("| 参数 | 等效输入 | 外壳耗材 | 内部金属/机电核心 | 单位 | 来源 |")
        lines.append("|---|---:|---:|---:|---|---|")
        for key, label, unit in (
            ("thermal_conductivity_w_mk", "导热系数 k", "W/(m·K)"),
            ("specific_heat_j_kg_k", "比热容 cp", "J/(kg·K)"),
            ("density_kg_m3", "密度 rho", "kg/m3"),
        ):
            shell_item = component_model.get("printed_shell", {}).get(key, {})
            core_item = component_model.get("metal_core", {}).get(key, {})
            eff_item = component_model.get("effective", {}).get(key, {})
            lines.append(
                f"| {label} | {_format_property_value(eff_item.get('recommended'), '')} | "
                f"{_format_property_value(shell_item.get('recommended'), '')} | "
                f"{_format_property_value(core_item.get('recommended'), '')} | "
                f"{unit} | "
                f"{eff_item.get('source', '')} |"
            )
        lines.append("")
        lines.append("说明：该表用于当前仿真端的单一等效电机模型。外壳耗材仍按 3D 打印件选型；内部电机、齿轮、轴承、螺丝、铜绕组和磁钢按等效金属/机电核心处理。正式仿真前建议用实测质量、外壳厚度或 CAD 体积分数替换默认体积分数。")
        return "\n".join(lines) + "\n"

    lines.append(f"对象：**{data.get('material_name', '当前第一候选')}**")
    lines.append("")
    lines.append("| 参数 | 推荐值 | 估算/取值区间 | 单位 | 来源 | 置信度 |")
    lines.append("|---|---:|---:|---|---|---|")
    for key, label, unit in (
        ("thermal_conductivity_w_mk", "导热系数 k", "W/(m·K)"),
        ("specific_heat_j_kg_k", "比热容 cp", "J/(kg·K)"),
        ("density_kg_m3", "密度 rho", "kg/m3"),
    ):
        item = data.get(key) or {}
        if not item:
            continue
        rec = _format_property_value(item.get("recommended"), "")
        range_text = f"{_format_property_value(item.get('range_min'), '')} 至 {_format_property_value(item.get('range_max'), '')}"
        lines.append(
            f"| {label} | {rec} | {range_text} | {unit} | "
            f"{item.get('source', '')} | {item.get('confidence', '')} |"
        )
    lines.append("")
    lines.append("说明：若来源为工程估算，推荐值用于保证下游仿真可执行；正式设计前应以牌号 TDS 或样条实测替换，并用区间做敏感性分析。")
    return "\n".join(lines) + "\n"


def _motor_component_thermal_model(
    shell_name: str,
    shell_k: JsonDict,
    shell_cp: JsonDict,
    shell_rho: JsonDict,
    shell_vf: float,
    core_vf: float,
) -> JsonDict:
    shell_vf = min(max(float(shell_vf or 0.30), 0.05), 0.80)
    core_vf = min(max(float(core_vf or (1.0 - shell_vf)), 0.20), 0.95)
    total = shell_vf + core_vf
    shell_vf, core_vf = shell_vf / total, core_vf / total

    core_k = _thermal_value(35.0, 20.0, 60.0, "HS-225BB 类小型舵机内部金属/机电核心模板", "低", True)
    core_cp = _thermal_value(480.0, 420.0, 560.0, "钢/铜/磁钢/轴承/齿轮混合核心工程估算", "低", True)
    core_rho = _thermal_value(7200.0, 6500.0, 7800.0, "小型舵机内部金属件等效密度工程估算", "低", True)

    shell_k_rec = float(shell_k.get("recommended") or 0.25)
    shell_cp_rec = float(shell_cp.get("recommended") or 1500.0)
    shell_rho_rec = float(shell_rho.get("recommended") or 1200.0)
    core_k_rec = float(core_k["recommended"])
    core_cp_rec = float(core_cp["recommended"])
    core_rho_rec = float(core_rho["recommended"])

    rho_eff = shell_vf * shell_rho_rec + core_vf * core_rho_rec
    cp_eff = (shell_vf * shell_rho_rec * shell_cp_rec + core_vf * core_rho_rec * core_cp_rec) / max(rho_eff, 1e-9)
    # Geometric mean is a conservative middle-ground between series and
    # parallel bounds for a compact motor core wrapped by a polymer shell.
    k_eff = (shell_k_rec ** shell_vf) * (core_k_rec ** core_vf)

    eff_k = _thermal_value(k_eff, max(shell_k_rec, k_eff * 0.55), min(core_k_rec, k_eff * 1.8), "外壳耗材与内部金属核心体积分数混合估算", "低", True)
    eff_cp = _thermal_value(cp_eff, cp_eff * 0.90, cp_eff * 1.10, "质量加权等效比热估算", "低", True)
    eff_rho = _thermal_value(rho_eff, rho_eff * 0.90, rho_eff * 1.10, "体积分数线性混合密度估算", "低", True)

    return {
        "object_name": "HS-225BB 类关节电机等效热模型",
        "shell_name": shell_name,
        "shell_volume_fraction": shell_vf,
        "core_volume_fraction": core_vf,
        "printed_shell": {
            "thermal_conductivity_w_mk": shell_k,
            "specific_heat_j_kg_k": shell_cp,
            "density_kg_m3": shell_rho,
        },
        "metal_core": {
            "thermal_conductivity_w_mk": core_k,
            "specific_heat_j_kg_k": core_cp,
            "density_kg_m3": core_rho,
        },
        "effective": {
            "thermal_conductivity_w_mk": eff_k,
            "specific_heat_j_kg_k": eff_cp,
            "density_kg_m3": eff_rho,
        },
    }


def _thermal_value(recommended: float, range_min: float, range_max: float, source: str, confidence: str, estimated: bool) -> JsonDict:
    return {
        "recommended": round(float(recommended), 4),
        "range_min": round(float(range_min), 4),
        "range_max": round(float(range_max), 4),
        "source": source,
        "confidence": confidence,
        "estimated": bool(estimated),
    }


def _thermal_density_input(props: JsonDict, family: str, tags: set[str]) -> JsonDict:
    rho_g = _numeric(props, "density_g_cm3")
    if rho_g is not None:
        rho = rho_g * 1000.0
        return _thermal_value(rho, rho * 0.97, rho * 1.03, "候选材料 properties 字段", "高", False)

    if "carbon_fiber" in tags:
        return _thermal_value(1150.0, 1050.0, 1350.0, "碳纤维增强热塑性复合材料工程估算", "中", True)
    if "glass_fiber" in tags or "ceramic_filler" in tags or "thermal_filler" in tags:
        return _thermal_value(1300.0, 1150.0, 1700.0, "玻纤/陶瓷填料增强聚合物工程估算", "中", True)
    if "pa" in family:
        return _thermal_value(1080.0, 1010.0, 1160.0, "尼龙类热塑性材料工程估算", "中", True)
    if "peek" in family or "pps" in family or "pei" in family:
        return _thermal_value(1320.0, 1250.0, 1450.0, "高温工程塑料工程估算", "中", True)
    return _thermal_value(1200.0, 950.0, 1500.0, "聚合物复合材料通用工程估算", "低", True)


def _thermal_conductivity_input(props: JsonDict, family: str, tags: set[str]) -> JsonDict:
    k = _first_numeric(props, ["thermal_conductivity_w_mk", "thermal_conductivity_z_w_mk", "thermal_conductivity_xy_w_mk"])
    if k is not None:
        return _thermal_value(k, k * 0.85, k * 1.15, "候选材料 properties 字段", "高", False)

    if "oriented_structure" in tags and ("thermal_filler" in tags or "ceramic_filler" in tags):
        return _thermal_value(10.0, 3.0, 25.0, "取向导热填料复合材料工程估算", "中低", True)
    if "thermal_filler" in tags or "ceramic_filler" in tags or "bn_filled" in tags or "aln_filled" in tags:
        return _thermal_value(3.0, 1.0, 8.0, "陶瓷导热填料复合材料工程估算", "中低", True)
    if "carbon_fiber" in tags:
        return _thermal_value(0.8, 0.4, 1.5, "碳纤维增强热塑性材料工程估算", "低", True)
    if "glass_fiber" in tags:
        return _thermal_value(0.35, 0.25, 0.6, "玻纤增强热塑性材料工程估算", "低", True)
    return _thermal_value(0.25, 0.15, 0.45, "普通热塑性聚合物工程估算", "低", True)


def _thermal_specific_heat_input(props: JsonDict, family: str, tags: set[str]) -> JsonDict:
    cp = _numeric(props, "specific_heat_j_kg_k")
    if cp is not None:
        return _thermal_value(cp, cp * 0.95, cp * 1.05, "候选材料 properties 字段", "高", False)

    if "carbon_fiber" in tags or "glass_fiber" in tags or "ceramic_filler" in tags or "thermal_filler" in tags:
        return _thermal_value(1200.0, 900.0, 1600.0, "纤维/陶瓷填料增强聚合物工程估算", "中低", True)
    if "pa" in family:
        return _thermal_value(1700.0, 1500.0, 1900.0, "尼龙类热塑性材料工程估算", "中", True)
    if "peek" in family or "pps" in family or "pei" in family:
        return _thermal_value(1300.0, 1000.0, 1500.0, "高温工程塑料工程估算", "中", True)
    return _thermal_value(1500.0, 1000.0, 1900.0, "聚合物材料通用工程估算", "低", True)


def _material_property_meta() -> JsonDict:
    return {
        "thermal_conductivity_w_mk": {"label": "导热系数", "unit": "W/(m·K)"},
        "thermal_conductivity_xy_w_mk": {"label": "面内导热系数", "unit": "W/(m·K)"},
        "thermal_conductivity_z_w_mk": {"label": "Z向导热系数", "unit": "W/(m·K)"},
        "specific_heat_j_kg_k": {"label": "比热容", "unit": "J/(kg·K)"},
        "tensile_strength_mpa": {"label": "拉伸强度", "unit": "MPa"},
        "flexural_strength_mpa": {"label": "弯曲强度", "unit": "MPa"},
        "flexural_modulus_mpa": {"label": "弯曲模量", "unit": "MPa"},
        "interlayer_shear_strength_mpa": {"label": "层间剪切强度", "unit": "MPa"},
        "elongation_break_pct": {"label": "断裂伸长率", "unit": "%"},
        "impact_xy_kj_m2": {"label": "XY向冲击强度", "unit": "kJ/m2"},
        "z_impact_kj_m2": {"label": "Z向冲击强度", "unit": "kJ/m2"},
        "hdt_045_mpa_c": {"label": "HDT(0.45MPa)", "unit": "C"},
        "hdt_18_mpa_c": {"label": "HDT(1.8MPa)", "unit": "C"},
        "continuous_use_temp_c": {"label": "连续使用温度", "unit": "C"},
        "water_absorption_pct": {"label": "吸水率", "unit": "%"},
        "density_g_cm3": {"label": "密度", "unit": "g/cm3"},
        "cte_um_m_c": {"label": "CTE", "unit": "um/(m·C)"},
        "ctE_um_m_c": {"label": "CTE", "unit": "um/(m·C)"},
        "volume_resistivity_ohm_cm": {"label": "体积电阻率", "unit": "ohm·cm"},
        "fatigue_life_cycles": {"label": "疲劳寿命", "unit": "cycles"},
        "fatigue_strength_mpa": {"label": "疲劳强度", "unit": "MPa"},
    }


def _format_property_value(value: Any, unit: str = "") -> str:
    if isinstance(value, (int, float)):
        text = f"{value:g}"
    else:
        text = str(value)
    return f"{text} {unit}".strip()


def _format_summary_numeric_value(label: str, value: float) -> str:
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
        "疲劳": "kJ/m2",
    }
    return _format_property_value(value, units.get(label, ""))


def _summary_target_text(label: str, status_item: JsonDict) -> str:
    target = status_item.get("target")
    if target in (None, ""):
        return "本轮未设硬阈值"
    if target == "required":
        return "需要"
    if isinstance(target, str):
        return target
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
    op = "≤" if label in {"密度", "CTE"} else "≥"
    if isinstance(target, (int, float)):
        return f"{op} {target:g} {units.get(label, '')}".strip()
    return _target_label(target)


def _reason_for_requirement(reasons: Iterable[Any], req: str) -> str:
    prefix = f"{_requirement_label(req)}:"
    for reason in reasons or []:
        text = str(reason or "").strip()
        if text.startswith(prefix):
            return text.split(":", 1)[1].strip()
    return ""


def _dedupe_conclusion_labels(labels: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    aliases = {
        "拉伸强度": "强度",
        "弯曲强度": "强度",
        "HDT": "耐热/热循环",
        "连续使用温度": "耐热/热循环",
        "CTE": "尺寸稳定",
        "密度": "尺寸稳定",
        "体积电阻率": "电绝缘",
    }
    for raw in labels:
        label = aliases.get(str(raw), str(raw))
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _optimization_difficulty(rows: List[JsonDict]) -> str:
    props = {str(row.get("property") or "") for row in rows}
    if any("协同" in prop for prop in props) or (("电绝缘" in props or "导热系数" in props) and len(props) >= 3):
        return "中高"
    if len(props) >= 4:
        return "中高"
    if props.intersection({"层间结合", "疲劳寿命", "耐热边界"}):
        return "中"
    if len(props) <= 1:
        return "中低"
    return "中"


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


def _candidate_advantage_phrases(item: FilamentScore, limit: int) -> List[str]:
    candidate = item.candidate or {}
    props = candidate.get("properties") if isinstance(candidate.get("properties"), dict) else {}
    out: List[str] = []
    used_concepts: set[str] = set()

    numeric_preferences = [
        ("thermal_conductivity_w_mk", "导热系数", "W/(m·K)"),
        ("flexural_strength_mpa", "弯曲强度", "MPa"),
        ("tensile_strength_mpa", "拉伸强度", "MPa"),
        ("flexural_modulus_mpa", "弯曲模量", "MPa"),
        ("hdt_045_mpa_c", "HDT", "℃"),
        ("continuous_use_temp_c", "长期耐温", "℃"),
        ("z_impact_kj_m2", "Z向冲击", "kJ/m2"),
        ("density_g_cm3", "密度", "g/cm3"),
        ("water_absorption_pct", "吸水率", "%"),
        ("volume_resistivity_ohm_cm", "体积电阻率", "ohm·cm"),
    ]
    for key, label, unit in numeric_preferences:
        value = _numeric(props, key)
        if value is None:
            continue
        phrase = f"{label} {value:g} {unit}".strip()
        if phrase not in out:
            out.append(phrase)
            used_concepts.add(label)
        if len(out) >= limit:
            return out[:limit]

    positive_labels = {
        "导热系数": "导热系数",
        "HDT": "HDT",
        "连续使用温度": "长期耐温",
        "密度": "密度",
        "拉伸强度": "拉伸强度",
        "弯曲强度": "弯曲强度",
        "层间剪切强度": "层间剪切",
        "断裂伸长率": "韧性",
        "体积电阻率": "体积电阻率",
    }
    for label, status_item in (item.hard_constraint_status or {}).items():
        if status_item.get("status") not in {"pass", "proxy"}:
            continue
        concept = positive_labels.get(str(label), str(label))
        value = status_item.get("value")
        if not isinstance(value, (int, float)):
            continue
        evidence = _evidence_text(str(label), value, str(status_item.get("basis") or ""), status_item.get("status"))
        phrase = f"{concept} {evidence}".strip()
        if phrase and phrase not in out:
            out.append(phrase)
            used_concepts.add(concept)
        if len(out) >= limit:
            return out[:limit]

    for phrase in _visible_material_phrases(candidate.get("advantages") or [], limit=limit):
        if phrase not in out:
            out.append(phrase)
        if len(out) >= limit:
            return out[:limit]

    for reason in item.reasons or []:
        text = str(reason or "").strip()
        if not text or "缺少" in text or "未见" in text or "待补" in text:
            continue
        text = re.sub(r"^[^:：]{1,12}[:：]\s*", "", text)
        if not re.search(r"\d", text):
            continue
        if any(concept and concept in text for concept in used_concepts):
            continue
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    if not out:
        return ["暂无直接数值"]
    return out[:limit]


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
