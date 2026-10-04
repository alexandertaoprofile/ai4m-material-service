"""Customer reports: a short execution plan and an evidence-backed final page."""
from __future__ import annotations

import math
import re

from .omatg_chart import ELEMENT_NAMES, crystal_identity, scheme_names, stability_number, primary_structure
from .omatg_output_status import candidate_issues, missing_output_issue, generation_issue, readable_reason


def cell(value):
    return str(value).replace("|", "／").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")


def number(value, digits=5):
    if value is None or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return "—（数值未记录）"
    return f"{value:.{digits}f}"


PROPERTY_LABELS = {"band_gap": "带隙", "bulk_modulus": "体积模量", "shear_modulus": "剪切模量", "electron_effective_mass": "电子有效质量", "hole_effective_mass": "空穴有效质量", "dielectric_constant_x": "x 方向介电常数"}
PROPERTY_UNITS = {"band_gap": "eV", "bulk_modulus": "GPa", "shear_modulus": "GPa", "electron_effective_mass": "m0", "hole_effective_mass": "m0", "dielectric_constant_x": "1"}
PROPERTY_MEANINGS = {
    "band_gap": "表示价带与导带之间的能量差，用于比较电子结构；绝缘性能还需结合击穿等验证。",
    "bulk_modulus": "表示抵抗体积压缩的能力；数值越大，压缩同样体积所需压力通常越大。",
    "shear_modulus": "表示抵抗剪切变形的能力，用于比较材料的弹性响应。",
    "electron_effective_mass": "表示电子在晶体中的运动响应，以自由电子质量 m0 为基准，需结合能带结构解释。",
    "hole_effective_mass": "表示空穴在晶体中的运动响应，以自由电子质量 m0 为基准，需结合能带结构解释。",
    "dielectric_constant_x": "表示晶体在 x 方向对电场的响应，需在一致的方向和计算条件下比较。",
}
SYMMETRY_LABELS = {"non_centrosymmetric": "非中心对称", "space_group_number": "空间群编号"}
FOLLOW_UP_LABELS = {"high_temperature_strength": "高温强度", "creep_resistance": "抗蠕变能力", "oxidation_resistance": "抗氧化能力", "thermal_fatigue": "热疲劳性能", "additive_manufacturability": "增材制造适配性", "ionic_conductivity": "离子电导率"}


def element_label(symbol):
    name = ELEMENT_NAMES.get(symbol)
    return f"{name}（{symbol}）" if name else symbol


def formula_label(formula, amounts=None):
    formula, name = crystal_identity({"formula_pretty": formula, "metadata": {"element_counts": amounts or {}}}, {})
    return f"{cell(name)}（{cell(formula)}）"


def _setting(c, key, value):
    source = c.parameter_sources.get(key, "")
    suffix = "（默认设置）" if "默认" in source else "（已确认）" if "确认" in source else ""
    return cell(str(value) + suffix)


def hull_limit(c):
    return c.target_properties.get("energy_above_hull", 0.05)


def hull_is_hard(c):
    return c.parameter_sources.get("energy_above_hull_mode") == "硬性上限"


def hull_policy(c):
    value = f"{hull_limit(c):g} eV/atom"
    return f"硬性上限 {value}" if hull_is_hard(c) else f"复核参考线 {value}（超过仍继续评价）"


def bounds_text(key, limits):
    text = "；".join(f"{'下限' if bound == 'min' else '上限'} {limits[bound]:g}" + ("（严格大于）" if bound == "min" else "（严格小于）") * (limits.get(bound + "_inclusive") is False) for bound in ("min", "max") if bound in limits)
    return text + " " + limits.get("unit", PROPERTY_UNITS.get(key, ""))


def domain_text(c):
    return "、".join(c.allowed_elements) or "用户确认的开放元素空间"


def task_goals(c):
    return (set(c.validation_targets) | set(c.property_constraints)
            | (set(c.target_properties) - {"energy_above_hull"})
            | set(c.task_context.get("performance_goals", [])))


def goal_purpose(c, key):
    scene = c.task_context.get("scenario", "")
    if key == "band_gap":
        return "比较电子绝缘潜力" if any(word in scene for word in ("绝缘", "电解质")) else "比较候选的电子结构差异"
    return {"bulk_modulus": "比较晶体抗体积压缩的弹性响应", "shear_modulus": "比较晶体抗剪切的弹性响应",
        "electron_effective_mass": "比较电子输运的结构模型代理量", "hole_effective_mass": "比较空穴输运的结构模型代理量",
        "dielectric_constant_x": "比较所指定方向的介电响应"}.get(key, "结构性质初筛")


def problem_description(c):
    """Bind a traceable application brief to the actual CSP/DNG task."""
    context = c.task_context
    label, scenario = context.get("scenario_label"), context.get("scenario")
    inherited = context.get("scenario_origin") == "upstream"
    if label:
        prefix = ("承接上文" if inherited else "面向当前提出的") + cell(label.replace("的部分", "局部部位")) + "的材料需求，本次"
    elif scenario:
        prefix = ("承接上文材料需求" if inherited else "面向当前材料需求") + "（" + cell(scenario) + "），本次"
    else:
        prefix = "本次"
    if c.target_formula:
        scope = f"以 {cell(c.target_formula)} 为固定化学计量，在每胞 {c.num_atoms} 原子的条件下探索不同晶格与原子排布"
    elif c.allowed_elements:
        scope = f"在 {cell(domain_text(c))} 元素白名单内同时探索组成与晶体结构"
        scope += f"；{cell('、'.join(c.required_elements))} 必须出现" if c.required_elements else "；允许元素可只出现一部分"
        optional = [element for element in c.allowed_elements if element not in c.required_elements]
        if c.required_elements and optional:
            scope += f"，{cell('、'.join(optional))} 为允许但非必含元素"
    else:
        scope = "在用户已确认的开放化学空间中同时探索无机周期晶体的组成与结构"
    if c.generation_mode == "dng" and c.min_distinct_elements > 1:
        scope += f"；每个候选至少含 {c.min_distinct_elements} 种元素"
    rows = [prefix + scope + "，形成可供后续材料验证比较的结构候选。", ""]
    if not scenario and c.material_family:
        rows.extend(["材料探索方向：" + cell(c.material_family) + "。当前未指定具体应用部件，先比较候选组成、结构及可计算性质。", ""])
    bridge = ["比较候选相对其他竞争相组合的能量差（凸包能估算），提示热力学稳定性复核优先级"]
    for key in sorted(task_goals(c)):
        if key in PROPERTY_LABELS:
            bridge.append(PROPERTY_LABELS[key] + "用于" + goal_purpose(c, key))
    if c.symmetry_constraints:
        bridge.append("用结构优化后的空间群与对称性检查已确认的结构条件")
    rows.append("将材料需求对应到本次结构初筛：" + "；".join(bridge) + "。")
    follow_up = context.get("follow_up_validation", [])
    if follow_up:
        rows.extend(["", "针对该场景，候选结构还需进入" + cell("、".join(follow_up)) + "专项验证；上述结构初筛为这些验证提供候选起点。"])
    return rows


def build_plan(c, count=10):
    """Pre-execution method definition, deliberately distinct from final results."""
    mode = c.generation_mode
    route = f"固定化学式 {cell(c.target_formula)} 的 CSP" if mode == "csp" else "限定元素 DNG" if c.allowed_elements else "已确认开放空间的 DNG"
    scene = c.task_context.get("scenario_label") or c.task_context.get("scenario")
    rows = ["### 计算方案", ""]
    if scene:
        rows.extend([f"面向{cell(scene)}的材料需求，本次先探索晶体候选，再比较稳定性与目标性能。", ""])
    rows.extend([f"采用{route}，计划生成 {count} 个候选。以下为本次实际采用的条件和计算安排。", "",
        "| 材料条件或性能要求 | 本次采用的条件 |", "| --- | --- |",
        f"| {'固定化学式' if c.target_formula else '元素探索范围'} | {cell(c.target_formula or domain_text(c))} |",
        f"| 计算晶胞 | 每胞 {c.num_atoms} 个原子 |",
        f"| 稳定性比较 | {hull_policy(c)} |"])
    if c.required_elements:
        rows.append("| 每个候选必须包含 | " + cell("、".join(element_label(e) for e in c.required_elements)) + " |")
    if c.excluded_elements:
        rows.append("| 排除的元素 | " + cell("、".join(element_label(e) for e in c.excluded_elements)) + " |")
    for key in sorted(task_goals(c)):
        if key in PROPERTY_LABELS:
            adopted = bounds_text(key, c.property_constraints[key]) if key in c.property_constraints else f"参考目标 {c.target_properties[key]:g} {PROPERTY_UNITS[key]}" if key in c.target_properties else "预测后比较"
            rows.append(f"| {PROPERTY_LABELS[key]} | {cell(adopted)} |")
    rows.extend([
        "", "| 顺序 | 模型或方法 | 输入与描述符 | 计划输出 |", "| --- | --- | --- | --- |",
        f"| 1 | OMatG {mode.upper()} | 晶格、元素种类与原子位置；{cell(c.target_formula) if mode == 'csp' else cell(domain_text(c))} | {'固定组成的候选晶体' if mode == 'csp' else '候选组成与晶体结构'} |",
        "| 2 | pymatgen 周期结构检查 | 元素计数、晶胞及周期近邻 | 组成与几何准入结果 |",
        "| 3 | MatterSim 结构优化 | 原子位置、晶格、能量与原子力 | 优化后的结构及收敛情况 |",
        "| 4 | 参考相能量比较 | 候选能量及同元素参考相 | 稳定性差值估算 |",
        "| 5 | StructureMatcher + ALIGNN | 松弛结构；原子图与键角线图 | 结构族代表、实际可完成的性质预测与候选排序 |", "",
        "本次调用已训练的模型进行推理采样。OMatG 使用 CSPNetFull 与随机插值生成结构，MatterSim 根据能量和力调整结构，ALIGNN 根据原子图与键角预测性质。完整模型信息与公式在结果报告的方法章节中说明。"])
    if mode == "dng":
        rows.append(f"候选至少包含 {c.min_distinct_elements} 种元素。")
    if mode == "dng" and c.refine_with_csp:
        rows.append(f"已确认追加 CSP：DNG 松弛初筛后，选取凸包能估算最低的合规组成，再生成 {count} 个固定计量构型；保留来源 DNG 结构用于比较。")
    return "\n".join(rows)


def evaluation_completion_text(candidates, validations):
    values = [v.to_dict() if hasattr(v, "to_dict") else v for v in validations]
    admitted = [v for v in values if v.get("is_valid") is True]
    converged = [v for v in admitted if v.get("metadata", {}).get("mattersim", {}).get("relaxation_converged") is True]
    stability = sum(_finite(v.get("energy_above_hull")) for v in converged)
    properties = sum(any(_finite(p.get("value")) for p in v.get("property_predictions", {}).values()) for v in converged)
    return f"已生成 {len(candidates)} 个候选结构；{len(admitted)} 个通过原子排布检查，{len(converged)} 个结构优化收敛，{stability} 个已有稳定性结果，{properties} 个已有性质预测。"


def build_review_context(payload):
    from .constraints import _current_task_text
    from .task_context import build_task_context
    source = payload.get("new_material") or {}
    raw = str(payload.get("idea") or payload.get("instruction") or "")
    current = _current_task_text(raw)
    context = build_task_context(payload, source, raw, current)
    scene = context.get("scenario_label") or context.get("scenario")
    received = f"已了解您希望为{cell(scene)}探索候选晶体。" if scene else "已收到材料探索需求：" + cell(current[:180] or source.get("material_family") or "探索新的无机晶体") + "。"
    return "\n".join(["### 已了解的材料需求", "", received, "", "请确认本次从哪种组成范围开始，随后生成候选晶体并比较性能。", "",
        "| 可选择的起点 | 将开展的计算 |", "| --- | --- |",
        "| 化学式 | 保持元素及其原子数比例，探索不同晶体结构 |",
        "| 元素范围 | 在所列元素中探索组成和结构，可另写必含或排除元素 |",
        "| 开放探索 | 同意开放元素空间，围绕材料用途和性能要求评价生成结果 |"])


def result_visuals(presentation, markdown):
    """Place images from the existing publisher; no alternate upload route."""
    images = {match.group(1): match.group(0) for match in re.finditer(r"!\[([^\]]+)\]\([^\n]+\)", markdown)}
    from pathlib import Path
    return {Path(asset.get("path") or "").stem: images[asset["name"]]
            for asset in presentation.get("assets", []) if asset.get("name") in images}


def _visual(rows, name, visuals):
    if visuals and visuals.get(name):
        rows.extend(["", visuals[name], ""])


def counts(generation, validations):
    geometries = [item for item in validations if item.get("metadata", {}).get("composition_ok")]
    admitted = [item for item in validations if item.get("is_valid") is True]
    relaxed = [item for item in admitted if item.get("metadata", {}).get("mattersim", {}).get("relaxation_converged") is True]
    thermo = [item for item in relaxed if isinstance(item.get("energy_above_hull"), (int, float)) and math.isfinite(item["energy_above_hull"])]
    return [len(generation.get("candidates", [])), len(geometries), len(admitted), len(relaxed), len(thermo)]


def generation_records(result):
    metadata = result.generation.metadata
    selected = metadata.get("selected_dng_composition")
    dng = metadata.get("dng_stage")
    records = []
    if dng and selected:
        records.append(("dng", dng["generation"], dng["validations"]))
    mode = metadata.get("mode") or ("csp" if selected else result.constraints.generation_mode)
    records.append((mode, result.generation.to_dict(), [item.to_dict() for item in result.validations]))
    return records


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def output_cell(validation, key, digits=4, reference=.05, unit=""):
    v = validation.to_dict() if hasattr(validation, "to_dict") else validation or {}
    value = (v.get("property_predictions", {}).get(key) or {}).get("value") if key in PROPERTY_LABELS else v.get(key)
    if _finite(value):
        shown = stability_number(value, reference) if key == "energy_above_hull" else number(value, digits)
        return shown + (" " + unit if unit else "")
    return cell("—（" + missing_output_issue(v, key)["title"] + "）")


def _equation(rows, formula, explanation):
    rows.extend(["", "$$", formula, "$$", "", explanation])


def _methods(rows, records, validations):
    generated = [(mode, generation) for mode, generation, _ in records if generation.get("candidates")]
    geometry_checked = any(isinstance(v.get("is_valid"), bool) for v in validations)
    energies = [v for v in validations if _finite(v.get("metadata", {}).get("mattersim", {}).get("relaxed_energy_per_atom_ev"))]
    thermodynamic = [v for v in validations if _finite(v.get("energy_above_hull"))]
    heads = {key: prediction for v in validations for key, prediction in v.get("property_predictions", {}).items() if _finite(prediction.get("value"))}
    rows.extend(["", "### 3. 晶体生成、结构优化与性能评价方法", "", "#### 3.1 采用的模型及其计算内容", ""])
    if not generated and not energies and not heads:
        rows.append("本次尚无可用于比较的计算产物；具体原因及支持的继续方式见结果部分。")
        return
    rows.extend(["| 计算内容 | 使用模型或方法 | 实际输入与输出 |", "| --- | --- | --- |"])
    for mode, generation in generated:
        p = generation.get("metadata", {}).get("model_provenance", {})
        rows.append(f"| {'固定组成构型生成' if mode == 'csp' else '组成与结构联合生成'} | OMatG {cell(p.get('model') or mode.upper())} | 晶格、元素种类与分数坐标；{'固定元素计数，生成晶格与坐标' if mode == 'csp' else '同时生成元素、晶格与坐标'} |")
        if mode == "dng" and p.get("service_conditioning"):
            rows.append("| 多元素组成约束采样 | 服务侧部分元素位点锚定 | 采样开始前设置必含元素与最少元素种数的部分位点，不同候选轮换锚定模板；其余位点由离散流选择，晶格和坐标共同积分 |")
    if geometry_checked:
        rows.append("| 组成与周期几何检查 | pymatgen | 元素计数、有序占位、周期近邻距离；筛除组成冲突及原子过近结构 |")
    if energies:
        rows.append("| 结构松弛与能量 | MatterSim v1.0.0-1M + ASE FIRE / ExpCellFilter | 晶格与原子位置；输出松弛结构、能量及收敛记录 |")
    if thermodynamic:
        rows.append("| 稳定性估算 | MP2020 校正竞争相凸包 | MatterSim 候选能量与 MP/Alexandria 同元素竞争相；输出形成能与凸包能估算 |")
    if energies:
        rows.append("| 近似结构族去重 | StructureMatcher | 松弛后晶体结构；保留结构族代表用于比较 |")
    if heads:
        properties = "、".join(PROPERTY_LABELS.get(key, key) for key in heads)
        versions = "、".join(dict.fromkeys(str(p.get('model_version') or '预训练性质模型') for p in heads.values()))
        rows.append(f"| {cell(properties)}快速预测 | ALIGNN 性质预测模型；{cell(versions)} | 读取候选的结构优化结果；各项输出单位见结果表 |")
    if generated:
        descriptions = []
        for mode, generation in generated:
            p = generation.get("metadata", {}).get("model_provenance", {})
            dataset = {"alex_mp_20": "Alex-MP-20", "mp_20": "MP-20"}.get(p.get("dataset"), p.get("dataset") or ("Alex-MP-20" if mode == "csp" else "MP-20"))
            descriptions.append(f"{mode.upper()} 使用 {cell(dataset)} 预训练权重，架构为 {cell(p.get('architecture') or 'CSPNetFull + stochastic interpolants')}")
        rows.extend(["", "；".join(descriptions) + "。本任务调用预训练模型进行推理采样，性质需求通过生成后的评价、筛选和比较落实。"])
    rows.extend(["", "#### 3.2 晶体结构的表示与生成方法", "", "下表解释模型读取和生成哪些结构信息；描述符是将这些信息转换为模型可计算的特征。", "", "| 结构信息或模型特征 | 在本次计算中的作用 |", "| --- | --- |"])
    if generated:
        rows.append("| 晶格、原子种类与原子位置 | 晶格描述重复单元的形状和大小，分数坐标描述原子在单元内的位置；二者组成 CIF 晶体结构文件 |")
    if geometry_checked:
        rows.append("| 周期近邻距离与共价半径和 | 考虑相邻重复单元中的原子，检查原子是否过近；共价半径提供元素大小的参考尺度 |")
    if thermodynamic:
        rows.append("| 元素原子分数与校正能量 | 与同一化学空间内的竞争相凸包比较 |")
    if heads:
        rows.append("| 原子邻接图与键角线图 | ALIGNN 将原子、近邻连线和连线夹角编码为特征；近邻距离上限为 8 Å，每个原子最多取 12 个近邻 |")
    if generated:
        modes = {mode for mode, _ in generated}
        species_method = "CSP 固定元素计数；DNG 同时生成组成与结构。" if modes == {"csp", "dng"} else "CSP 固定元素计数，探索晶格与原子排布。" if modes == {"csp"} else "DNG 同时生成元素种类、晶格与原子排布。"
        _equation(rows, r"S=(\mathbf L,\mathbf s,\mathbf f),\qquad \mathbf r_i=\mathbf L^{\mathsf T}\mathbf f_i",
            "该式概括晶体的完整结构表示。$S$ 为周期晶体，$\\mathbf L$ 的行是三条晶格向量，$\\mathbf s$ 为各原子位置上的元素，$\\mathbf f_i$ 为第 $i$ 个原子相对晶胞的分数坐标，$\\mathbf r_i$ 为换算后的实际空间坐标。" + species_method)
        _equation(rows, r"\mathbf x_t=\alpha(t)\mathbf x_0+\beta(t)\mathbf x_1+\gamma(t)\boldsymbol\epsilon",
            "该式表示生成模型采用的随机插值关系。$t$ 表示生成进度，$\\mathbf x_t$ 表示该时刻的结构状态；$\\mathbf x_0$ 为起始噪声，$\\mathbf x_1$ 为训练时的目标结构，$\\boldsymbol\\epsilon$ 为附加噪声，$\\alpha(t)、\\beta(t)、\\gamma(t)$ 为随进度变化的权重函数。推理时，预训练网络逐步更新晶格和坐标。" + ("DNG 的元素种类另由离散流模型生成；限定元素时，服务通过采样掩码限制允许元素。" if "dng" in modes else ""))
        if any(generation.get("metadata", {}).get("model_provenance", {}).get("service_conditioning") for mode, generation in generated if mode == "dng"):
            _equation(rows, r"s_i(t)=a_i\quad (i\in\mathcal A),\qquad \left|\{s_i(1)\}_{i=1}^{N}\right|\ge K_{\min}",
                "$s_i(t)$ 表示生成进度 $t$ 时第 $i$ 个位置上的元素；$\\mathcal A$ 是采样前选定的部分元素锚定位点集合，$a_i$ 是该位置指定的元素。$N$ 是晶胞原子总数，$K_{\\min}$ 是要求的最少元素种数，集合大小表示最终实际出现的不同元素数量。服务在采样时保持锚定位点的元素身份，其余元素由模型生成；随后依次检查实际组成、原子排布、结构优化与稳定性。")
    rows.extend(["", "#### 3.3 原子排布检查、结构优化与稳定性计算", ""])
    if not geometry_checked and not energies:
        rows.append("本次记录没有结构检查与优化的完成结果，具体停止阶段、原因和处理方式见第 4 节。")
    if geometry_checked:
        _equation(rows, r"d_{ij}=\min_{\mathbf n\in\mathbb Z^3}\left\|\mathbf L^{\mathsf T}(\mathbf f_i-\mathbf f_j+\mathbf n)\right\|_2,\qquad d_{ij}\ge0.75(r_i^{\rm cov}+r_j^{\rm cov})",
            "$d_{ij}$ 是第 $i、j$ 个原子考虑周期重复后最短的距离；$\\mathbf n$ 是整数周期平移，$\\mathbf L$ 和 $\\mathbf f$ 与上式一致，$r_i^{\\rm cov}、r_j^{\\rm cov}$ 为两种元素的共价半径。$\\|\\cdot\\|_2$ 表示距离。排除原子与自身在同一位置的比较，但保留相邻晶胞中的周期镜像；距离至少为共价半径和的 0.75 倍，是本次剔除原子过近结构的检查条件。")
    if energies:
        _equation(rows, r"\mathbf F_i=-\nabla_{\mathbf r_i}E_\theta(S),\qquad S^*\approx\operatorname*{arg\,min}_{\mathbf L,\mathbf f}E_\theta(S)",
            "结构优化也称松弛：保持元素计数，调整原子位置和晶胞，使能量在局部降低。$E_\\theta(S)$ 是固定参数 $\\theta$ 的 MatterSim 对结构 $S$ 预测的总能量，$\\mathbf F_i$ 为第 $i$ 个原子的力，$\\nabla$ 表示能量对位置的变化率；$S^*$ 为优化后的局部低能结构。ASE FIRE 更新原子坐标，ExpCellFilter 同时调整晶胞；缩放后的晶胞项与原子力共同进入收敛判断。" + ("各项性质通过上表中的对应模型预测和比较。" if heads else ""))
    if thermodynamic:
        _equation(rows, r"\Delta E_f=\frac{E_{\rm cand}^{\rm corr}-\sum_\alpha n_\alpha\mu_\alpha^{\rm ref}}{N},\qquad E_{\rm hull}^{\rm est}=\max\!\left(0,\frac{E_{\rm cand}^{\rm corr}}{N}-e_{\rm hull}^{\rm ref}(\mathbf x)\right)",
            "$\\Delta E_f$ 为相对元素参考态的单原子形成能估算；$E_{\\rm hull}^{\\rm est}$ 是报告中的稳定性差值，即相对同组成的其他相组合多出的能量，越低越有利。$n_\\alpha$ 为元素 $\\alpha$ 的原子数，$N=\\sum_\\alpha n_\\alpha$ 为总原子数，$E_{\\rm cand}^{\\rm corr}$ 为校正后的候选总能量，$\\mu_\\alpha^{\\rm ref}$ 为元素参考能，$\\mathbf x$ 为元素原子分数，$e_{\\rm hull}^{\\rm ref}(\\mathbf x)$ 为同组成竞争相组合的单原子最低参考能。候选能量采用 MatterSim，参考相能量来自 MP/Alexandria；该 D 级混合基准估算用于初步比较，后续通过统一精度的计算复核。有限温度下的稳定性可进一步加入熵和振动贡献。")
    rows.extend(["", "#### 3.4 候选结构的比较与优选规则", "", "先核对组成、原子排布和结构优化收敛，再检查已明确的性质与对称性条件。稳定性差值越低越有利；高于参考线的候选继续完成性质评价并标注稳定性待复核。明确设置硬性上限时，超限记为未满足条件。相似结构保留一个代表；缺少必需结果时，逐项说明停止阶段与原因。", "", "通过上述条件后，按稳定性差值从低到高确定优先验证顺序，各项预测性能在结果表中并列比较。"])


def _parameters(rows, result, records):
    c = result.constraints
    selected = result.generation.metadata.get("selected_dng_composition")
    route = "DNG → 固定所选组成的 CSP" if selected else "固定组成 CSP" if c.generation_mode == "csp" else "限定元素 DNG" if c.allowed_elements else "开放化学空间 DNG"
    rows.extend(["", "### 2. 材料组成范围与计算条件", "", "#### 2.1 元素组成与计算晶胞设置", "",
        "晶胞是晶体结构的周期重复单元。下表列出本次使用的元素范围和晶胞原子数，实际组成按生成结构中的原子计数核对。", "",
        "| 设置项目 | 本次设置 | 如何理解 |", "| --- | --- | --- |"])
    allowed_meaning = "本次由固定化学式确定全部元素及其数量" if c.target_formula else "候选从所列元素中选择组成" if c.allowed_elements else "在已确认的开放元素空间中探索组成"
    required_text = "按固定化学式全部保留" if c.target_formula else _setting(c, 'required_elements', '、'.join(element_label(e) for e in c.required_elements) or '未设置额外必含元素')
    rows.extend([
        f"| 生成方式 | {route} | CSP 固定元素计数，探索不同排布；DNG 同时探索组成与排布 |",
        f"| 允许使用的元素 | {_setting(c, 'allowed_elements', '、'.join(element_label(e) for e in c.allowed_elements) or domain_text(c))} | {allowed_meaning} |"])
    if c.target_formula or c.required_elements:
        rows.append(f"| 每个候选必须包含的元素 | {required_text} | 逐项核对实际组成；固定化学式按其中全部元素计数 |")
    if c.excluded_elements:
        rows.append(f"| 排除的元素 | {cell('、'.join(element_label(e) for e in c.excluded_elements))} | 生成和组成检查均按此排除条件执行 |")
    if c.target_formula:
        rows.extend([f"| 固定化学式 | {_setting(c, 'target_formula', c.target_formula)} | 元素后的数字表示原子个数比；按该计量关系探索不同排布 |",
            f"| 每胞化学式单位数 Z | {_setting(c, 'formula_units', c.formula_units)} | 一个计算晶胞包含上述化学式的份数 |"])
    rows.append(f"| 每个计算晶胞的原子数 | {_setting(c, 'num_atoms', c.num_atoms)} | 本次用于生成结构的周期重复单元大小；当前模型每胞最多 20 原子 |")
    if c.generation_mode == "dng":
        rows.append(f"| 每个候选至少包含的元素种数 | {_setting(c, 'min_distinct_elements', c.min_distinct_elements)} | 按每个候选中实际出现的不同元素计数 |")
    if selected:
        rows.append(f"| 追加固定组成探索的化学式 | {cell(selected['formula'])}；Z=1 | 保留所选 DNG 结构的完整晶胞原子计数，用于下一步固定组成探索 |")
    rows.extend(["", "#### 2.2 候选数量与性质评价条件", "", "候选数量决定本次探索规模；已确认的上下限用于逐项判断，参考目标用于比较表现和安排复核。", "",
        "| 评价或计算项目 | 本次设置 | 如何理解 |", "| --- | --- | --- |",
        f"| 每阶段生成的候选数量 | {_setting(c, 'max_candidates', str(c.requested_candidate_count) + ' 个')} | 本次计划生成的结构数量；实际完成情况见第 4 节 |",
        f"| 稳定性差值{'硬性上限' if hull_is_hard(c) else '复核参考线'} | {_setting(c, 'energy_above_hull', f'{hull_limit(c):g} eV/atom')} | {'用于判断稳定性是否满足已确认上限；超限候选在结果中单独列出' if hull_is_hard(c) else '高于参考线的候选继续完成性质评价，并优先安排稳定性复核'} |"])
    for key, limits in c.property_constraints.items():
        rows.append(f"| {cell(PROPERTY_LABELS.get(key, key))}必须满足的范围 | {cell(bounds_text(key, limits))} | 逐项核对预测值与上下限；结果缺失时说明原因，该项要求保留为尚无法判断 |")
    for key, value in c.target_properties.items():
        if key != "energy_above_hull":
            rows.append(f"| {cell(PROPERTY_LABELS.get(key, key))}参考目标 | {value:g} {PROPERTY_UNITS.get(key, '')} | 用于结果比较；必须满足的要求以上下限为准 |")
    for key, value in c.symmetry_constraints.items():
        rendered = "是" if value is True else "否" if value is False else str(value)
        meaning = "检查晶体的中心反演对称性" if key == "non_centrosymmetric" else "编号对应晶体的空间对称性类型"
        rows.append(f"| {SYMMETRY_LABELS[key]}要求 | {rendered} | {meaning}；以结构优化后的分析结果判断 |")
    # Reproduction settings stay in the manifest rather than the customer table.
    settings = result.generation.metadata.get("calculation_settings", {})
    thermo = next((v.get("metadata", {}).get("mattersim", {}) for _, _, values in records for v in values if v.get("metadata", {}).get("mattersim", {}).get("fmax_ev_angstrom") is not None), {})
    has_relaxation = any(v.get("metadata", {}).get("mattersim") for _, _, values in records for v in values)
    if thermo or has_relaxation:
        rows.extend(["", "#### 2.3 结构优化与相似结构处理", "", "结构优化调整原子位置和晶胞，收敛条件决定何时结束；相近的结果合并为结构组，保留代表结构供比较。", "",
            "| 计算项目 | 本次设置 | 作用 |", "| --- | --- | --- |"])
    if thermo:
        rows.append(f"| 结构优化收敛阈值 | {thermo['fmax_ev_angstrom']} eV/Å | 原子力与缩放后的晶胞项共同用于判断是否达到优化停止条件 |")
    if thermo and settings.get("relax_max_steps") is not None:
        rows.append(f"| 结构优化步数上限 | 最多 {settings['relax_max_steps']} 步 | 达到收敛条件后提前结束；用满步数的候选逐项记录收敛状态 |")
    if has_relaxation:
        rows.append("| 相似结构处理 | 合并为结构组，保留代表结构 | 根据元素组成和优化后的原子排列比较结构，减少重复展示 |")


def _state_text(validation, names):
    issues = candidate_issues(validation)
    if validation is None:
        return cell(issues[0]["title"] + "：" + issues[0]["cause"])
    state = validation.metadata.get("screening", {})
    label = {"passed": "可继续比较", "failed": "未满足硬性条件", "incomplete": "评价结果不完整"}.get(state.get("state"), "评价结论未记录")
    if issues:
        label = issues[0]["title"] + "；" + issues[0]["effect"]
    duplicate = validation.metadata.get("duplicate_of")
    if duplicate and state.get("state") != "failed" and not issues:
        label = "相似结构已合并"

    def reason_text(reason):
        text = PROPERTY_LABELS.get(reason, SYMMETRY_LABELS.get(reason, reason))
        for key, name in {**PROPERTY_LABELS, **SYMMETRY_LABELS}.items():
            text = str(text).replace(key, name)
        return str(text).replace("凸包能超过上限", "稳定性差值超过上限").replace("凸包能估算", "稳定性估计").replace("松弛收敛", "结构优化收敛")

    failures = state.get("failures", []) or (validation.errors if validation.is_valid is False else [])
    if failures:
        label = label.rstrip("。") + ("；具体原因：" if issues else "：") + "；".join(reason_text(r).rstrip("。") for r in failures)
    missing = [r for r in state.get("missing", []) if r not in {"松弛收敛", "凸包能估算"}]
    if missing and validation.is_valid:
        label += "；尚无法判断的要求：" + "、".join(reason_text(r) for r in missing)
    if state.get("stability_review"):
        label += "；稳定性高于参考线，需高精度复核"
    if duplicate:
        label += "；与 " + names.get(duplicate, "已有候选") + " 属于同一近似结构，由该组代表结构参与比较"
    return cell(label)


def build_result_report(result, visuals=None):
    """Five aligned customer sections; display labels never change the evidence."""
    c = result.constraints
    records = generation_records(result)
    selected = result.generation.metadata.get("selected_dng_composition")
    all_validations = [v for _, _, validations in records for v in validations]
    display_items = [(item.candidate, item.validation) for item in result.ranked_candidates]
    ranked_ids = {candidate.candidate_id for candidate, _ in display_items}
    validations_by_id = {v.candidate_id: v for v in result.validations}
    display_items.extend((candidate, validations_by_id.get(candidate.candidate_id)) for candidate in result.generation.candidates if candidate.candidate_id not in ranked_ids)
    names = scheme_names((candidate.candidate_id for candidate, _ in display_items), result.taskid)
    groups = [item for item in result.ranked_candidates if item.validation and item.validation.is_valid and item.validation.metadata.get("mattersim", {}).get("relaxation_converged") is True and not item.validation.metadata.get("duplicate_of")]
    passing = [item for item in groups if item.validation.metadata.get("screening", {}).get("state") == "passed"]
    primary = primary_structure(result.to_dict())
    top = next((item for item in result.ranked_candidates if primary and item.candidate.candidate_id == primary["candidate"]["candidate_id"]), None)
    rows = [f"## {'固定化学式晶体结构探索' if c.generation_mode == 'csp' else '组成与晶体结构发现'}", ""]
    if passing:
        review_count = sum(bool(item.validation.metadata.get("screening", {}).get("stability_review")) for item in passing)
        review_note = f"其中 {review_count} 个稳定性需要复核。" if review_count else ""
        rows.extend([f"本次结果：生成 {len(result.generation.candidates)} 个候选结构，{len(passing)} 个独立结构可继续比较。{review_note}优先验证{formula_label(top.candidate.formula_pretty)}，编号 {names[top.candidate.candidate_id]}；优选依据为满足组成和性能条件后，稳定性差值较低。", ""])
    else:
        rows.extend([evaluation_completion_text(result.generation.candidates, result.validations), "",
                     "后续按各候选的检查和计算状态继续验证，具体项目见第4节。", ""])
    rows.extend([
        "### 1. 材料设计需求与探索目标", "", *problem_description(c), "",
        "下表将材料需求对应到本次关注的性能和结构要求，作为后续候选比较的依据。", "",
        "| 关注性能或结构要求 | 本次比较或筛选要求 | 如何理解 |", "| --- | --- | --- |",
        f"| 热力学稳定性 | 稳定性差值越低越有利；{hull_policy(c)} | 表示相对同组成的其他相组合多出的能量，即凸包能估算，用于比较结构的稳定性 |"])
    for key in sorted(task_goals(c)):
        if key in PROPERTY_LABELS:
            purpose = bounds_text(key, c.property_constraints[key]) if key in c.property_constraints else f"比较参考目标 {c.target_properties[key]:g} {PROPERTY_UNITS[key]}" if key in c.target_properties else "快速预测与候选比较" if key in c.validation_targets else "上文关注点；有预测时比较，不增加数值筛选门槛"
            rows.append(f"| {PROPERTY_LABELS[key]} | {cell(purpose)}；{goal_purpose(c, key)} | {PROPERTY_MEANINGS[key]} |")
    for key, value in c.symmetry_constraints.items():
        rendered = "是" if value is True else "否" if value is False else str(value)
        meaning = "检查结构的中心反演对称性" if key == "non_centrosymmetric" else "晶体空间对称性分类的编号"
        rows.append(f"| {SYMMETRY_LABELS[key]} | 结构优化后检查：{rendered} | {meaning} |")
    other_goals = [key for key in c.validation_targets if key not in PROPERTY_LABELS]
    if other_goals:
        rows.extend(["", "后续专项验证项目：" + "、".join(cell(FOLLOW_UP_LABELS.get(key, key)) for key in other_goals) + "。"])
    _parameters(rows, result, records)
    _methods(rows, records, all_validations)
    if selected:
        rows.extend(["", f"本次按已确认的追加步骤，将 DNG 选定组成 {cell(selected.get('display_formula') or selected['formula'])} 的完整原子计数固定后再进行 CSP。选取依据为{cell(selected['criterion'])}，原 DNG 结构单独保留。"])
    rows.extend(["", "### 4. 晶体生成结果与候选结构比较", "", "#### 4.1 候选生成与评价完成情况", "",
        "各列从左到右记录逐步完成的数量：生成结构后，依次核对组成、检查原子排布、优化结构并计算稳定性。CSP 探索固定组成的构型，DNG 联合探索组成与结构。"])
    elapsed = result.generation.metadata.get("scientific_wall_seconds")
    if _finite(elapsed):
        rows.extend(["", f"本次结构生成、结构优化与性质评价用时 {number(elapsed, 1)} 秒。"])
    rows.extend(["", "| 生成阶段 | 生成候选数 | 元素组成符合要求 | 原子排布检查通过 | 结构优化收敛 | 稳定性已有结果 |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for mode, generation, validations in records:
        rows.append(f"| {mode.upper()} | " + " | ".join(str(value) for value in counts(generation, validations)) + " |")
    for mode, generation, _ in records:
        if mode == "dng":
            from .omatg_sampling import composition_summary
            diversity = composition_summary(generation.get("candidates", []))
            # Invalid candidates stay visible; diversity is not proof of novelty.
            rows.extend(["", f"DNG 组成多样性：{diversity['multi_element_count']} 个多元素候选、{diversity['unary_count']} 个单质候选，约分后 {diversity['distinct_composition_count']} 种不同组成。组成种类、不同结构数量和材料新颖性分别判断。", ""])
    review_count = sum(bool(item.validation.metadata.get("screening", {}).get("stability_review")) for item in passing)
    rows.extend(["", f"最终保留 {len(groups)} 个收敛的近似结构代表，其中 {len(passing)} 个可继续比较。" + (f"可比较结构中 {review_count} 个高于稳定性参考线，需优先进行高精度复核。" if review_count else "") + "相似候选合并为结构组，以代表结构参与比较。",
        "", "#### 4.2 候选晶体的稳定性与性能比较", "",
        "候选编号在本次任务中唯一，对应正文、表格和配套图中的同一结构。下表保留全部候选，空缺数值旁标明原因；具体停止阶段和后续处理在表后说明。"])
    requested = [key for key in PROPERTY_LABELS if key in task_goals(c)]
    comparison_keys = requested + [key for key in ("band_gap", "bulk_modulus", "shear_modulus") if key not in requested and len(requested) <= 2 and any(v and _finite(v.property_predictions.get(key, {}).get("value")) for _, v in display_items)]
    rows.extend(["", "| 候选结构编号 | 材料组成 | 稳定性差值（eV/atom） | " + " | ".join([f"{PROPERTY_LABELS[k]}（{PROPERTY_UNITS[k]}）" for k in comparison_keys] + ["评价结论及复核事项"]) + " |",
        "| --- | --- | ---: | " + " | ".join(["---:" for _ in comparison_keys] + ["---"]) + " |"])
    for candidate, v in display_items:
        formula = candidate.formula_pretty or (v.formula_pretty if v else None)
        label = names[candidate.candidate_id] + (("（优选结构）" if passing else "（待复核结构）") if top and candidate.candidate_id == top.candidate.candidate_id else "")
        fields = [label, formula_label(formula, candidate.metadata.get("element_counts")), output_cell(v, "energy_above_hull", reference=hull_limit(c))]
        fields += [output_cell(v, k) for k in comparison_keys]
        rows.append("| " + " | ".join(fields + [_state_text(v, names)]) + " |")
    if not display_items:
        rows.append("| " + " | ".join(["—", "—", "—（没有候选输入）"] + ["—（没有候选输入）" for _ in comparison_keys] + [generation_issue(result.generation, result.message)["title"]]) + " |")
    rows.extend(["", "数值为本次计算结果，显示时舍入，比较及判定仍使用完整精度。eV/atom 表示平均到每个原子的能量单位。"])
    if comparison_keys:
        rows.extend(["", "本表中的性能含义：" + "；".join(PROPERTY_LABELS[key] + "——" + PROPERTY_MEANINGS[key].rstrip("。") for key in comparison_keys) + "。"])
    if any(v and _finite(v.property_predictions.get('band_gap', {}).get('value')) and v.property_predictions['band_gap']['value'] < 0 for _, v in display_items):
        rows.extend(["", "表中的负带隙为回归模型超出物理范围的原始预测，需要通过电子结构计算复核。"])
    details = [(names[candidate.candidate_id], item) for candidate, v in display_items for item in candidate_issues(v, comparison_keys)]
    for candidate, v in display_items:
        if not v or v.is_valid is not True:
            continue
        failures = v.metadata.get("screening", {}).get("failures", [])
        if failures:
            cause = "；".join(readable_reason(reason) for reason in failures)
            for key, bounds in c.property_constraints.items():
                value = v.property_predictions.get(key, {}).get("value")
                if _finite(value) and any(key in failure for failure in failures):
                    cause += f"；{PROPERTY_LABELS.get(key, key)}预测 {value:g} {PROPERTY_UNITS.get(key, '')}，要求 {bounds_text(key, bounds)}"
            details.append((names[candidate.candidate_id], dict(stage="性能与结构条件核对", cause=cause,
                effect="该结构的已完成结果保留，当前不作为满足全部条件的推荐。",
                action="按原条件追加候选探索；调整约束时先确认新的性能或结构要求。")))
    historical_hard = any(v and "凸包能超过上限" in v.metadata.get("screening", {}).get("failures", []) for _, v in display_items) and not hull_is_hard(c)
    if historical_hard:
        rows.extend(["", "历史计算口径：本预览沿用保存记录的评价结论，该批次曾按稳定性上限标记超限候选。当前默认采用复核参考线，超参考线仍继续评价；按新规则重算评价时应另存结果。"])
    if not display_items:
        details.append(("本次任务", generation_issue(result.generation, result.message)))
    if details:
        rows.extend(["", "**结果缺失的原因与后续处理**", "", "本轮计算已经结束。以下处理需要另行安排计算；已完成的数值仍保留在上表中。", "",
                     "| 候选结构编号 | 对应阶段 | 本次实际情况 | 对后续评价的影响 | 建议处理 |", "| --- | --- | --- | --- | --- |"])
        for label, item in details:
            rows.append("| " + " | ".join(cell(value) for value in (label, item["stage"], item["cause"], item["effect"], item["action"])) + " |")
    if c.refine_with_csp and not selected:
        rows.extend(["", "追加 CSP 的执行情况：本次 DNG 没有形成组成合规、结构收敛且已有稳定性结果的选择起点，因此未启动追加 CSP。先处理上述 DNG 评价问题，再选定组成追加固定计量探索。"])
    presentation = result.artifacts.get("presentation") or {}
    if presentation.get("status") in {"unavailable", "failed", "timeout"}:
        reason = "可视化渲染超过本次时限" if "tim" in str(presentation.get("message", "")).lower() or presentation.get("status") == "timeout" else "可视化资产未成功生成，具体原因需核对渲染日志"
        reason = {"environment_unavailable": "可视化运行环境无法启动", "invalid_manifest": "可视化结果文件无法读取"}.get(presentation.get("error_code"), reason)
        rows.extend(["", "图片与三维预览：" + reason + "。已完成的结构和性能数值见上表，可在核对渲染环境后重新生成图片。"])
    delivery_reasons = {"source_missing": "源文件缺失，需重新生成对应预览", "upload_rejected": "资产上传未成功，需检查存储服务后重新发送",
                        "verification_error": "上传后的可访问性检查报错，需核对存储连接", "storage_absent": "上传后未找到对应对象，需核对存储写入并重新发送",
                        "delivery_error": "资产发送发生异常，需核对交付日志后重新发送"}
    for delivery in presentation.get("delivery_status", []):
        rows.extend(["", cell(delivery.get("name", "结构预览")) + "：" + delivery_reasons.get(delivery.get("code"), "交付原因未记录，需核对该任务的资产日志") + "。已完成的计算数值仍保留。"])
    if presentation.get("glb_error_code") == "export_failed":
        rows.extend(["", "三维交互模型：格式转换未成功。已保存晶体结构和成功生成的旋转预览仍可使用；请核对格式转换日志，再重新生成三维模型。"])
    if visuals and visuals.get("candidate_comparison"):
        rows.extend(["", "#### 4.3 候选结构的评价过程与稳定性比较", "",
            "图上方记录各阶段完成的数量；左侧说明其余候选需要调整、补算或合并的原因。右侧条形对应上表中的候选编号，稳定性差值越低越有利，虚线为本次参考线或硬性上限。"])
        _visual(rows, "candidate_comparison", visuals)
    if passing:
        rows.extend(["", "### 5. 优选晶体的组成、结构、预测性能与验证建议", "",
            f"优选结构（{names[top.candidate.candidate_id]}）为 {formula_label(top.candidate.formula_pretty)}，稳定性差值为 {stability_number(top.validation.energy_above_hull, hull_limit(c))} eV/atom。以下汇总该结构的组成、结构和性能。" + ("其稳定性差值高于本次参考线，建议优先通过统一精度计算复核。" if top.validation.metadata.get('screening', {}).get('stability_review') else "")])
    else:
        rows.extend(["", "### 5. 候选晶体的复核与后续验证建议", "", "请按第4节列出的条件和计算状态继续处理候选。已完成数量以第 4.1 节的实际记录为准。", "",
            "第 4.2 节逐项列出未满足的条件、结果缺失原因和对应处理方式。"])
        if top:
            rows.extend(["", f"以下汇总待复核结构（{names[top.candidate.candidate_id]}）的已完成数据；结果空缺及其原因见第 4.2 节。"])
    if top:
        v = top.validation
        thermo = v.metadata["mattersim"]
        element_counts = v.metadata.get("element_counts") or top.candidate.metadata.get("element_counts", {})
        total = sum(element_counts.values())
        rows.extend(["", f"#### 5.1 {'优选结构' if passing else '待复核结构'}（{names[top.candidate.candidate_id]}）的元素组成与结构信息", "",
            "以下组成来自同一保留结构的晶胞原子计数。at.% 是按原子个数计算的百分比，用于记录晶体组成；实际投料时可结合各元素原子质量换算质量比例。", "",
            "| 组成元素 | 晶胞中的原子数 | 原子百分比（at.%） |", "| --- | ---: | ---: |"])
        for symbol, amount in sorted(element_counts.items()):
            rows.append(f"| {element_label(symbol)} | {amount} | {number(amount / total * 100, 4) if total else '—（原子总数未记录）'} |")
        if not element_counts:
            rows.append("| —（记录缺少元素计数） | —（需要从 CIF 提取） | —（缺少原子计数） |")
        _equation(rows, r"x_\alpha=\frac{n_\alpha}{\sum_\beta n_\beta}\times100\%",
            "$n_\\alpha$ 为元素 $\\alpha$ 在晶胞中的原子数，$\\sum_\\beta n_\\beta$ 为所有元素的总原子数，$x_\\alpha$ 为原子百分比；完整组成合计为 100%，表中显示值经过舍入。")
        rows.extend(["", "| 晶体结构项目 | 本次结果 | 如何理解 |", "| --- | --- | --- |",
            f"| 化学式 | {cell(top.candidate.formula_pretty or v.formula_pretty)} | 表示元素计量关系；完整晶胞的实际计数见上表 |",
            f"| 空间群 | {cell(v.space_group or '—（对称性结果未记录，需从优化后 CIF 重新分析）')}（编号 {v.metadata.get('space_group_number', '结果未记录')}） | 晶体空间对称性的分类；本次采用优化后的结构进行分析 |",
            f"| 是否非中心对称 | {'是' if v.metadata.get('non_centrosymmetric') is True else '否' if v.metadata.get('non_centrosymmetric') is False else '—（对称性结果未记录，需重新分析 CIF）'} | 说明晶体是否缺少中心反演对称性，用于比较结构特征 |",
            f"| 计算晶胞的原子总数 | {v.metadata.get('num_sites', total or '—（原子计数未记录）')} | 该周期重复单元包含的原子个数 |",
            f"| 晶胞体积 | {number(v.metadata.get('volume_angstrom3'), 4) + ' Å³' if _finite(v.metadata.get('volume_angstrom3')) else '—（晶胞体积未记录，需从 CIF 提取）'} | 结构优化后输出晶胞的体积；Å 是埃，1 Å = 10⁻¹⁰ m |"])
        lattice = v.metadata.get("lattice_parameters", [])
        if len(lattice) == 6:
            rows.extend([f"| 三条晶格向量的长度 a、b、c | {' / '.join(number(value, 4) for value in lattice[:3])} Å | 描述输出晶胞的三个边长 |",
                f"| 晶格向量之间的夹角 α、β、γ | {' / '.join(number(value, 4) for value in lattice[3:])}° | 分别为 b 与 c、a 与 c、a 与 b 的夹角 |"])
        force, threshold = thermo.get('max_generalized_force_ev_angstrom'), thermo.get('fmax_ev_angstrom')
        convergence = f"已收敛；广义力指标 {number(force)} eV/Å；阈值 {threshold:g} eV/Å" if _finite(force) and _finite(threshold) else "记录结论为已收敛；力指标或阈值未记录，需核对原始优化结果"
        rows.append(f"| 结构优化收敛情况 | {convergence} | 原子力和缩放后的晶胞项用于核对本次优化停止条件 |")
        rows.extend(["", "完整原子坐标、元素占位和晶格保留在对应的 CIF 晶体结构文件中；三维模型与旋转预览展示原子排布，连线标示相邻原子关系。", "",
            ])
        if visuals and visuals.get("candidate_rotation"):
            rows.append(f"{'优选结构' if passing else '待复核结构'}（{names[top.candidate.candidate_id]}）的旋转预览：颜色区分元素，连线帮助阅读相邻原子的排布。")
            _visual(rows, "candidate_rotation", visuals)
        rows.extend([
            f"#### 5.2 {'优选结构' if passing else '待复核结构'}（{names[top.candidate.candidate_id]}）的预测性能及其含义", "",
            "以下数值均对应上述结构优化后的同一个晶体。能量为第 3 节所列模型预测，稳定性采用混合参考能量估算；结构派生量与性质模型预测分别解释。", "",
            "| 预测或结构派生性能 | 本次结果 | 如何理解 |", "| --- | --- | --- |",
            f"| 密度 | {output_cell(v, 'density', unit='g/cm³')} | 由晶胞体积和原子质量计算得到的单位体积质量；缺少时需从 CIF 重新提取晶胞与组成 |",
            f"| 单原子能量 | {number(thermo.get('relaxed_energy_per_atom_ev')) + ' eV/atom' if _finite(thermo.get('relaxed_energy_per_atom_ev')) else '—（优化后的能量未记录，需核对优化结果文件）'} | 总能量平均到每个原子，可用于比较同一组成下的不同构型 |",
            f"| 形成能估算 | {output_cell(v, 'formation_energy_per_atom', digits=5, unit='eV/atom')} | 相对元素参考态的能量变化；空缺时需核对参考相与能量计算记录 |",
            f"| 稳定性差值（凸包能估算） | {output_cell(v, 'energy_above_hull', reference=hull_limit(c), unit='eV/atom')} | 相对同组成竞争相组合多出的能量，用于确定稳定性复核优先级；具体空缺原因见第 4.2 节 |"])
        for key, p in v.property_predictions.items():
            if _finite(p.get("value")):
                rows.append(f"| {cell(PROPERTY_LABELS.get(key, p.get('label', key)))} | {number(p['value'], 4)} {cell(p.get('unit', PROPERTY_UNITS.get(key, '')))} | {PROPERTY_MEANINGS.get(key, '模型预测值，需结合对应计算条件复核。')} |")
        missing_properties = [key for key in task_goals(c) if key in PROPERTY_LABELS and not _finite(v.property_predictions.get(key, {}).get("value"))]
        for key in sorted(missing_properties):
            detail = missing_output_issue(v, key)
            rows.append(f"| {PROPERTY_LABELS[key]} | {output_cell(v, key)} | {cell(detail['cause'] + detail['action'])} |")
        gap = v.property_predictions.get("band_gap", {}).get("value")
        if _finite(gap) and gap <= 0.05:
            rows.extend(["", "带隙预测接近零，优先按金属或半金属候选复核；电子与空穴有效质量需结合进一步的能带计算解释。"])
    else:
        rows.extend(["", "#### 5.1 继续计算前需要复核的事项", "", "本次尚无可汇总完整结构与性能的收敛候选。请按第 4.2 节给出的停止阶段、原因和处理方式恢复计算或重新生成候选。", "",
            "| 支持的任务 | 能够提供的输出 |", "| --- | --- |",
            "| 给定整数化学式的周期晶体（CSP） | 固定元素计数的构型、CIF、结构优化与稳定性初筛 |",
            "| 给定元素集合的组成与结构探索（DNG） | 允许元素内的完整组成与晶体结构，经结构优化后直接比较 |",
            "| 明确确认开放化学空间的探索 | DNG 组成与结构，以及实际完成的稳定性和性质评价 |",
            "| 晶体结构性质快速预测 | 实际完成的带隙、体积模量、剪切模量及电子与空穴有效质量 |", "",
            "合金配比优化由1111处理；有机分子、聚合物、非晶及工程服役性能需要对应专项流程。"])
    if selected and top and _finite(top.validation.energy_above_hull) and _finite(selected.get("energy_above_hull")):
        rows.extend(["", f"原 DNG 结构与当前 CSP 结构的稳定性差值分别为 {number(selected['energy_above_hull'])}、{number(top.validation.energy_above_hull)} eV/atom，采用相同参考口径。" + ("本次追加 CSP 在该指标上未优于原 DNG 结构，两者均保留供比较。" if top.validation.energy_above_hull > selected['energy_above_hull'] else "这一比较仅反映此次采样结果。")])
    rows.extend(["", (f"#### 5.3 优选结构（{names[top.candidate.candidate_id]}）的进一步验证事项" if passing else "#### 5.3 候选结构的进一步验证事项") if top else "#### 5.2 获得合规候选后的验证步骤", "",
        "对候选及相关竞争相开展统一计算口径的密度泛函理论（DFT）复算，以更高精度核对能量及电子结构；进行声子计算，检查原子振动是否提示结构不稳定；再按实际目标验证性能、合成可行性与材料新颖性。", "",
        ("针对" + cell(c.task_context.get('scenario_label') or c.task_context.get('scenario')) + "，进一步在对应温度、载荷和制造状态下开展专项计算或实验，验证候选材料与实际部件的适配性。") if c.task_context.get('scenario') or c.task_context.get('scenario_label') else "结合后续确定的应用场景，在对应温度和制造状态下验证目标性能与合成条件。"])
    return "\n".join(rows)


def build_missing_input_report(payload, reason):
    current = cell(str(payload.get("idea") or payload.get("instruction") or "")[:240])
    return "\n".join([
        "### 当前需求与可继续的方向", "",
        f"当前请求：{current or '尚无明确生成对象'}。", "",
        "材料类别、用途和性能方向可以帮助确定筛选目标，但还需要确认本次生成的化学空间。" + cell(reason), "",
        "#### 支持的任务与输出", "", "| 支持任务 | 可输出 |", "| --- | --- |",
        "| 给定整数化学式的周期无机晶体（CSP） | 固定元素计数的候选结构、CIF、松弛及稳定性初筛 |",
        "| 给定元素集合的组成/结构探索（DNG） | 白名单内的组成与完整晶体结构，经松弛后直接比较 |",
        "| 用户明确同意的开放化学空间探索 | DNG 组成与结构、已完成的稳定性及性质后筛结果 |",
        "| 晶体结构性质快速预测 | 实际完成的带隙、体积/剪切模量、电子/空穴有效质量 |", "",
        "给出化学式可走 CSP；给出元素集合可走限定域 DNG，并区分允许与必含元素。若只有材料大类或性质方向，请确认是否允许开放化学空间；DNG 已同时生成结构，选定组成后可再追加固定计量探索。", "",
        "当前检查点每胞最多20原子，每阶段默认10个候选。合金配比优化走1111；独立有机分子、聚合物、非晶、合成工艺及屈服/蠕变/压电系数等需要专项模型或计算。稳定性与已接入性质用于模型初筛，后续由统一口径计算及实验验证。"
    ])
