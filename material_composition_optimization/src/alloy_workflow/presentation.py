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


def _composition_text(composition: dict[str, Any]) -> str:
    return " ".join(f"{element}{float(value):.1f}" for element, value in composition.items())


def pretrained_model_and_constraints_block(result: dict[str, Any]) -> str:
    """Explain the pretrained MLP design and screening guardrails plainly."""
    return "\n".join([
        "#### 预训练模型与筛选约束",
        "模型库中包含已离线训练好的高熵/多主元合金 MLP（多层感知机）。本轮只加载既有模型评估候选，不会用当前任务数据重新训练。",
        "",
        "**MLP 如何理解一个候选配方**",
        "",
        "| 环节 | 网络结构 / 输入 | 它在做什么 |",
        "|---|---|---|",
        "| 输入层 | 元素比例、元素数、混合熵、平均原子序数、平均 VEC、工艺、温度 | 把一个配方及其使用条件转换为模型能比较的特征。 |",
        "| 第一隐藏层 | 64 个单元 + ReLU | 从原始输入中识别元素组合与工况的初步特征。 |",
        "| 第二隐藏层 | 32 个单元 + ReLU | 进一步组合特征，形成对强度、硬度和相风险有用的判断。 |",
        "| 输出 | 性能预测、相组成风险与预测离散度 | 给出候选的预估结果，以及结果是否一致的提示。 |",
        "",
        "**工程约束：先筛除，再排序**",
        "",
        "| 约束 | 筛选要求 |",
        "|---|---|",
        "| 配比边界 | 只允许指定元素；各元素含量在设定范围内；总和必须为 100 at.%。 |",
        "| 使用工况 | 制备工艺和评价温度与成分一起输入，避免脱离场景比较配方。 |",
        "| 相风险 | 仅保留预测中不利相风险较低的候选。 |",
        "| 数据适用域 | 检查候选是否落在训练数据覆盖范围；边界附近结果仅作为探索性备选。 |",
        "",
        "最终用于本轮预测的模型，是在相同独立验证条件下比较后效果最好的模型。整个流程用于快速缩小配方搜索范围，不替代 CALPHAD、专项热力学计算或实验验证。",
    ])


def screening_workflow_block(result: dict[str, Any]) -> str:
    sampling = result.get("sampling") or {}
    space = result.get("search_space") or {}
    criteria = result.get("screening_criteria") or {}
    return "\n".join([
        "#### 这轮筛选是怎样完成的",
        f"1. **确定探索边界**：限定允许元素、各元素原子百分比范围、总和为 100 at.%、工艺为“{space.get('processing_method', '-')}”、温度为 {space.get('test_temperature_C', '-')}°C。",
        f"2. **生成候选配比**：使用 Dirichlet 成分单纯形采样，可理解为在“所有元素比例非负、总和必为 100%”的配比空间内均匀抽取组合；本轮生成 {sampling.get('generated', 0)} 个候选，且都满足预设元素范围。",
        "3. **逐个模型推理**：对每个候选输入成分、工艺和温度，集成模型输出屈服强度、硬度、相组成概率、模型间离散度和训练适用域。",
        f"4. **先做硬性筛除**：检查元素边界、相风险、性能门槛、不确定性和适用域。本轮保留 {sampling.get('feasible', 0)} 个候选；适用域规则为“{criteria.get('applicability_rule', '-')}”。",
        "5. **再综合排序**：只在通过硬性筛除的候选中综合比较强度、硬度、相稳定倾向和可靠性；排序靠前不等于已经完成工程认证。",
    ])


def candidate_table(result: dict[str, Any]) -> str:
    rows = []
    for index, item in enumerate(result.get("initial_candidates", [])[:10], 1):
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
        "### 本轮推荐候选",
        "| 排名 | 成分（at.%） | 屈服强度（MPa） | 硬度（HV） | 相风险 | 数据覆盖情况 |",
        "|---:|---|---:|---:|---|---|",
        *rows,
        "",
        "这些候选按当前初筛结果排序，并非最终工程定型配方。",
    ])


def response_relationship_block(result: dict[str, Any]) -> str:
    """Explain the nonlinear surrogate in engineering language and retain its API form."""
    response = result.get("nonlinear_response_function", {})
    criteria = result.get("screening_criteria", {})
    mode = criteria.get("mode")
    threshold_note = ""
    if mode == "conservative_adaptive":
        threshold_note = "- 由于本轮需求信息不完整，系统以当前搜索空间内的相对较优区间自动设置强度、硬度和预测稳定性门槛，并仅保留训练数据范围内的候选。补充明确指标后，会优先采用用户指定的门槛。"
    return "\n".join([
        "#### 成分和性能如何关联",
        "成分比例、制备工艺和温度共同影响强度、硬度及相组成；这种关系通常是非线性的，不能可靠地简化成“某元素每增加 1% 就固定提高多少强度”的线性公式。",
        "本服务用已训练的高熵合金/多主元合金（HEA/MPEA）集成模型近似这条响应关系：",
        f"`{response.get('mathematical_form', 'F(成分, 工艺, 温度) → 性能、相风险与可信度')}`",
        "- 这意味着后续可在给定元素范围内改变一个或多个元素比例，并由同一模型重新计算强度、硬度、相风险和不确定性；而不是把当前候选范围误当成硬编码配方。",
        threshold_note,
        "- 如需进一步比较多个新配比，系统会逐个计算它们的强度、硬度、相风险和可信度，并将结果并排呈现；每条配比只需给出元素原子百分比（合计 100）、工艺和温度。",
    ])


def selection_formula_block(result: dict[str, Any]) -> str:
    formula = (result.get("screening_criteria") or {}).get("selection_formula") or {}
    if not formula:
        return ""
    weights = formula.get("weights") or {}
    return "\n".join([
        "#### 候选如何综合排序",
        "通过硬性初筛后，系统不会只看单一强度，而是按下式综合比较候选：",
        "",
        "`J = wᵧ·z(屈服强度 − 强度不确定性) + wₕ·z(硬度) + wₚ·z(SS 相概率) + wᵣ·[0.5·(1−z(强度不确定性)) + 0.5·(1−z(距训练数据距离))]`",
        "",
        f"本轮权重：强度 {weights.get('strength', 0):.0%}，硬度 {weights.get('hardness', 0):.0%}，相稳定倾向 {weights.get('phase', 0):.0%}，预测可靠性 {weights.get('reliability', 0):.0%}。",
        "其中 `z()` 表示在本轮可行候选中归一化；这只用于排序。元素范围、相风险上限、最低性能门槛和适用域仍是先行的硬性条件。",
        "当前公式尚未包含混合焓、Omega 或 CALPHAD 自由能项；这些项须在统一重算或独立热力学计算接入后才能加入。",
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
        pretrained_model_and_constraints_block(result),
        default_assumptions_block(result),
        screening_workflow_block(result),
        response_relationship_block(result),
        selection_formula_block(result),
        candidate_table(result),
        concise_conclusion_block(result),
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
        "建议下一步结合实际服役温度、氧化环境和制造路线进一步收敛范围，并对优先候选开展热力学、高温性能和实验验证。",
    ])


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
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        title = str(item.get("title") or "分析图表")
        description = str(item.get("description") or "")
        lines.extend([
            f"#### {title}",
            description,
            f'<img src="{url}" alt="{title}" style="max-width:900px;width:78%;height:auto;display:block;margin:8px auto 18px;" />',
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
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    llm = _llm()
    if llm is None:
        await _stream_deterministic(websocket, rendered_content)
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
        return
    try:
        from src.alloy_workflow.llm_streaming import stream_llm_response
        authoritative_markdown = rendered_content
        relay_prompt = (
            "你是合金服务的 Markdown 流式转发器，不负责推理、概括、润色或补充。"
            "下方 <AUTHORITATIVE_MARKDOWN> 中的内容由程序根据已保存的模型结果生成，"
            "包括数字、表格、公式和图片链接。你的唯一任务是从正文的第一个字符到最后一个字符逐字输出其内部内容。"
            "绝对不得改写、翻译、删减、补充、重新排序、解释、添加标题、添加代码围栏，"
            "也不要输出 XML 标签本身。必须完整保留所有 Markdown 和 HTML。\n"
            "<AUTHORITATIVE_MARKDOWN>\n"
            f"{authoritative_markdown}\n"
            "</AUTHORITATIVE_MARKDOWN>"
        )
        await stream_llm_response(llm, [llm._default_system_msg(), llm._user_msg(relay_prompt)], websocket)
    except Exception:
        # Same failure philosophy as neighboring services: preserve a complete,
        # deterministic result rather than suppressing evidence on LLM failure.
        await _stream_deterministic(websocket, rendered_content)
    finally:
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
