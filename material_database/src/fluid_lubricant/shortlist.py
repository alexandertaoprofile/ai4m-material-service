"""Traceable project shortlist derived from the published screening report.

The rules below are deliberately explicit rather than a hidden score: each A
rule maps to a named, reported room-temperature formulation in report section
5.1.  B candidates are the report's lubricant-base-oil-near formulations from
SRC014; they are shown separately because their room-temperature electrical
evidence is absent.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.fluid_lubricant.query import run_query


_A_RULES = (
    ("A1", ".gamma.-butyrolactone", "1-butyl-1-methylpyrrolidinium bis", "mole_fraction", 0.1000, "x(IL)=0.1000"),
    ("A1", ".gamma.-butyrolactone", "1-butyl-3-methylimidazolium bis", "mole_fraction", 0.1005, "x(IL)=0.1005"),
    ("A1", "1-methyl-1-propylpyrrolidinium bis", ".gamma.-butyrolactone", "mass_fraction", 0.2500, "w(IL)=0.25"),
    ("A1", "propylene carbonate", "1-butyl-1-methylpyrrolidinium bis", "mole_fraction", 0.0500, "x(IL)=0.0500"),
    ("A2", ".gamma.-butyrolactone", "1-butyl-1-methylpyrrolidinium dicyanamide", "mole_fraction", 0.1002, "x(IL)=0.1002"),
    ("A2", "1-(2-methoxyethyl)-3-methylimidazolium thiocyanate", "propylene carbonate", "mole_fraction", 0.1022, "x(IL)=0.1022"),
)

_B_RECORDS = (
    "SRC014-V-009-040C", "SRC014-V-010-040C", "SRC014-V-008-040C", "SRC014-V-006-040C",
)

_COMPONENT_CN = {
    "acetonitrile": "乙腈",
    "1-butyl-2,3-dimethylimidazolium thiocyanate": "1-丁基-2,3-二甲基咪唑鎓硫氰酸盐",
    "tetraethylammonium nitrate": "四乙基铵硝酸盐",
    "ethylammonium nitrate": "乙基铵硝酸盐",
    ".gamma.-butyrolactone": "γ-丁内酯",
    "1-(2-hydroxyethyl)-3-methylimidazolium nonafluoro-1-butanesulfonate": "1-(2-羟乙基)-3-甲基咪唑鎓九氟丁烷磺酸盐",
    "water": "水",
    "1-ethyl-3-methylimidazolium acetate": "1-乙基-3-甲基咪唑鎓乙酸盐",
    "1-methylimidazole": "1-甲基咪唑",
    "ethyl acetate": "乙酸乙酯",
    "1-butyl-3-methylimidazolium bis(trifluoromethylsulfonyl)imide": "1-丁基-3-甲基咪唑鎓双(三氟甲磺酰)亚胺",
    "1-butyl-3-methylimidazolium acetate": "1-丁基-3-甲基咪唑鎓乙酸盐",
    "trimethylbenzylammonium chloride": "苄基三甲基氯化铵",
    "diethylene glycol": "二甘醇",
}


def _application_class(candidate: dict[str, Any]) -> tuple[str, str]:
    """Classify an evidence direction without promoting it to a lubricant.

    The numerical query deliberately knows only transport properties.  This
    conservative label is therefore a communication guardrail, not an
    engineering qualification: a formulation becomes a lubricant candidate
    only when its recorded components include a recognisable base-oil family.
    """
    components = " ".join(
        str(candidate["composition"].get(key) or "").casefold()
        for key in ("component_1", "component_2", "component_3")
    )
    if "water" in components:
        return "工程不适用候选", "含水体系可作离子传导参考，但未证明油膜、高温寿命或防腐能力。"
    if any(name in components for name in ("acetonitrile", "ethyl acetate", "acetone", "methanol", "ethanol")):
        return "工程不适用候选", "含低沸点/高挥发小分子；数值可匹配，但不建议作为高温轴承润滑介质。"
    if any(name in components for name in (
        "pentaerythritol", "trimethylolpropane", "neopentyl glycol", "polyol ester",
        "polyalkylene glycol", "polyalphaolefin", "pao",
    )):
        return "导电润滑油研发候选", "记录中出现润滑基础油家族；仍需验证抗磨、氧化、相容性及长期均相稳定。"
    return "导电功能液体候选（参考）", "满足本轮电学/黏度窗口，但未确认其可作为润滑基础油或配方主体。"


def _chinese_system(component_1: str | None, component_2: str | None) -> str:
    """Plain-language system description; original English remains in evidence."""
    first = (component_1 or "").lower()
    second = (component_2 or "").lower()
    components = " ".join((first, second))
    solvent = ""
    if "gamma.-butyrolactone" in components:
        solvent = "γ-丁内酯（极性共溶剂）"
    elif "propylene carbonate" in components:
        solvent = "碳酸丙烯酯（极性共溶剂）"
    if "pentaerythritol tetraoleate" in components:
        if "1-hexyl-3-methylimidazolium oleate" in components:
            additive = "1-己基-3-甲基咪唑鎓油酸盐"
        elif "dioctylmethylpentylammonium oleate" in components:
            additive = "二辛基甲基戊基铵油酸盐"
        elif "hexyldimethylcyclohexylammonium oleate" in components:
            additive = "己基二甲基环己基铵油酸盐"
        elif "tetrabutylammonium oleate" in components:
            additive = "四丁基铵油酸盐"
        else:
            additive = "油酸盐型离子添加剂"
        return f"季戊四醇四油酸酯（酯类润滑基础油） + {additive}（1.5 wt% 添加剂）"
    if "pyrrolidinium" in components and "sodium (fluorosulfonyl)" in components:
        return "吡咯烷鎓类离子液体 + 氟磺酰亚胺钠盐"
    if "dicyanamide" in components:
        ionic = "双氰胺根型离子液体"
    elif "thiocyanate" in components:
        ionic = "硫氰酸根型咪唑鎓离子液体"
    elif "1-butyl-1-methylpyrrolidinium bis" in components:
        ionic = "1-丁基-1-甲基吡咯烷鎓双(三氟甲磺酰)亚胺离子液体"
    elif "1-methyl-1-propylpyrrolidinium bis" in components:
        ionic = "1-甲基-1-丙基吡咯烷鎓双(三氟甲磺酰)亚胺离子液体"
    elif "1-butyl-3-methylimidazolium bis" in components:
        ionic = "1-丁基-3-甲基咪唑鎓双(三氟甲磺酰)亚胺离子液体"
    else:
        ionic = "离子液体"
    return f"{solvent or '极性有机溶剂'} + {ionic}"


def _components_match(candidate: dict[str, Any], first: str, second: str) -> bool:
    values = [str(candidate["composition"].get(key) or "").lower() for key in ("component_1", "component_2", "component_3")]
    return any(first in value for value in values) and any(second in value for value in values)


def _fraction_match(candidate: dict[str, Any], target: float) -> bool:
    return any(
        isinstance(candidate["composition"].get(key), (float, int))
        and abs(float(candidate["composition"][key]) - target) <= 0.002
        for key in ("component_1_fraction", "component_2_fraction", "component_3_fraction")
    )


def _a_shortlist(database, screening: dict[str, Any]) -> list[dict[str, Any]]:
    request = dict(screening["request"])
    request["limit"] = 2000
    all_matches = run_query(database, request)["candidates"]
    shortlisted: list[dict[str, Any]] = []
    for grade, first, second, basis, fraction, fraction_text in _A_RULES:
        choices = [
            item for item in all_matches
            if item["conditions"]["temperature_k"] == 298.15
            and item["composition"]["composition_basis"] == basis
            and _components_match(item, first, second)
            and _fraction_match(item, fraction)
        ]
        if not choices:
            continue
        item = choices[0]
        item["grade"] = grade
        item["reported_fraction"] = fraction_text
        item["composition_display"] = _chinese_system(
            item["composition"].get("component_1"), item["composition"].get("component_2"),
        )
        item["composition_original"] = " + ".join(
            str(item["composition"].get(key) or "") for key in ("component_1", "component_2")
        )
        item["shortlist_status"] = "可进入短名单；投料前须回查原始组成字段"
        item["screening_reason"] = "室温同条件电导率和动态黏度满足本轮数值条件。"
        item["evidence"]["report_reference"] = "公开数据集处理与候选筛选报告，第 5.1 节"
        shortlisted.append(item)
    return shortlisted


def _b_shortlist(database) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT record_id, component_1, component_2, composition_basis,
                      component_1_fraction, component_2_fraction, temperature_k,
                      value_normalized, normalized_unit, source_id, source_reference
                 FROM property_evidence
                WHERE record_id IN ({}) AND property_name='kinematic_viscosity'
             GROUP BY record_id""".format(",".join("?" for _ in _B_RECORDS)),
            _B_RECORDS,
        ).fetchall()
    finally:
        connection.close()
    by_id = {row["record_id"]: dict(row) for row in rows}
    candidates: list[dict[str, Any]] = []
    for record_id in _B_RECORDS:
        row = by_id.get(record_id)
        if not row:
            continue
        candidates.append({
            "grade": "B",
            "composition": {"component_1": row["component_1"], "component_2": row["component_2"], "composition_basis": row["composition_basis"], "component_1_fraction": row["component_1_fraction"], "component_2_fraction": row["component_2_fraction"]},
            "reported_fraction": "1.5 wt% 添加剂",
            "composition_display": _chinese_system(row["component_1"], row["component_2"]),
            "composition_original": f"{row['component_1']} + {row['component_2']}",
            "conditions": {"temperature_k": row["temperature_k"]},
            "properties": {"kinematic_viscosity_mm2_s": row["value_normalized"]},
            "evidence": {"source_id": row["source_id"], "record_ids": record_id, "source_reference": row["source_reference"], "report_reference": "公开数据集处理与候选筛选报告，第 5.2 节"},
            "shortlist_status": "与聚醇酯润滑基础液接近；需补测室温电导率与动态/旋转黏度",
            "screening_reason": "公开数据仅提供 40 °C 运动黏度，不能替代本轮室温动态黏度阈值。",
        })
    return candidates


def _matched_evidence(screening: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduplicate the actual query result by formulation and exact condition.

    These are *not* promoted to validated lubricants.  They are the evidence
    directions that numerically satisfy the user's current request and must be
    shown before any report-curated B-class follow-up clues.
    """
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in screening["candidates"]:
        composition = item["composition"]
        condition = item["conditions"]
        key = (
            item["evidence"]["source_id"], *(composition.get(name) for name in (
                "component_1", "component_2", "component_3", "composition_basis",
                "component_1_fraction", "component_2_fraction", "component_3_fraction",
            )), condition.get("temperature_k"), condition.get("pressure_pa"),
        )
        groups.setdefault(key, item)
    result: list[dict[str, Any]] = []
    for number, item in enumerate(groups.values(), start=1):
        composition = item["composition"]
        item["evidence_id"] = f"E{number:02d}"
        item["composition_original"] = " + ".join(
            str(composition.get(key) or "") for key in ("component_1", "component_2", "component_3")
            if composition.get(key)
        )
        components = [str(composition.get(key) or "") for key in ("component_1", "component_2", "component_3") if composition.get(key)]
        translations = [_COMPONENT_CN.get(component.lower()) for component in components]
        item["composition_chinese"] = " + ".join(translations) if components and all(translations) else ""
        item["composition_display"] = _chinese_system(
            str(composition.get("component_1") or ""), str(composition.get("component_2") or ""),
        )
        if item["composition_chinese"]:
            item["composition_display"] = item["composition_chinese"]
        candidate_class, engineering_note = _application_class(item)
        item["candidate_class"] = candidate_class
        item["engineering_note"] = engineering_note
        item["evidence_tier"] = (
            "B 类：数值匹配，建议先做来源与配方复核"
            if item["evidence"].get("manual_review_required") or item["evidence"].get("composition_status") != "complete"
            else "A 类：数据较完整，可优先核验"
        )
        item["shortlist_status"] = "数值匹配；不构成润滑适用性结论"
        result.append(item)
    return result


def _primary_reference(candidates: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any] | None:
    """Choose one research reference, never a product recommendation.

    The ordering is deliberately visible in the presentation: avoid water and
    volatile solvent systems first, then prefer an all-ionic direction over a
    neutral co-solvent system, then minimise distance from the requested
    transport-property window centre.
    """
    eligible = [item for item in candidates if item.get("candidate_class") == "导电功能液体候选（参考）"]
    if not eligible:
        return None

    constraints = request.get("property_constraints", [])
    def midpoint(name: str, fallback: float) -> float:
        lower = next((float(item["value"]) for item in constraints if item["name"] == name and item["operator"] in {">=", ">"}), None)
        upper = next((float(item["value"]) for item in constraints if item["name"] == name and item["operator"] in {"<=", "<"}), None)
        return (lower + upper) / 2 if lower is not None and upper is not None else fallback

    target_resistivity = midpoint("resistivity", 5.5)
    target_viscosity = midpoint("dynamic_viscosity", 140.0)
    def rank(item: dict[str, Any]) -> tuple[float, float, float]:
        components = " ".join(str(item["composition"].get(key) or "").casefold() for key in ("component_1", "component_2", "component_3"))
        ionic_markers = ("imidazolium", "pyrrolidinium", "ammonium", "phosphonium", "sodium")
        ionic_count = sum(marker in components for marker in ionic_markers)
        neutral_solvent_markers = ("butyrolactone", "glycol", "ethanediol", "aminoethan", "diol")
        has_neutral_solvent = any(marker in components for marker in neutral_solvent_markers)
        properties = item["properties"]
        resistivity = float(properties["resistivity_ohm_m"]["max"])
        viscosity = float(properties["dynamic_viscosity_mpa_s"]["max"])
        return (
            0 if ionic_count >= 2 and not has_neutral_solvent else 1,
            abs(resistivity - target_resistivity) + abs(viscosity - target_viscosity) / 10,
            resistivity,
        )
    return min(eligible, key=rank)


def build_report_shortlist(database, screening: dict[str, Any]) -> dict[str, Any]:
    """Return A/B cards without promoting them to validated final formulations."""
    a_candidates = _a_shortlist(database, screening)
    b_candidates = _b_shortlist(database)
    matched_evidence = _matched_evidence(screening)
    return {
        "matched_evidence": matched_evidence,
        "primary_research_reference": _primary_reference(matched_evidence, screening["request"]),
        "a_candidates": a_candidates,
        "b_candidates": b_candidates,
        "data_gaps": [
            {"item": "135 °C 长期老化后电导率与黏度", "status": "缺失", "meaning": "不能由室温传输性质或热分解温度替代"},
            {"item": "均相、分层、沉淀与材料相容性", "status": "缺失或不完整", "meaning": "不能认定无颗粒、均相或无腐蚀"},
            {"item": "规定转子/转速/剪切条件的室温黏度", "status": "缺失", "meaning": "当前动态黏度仅作为 proxy"},
        ],
        "rule_reference": "公开数据集处理与候选筛选报告，第 4–5 节；候选仅用于初筛和原始来源回查。",
    }
