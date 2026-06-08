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
    reasons: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "candidate": self.candidate,
            "score": round(float(self.score), 3),
            "requirement_scores": dict(self.requirement_scores),
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
        }


@dataclass
class FilamentSelectionResult:
    taskid: str
    scenario: JsonDict
    requirements: List[str]
    ranked: List[FilamentScore]
    source_payload_path: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "taskid": self.taskid,
            "scenario": dict(self.scenario),
            "requirements": list(self.requirements),
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
    ("layer_adhesion", ["层间", "z向", "Z向", "粘接", "结合力", "interlayer"], 1.0),
    ("heat_resistance", ["耐热", "热变形", "长期热", "交变热", "hdt", "tg"], 1.15),
    ("dimensional_stability", ["尺寸稳定", "低吸水", "吸水", "翘曲", "稳定性"], 0.95),
    ("printability", ["可打印", "加工性", "打印窗口", "易打印", "喷嘴", "热床"], 0.75),
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
        latest = max(paths, key=lambda p: p.stat().st_mtime)
        return json.loads(latest.read_text(encoding="utf-8")), str(latest)
    except Exception as exc:
        logger.warning(f"[FILAMENT] failed to read latest in-LS payload: {exc!s}")
        return None, None


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


def parse_requirements(text: str, payload: Optional[JsonDict]) -> List[str]:
    scenario = _first_scenario(payload)
    raw: List[str] = []
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
    have = set(candidate.get("tags") or [])
    want = set(tags)
    if not want:
        return 0.0
    return min(1.0, len(have.intersection(want)) / max(1, len(want)))


def _score_requirement(candidate: JsonDict, requirement: str) -> Tuple[float, str, Optional[str]]:
    props = candidate.get("properties") or {}
    name = str(candidate.get("name") or "")

    if requirement == "thermal":
        tc = _numeric(props, "thermal_conductivity_w_mk")
        if tc is not None:
            score = _norm(tc, 0.2, 3.0)
            return score, f"导热系数可用，按 {tc:g} W/mK 计入", None if score >= 0.6 else "导热系数偏低"
        bonus = _tag_bonus(candidate, ["carbon_fiber", "thermal_path_candidate"]) * 0.42
        score = 0.22 + bonus
        gap = "缺少实测导热系数，当前仅用填料/体系标签做代理判断"
        return min(score, 0.7), "含增强相或工程体系标签，可作为散热路径候选" if bonus else "未见专门导热增强信息", gap

    if requirement == "strength":
        val = _numeric(props, "flexural_strength_mpa")
        score = _norm(val, 45.0, 90.0)
        gap = None if val is not None else "缺少强度实测值"
        return score, f"弯曲强度代理值 {val:g} MPa" if val is not None else "按工程体系默认强度潜力评估", gap

    if requirement == "stiffness":
        val = _numeric(props, "flexural_modulus_mpa")
        score = _norm(val, 1500.0, 3200.0) + 0.15 * _tag_bonus(candidate, ["carbon_fiber", "glass_fiber", "stiff"])
        score = min(1.0, score)
        gap = None if val is not None else "缺少刚度/模量实测值"
        return score, f"弯曲模量代理值 {val:g} MPa" if val is not None else "按增强相和工程塑料体系评估刚度潜力", gap

    if requirement == "layer_adhesion":
        val = _numeric(props, "z_impact_kj_m2")
        score = _norm(val, 4.0, 15.0)
        gap = None if val is not None and score >= 0.55 else "Z 向层间性能需要打印参数和试样验证"
        return score, f"Z向冲击代理值 {val:g} kJ/m2" if val is not None else "缺少Z向层间代理指标", gap

    if requirement == "heat_resistance":
        val = _numeric(props, "hdt_045_mpa_c")
        score = _norm(val, 55.0, 120.0) + 0.12 * _tag_bonus(candidate, ["heat_resistant", "engineering"])
        score = min(1.0, score)
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

    return 0.5, "未映射需求，按中性分处理", None


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
        "ctE_um_m_c": ["ctE_um_m_c", "CTE", "线膨胀系数", "热膨胀系数"],
        "tensile_strength_mpa": ["tensile_strength_mpa", "拉伸强度", "tensile_strength"],
        "tensile_modulus_mpa": ["tensile_modulus_mpa", "拉伸模量", "tensile_modulus"],
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
        by_name = {str(c.get("name", "")).lower(): c for c in candidates}
        for c in upstream_candidates:
            by_name[str(c.get("name", "")).lower()] = c
        candidates = list(by_name.values())
    return candidates


def rank_filaments(taskid: str, text: str, payload: Optional[JsonDict], payload_path: Optional[str] = None) -> FilamentSelectionResult:
    requirements = parse_requirements(text, payload)
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
            score, reason, gap = _score_requirement(candidate, req)
            weight = weights.get(req, 1.0)
            total += score * weight
            total_w += weight
            detail[req] = round(float(score), 3)
            if reason:
                reasons.append(f"{req}: {reason}")
            if gap:
                gaps.append(gap)
        final = total / total_w if total_w else 0.0
        tags = set(candidate.get("tags") or [])
        props = candidate.get("properties") or {}
        hdt = _numeric(props, "hdt_045_mpa_c")
        if "thermal" in requirements and not tags.intersection({"carbon_fiber", "glass_fiber", "thermal_path_candidate"}):
            final *= 0.88
        if "heat_resistance" in requirements and hdt is not None and hdt < 70:
            final *= 0.82
        ranked.append(FilamentScore(candidate=candidate, score=final, requirement_scores=detail, reasons=reasons, gaps=list(dict.fromkeys(gaps))))

    ranked.sort(key=lambda item: (-item.score, str(item.candidate.get("name") or "")))
    notes = [
        "当前版本为第一阶段已有耗材推荐框架，使用上游 candidate_materials 时会优先覆盖内置 starter profiles。",
        "若缺少导热系数、CTE、拉伸强度、疲劳等字段，会用材料体系标签和可得代理指标保守评分。",
    ]
    return FilamentSelectionResult(
        taskid=taskid,
        scenario=scenario,
        requirements=requirements,
        ranked=ranked,
        source_payload_path=payload_path,
        notes=notes,
    )


def write_selection_manifest(repo_root: str, result: FilamentSelectionResult) -> str:
    out_dir = Path(repo_root) / "src" / "MNS_CaseHub" / "cases" / "material_discovery_demo" / "results" / "filament_selection" / str(result.taskid)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "filament_selection_manifest.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def build_markdown_report(result: FilamentSelectionResult, top_n: int = 5) -> str:
    ranked = result.ranked[:top_n]
    top = ranked[0] if ranked else None
    scenario = result.scenario or {}
    req_names = {
        "thermal": "导热/热管理",
        "strength": "强度",
        "stiffness": "刚度",
        "layer_adhesion": "层间结合",
        "heat_resistance": "耐热/热循环",
        "dimensional_stability": "尺寸稳定",
        "printability": "可打印性",
    }
    lines: List[str] = []
    lines.append("### 3D打印耗材工程筛选")
    lines.append("")
    if scenario:
        lines.append(f"应用场景：{scenario.get('scenario_name') or scenario.get('application') or '未指定'}")
        if scenario.get("application"):
            lines.append(f"使用位置：{scenario.get('application')}")
        lines.append("")
    lines.append("需求映射：" + "、".join(req_names.get(r, r) for r in result.requirements))
    lines.append("")
    lines.append("| 排名 | 候选耗材 | 综合分 | 主要优势 | 主要缺口 |")
    lines.append("|---|---|---:|---|---|")
    for idx, item in enumerate(ranked, start=1):
        c = item.candidate
        advantages = "；".join((c.get("advantages") or [])[:2]) or "待补充"
        gaps = "；".join(item.gaps[:2]) or "暂无明显缺口"
        lines.append(f"| {idx} | {c.get('display_name') or c.get('name')} | {item.score:.2f} | {advantages} | {gaps} |")
    lines.append("")

    if top:
        c = top.candidate
        lines.append(f"#### 推荐现有耗材：{c.get('display_name') or c.get('name')}")
        lines.append("")
        lines.append("推荐理由：")
        for reason in top.reasons[:5]:
            lines.append(f"- {reason}")
        if top.gaps:
            lines.append("")
            lines.append("需要补充确认：")
            for gap in top.gaps[:4]:
                lines.append(f"- {gap}")
        process = c.get("process") or {}
        if process:
            lines.append("")
            lines.append("建议打印与前处理：")
            for key, label in [("drying", "干燥"), ("nozzle", "喷嘴"), ("bed", "热床"), ("nozzle_temp", "喷嘴温度"), ("speed", "速度"), ("enclosure", "封箱")]:
                val = process.get(key)
                if val:
                    lines.append(f"- {label}: {val}")

    lines.append("")
    lines.append("#### 后续改性方向")
    lines.append("")
    lines.append("- 若目标是高散热机器人关节，优先补充候选耗材的导热系数 XY/Z、CTE、拉伸/弯曲、层间剪切和热循环后强度保持率。")
    lines.append("- 若现有耗材无法同时满足导热和层间强度，可考虑 PA/PC/PEEK 基体叠加短切碳纤维与 BN/AlN 陶瓷填料，并通过打印路径和退火工艺控制取向与残余应力。")
    lines.append("- 第二阶段可以把该推荐结果作为基准，继续做填料比例、取向、打印参数和结构散热路径的优化。")
    lines.append("")
    if result.source_payload_path:
        lines.append(f"来源 JSON：`{result.source_payload_path}`")
    return "\n".join(lines) + "\n"


def build_selection_from_latest(repo_root: str, taskid: str, text: str) -> Tuple[FilamentSelectionResult, str]:
    payload, path = latest_in_ls_payload(repo_root)
    result = rank_filaments(taskid=taskid, text=text, payload=payload, payload_path=path)
    manifest = write_selection_manifest(repo_root, result)
    return result, manifest
