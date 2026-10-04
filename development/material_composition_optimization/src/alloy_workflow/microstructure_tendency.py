"""Auditable, rule-based explanations for alloy screening results.

The organization panel intentionally interprets existing phase-classifier
outputs.  It is not a microstructure simulator and its drawing parameters are
not physical predictions.
"""
from __future__ import annotations

from typing import Any


def build_microstructure_tendency(candidate: dict[str, Any]) -> dict[str, Any]:
    """Map saved phase probabilities to a customer-readable tendency label."""
    phase = candidate.get("phase_probabilities") or {}
    ss = float(phase.get("SS", 0.0))
    im = float(phase.get("IM", 0.0))
    mixed = float(phase.get("SS+IM", 0.0))
    domain = str((candidate.get("applicability_domain") or {}).get("level", "-"))

    # Evaluate the higher-risk branch first so the visual never hides IM risk.
    if ss < 0.60 or im >= 0.15:
        level, title, mixed_risk, im_risk, marker_count = (
            "C", "混相/金属间化合物风险较高", "较高", "较高", 14,
        )
        explanation = "相分类结果显示固溶体主导性不足，建议优先确认主相组成及是否存在第二相。"
    elif ss >= 0.85 and im <= 0.05:
        level, title, mixed_risk, im_risk, marker_count = (
            "A", "单相固溶体主导", "低", "低", 2,
        )
        explanation = "当前相分类更偏向固溶体基体；图中的少量标记仅表示仍保留的模型不确定性。"
    else:
        level, title, mixed_risk, im_risk, marker_count = (
            "B", "固溶体主导，存在混相风险", "中等", "低至中等", 7,
        )
        explanation = "当前相分类以固溶体为主，同时存在混相或第二相风险，建议在验证中关注相组成。"

    exploratory = domain != "inside"
    confidence = "探索性" if exploratory else "模型初筛"
    validation = ["XRD 确认主相与可能的第二相", "SEM/EDS 检查成分均匀性与第二相"]
    if exploratory:
        validation.insert(0, "候选位于训练数据边界或范围外，优先复核相组成")
    return {
        "source": "现有相分类模型输出 phase_probabilities 的规则映射",
        "phase_probabilities": {"SS": ss, "IM": im, "SS+IM": mixed},
        "level": level,
        "title": title,
        "mixed_phase_risk": mixed_risk,
        "intermetallic_risk": im_risk,
        "applicability_domain": {"inside": "训练数据范围内", "boundary": "训练数据边界附近", "outside": "训练数据范围外"}.get(domain, domain),
        "confidence": confidence,
        "explanation": explanation,
        # Decorative marker density only; it has no particle-count, size or
        # location interpretation.
        "visual_marker_count": marker_count,
        "validation_priorities": validation,
    }


_ATOMIC_WEIGHT = {
    "Al": 26.982, "Co": 58.933, "Cr": 51.996, "Fe": 55.845,
    "Hf": 178.49, "Mn": 54.938, "Mo": 95.95, "Nb": 92.906,
    "Ni": 58.693, "Ta": 180.948, "Ti": 47.867, "V": 50.942,
    "W": 183.84, "Zr": 91.224,
}
_DENSITY_G_CM3 = {
    "Al": 2.70, "Co": 8.90, "Cr": 7.19, "Fe": 7.87,
    "Hf": 13.31, "Mn": 7.21, "Mo": 10.28, "Nb": 8.57,
    "Ni": 8.91, "Ta": 16.65, "Ti": 4.51, "V": 6.11,
    "W": 19.25, "Zr": 6.52,
}


def build_engineering_estimates(candidate: dict[str, Any], search_space: dict[str, Any]) -> list[dict[str, str]]:
    """Return D-level screening estimates; none participates in ranking."""
    composition = candidate.get("composition_at_pct") or {}
    strength = candidate.get("yield_strength_MPa") or {}
    rows: list[dict[str, str]] = []
    if composition and all(element in _ATOMIC_WEIGHT and element in _DENSITY_G_CM3 for element in composition):
        fractions = {element: float(amount) / 100.0 for element, amount in composition.items()}
        molar_mass = sum(fractions[element] * _ATOMIC_WEIGHT[element] for element in fractions)
        molar_volume = sum(fractions[element] * _ATOMIC_WEIGHT[element] / _DENSITY_G_CM3[element] for element in fractions)
        density = molar_mass / molar_volume
        rows.append({
            "property": "密度", "estimate": f"{density * .92:.2f}–{density * 1.08:.2f} g/cm³",
            "basis": "D级工程估算：元素原子分数—摩尔体积理想混合法；未计入相组成、孔隙与热处理影响。",
            "validation": "阿基米德法或几何法密度；确认铸态/热处理状态。",
        })
    mean = float(strength.get("mean", 0))
    if mean > 0:
        rows.append({
            "property": "抗拉强度", "estimate": f"{mean * 1.05:.0f}–{mean * 1.30:.0f} MPa",
            "basis": "D级工程估算：以本轮预测屈服强度按金属拉伸强屈比 1.05–1.30 换算；适用于相同工艺与评价温度的初筛比较。",
            "validation": "按目标温度开展拉伸试验，同时记录延伸率与断口形貌。",
        })
    al_cr = sum(float(composition.get(element, 0.0)) for element in ("Al", "Cr"))
    oxide_tendency = "较强" if al_cr >= 20 else "中等" if al_cr >= 10 else "有限"
    rows.append({
        "property": "高温抗氧化倾向（含氧环境）", "estimate": oxide_tendency,
        "basis": f"D级工程筛查：Al+Cr 为 {al_cr:.1f} at.% 的保护氧化物形成倾向映射；不覆盖盐雾、熔盐或具体腐蚀介质。",
        "validation": f"在 {search_space.get('test_temperature_C', '目标')}°C、目标气氛与保温时间下进行氧化增重/截面分析。",
    })
    rows.append({
        "property": "蠕变、高温持久与热疲劳", "estimate": "按工况确定验证优先级",
        "basis": "D级工程判断：现有单点强度、硬度和成分不足以可靠换算寿命；不生成缺乏载荷—时间数据支撑的寿命数值。",
        "validation": "按实际温度、应力、保温时间和循环载荷开展蠕变/持久及热疲劳试验。",
    })
    return rows
