"""Customer-readable chart data from retained results, without new screening."""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import re


_ELEMENT_WORDS = """
H 氢 He 氦 Li 锂 Be 铍 B 硼 C 碳 N 氮 O 氧 F 氟 Ne 氖
Na 钠 Mg 镁 Al 铝 Si 硅 P 磷 S 硫 Cl 氯 Ar 氩 K 钾 Ca 钙
Sc 钪 Ti 钛 V 钒 Cr 铬 Mn 锰 Fe 铁 Co 钴 Ni 镍 Cu 铜 Zn 锌
Ga 镓 Ge 锗 As 砷 Se 硒 Br 溴 Kr 氪 Rb 铷 Sr 锶 Y 钇 Zr 锆
Nb 铌 Mo 钼 Tc 锝 Ru 钌 Rh 铑 Pd 钯 Ag 银 Cd 镉 In 铟 Sn 锡
Sb 锑 Te 碲 I 碘 Xe 氙 Cs 铯 Ba 钡 La 镧 Ce 铈 Pr 镨 Nd 钕
Pm 钷 Sm 钐 Eu 铕 Gd 钆 Tb 铽 Dy 镝 Ho 钬 Er 铒 Tm 铥 Yb 镱
Lu 镥 Hf 铪 Ta 钽 W 钨 Re 铼 Os 锇 Ir 铱 Pt 铂 Au 金 Hg 汞
Tl 铊 Pb 铅 Bi 铋 Po 钋 At 砹 Rn 氡 Fr 钫 Ra 镭 Ac 锕 Th 钍
Pa 镤 U 铀 Np 镎 Pu 钚 Am 镅 Cm 锔 Bk 锫 Cf 锎 Es 锿 Fm 镄
Md 钔 No 锘 Lr 铹
""".split()
ELEMENT_NAMES = dict(zip(_ELEMENT_WORDS[::2], _ELEMENT_WORDS[1::2]))

REASONS = {
    "composition": ("元素不符合要求", "超出允许元素或未满足必含元素", "failed"),
    "geometry": ("原子排布未通过", "原子间距离等结构检查未通过", "failed"),
    "hull": ("稳定性估计超过上限", "未满足本次稳定性筛选条件", "failed"),
    "conditions": ("其他设定条件未通过", "性质或对称性等条件未全部满足", "failed"),
    "relaxation": ("结构优化尚未完成", "尚未达到计算的收敛要求", "incomplete"),
    "hull_missing": ("稳定性结果待补充", "结构优化完成，稳定性暂未有结果", "incomplete"),
    "other_missing": ("其他结果待补充", "现有计算证据尚不足以判定", "incomplete"),
    "duplicate": ("相似结构已合并", "与已有候选相似，不重复计数", "duplicate"),
    "review": ("稳定性需要复核", "高于参考线，仍保留后续评价", "review"),
}


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def crystal_identity(candidate, validation):
    formula = candidate.get("formula_pretty") or validation.get("formula_pretty") or "组成待解析"
    counts = (candidate.get("metadata") or {}).get("element_counts") or (validation.get("metadata") or {}).get("element_counts") or {}
    symbols = sorted(counts) or sorted(set(re.findall(r"[A-Z][a-z]?", formula)))
    name = "-".join(ELEMENT_NAMES.get(symbol, symbol) for symbol in symbols) + "晶体" if 0 < len(symbols) <= 4 else f"{len(symbols)}元素晶体" if symbols else "候选晶体"
    return formula, name


def scheme_names(identifiers, taskid=""):
    """Stable task-scoped labels shared by report and chart; raw IDs remain in records."""
    prefix = hashlib.sha256(str(taskid).encode("utf-8")).hexdigest()[:10].upper()
    return {identifier: f"JC-{prefix}-{index + 1:02d}" for index, identifier in enumerate(dict.fromkeys(identifiers))}


def build_comparison_data(manifest: dict) -> dict:
    """Keep failure, missing evidence and duplicate results mutually exclusive.

    This is a view of saved screening states, not a second pass/fail algorithm:
    a low hull value alone can never promote a failed or incomplete candidate.
    """
    generation = manifest.get("generation") or {}
    ranked = manifest.get("ranked_candidates") or []
    candidates = generation.get("candidates") or [item.get("candidate") or {} for item in ranked]
    validations = {row["candidate_id"]: row for row in manifest.get("validations") or []}
    for item in ranked:
        validation = item.get("validation") or {}
        if validation.get("candidate_id"):
            validations.setdefault(validation["candidate_id"], validation)
    rank_order = {item.get("candidate", {}).get("candidate_id"): index for index, item in enumerate(ranked)}
    candidates = sorted(candidates, key=lambda row: rank_order.get(row.get("candidate_id"), len(ranked)))
    threshold = finite_number(((manifest.get("constraints") or {}).get("target_properties") or {}).get("energy_above_hull"))
    rows, reason_counts, outcome_counts = [], Counter(), Counter()
    for candidate in candidates:
        identifier = candidate.get("candidate_id", "待编号")
        validation = validations.get(identifier, {})
        metadata = validation.get("metadata") or {}
        screening = metadata.get("screening") or {}
        composition = metadata.get("composition_ok")
        geometry = validation.get("is_valid")
        converged = (metadata.get("mattersim") or {}).get("relaxation_converged") is True
        hull = finite_number(validation.get("energy_above_hull"))
        duplicate = metadata.get("duplicate_of")
        if composition is False:
            reason = "composition"
        elif geometry is False:
            reason = "geometry"
        elif screening.get("state") == "failed":
            reason = "hull" if "凸包能超过上限" in screening.get("failures", []) else "conditions"
        elif composition is not True or geometry is not True:
            reason = "other_missing"
        elif not converged:
            reason = "relaxation"
        elif hull is None:
            reason = "hull_missing"
        elif screening.get("state") != "passed":
            reason = "other_missing"
        elif duplicate:
            reason = "duplicate"
        elif screening.get("stability_review"):
            reason = "review"
        else:
            reason = "passed"
        state = "passed" if reason == "passed" else REASONS[reason][2]
        outcome_counts[state] += 1
        if reason not in {"passed", "review"}:
            reason_counts[reason] += 1
        formula, name = crystal_identity(candidate, validation)
        rows.append({"candidate_id": identifier, "formula": formula, "name": name,
            "composition_ok": composition is True, "geometry_ok": geometry is True,
            "converged": converged, "hull": hull, "state": state, "reason": reason,
            "duplicate_of": duplicate})
    names = scheme_names((row["candidate_id"] for row in rows), manifest.get("taskid", ""))
    for row in rows:
        row["display_name"] = names[row["candidate_id"]]
    composition_rows = [row for row in rows if row["composition_ok"]]
    geometry_rows = [row for row in composition_rows if row["geometry_ok"]]
    converged_rows = [row for row in geometry_rows if row["converged"]]
    hull_rows = [row for row in converged_rows if row["hull"] is not None]
    passed = outcome_counts["passed"] + outcome_counts["review"]
    mode = (generation.get("metadata") or {}).get("mode") or (manifest.get("constraints") or {}).get("generation_mode")
    constraints = manifest.get("constraints") or {}
    scope = "固定化学式探索" if mode == "csp" else "指定元素探索" if constraints.get("allowed_elements") else "开放元素探索"
    starting_point = constraints.get("target_formula") if mode == "csp" else " / ".join(constraints.get("allowed_elements") or []) or "未限定具体元素"
    if mode != "csp":
        required = constraints.get("required_elements") or []
        if required:
            starting_point += "；必含 " + " / ".join(required)
        minimum = constraints.get("min_distinct_elements")
        if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0:
            starting_point += f"；每个候选至少 {minimum} 种元素"
    scope += " · " + (starting_point or "化学式见报告")
    return {
        "title": f"生成 {len(rows)} 个候选结构，{passed} 个可继续比较（{outcome_counts['review']} 个稳定性需复核）" if passed else f"生成 {len(rows)} 个候选结构，暂未得到可继续比较的结构",
        "scope": scope, "threshold": threshold,
        "stages": [
            {"label": "生成结构", "count": len(rows), "caption": "候选晶体结构"},
            {"label": "元素符合要求", "count": len(composition_rows), "caption": "核对允许和必含元素"},
            {"label": "原子排布通过", "count": len(geometry_rows), "caption": "检查原子间距离"},
            {"label": "结构优化完成", "count": len(converged_rows), "caption": "达到计算收敛要求"},
            {"label": "稳定性有结果", "count": len(hull_rows), "caption": "可与竞争相比较"},
            {"label": "可继续比较", "count": passed, "caption": "超参考线需复核\n相似结构只保留一个" if passed else "暂未获得可比较结构\n复核原因或补充计算"},
        ],
        "outcomes": {key: outcome_counts[key] for key in ("passed", "review", "failed", "incomplete", "duplicate")},
        "reasons": [{"code": key, "label": REASONS[key][0], "detail": REASONS[key][1], "state": REASONS[key][2], "count": reason_counts[key]} for key in REASONS if reason_counts[key]],
        "rows": rows, "comparison_rows": converged_rows,
    }
