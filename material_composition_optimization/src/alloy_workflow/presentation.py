"""Dedicated semantic streaming renderer for alloy workflow results.

It deliberately renders computed values deterministically: an LLM may later
polish prose, but must never alter model values or the machine handoff table.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from src.alloy_workflow.microstructure_tendency import build_engineering_estimates


def _composition_text(composition: dict[str, Any]) -> str:
    return " ".join(f"{element}{float(value):.1f}" for element, value in composition.items())


def planned_alloy_method_block(payload: dict[str, Any]) -> str:
    """Define the configured HEA/MPEA calculation chain before it is run."""
    from src.alloy_workflow.contracts import requirement_plan

    effective, plan = requirement_plan(payload)
    elements = "、".join(effective.get("allowed_elements") or []) or "由任务约束确定"
    bounds = effective.get("element_bounds_at_pct") or {}
    bounds_text = "；".join(
        f"{element} {value[0]}–{value[1]} at.%"
        for element, value in bounds.items()
        if isinstance(value, (list, tuple)) and len(value) == 2
    ) or "由任务约束确定"
    objectives = effective.get("objectives") or {}
    objective_labels = {
        "yield_strength_MPa": "屈服强度",
        "hardness_HV": "硬度",
        "phase_risk": "相风险",
    }
    objective_text = "、".join(
        objective_labels.get(str(name), str(name)) for name in objectives
    ) or "强度、硬度、相风险与数据适用域"
    return "\n".join([
        "## 1. 问题描述",
        f"在 {elements} 组成的合金体系中，搜索满足成分边界、工艺和温度条件的候选配比，并对 {objective_text} 进行初步量化评估。",
        "",
        "## 2. 变量与约束",
        "| 符号/变量 | 定义 | 本轮设定 |",
        "|---|---|---|",
        f"| $x_i$ | 第 i 种元素的原子分数 | 元素：{elements} |",
        "| $\\mathbf{x}$ | 成分向量 | $x_i \\geq 0$，$\\sum_i x_i = 1$ |",
        f"| 成分边界 | 每种元素允许的原子百分比 | {bounds_text} |",
        f"| $p$、$T$ | 制备工艺与评价温度 | {effective.get('processing_method') or '未指定'}；{effective.get('test_temperature_C', 25)} °C |",
        "",
        "## 3. 计划计算链与模型定义",
        "以下为本任务已配置、将按顺序执行的方法定义，不包含任何预测结果。",
        "",
        "### 3.1 成分归一化随机采样（Dirichlet）",
        "先在所有元素原子分数之和为 1 的配比空间中生成候选，再施加每种元素的含量上下限。这样每个候选天然满足总成分为 100 at.% 的条件。",
        "",
        "候选成分的采样条件为：",
        r"$$\sum_i x_i=1,\qquad \mathbf{x}\sim\operatorname{Dir}(\boldsymbol\alpha)$$",
        "",
        "其概率密度定义为：",
        r"$$p(\mathbf{x}\mid\boldsymbol\alpha)=\frac{\Gamma(\sum_i\alpha_i)}{\prod_i\Gamma(\alpha_i)}\prod_i x_i^{\alpha_i-1},\qquad x_i\geq0$$",
        "",
        "随后仅保留落在用户设定元素范围内的候选：",
        r"$$l_i\leq100x_i\leq u_i$$",
        "",
        "| 输入 | 输出 |",
        "|---|---|",
        "| 元素种类、各元素含量范围、制备工艺、评价温度 | 满足约束的候选配比 |",
        "",
        "### 3.2 成分—性能预测模型",
        "模型以成分描述符、制备工艺和评价温度为输入，输出强度、硬度与相组成倾向，并以多个独立训练结果的离散度表征预测稳定性。",
        "",
        "| 特征组 | 具体描述符 |",
        "|---|---|",
        "| 成分表示 | 各元素原子分数 $x_i$、元素数 $N$ |",
        "| 成分统计 | 理想混合熵、平均原子序数、平均价电子浓度（VEC） |",
        "| 工艺上下文 | 制备工艺的分类编码、评价温度、温度是否缺失 |",
        "",
        "本服务从候选配比中计算下列连续描述符：",
        r"$$N=\sum_i\mathbb{I}(x_i>0),\qquad \Delta S_{mix}=-R\sum_i x_i\ln x_i$$",
        "",
        r"$$\bar Z=\sum_i x_iZ_i,\qquad \overline{VEC}=\sum_i x_iVEC_i$$",
        "",
        "其中，N 是参与合金的元素数；ΔSₘᵢₓ反映理想混合程度；平均原子序数和平均价电子浓度用于刻画成分的电子结构差异。制备工艺转为分类编码后，与评价温度及温度缺失标记一起构成模型输入。",
        "",
        "### 3.3 输出、不确定性与筛选",
        "| 模型输出 | 本轮用途 |",
        "|---|---|",
        "| 屈服强度与硬度 | 5 个集成成员的均值与标准差，用于性能比较与不确定性控制 |",
        "| 相类别概率 | 固溶体主导（SS）、金属间相（IM）及混相（SS+IM）的概率，用于相风险筛除 |",
        "| 数据适用域 | 候选成分与训练成分云的最近距离，用于标记域内、边界或域外候选 |",
        "| 综合排序 J | 在通过硬约束的候选中组合强度、硬度、相稳定倾向和可靠性 |",
        "",
        "若本轮选定模型为集成模型，性能预测的均值和离散度按下式计算：",
        r"$$\bar y(\mathbf{z})=\frac{1}{M}\sum_{m=1}^{M}\hat y_m(\mathbf{z}),\qquad \sigma_y(\mathbf{z})=\sqrt{\frac{1}{M}\sum_{m=1}^{M}(\hat y_m-\bar y)^2}$$",
        "",
        "数据适用域以候选与训练配比的最近距离衡量：",
        r"$$d_{AD}(\mathbf{x})=\min_{\mathbf{x}^{train}}\lVert\mathbf{x}-\mathbf{x}^{train}\rVert_2$$",
        "",
        "通过硬约束后，再按综合评分排序：",
        r"$$J=w_yz(YS-\sigma_{YS})+w_hz(HV)+w_pz(P_{SS})+w_r\left[0.5\left(1-z(\sigma_{YS})\right)+0.5\left(1-z(d_{AD})\right)\right]$$",
        "",
        "其中 YS 为屈服强度，HV 为硬度，P₍SS₎为固溶体主导的预测概率；z(·) 仅在本轮可行候选中进行 min-max 归一化。",
        "",
        f"任务条件来源：{plan.get('template', '当前需求解析')}。计算完成后，结果区仅保留实际产出并用于结论的数值。",
    ])


def prediction_method_and_constraints_block(result: dict[str, Any]) -> str:
    """Describe usable prediction scope without exposing implementation details."""
    return "\n".join([
        "#### 预测依据与适用范围",
        "本轮数值来自经离线验证的合金成分—性能预测模型，用于在给定元素范围、工艺和温度下比较候选配比。",
        "",
        "| 项目 | 本轮说明 |",
        "|---|---|",
        "| 预测方式 | 多次独立训练结果集成，均值用于比较，离散度用于提示预测稳定性 |",
        "| 输入 | 元素原子分数、元素数、理想混合熵、平均原子序数、平均价电子浓度（VEC）、制备工艺与评价温度 |",
        "| 输出 | 屈服强度、硬度、相组成倾向、预测离散度与训练数据适用域 |",
        "| 数据依据 | 已整理的实验 HEA/MPEA 数据，并按规范化成分分组评估 |",
        "| 结果性质 | 模型预测用于研发排序与候选收敛 |",
        "",
        "先按元素边界、工艺温度、相风险、性能目标和数据适用域筛除不合适的候选，再比较保留候选。训练数据边界附近的候选会单独标明可信度。",
        "",
        "#### 当前筛选边界",
        "| 约束 | 筛选要求 |",
        "|---|---|",
        "| 配比边界 | 只允许指定元素；各元素含量在设定范围内；总和必须为 100 at.%。 |",
        "| 使用工况 | 制备工艺和评价温度与成分一起输入，避免脱离场景比较配方。 |",
        "| 相风险 | 仅保留预测中不利相风险较低的候选。 |",
        "| 数据适用域 | 检查候选是否落在训练数据覆盖范围；边界附近结果仅作为探索性备选。 |",
    ])


def screening_workflow_block(result: dict[str, Any]) -> str:
    sampling = result.get("sampling") or {}
    space = result.get("search_space") or {}
    criteria = result.get("screening_criteria") or {}
    return "\n".join([
        "#### 筛选方式",
        f"在指定元素范围、{space.get('processing_method', '-')} 工艺和 {space.get('test_temperature_C', '-')}°C 条件下生成 {sampling.get('generated', 0)} 个配比；每个配比均满足总成分 100 at.% 的约束。",
        f"随后计算强度、硬度、相风险、预测离散度和数据适用域，先执行硬性筛除，再对保留的 {sampling.get('feasible', 0)} 个候选综合排序。适用域规则为“{criteria.get('applicability_rule', '-')}”。",
    ])


def candidate_table(result: dict[str, Any]) -> str:
    rows = []
    for index, item in enumerate(result.get("initial_candidates", [])[:5], 1):
        strength = item.get("yield_strength_MPa", {})
        hardness = item.get("hardness_HV", {})
        domain = {"inside":"训练数据范围内", "boundary":"训练数据边界附近", "outside":"训练数据范围外"}.get(item.get("applicability_domain", {}).get("level", "-"), "-")
        phase_risk = {"low":"较低", "high":"较高"}.get(item.get("phase_risk", "-"), item.get("phase_risk", "-"))
        rows.append(
            f"| {index} | {_composition_text(item.get('composition_at_pct', {}))} | "
            f"{strength.get('mean', 0):.0f} ± {strength.get('std', 0):.0f} | "
            f"{hardness.get('mean', 0):.0f} ± {hardness.get('std', 0):.0f} | "
            f"{phase_risk} | {domain} |"
        )
    if not rows:
        return "#### 初始候选\n当前约束下没有通过初筛的候选。"
    return "\n".join([
        "### 可继续比较的候选",
        "| 排名 | 成分（at.%） | 屈服强度（MPa） | 硬度（HV） | 相风险 | 数据覆盖情况 |",
        "|---:|---|---:|---:|---|---|",
        *rows,
        "",
        "下表仅保留前 5 个候选用于横向比较；完整的优先候选信息见报告末尾的数据卡。",
    ])


def response_relationship_block(result: dict[str, Any]) -> str:
    """Explain the nonlinear surrogate in engineering language and retain its API form."""
    criteria = result.get("screening_criteria", {})
    mode = criteria.get("mode")
    threshold_note = ""
    if mode == "conservative_adaptive":
        threshold_note = "本轮未给出完整性能门槛，因此以当前搜索空间的相对较优区间进行初筛；补充明确指标后将按指定门槛筛选。"
    return "\n".join([
        "#### 成分—性能关系",
        "成分、制备工艺和温度共同影响强度、硬度与相组成，通常呈非线性关系。模型在给定边界内重新计算每个配比的响应：",
        "",
        r"$$\mathcal{F}(\mathbf{x},p,T)\longmapsto\left(\widehat{YS},\widehat{HV},\widehat{\mathbf{P}}_{phase},\sigma,d_{AD}\right)$$",
        "",
        r"其中 \(\mathbf{x}\) 为元素原子分数，\(p\) 为制备工艺，\(T\) 为评价温度；输出依次为强度、硬度、相组成倾向、预测离散度和数据适用域距离。",
        threshold_note,
        "如需比较新配比，只需提供元素原子百分比（合计 100 at.%）、工艺和温度。",
    ])


def selection_formula_block(result: dict[str, Any]) -> str:
    formula = (result.get("screening_criteria") or {}).get("selection_formula") or {}
    if not formula:
        return ""
    weights = formula.get("weights") or {}
    return "\n".join([
        "#### 综合排序",
        "通过硬性初筛后，按下式综合比较候选：",
        "",
        r"$$J=w_yz(YS-\sigma_{YS})+w_hz(HV)+w_pz(P_{SS})+w_r\left[0.5\left(1-z(\sigma_{YS})\right)+0.5\left(1-z(d_{AD})\right)\right]$$",
        "",
        f"本轮权重：强度 {weights.get('strength', 0):.0%}，硬度 {weights.get('hardness', 0):.0%}，相稳定倾向 {weights.get('phase', 0):.0%}，预测可靠性 {weights.get('reliability', 0):.0%}。",
        r"\(z(\cdot)\) 只在本轮可行候选中归一化；元素范围、相风险、性能门槛和适用域仍是先行条件。",
    ])


def default_assumptions_block(result: dict[str, Any]) -> str:
    """Show only editable engineering assumptions, never implementation keys."""
    assumptions = (result.get("requirement_interpretation") or {}).get("default_assumptions") or []
    values = {str(item.get("field")): item.get("value") for item in assumptions}
    rows = []
    elements = values.get("allowed_elements")
    if elements:
        rows.append(("探索元素", "、".join(map(str, elements))))
    bounds = values.get("element_bounds_at_pct")
    if isinstance(bounds, dict):
        rows.append(("元素含量范围", "；".join(f"{element} {float(rng[0]):g}–{float(rng[1]):g} at.%" for element, rng in bounds.items() if isinstance(rng, (list, tuple)) and len(rng) == 2)))
    processing = values.get("processing_method")
    if processing:
        rows.append(("制备工艺", {"CAST":"铸造（CAST）"}.get(str(processing), str(processing))))
    temperature = values.get("test_temperature_C")
    if temperature is not None:
        rows.append(("评价温度", f"{temperature}°C"))
    if values.get("screening_mode"):
        rows.append(("筛选原则", "优先保留性质较优、预测较稳定且训练数据覆盖较好的候选"))
    if not rows:
        return ""
    lines = ["#### 当前采用的默认条件", "以下为本轮筛选使用的设计条件。", "", "| 项目 | 当前设定 |", "|---|---|"]
    lines.extend(f"| {name} | {value} |" for name, value in rows)
    return "\n".join(lines)


def optimization_handoff_table(result: dict[str, Any]) -> str:
    space = result.get("search_space", {}); criteria = result.get("screening_criteria", {}); sampling = result.get("sampling", {}); candidates = result.get("initial_candidates", [])
    rows = [
        ("搜索变量", "search_space", f"元素范围、总和={space.get('sum_constraint_at_pct')} at.%，工艺={space.get('processing_method')}，温度={space.get('test_temperature_C')}°C", "数学优化只能在这些明确边界内搜索。"),
        ("筛选目标", "screening_criteria", json.dumps(criteria.get("objectives", {}), ensure_ascii=False), "本轮初筛采用的性质目标；用户可修改后重新计算。"),
        ("初始种群", "initial_candidates", f"返回 {len(candidates)} 个；本轮 {sampling.get('feasible', 0)}/{sampling.get('generated', 0)} 个通过初筛", "可作为后续数学优化的起点，不是最终最优解。"),
        ("非线性评价器", "/alloy/evaluate-batch", "屈服强度/硬度均值与标准差、相概率、适用域、约束", "优化器提出新成分后，由该评价器判断性质与可信度。"),
        ("成分分布图", "derived_candidate_percentiles_at_pct", "筛后候选 P5–P50–P95", "只表示当前筛后样本的集中区域，不能当作新的硬边界。"),
    ]
    lines = ["| 交接项目 | 结构化字段/接口 | 本轮内容 | 用户解读 |", "|---|---|---|---|"]
    lines.extend(f"| {a} | `{b}` | {c} | {d} |" for a,b,c,d in rows)
    return "\n".join(lines)


def final_conclusion_block(result: dict[str, Any]) -> str:
    blocks = [
        prediction_method_and_constraints_block(result),
        default_assumptions_block(result),
        screening_workflow_block(result),
        response_relationship_block(result),
        selection_formula_block(result),
        candidate_table(result),
        concise_conclusion_block(result),
        optimal_candidate_data_card(result),
    ]
    return "\n\n".join(block for block in blocks if block)


def concise_conclusion_block(result: dict[str, Any]) -> str:
    """Close the report with a brief, action-oriented conclusion."""
    sampling = result.get("sampling", {})
    candidates = result.get("initial_candidates", [])
    generated = int(sampling.get("generated", 0))
    feasible = int(sampling.get("feasible", 0))
    if candidates:
        top = candidates[0]
        strength = top.get("yield_strength_MPa", {})
        hardness = top.get("hardness_HV", {})
        finding = (
            f"在 {generated} 个候选中保留了 {feasible} 个可继续比较的配方；排名第一的候选预测屈服强度为 "
            f"{strength.get('mean', 0):.0f} ± {strength.get('std', 0):.0f} MPa，硬度为 "
            f"{hardness.get('mean', 0):.0f} ± {hardness.get('std', 0):.0f} HV。"
        )
    else:
        finding = f"本轮生成了 {generated} 个候选，但没有配方通过当前初筛条件。"
    return "\n\n".join([
        "### 本轮结论",
        "本轮结合预训练机器学习模型、成分相关的材料机理特征，以及工艺温度、配比边界、相风险和数据适用域等约束，对候选配方进行了初步筛选。",
        finding,
        "建议下一步结合实际服役温度、氧化环境和制造路线进一步收敛范围，并对优先候选开展热力学、高温性能和实验验证。完整配方、预测结果、来源和可信度见下方材料卡。",
    ])


def _domain_and_confidence(candidate: dict[str, Any]) -> tuple[str, str]:
    level = (candidate.get("applicability_domain") or {}).get("level", "-")
    return {
        "inside": ("训练数据范围内", "较高：模型输入与已有训练样本较接近，仍需实验验证。"),
        "boundary": ("训练数据边界附近", "中等：可作为探索候选，需优先补充计算或实验验证。"),
        "outside": ("训练数据范围外", "较低：仅保留为探索线索，不作为优先工程判断。"),
    }.get(level, (str(level), "当前适用域信息未完整记录。"))


def optimal_candidate_data_card(result: dict[str, Any]) -> str:
    """Render the complete, traceable final card for the highest-ranked candidate."""
    candidates = result.get("initial_candidates") or []
    if not candidates:
        return ""
    candidate = candidates[0]
    space = result.get("search_space") or {}
    composition = candidate.get("composition_at_pct") or {}
    strength = candidate.get("yield_strength_MPa") or {}
    hardness = candidate.get("hardness_HV") or {}
    phase = candidate.get("phase_probabilities") or {}
    domain, confidence = _domain_and_confidence(candidate)
    applicability = candidate.get("applicability_domain") or {}
    phase_risk = {"low": "较低", "high": "较高"}.get(candidate.get("phase_risk"), str(candidate.get("phase_risk") or "未记录"))
    source = "经离线验证的合金成分—性能预测模型"
    composition_rows = [f"| {element} | {float(amount):.2f} at.% |" for element, amount in composition.items()]
    return "\n".join([
        "### 最优候选材料卡",
        "**高熵合金优化候选 01**：这是当前约束下综合排序第一的模型预测候选，供后续热力学与实验验证优先评估。",
        "",
        "| 配方与条件 | 当前信息 |",
        "|---|---|",
        "| 候选身份 | 高熵合金优化候选 01（模型生成配方，不对应既有商品牌号） |",
        f"| 制备工艺 | {space.get('processing_method', '当前未记录')} |",
        f"| 评价温度 | {space.get('test_temperature_C', '当前未记录')}°C |",
        f"| 成分总和 | {sum(float(value) for value in composition.values()):.2f} at.% |",
        "",
        "| 元素 | 成分 |",
        "|---|---:|",
        *composition_rows,
        "",
        "| 关键性质与判断 | 本轮结果 | 条件、来源与可信度 |",
        "|---|---|---|",
        f"| 屈服强度 | {strength.get('mean', 0):.0f} ± {strength.get('std', 0):.0f} MPa | {space.get('processing_method', '工艺未记录')}；{space.get('test_temperature_C', '温度未记录')}°C；{source}，集成离散度为 ± 值。 |",
        f"| 硬度 | {hardness.get('mean', 0):.0f} ± {hardness.get('std', 0):.0f} HV | {space.get('processing_method', '工艺未记录')}；{space.get('test_temperature_C', '温度未记录')}°C；{source}，集成离散度为 ± 值。 |",
        f"| 相组成倾向 | SS {float(phase.get('SS', 0)):.1%}；IM {float(phase.get('IM', 0)):.1%}；SS+IM {float(phase.get('SS+IM', 0)):.1%} | 模型相分类预测；相风险判定为“{phase_risk}”。 |",
        f"| 数据适用域 | {domain} | 最近训练成分距离 {float(applicability.get('nearest_training_composition_distance', 0)):.3f}；{confidence} |",
        f"| 综合排序分数 | {float(candidate.get('selection_score', 0)):.3f} | 仅用于本轮通过初筛候选之间的排序，不是材料性能或工程放行指标。 |",
        "",
        "| 工程估算与验证重点 | 初筛估算/判断 | 依据、可信度与后续验证 |",
        "|---|---|---|",
        *[
            f"| {row['property']} | {row['estimate']} | {row['basis']} 验证：{row['validation']} |"
            for row in build_engineering_estimates(candidate, space)
        ],
        "",
        "| 来源与可信度说明 | 记录 |",
        "|---|---|",
        f"| 预测依据 | {source}；用于研发筛选与候选排序。 |",
        "| 数据范围 | HEA/MPEA 实验数据覆盖范围；适用域已在上方单独标注。 |",
        f"| 可信度结论 | 屈服强度、硬度和相组成来自模型预测；工程估算表中的条目单独按 D 级标注。两类结果均用于研发初筛，不作为工程放行依据。{confidence} |",
    ])


def microstructure_tendency_block(result: dict[str, Any], asset_url: str | None = None) -> str:
    """Place the explanatory schematic directly after the optimal-candidate card."""
    tendency = result.get("microstructure_tendency") or {}
    if not tendency:
        return ""
    phase = tendency.get("phase_probabilities") or {}
    lines = [
        "### 预测组织倾向示意图",
        f"**{tendency.get('title', '组织倾向待确认')}**。{tendency.get('explanation', '')}",
    ]
    if asset_url:
        lines.extend(["", f"![预测组织倾向示意图]({asset_url})"])
    lines.extend([
        "",
        "| 项目 | 当前判断 |",
        "|---|---|",
        f"| 相分类输入 | SS {float(phase.get('SS', 0)):.1%}；IM {float(phase.get('IM', 0)):.1%}；SS+IM {float(phase.get('SS+IM', 0)):.1%} |",
        f"| 混相风险 | {tendency.get('mixed_phase_risk', '-')} |",
        f"| 金属间化合物风险 | {tendency.get('intermetallic_risk', '-')} |",
        f"| 数据适用域与表达强度 | {tendency.get('applicability_domain', '-')}；{tendency.get('confidence', '模型初筛')} |",
        f"| 优先验证 | {'；'.join(tendency.get('validation_priorities') or [])} |",
        "",
        "注：本图为基于相组成预测结果生成的组织倾向示意，用于辅助理解候选材料的可能组织特征；不代表真实显微照片、相场模拟结果或最终实验组织。",
    ])
    return "\n".join(lines)


def _llm() -> Any | None:
    """Load presentation credentials from this service's environment first.

    ``config/config.yaml`` remains a read-only compatibility fallback for old
    tmux deployments.  It is deliberately not the preferred source because
    credentials and deployment endpoints belong in this service's ignored
    ``.env`` file.
    """
    if os.getenv("ALLOY_PRESENTATION_LLM", "true").lower() not in {"1", "true", "yes"}: return None
    try:
        import yaml
        from openai import AsyncOpenAI
        base_url = os.getenv("ALLOY_PRESENTATION_BASE_URL", "").strip()
        api_key = os.getenv("ALLOY_PRESENTATION_API_KEY", "").strip()
        if not (base_url and api_key):
            config_path = Path(os.getenv("ALLOY_LLM_CONFIG", "config/config.yaml"))
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            base_url = base_url or str(config.get("base_url_1", ""))
            api_key = api_key or str(config.get("api_key", ""))
        if not (base_url and api_key):
            return None

        class PresentationLLM:
            def __init__(self): self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            def _default_system_msg(self): return {"role":"system", "content":"你是严谨的材料工程结果呈现助手。"}
            def _user_msg(self, content): return {"role":"user", "content":content}
            async def acompletion_text(self, messages, timeout=30):
                return await self.client.chat.completions.create(messages=messages, model=os.getenv("ALLOY_PRESENTATION_MODEL", "SE_V0.0"), temperature=0, max_tokens=int(os.getenv("ALLOY_PRESENTATION_MAX_TOKENS", "8192")), timeout=timeout, stream=True)
        return PresentationLLM()
    except Exception:
        return None


async def _stream_deterministic(websocket: Any, text: str) -> None:
    for block in text.split("\n\n"):
        if block:
            await websocket.send_text(block.rstrip() + "\n\n")
            await asyncio.sleep(0)


async def stream_authoritative_markdown(websocket: Any, markdown: str, *, step_id: str) -> None:
    """Use the established LLM token stream for every customer-facing Markdown block."""
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    llm = _llm()
    if llm is None:
        await _stream_deterministic(websocket, markdown)
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
        return
    try:
        from src.alloy_workflow.llm_streaming import stream_llm_response
        prompt = (
            "你是合金服务的 Markdown 流式转发器。请通过 token 流逐字输出 "
            "<AUTHORITATIVE_MARKDOWN> 内的全部 Markdown；不得改写、删减、补充、"
            "翻译、重排或输出 XML 标签。必须完整保留标题、表格、公式和图片链接。\n"
            "<AUTHORITATIVE_MARKDOWN>\n"
            f"{markdown}\n"
            "</AUTHORITATIVE_MARKDOWN>"
        )
        await stream_llm_response(llm, [llm._default_system_msg(), llm._user_msg(prompt)], websocket)
    except Exception:
        await _stream_deterministic(websocket, markdown)
    finally:
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")


def visual_assets_block(visual_assets: list[dict[str, str]] | None = None) -> str:
    """Render public charts inside the conversation body, like the 3D service.

    Asset events alone are not guaranteed to be rendered by every Alpha client.
    The HTML image tags are a deliberate second presentation path, not a second
    computation or duplicate result.
    """
    if not visual_assets:
        return ""
    lines = ["#### 图表解读"]
    for item in visual_assets:
        if item.get("name") == "microstructure_tendency":
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        title = str(item.get("title") or "分析图表")
        description = str(item.get("description") or "")
        lines.extend([
            f"#### {title}",
            description,
            f"![{title}]({url})",
        ])
    return "\n\n".join(lines) if len(lines) > 1 else ""


def _place_visuals_before_conclusion(narrative: str, visuals: str) -> str:
    """Keep the brief conclusion as the last thing the user reads."""
    if not visuals:
        return narrative
    marker = "\n\n### 本轮结论\n"
    if marker not in narrative:
        return narrative.rstrip() + "\n\n" + visuals
    before, conclusion = narrative.rsplit(marker, 1)
    return before.rstrip() + "\n\n" + visuals.strip() + marker + conclusion


async def emit_result_content(websocket: Any, result: dict[str, Any], *, step_id: str = "FILAMENT_SELECTION_OPTIMIZATION", visual_assets: list[dict[str, str]] | None = None) -> None:
    """Stream LLM-rendered narrative/table like adjacent services, with safe fallback."""
    path = result.get("_summary_path")
    fallback = path.read_text(encoding="utf-8") if path else final_conclusion_block(result)
    visuals = visual_assets_block(visual_assets)
    rendered_content = _place_visuals_before_conclusion(fallback, visuals)
    tendency_asset = next((item for item in (visual_assets or []) if item.get("name") == "microstructure_tendency"), None)
    tendency = microstructure_tendency_block(result, str((tendency_asset or {}).get("url") or ""))
    if tendency:
        rendered_content = rendered_content.rstrip() + "\n\n" + tendency
    await stream_authoritative_markdown(websocket, rendered_content, step_id=step_id)
