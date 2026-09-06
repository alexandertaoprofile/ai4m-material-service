"""Dedicated semantic streaming renderer for alloy workflow results.

It deliberately renders computed values deterministically: an LLM may later
polish prose, but must never alter model values or the machine handoff table.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Any

from src.alloy_workflow.microstructure_tendency import build_engineering_estimates


def _composition_text(composition: dict[str, Any]) -> str:
    def number(value: Any) -> str:
        amount = float(value)
        return f"{amount:.3f}" if 0 < amount < 0.1 else f"{amount:.1f}"
    return " ".join(f"{element}{number(value)}" for element, value in composition.items())


def _heat_treatment_text(value: Any) -> str:
    """Turn the persisted stage string into the customer-facing process card."""
    text = str(value or "").strip()
    if not text:
        return "未设置"
    import re

    labels = (
        ("solution_stage_1", "固溶处理"),
        ("precipitation_stage_1", "一级时效"),
        ("precipitation_stage_2", "二级时效"),
    )
    stages: list[str] = []
    for key, label in labels:
        temperature = re.search(rf"{key}_temp_C=([0-9.]+)", text)
        duration = re.search(rf"{key}_time_h=([0-9.]+)", text)
        if temperature and duration:
            stages.append(f"{label}：{temperature.group(1)} °C × {duration.group(1)} h")
    return "；".join(stages) if stages else text.replace("_", " ").replace(";", "；")


def _rocket_process_text(value: Any) -> str:
    """Render the structured stainless processing card without exposing JSON."""
    if not isinstance(value, dict):
        return _heat_treatment_text(value)
    temperature = value.get("solution_treatment_temperature_K")
    duration = value.get("solution_treatment_time_s")
    quench = {"water": "水淬", "air": "空冷"}.get(str(value.get("quench") or ""), "待确认")
    if temperature is not None and duration is not None:
        return f"固溶退火：{float(temperature):.0f} K × {float(duration):.0f} s；{quench}"
    return "固溶退火状态待确认"


def _rocket_plan_block(effective: dict[str, Any]) -> str:
    bounds = effective.get("element_bounds_wt_percent") or {}
    bound_text = "；".join(f"{e} {v[0]}–{v[1]} wt.%" for e, v in bounds.items() if isinstance(v, (list, tuple)) and len(v) == 2)
    return "\n".join([
        "### 可回收火箭不锈钢配方设计",
        "", "### 1. 问题描述",
        f"针对 {effective.get('component', '可回收火箭承压壳体')}，在指定固溶处理与评价温度下，筛选 Fe–Cr–Ni–Mn 奥氏体不锈钢候选。",
        "", "### 2. 变量与约束", "| 符号/变量 | 定义 | 本轮设定 |", "|---|---|---|",
        "| $\mathbf{c}_{wt.\%}$ | 合金元素质量百分比向量 | " + (bound_text or "以任务输入为准") + "；Fe 为平衡元素 |",
        "| $p$ | 固溶处理状态 | " + _rocket_process_text(effective.get("processing")) + " |",
        f"| $T$ | 短时拉伸评价温度 | {effective.get('test_temperature_K', '-')} K |",
        "", "### 3. 计划计算链与模型定义",
        "### 3.1 成分约束与候选生成",
        "在当前 wt.% 边界内进行局部组合，Fe 自动平衡至 100 wt.%：",
        r"$$c_{Fe}=100-\sum_{i\ne Fe}c_i,\qquad c_i\in[l_i,u_i]$$",
        "### 3.2 成分—工艺—温度响应关系",
        "以成分、固溶处理和温度为输入，预测短时拉伸响应：",
        r"$$\mathcal{F}(\mathbf{c}_{wt.\%},p,T)\rightarrow\left(YS_{0.2},UTS,A,AD\right)$$",
        "其中 $AD$ 为候选与训练成分邻域的距离标记。",
        "### 3.3 候选综合排序",
        r"$$J=w_y z(YS_{0.2}-MAE_y)+w_u z(UTS-MAE_u)+w_a z(A-MAE_a)$$",
        "按屈服强度、UTS、延伸率和成分适用域形成优先验证顺序。",
        "", "### 4. 输出定义及验证路径",
        "输出 0.2% 屈服强度、UTS、延伸率和成分适用域；优先候选进入低温韧性、焊接、疲劳与 LOX 相容性验证。",
    ])


def _hot_end_plan_block(effective: dict[str, Any]) -> str:
    route = {"cast": "铸造", "directionally_solidified": "定向凝固", "single_crystal": "单晶"}.get(str(effective.get("manufacturing_route")), str(effective.get("manufacturing_route") or "待确认"))
    bounds = effective.get("element_bounds_wt_percent") or {}
    bounds_text = "；".join(f"{e} {v[0]}–{v[1]} wt.%" for e, v in bounds.items() if isinstance(v, (list, tuple)) and len(v) == 2) or "以任务输入为准"
    thresholds = effective.get("screening_thresholds") or {}
    thresholds_text = f"UTS ≥ {thresholds.get('uts_min_MPa', '-')} MPa；0.2% 屈服 ≥ {thresholds.get('proof_strength_min_MPa', '-')} MPa；蠕变寿命 ≥ {thresholds.get('rupture_life_min_h', '-')} h"
    return "\n".join([
        "### 1. 问题描述",
        f"针对 {route} 路线高温合金，在 {effective.get('test_temperature_C', '-')} °C / {effective.get('applied_stress_MPa', '-')} MPa 和指定热处理条件下，生成并比较镍基候选配方。",
        "", "### 2. 变量与约束", "| 符号/变量 | 定义 | 本轮设定 |", "|---|---|---|",
        "| $\mathbf{c}_{wt.\%}$ | 合金元素质量百分比向量 | " + bounds_text + "；Ni 为平衡元素 |",
        f"| $r,h$ | 制造路线与热处理 | {route}；{_heat_treatment_text(effective.get('heat_treatment'))} |",
        f"| $T,\sigma$ | 温度与蠕变载荷 | {effective.get('test_temperature_C', '-')} °C；{effective.get('applied_stress_MPa', '-')} MPa |",
        f"| $g$ | 平台初筛门槛 | {thresholds_text} |",
        "", "### 3. 计划计算链与模型定义",
        "### 3.1 成分约束与候选生成",
        "候选在当前 wt.% 边界内局部调整，Ni 自动平衡至 100 wt.%：",
        r"$$c_{Ni}=100-\sum_{i\ne Ni}c_i,\qquad c_i\in[l_i,u_i]$$",
        "### 3.2 成分—工艺—工况响应关系",
        "以 wt.% 成分、制造路线、热处理、温度和载荷为输入，输出短时强度与蠕变寿命：",
        r"$$\mathcal{F}(\mathbf{c}_{wt.\%},r,h,T,\sigma)\rightarrow\left(UTS,PS_{0.2},\log_{10}t_r\right)$$",
        "### 3.3 候选综合排序",
        r"$$J=w_u z(UTS)+w_p z(PS_{0.2})+w_l z(\log_{10}t_r)$$",
        "按短时承载、抗永久变形与蠕变断裂寿命形成优先验证顺序。",
        "", "### 4. 输出定义及验证路径",
        "输出短时 UTS、0.2% 屈服强度、蠕变断裂寿命、延性辅助信息和成分适用域；优先候选进入相稳定性、氧化环境与目标工况力学验证。",
    ])


def planned_alloy_method_block(payload: dict[str, Any]) -> str:
    """Define the configured HEA/MPEA calculation chain before it is run."""
    from src.alloy_workflow.contracts import requirement_plan

    effective, plan = requirement_plan(payload)
    if plan.get("requires_domain_confirmation"):
        return "\n".join([
            "### 高温合金配方设计",
            "已识别为高温合金配比任务；确认材料体系后将进入对应的成分与性能模型。",
            "",
            "| 场景信号 | 进入路线 | 主要输出 |",
            "|---|---|---|",
            "| 发动机热端、高温承力、蠕变或持久寿命 | 高温镍基合金 | 短时强度、0.2% 屈服强度、蠕变断裂寿命 |",
            "| 高熵/多主元、at.% 成分空间、强度—硬度与相稳定性探索 | HEA/MPEA | 屈服强度、硬度、相风险与适用域 |",
            "",
            "请补充部件、目标温度、载荷或强度—硬度目标。",
        ])
    if effective.get("model_domain") == "chip_glass_thermomechanical_family_v1":
        bounds = effective.get("oxide_bounds_mol_percent") or {}
        bound_text = "；".join(f"{name} {pair[0]}–{pair[1]} mol%" for name, pair in bounds.items() if isinstance(pair, (list, tuple)) and len(pair) == 2) or "采用低硼无碱玻璃家族已记录的 16 氧化物边界"
        return "\n".join([
            "### 芯片封装玻璃基板配方设计与热机械筛选", "",
            "### 1. 问题描述", f"针对 {effective.get('application', '芯片封装玻璃基板的热失配与挠曲初筛')}，在低硼无碱铝硼硅酸盐玻璃的已验证成分邻域中生成并比较候选配方。",
            "", "### 2. 变量与约束", "| 符号/变量 | 定义 | 本轮设定 |", "|---|---|---|",
            "| $\\mathbf{c}_{mol\\%}$ | 氧化物 mol% 成分向量 | " + bound_text + " |",
            "| $\\sum_i c_i$ | 氧化物组成总和 | 100 mol% |",
            "| $T_\\alpha$ | CTE 定义温区 | 0–300 °C（当前固定） |",
            "", "### 3. 计划计算链与模型定义", "### 3.1 成分约束与候选生成",
            "以可追溯同家族专利玻璃为锚点，对 Al2O3、B2O3、MgO、CaO、BaO、SrO 进行小幅局部扰动，并以 SiO2 平衡组成：",
            r"$$\sum_i c_i=100,\qquad c_i\in[l_i,u_i],\qquad c_{SiO_2}=100-\sum_{i\ne SiO_2}c_i$$",
            "### 3.2 成分—热机械响应关系", "以氧化物组成作为输入，输出可用于第一轮封装热—力筛选的裸玻璃性质：",
            r"$$\mathcal{F}(\mathbf{c}_{mol\%})\rightarrow\left(\alpha_{0\text{–}300\,^{\circ}C},\rho,E,SOC,T_{200P},T_{35kP}\right)$$",
            "### 3.3 候选综合排序", r"$$J=\frac{1}{m}\sum_{j=1}^{m}s_j\,z(y_j),\qquad s_j\in\{-1,+1\}$$",
            "其中 $s_j$ 由用户选择的最小化/最大化目标决定；排序只在当前同家族局部候选之间比较。",
            "", "### 4. 输出定义及验证路径", "输出 CTE、密度、E、SOC 和两项黏度特征温度及其家族内验证误差。泊松比、k(T)、Cp(T)、层堆、厚度、约束和热历史在后续封装仿真中单独输入。",
        ])
    if effective.get("model_domain") == "reusable_rocket_stainless":
        return _rocket_plan_block(effective)
    if effective.get("model_domain") == "ni_superalloy_hot_end":
        return _hot_end_plan_block(effective)
    if effective.get("model_domain") == "reusable_rocket_stainless":
        bounds = effective.get("element_bounds_wt_percent") or {}
        bound_text = "；".join(f"{e} {v[0]}–{v[1]} wt.%" for e, v in bounds.items() if isinstance(v, (list, tuple)) and len(v) == 2)
        return "\n".join([
            "### 可回收火箭不锈钢配方设计",
            f"针对 {effective.get('component', '火箭承压壳体')}，在 {effective.get('test_temperature_K', '-')} K 和当前固溶处理条件下，筛选 Fe–Cr–Ni–Mn 奥氏体不锈钢候选。",
            "", "### 1. 设计条件", "| 项目 | 当前设定 |", "|---|---|",
            f"| 成分边界 | {bound_text}；Fe 自动平衡至 100 wt.% |",
            f"| 制造与热处理 | {_rocket_process_text(effective.get('processing'))} |",
            f"| 数值评价温度 | {effective.get('test_temperature_K')} K（短时拉伸） |",
            f"| 低温验证关卡 | {' / '.join(str(x) + ' K' for x in effective.get('low_temperature_verification_K', [90, 111]))}：低温拉伸与韧性验证 |",
            "| 成分单位 | wt.%（质量百分比） |",
            "", "### 2. 数据基础与候选来源",
            "主模型仅使用可追溯的 Fe–Cr–Ni–Mn 奥氏体不锈钢成分—工艺—温度—拉伸记录：0.2% 屈服强度和 UTS 各 2,180 条、延伸率 2,083 条，按 259 个成分/状态组分组验证。301/304L 的低温记录只作为 90 K / 111 K 等关卡的参考，不被写成新配方或 30X 的性能。",
            "", "### 3. 如何生成候选",
            "在用户的元素边界内对已有奥氏体不锈钢邻域作局部组合，Fe 为平衡元素：",
            "", r"$$c_{Fe}=100-\sum_{i\ne Fe}c_i,\qquad c_i\in[l_i,u_i]$$",
            "", "候选按训练成分距离分为训练邻域内和数据边界附近两个层级。",
            "", "### 4. 如何预测与排序",
            "模型以成分 wt.%、固溶处理和温度为输入，输出 0.2% 屈服强度、UTS 与延伸率：",
            "", r"$$\mathcal{F}(\mathbf{c}_{wt.\%},p,T)\rightarrow\left(YS_{0.2},UTS,A,AD\right)$$",
            "", r"$$J=w_y z(YS_{0.2}-MAE_y)+w_u z(UTS-MAE_u)+w_a z(A-MAE_a)$$",
            "", "其中 $AD$ 为成分适用域标记；$z(\cdot)$ 将三个量纲不同的性能转为可比较尺度。训练邻域内候选进入优先验证队列。",
            "", "### 5. 怎样理解输出", "| 输出 | 作用 | 使用边界 |", "|---|---|---|", "| 0.2% 屈服强度 | 比较开始产生不可恢复变形前的承载能力。 | 当前模型的 293–1273 K 短时拉伸范围内。 |", "| UTS | 比较拉伸过程中的最大承载能力。 | 不代替疲劳、焊缝或结构屈曲评定。 |", "| 延伸率 | 比较拉伸断裂前的变形能力。 | 需结合冷作量、板厚和晶粒度复核。 |", "| 成分适用域 | 标记候选距训练成分的远近。 | 范围边界附近的候选只作探索线索。 |",
            "", "### 6. 后续验证", "对优先候选按目标温度开展母材拉伸；有焊接时单列焊缝和热影响区。90 K / 111 K 低温韧性、疲劳和 LOX 相容性均需独立试验验证。",
        ])
    if effective.get("model_domain") == "ni_superalloy_hot_end":
        route_label = {"cast": "铸造", "directionally_solidified": "定向凝固", "single_crystal": "单晶"}.get(str(effective.get("manufacturing_route")), str(effective.get("manufacturing_route") or "待确认"))
        bounds = effective.get("element_bounds_wt_percent") or {}
        bounds_text = "；".join(f"{element} {value[0]}–{value[1]} wt.%" for element, value in bounds.items() if isinstance(value, (list, tuple)) and len(value) == 2) or "待用户给出"
        default_fields = {item.get("field") for item in plan.get("default_assumptions") or [] if item.get("status") == "platform_default"}
        default_note = "；".join({
            "element_bounds_wt_percent": "成分边界", "manufacturing_route": "制造路线", "heat_treatment": "热处理",
            "test_temperature_C": "温度", "applied_stress_MPa": "载荷",
        }[field] for field in ("element_bounds_wt_percent", "manufacturing_route", "heat_treatment", "test_temperature_C", "applied_stress_MPa") if field in default_fields)
        return "\n".join([
            "### 高温镍基合金成分设计与服役性能筛选",
            f"针对 {route_label} 路线，在 {effective.get('test_temperature_C', '-')} °C、{effective.get('applied_stress_MPa', '-')} MPa 和指定热处理条件下，比较可追溯镍基合金邻域内的候选成分。",
            "", "#### 1. 设计条件", "| 项目 | 当前设定 |", "|---|---|", f"| 成分边界 | {bounds_text} |", f"| 制造路线 | {route_label}（叶片常用路线：材料整体按单一晶体生长，减少晶界对高温蠕变的影响） |", f"| 热处理 | {_heat_treatment_text(effective.get('heat_treatment'))} |", f"| 温度 / 蠕变载荷 | {effective.get('test_temperature_C', '待确认')} °C / {effective.get('applied_stress_MPa', '待确认')} MPa |", "| 成分单位 | wt.%（质量百分比） |", *( [f"| 默认值使用情况 | 本轮未给出的 {default_note} 使用平台默认热端工况。 |"] if default_note else [] ),
            "", "#### 2. 训练输入与性能预测框架", "服务将每个候选表示为一条‘成分—工艺—工况’记录，并通过三条独立回归支路输出强度和寿命：", "", r"$$\left(\mathbf{c}_{wt.\%},r,h,T,\sigma\right)\xrightarrow{\ \mathcal{F}\ }\left(UTS,PS_{0.2},\log_{10}t_r\right)$$", "", "| 输入网格 | 训练字段 | 对应输出 |", "|---|---|---|", "| 成分 | 各元素 wt.%、Ni 平衡 | 短时 UTS（最大承载） |", "| 工艺 | 铸造 / 定向凝固 / 单晶、固溶与时效温度—时间 | 0.2% 屈服强度（抗永久变形） |", "| 工况 | 测试温度、蠕变载荷 | 蠕变断裂寿命 |", "", "三项性能分别通过独立回归支路预测；短时拉伸部分使用 544 条观测（UTS 279 条、0.2% 屈服强度 241 条），蠕变部分使用 713 条严格准入记录。训练、验证与测试按合金牌号分组。",
            "", "#### 3. 候选生成与排序", "候选在当前成分边界内作小幅局部调整，Ni 自动平衡至 100 wt.%：", "", r"$$c_{Ni}=100-\sum_{i\ne Ni}c_i,\qquad c_i\in[l_i,u_i]$$", "", "模型分别输出短时抗拉强度、0.2% 屈服强度和蠕变断裂寿命；排序综合比较三者。",
            "", "#### 4. 指标怎么理解", "| 指标 | 含义 | 本服务中的用途 |", "|---|---|---|", "| 短时 UTS（短时极限抗拉强度） | 拉伸过程中材料可承受的最大工程应力。 | 比较短时承载上限，数值越高代表短时承载能力越强。 |", "| 0.2% proof strength（0.2% 屈服强度） | 规定产生 0.2% 塑性应变时对应的应力。 | 比较开始发生不可恢复变形前的承载能力。它与 UTS 不同：UTS 看最大承载，0.2% 屈服强度看抗永久变形。 |", "| 蠕变断裂寿命 | 在指定温度和载荷下，持续受载至断裂的预测时间。 | 比较热端长期耐久优先级。 |", "| 延伸率 | 拉伸断裂前的变形能力。 | 作为延性辅助信息。 |",
            "", "#### 5. 后续验证", "优先候选进入 CALPHAD 相稳定性、氧化环境筛查及目标工况的高温拉伸/蠕变验证。",
        ])
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
        "### 1. 问题描述",
        f"在 {elements} 组成的合金体系中，搜索满足成分边界、工艺和温度条件的候选配比，并对 {objective_text} 进行初步量化评估。",
        "",
        "### 2. 变量与约束",
        "| 符号/变量 | 定义 | 本轮设定 |",
        "|---|---|---|",
        f"| $x_i$ | 第 i 种元素的原子分数 | 元素：{elements} |",
        "| $\\mathbf{x}$ | 成分向量 | $x_i \\geq 0$，$\\sum_i x_i = 1$ |",
        f"| 成分边界 | 每种元素允许的原子百分比 | {bounds_text} |",
        f"| $p$、$T$ | 制备工艺与评价温度 | {effective.get('processing_method') or '未指定'}；{effective.get('test_temperature_C', 25)} °C |",
        "",
        "### 3. 计划计算链与模型定义",
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


def glass_summary_block(result: dict[str, Any]) -> str:
    """Customer-facing 1111 result block for the chip-glass family route."""
    sampling = result.get("sampling") or {}
    candidates = result.get("initial_candidates") or []
    stages = sampling.get("funnel_stages") or []
    lines = ["### 候选筛选结果", "针对低硼无碱芯片封装玻璃基板，在可追溯同家族配方邻域内生成并比较候选。", "", "#### 筛选过程", "| 阶段 | 数量 | 说明 |", "|---|---:|---|"]
    lines += [f"| {item.get('label', '筛选阶段')} | {int(item.get('count', 0))} | {item.get('description', '按当前约束保留。')} |" for item in stages]
    lines += ["", "{{VISUAL:glass_screening_funnel}}", "", "#### 优先候选", "| 排名 | 氧化物配方（mol%） | CTE（0–300°C） | 杨氏模量 E | SOC | 成分覆盖情况 |", "|---:|---|---:|---:|---:|---|"]
    for index, item in enumerate(candidates[:5], 1):
        composition = "；".join(f"{name} {value:.2f}" for name, value in (item.get("composition_mol_percent") or {}).items() if float(value) > 0)
        props = item["predicted_properties"]
        lines.append(f"| {index} | {composition} | {props['CTE_linear_0_to_300C']['prediction_ppm_per_K']:.3f} ppm/K | {props['young_modulus_GPa']['prediction']:.2f} GPa | {props['stress_optical_coefficient_nm_cm_per_MPa']['prediction']:.2f} nm/cm/MPa | 同家族局部邻域 |")
    if candidates:
        top = candidates[0]; props = top["predicted_properties"]; anchor = top.get("source_anchor") or {}
        lines += ["", "{{VISUAL:glass_cte_modulus_tradeoff}}", "", "#### 优先候选的完整性质卡", "| 项目 | 当前结果 |", "|---|---|",
                  f"| 候选编号 | {top.get('candidate_id', '-')} |", f"| 来源锚点 | {anchor.get('glass_id', '-')} |",
                  f"| 成分 | " + "；".join(f"{name} {value:.2f}" for name, value in (top.get('composition_mol_percent') or {}).items() if float(value) > 0) + "（mol%） |",
                  f"| CTE（0–300°C） | {props['CTE_linear_0_to_300C']['prediction_ppm_per_K']:.3f} ppm/K；家族内留出 MAE {props['CTE_linear_0_to_300C']['validation_MAE_ppm_per_K']:.3f} ppm/K |",
                  f"| 密度 | {props['density_g_per_cm3']['prediction']:.4f} g/cm³；MAE {props['density_g_per_cm3']['validation_MAE']:.4f} g/cm³ |",
                  f"| 杨氏模量 E | {props['young_modulus_GPa']['prediction']:.2f} GPa；MAE {props['young_modulus_GPa']['validation_MAE']:.3f} GPa |",
                  f"| 应力光学系数 SOC | {props['stress_optical_coefficient_nm_cm_per_MPa']['prediction']:.2f} nm/cm/MPa；MAE {props['stress_optical_coefficient_nm_cm_per_MPa']['validation_MAE']:.3f} nm/cm/MPa |",
                  f"| 200 poise 温度 | {props['viscosity_temperature_200_poise_C']['prediction']:.1f} °C；MAE {props['viscosity_temperature_200_poise_C']['validation_MAE']:.2f} °C |",
                  f"| 35 kpoise 温度 | {props['viscosity_temperature_35kpoise_C']['prediction']:.1f} °C；MAE {props['viscosity_temperature_35kpoise_C']['validation_MAE']:.2f} °C |",
                  "", "{{VISUAL:glass_composition_traceability}}"]
    lines += ["", "#### 结论", str(result.get("user_conclusion") or "当前候选用于制样和封装仿真输入收敛。")]
    return "\n".join(lines)


def hot_end_summary_block(result: dict[str, Any]) -> str:
    """Customer-facing report for a conditional Ni-superalloy screening run."""
    conditions = result.get("screening_conditions") or {}
    candidates = result.get("initial_candidates") or []
    nearest_candidates = result.get("nearest_candidates") or []
    reference_candidates = result.get("reference_candidates") or []
    sampling = result.get("sampling") or {}
    temperature_support = conditions.get("temperature_support") or {}
    support_level = str(temperature_support.get("level") or "inside")
    support_note = str(temperature_support.get("label") or "短时强度训练温区内")
    support_reference = temperature_support.get("reference_temperature_C")
    support_text = support_note + (f"；训练上限 {float(support_reference):.0f} °C" if support_reference is not None else "")
    display_candidates = candidates or nearest_candidates or reference_candidates
    if candidates:
        candidate_heading = "优先候选"
        card_heading = "优先候选的完整性质卡"
    elif nearest_candidates:
        candidate_heading = "下一步优先评估候选"
        card_heading = "优先评估候选的完整性质卡"
    elif support_level == "boundary":
        candidate_heading = "边界工况参考候选"
        card_heading = "边界工况参考候选的完整性质卡"
    else:
        candidate_heading = "工况外推参考候选"
        card_heading = "工况外推参考候选的完整性质卡"
    stages = [item for item in (sampling.get("funnel_stages") or []) if isinstance(item, dict)]
    if not stages:
        stages = [
            {"label": "满足成分约束的候选", "count": sampling.get("generated", 0)},
            {"label": "综合优先短名单", "count": len(candidates)},
        ]
    stage_rows = [f"| {item.get('label', '筛选阶段')} | {int(item.get('count', 0))} | {'综合排序前的门槛筛选。' if index < len(stages) - 1 else '保留用于横向比较与优先验证。'} |" for index, item in enumerate(stages)]
    no_pass_note = ""
    if not candidates and nearest_candidates:
        no_pass_note = "当前筛选门槛通过数为 0；在当前工况与目标组合下，暂未配比出同时严格满足全部需求的材料；以下保留最接近目标的候选，作为后续配比迭代与验证的优先方向。"
    elif not candidates and reference_candidates:
        no_pass_note = "当前筛选门槛通过数为 0；以下候选仅作为边界或工况外推参考，不代表已经满足全部筛选门槛。"
    include_gap = bool(nearest_candidates and not candidates)
    header = "| 排名 | 成分（wt.%） | 短时 UTS（最大承载） | 0.2% 屈服强度（抗永久变形） | 预测蠕变断裂寿命 | 成分覆盖情况 |"
    separator = "|---:|---|---:|---:|---:|---|"
    if include_gap:
        header = header[:-1] + " 与门槛的主要缺口 |"
        separator = separator[:-1] + "---|"
    lines = ["### 候选筛选结果", f"针对 **{conditions.get('manufacturing_route', '-')}** 路线，在 **{conditions.get('test_temperature_C', '-')} °C / {conditions.get('applied_stress_MPa', '-')} MPa** 与所列热处理条件下，生成并比较候选配方。", *([f"温度数据覆盖：{support_text}。"] if support_level != "inside" else []), "", "#### 筛选过程", "| 阶段 | 数量 | 说明 |", "|---|---:|---|", *stage_rows, "", "{{VISUAL:hot_end_screening_funnel}}", "", f"#### {candidate_heading}", *([no_pass_note] if no_pass_note else []), header, separator]
    for rank, item in enumerate(display_candidates[:5], 1):
        composition = _composition_text(item.get("composition_wt_percent") or {})
        uts = item["ultimate_tensile_strength_MPa"]; proof = item["proof_strength_0p2_MPa"]; creep = item["creep_rupture"]
        domain = {"inside": "训练成分范围内", "boundary": "训练边界附近"}.get((item.get("applicability_domain") or {}).get("level"), "范围外")
        row = f"| {rank} | {composition} | {uts['mean']:.0f} MPa | {proof['mean']:.0f} MPa | {creep['predicted_hours']:.1f} h | {domain}"
        if include_gap:
            gaps = ((item.get("strict_screening") or {}).get("gaps") or {})
            labels = {"uts_min_MPa": "UTS", "proof_strength_min_MPa": "0.2% 屈服", "rupture_life_min_h": "寿命"}
            gap_text = "；".join(
                f"{labels[key]}差 {value.get('shortfall', 0):.0f}{' h' if key == 'rupture_life_min_h' else ' MPa'}"
                for key, value in gaps.items() if float(value.get("shortfall", 0)) > 1e-9
            ) or "已满足全部门槛"
            row += f" | {gap_text}"
        lines.append(row + " |")
    top = display_candidates[0] if display_candidates else None
    if top:
        uts = top["ultimate_tensile_strength_MPa"]; proof = top["proof_strength_0p2_MPa"]; creep = top["creep_rupture"]; elongation = top.get("elongation_percent_auxiliary") or {}
        domain = {"inside": "训练成分范围内", "boundary": "训练边界附近"}.get((top.get("applicability_domain") or {}).get("level"), "范围外")
        anchor = top.get("source_anchor") or {}
        anchor_name = str(anchor.get("alloy_name") or anchor.get("tag") or "当前来源参考")
        conclusion = (f"针对上述需求，优先沿 **{top['candidate_id']}**（来源参考合金：{anchor_name}）开展下一轮配比迭代。它是当前候选中最接近目标组合的方向；结合表中性能差距，继续开展 CALPHAD 相稳定性、氧化环境及目标工况力学验证。" if nearest_candidates and not candidates else f"在上述工况下，优先评估研发候选 **{top['candidate_id']}**（来源参考合金：{anchor_name}）。它在当前候选中兼顾短时最大承载、抗永久变形与蠕变耐久表现；下一步按该配方开展 CALPHAD 相稳定性、氧化环境及目标工况力学验证。")
        lines += ["", "{{VISUAL:hot_end_strength_life_tradeoff}}", "", f"#### {card_heading}", "| 项目 | 当前结果 |", "|---|---|", f"| 研发候选 | {top['candidate_id']} |", f"| 来源参考合金 | {anchor_name} |", f"| 成分 | {_composition_text(top.get('composition_wt_percent') or {})}（wt.%） |", f"| 适用工况 | {conditions.get('manufacturing_route', '-')}；{conditions.get('test_temperature_C', '-')} °C；{conditions.get('applied_stress_MPa', '-')} MPa |", f"| 温度数据覆盖 | {support_text} |", f"| 短时 UTS（最大承载） | {uts['mean']:.0f} MPa；独立测试 MAE {uts['screening_MAE_MPa']:.0f} MPa |", f"| 0.2% 屈服强度（抗永久变形） | {proof['mean']:.0f} MPa；独立测试 MAE {proof['screening_MAE_MPa']:.0f} MPa |", f"| 预测蠕变断裂寿命 | {creep['predicted_hours']:.1f} h；独立测试典型误差约 {creep['screening_error_factor']:.2f}× |", f"| 延伸率（延性辅助） | {float(elongation.get('elongation_percent', 0)):.1f}% |", f"| 成分覆盖情况 | {domain} |", "", "{{VISUAL:hot_end_composition_traceability}}", "", "#### 结论", conclusion]
    return "\n".join(lines)


def hot_end_input_guide_block(plan: dict[str, Any]) -> str:
    missing = plan.get("missing_required_inputs") or []
    rows = [f"| {item['label']} | 需要用于条件预测，不能由服务替用户假设。 |" for item in missing]
    return "\n".join([
        "### 请补充筛选条件",
        "高温镍基合金的强度和蠕变寿命同时受路线、热处理、温度与载荷影响。补齐下列条件后，服务会生成受约束候选并给出可比较的优先级。",
        "", "| 需要补充 | 为什么需要 |", "|---|---|", *rows,
        "", "提交后将依次显示：来源锚点覆盖情况、候选筛选漏斗、短时强度—蠕变寿命对比，以及进入 CALPHAD/氧化/力学验证的建议。",
    ])


def _place_visuals_before_conclusion(narrative: str, visuals: str) -> str:
    """Keep the brief conclusion as the last thing the user reads."""
    if not visuals:
        return narrative
    marker = "\n\n### 本轮结论\n"
    if marker not in narrative:
        return narrative.rstrip() + "\n\n" + visuals
    before, conclusion = narrative.rsplit(marker, 1)
    return before.rstrip() + "\n\n" + visuals.strip() + marker + conclusion


def _embed_hot_end_visuals(narrative: str, visual_assets: list[dict[str, str]] | None) -> str:
    """Place each hot-end chart beside the report section it explains."""
    assets = {str(item.get("name")): item for item in visual_assets or []}
    for name in ("hot_end_screening_funnel", "hot_end_strength_life_tradeoff", "hot_end_composition_traceability"):
        token = f"{{{{VISUAL:{name}}}}}"
        item = assets.get(name)
        if not item or not str(item.get("url") or "").strip():
            narrative = narrative.replace(token, "")
            continue
        title = str(item.get("title") or "分析图表")
        description = str(item.get("description") or "")
        narrative = narrative.replace(token, f"#### {title}\n\n{description}\n\n![{title}]({item['url']})")
    return narrative


def _embed_glass_visuals(narrative: str, visual_assets: list[dict[str, str]] | None) -> str:
    assets = {str(item.get("name")): item for item in visual_assets or []}
    for name in ("glass_screening_funnel", "glass_cte_modulus_tradeoff", "glass_composition_traceability"):
        token = f"{{{{VISUAL:{name}}}}}"; item = assets.get(name)
        if not item or not str(item.get("url") or "").strip():
            narrative = narrative.replace(token, ""); continue
        title = str(item.get("title") or name); description = str(item.get("description") or "")
        narrative = narrative.replace(token, f"#### {title}\n\n{description}\n\n![{title}]({item['url']})")
    return narrative


async def emit_result_content(websocket: Any, result: dict[str, Any], *, step_id: str = "FILAMENT_SELECTION_OPTIMIZATION", visual_assets: list[dict[str, str]] | None = None) -> None:
    """Stream LLM-rendered narrative/table like adjacent services, with safe fallback."""
    path = result.get("_summary_path")
    fallback = path.read_text(encoding="utf-8") if path else final_conclusion_block(result)
    if result.get("model_domain") == "ni_superalloy_hot_end":
        rendered_content = _embed_hot_end_visuals(fallback, visual_assets)
    elif result.get("model_domain") == "reusable_rocket_stainless":
        rendered_content = _embed_rocket_visuals(fallback, visual_assets)
    elif result.get("model_domain") == "chip_glass_thermomechanical_family_v1":
        rendered_content = _embed_glass_visuals(fallback, visual_assets)
    else:
        visuals = visual_assets_block(visual_assets)
        rendered_content = _place_visuals_before_conclusion(fallback, visuals)
    tendency_asset = next((item for item in (visual_assets or []) if item.get("name") == "microstructure_tendency"), None) if result.get("model_domain") == "hea_mpea" else None
    tendency = microstructure_tendency_block(result, str((tendency_asset or {}).get("url") or ""))
    if tendency:
        rendered_content = rendered_content.rstrip() + "\n\n" + tendency
    await stream_authoritative_markdown(websocket, rendered_content, step_id=step_id)


def rocket_stainless_summary_block(result: dict[str, Any]) -> str:
    """Customer-facing result block for the third 1111 model domain."""
    if result.get("mode") == "cryogenic_reference":
        ref = result.get("cryogenic_reference") or {}
        rows = ref.get("nearest_records") or []
        table = ["| 参考材料 | 温度 | 屈服 | UTS | 延伸率 | 来源定位 |", "|---|---:|---:|---:|---:|---|"]
        def value(number: Any, unit: str) -> str:
            try:
                return "当前目录未收录" if not math.isfinite(float(number)) else f"{float(number):.1f} {unit}"
            except (TypeError, ValueError):
                return "当前目录未收录"
        table.extend(f"| {r.get('material_id','-')} | {r.get('temperature_K','-')} K | {value(r.get('yield_MPa'),'MPa')} | {value(r.get('uts_MPa'),'MPa')} | {value(r.get('elongation_pct'),'%')} | {r.get('source_locator','-')} |" for r in rows)
        steps = [f"- {item}" for item in (ref.get("next_validation") or [])]
        return "\n".join(["### 低温参考与验证优先级", "目标温度处于当前自由配方模型之外。本页使用 301/304L 可追溯参考建立低温试验基准，不把参考材料写成新配方或 30X 性能。", "", "#### 可追溯参考数据", *table, "", "#### 建议验证顺序", *steps, "", "#### 结论", "低温工况先以参考牌号和试验关卡收敛验证方案；取得同一成分—工艺状态下的低温拉伸与韧性数据后，才可建立该工况的专属校准层。"])
    conditions = result.get("screening_conditions") or {}
    candidates = result.get("initial_candidates") or []
    all_candidates = result.get("all_candidates") or candidates
    level_counts = {level: sum(1 for item in all_candidates if (item.get("applicability_domain") or {}).get("level") == level) for level in ("inside", "boundary", "outside")}
    priority_candidates = [
        item for item in all_candidates
        if (item.get("applicability_domain") or {}).get("level") == "inside"
    ][:5]
    effective = (result.get("requirement_interpretation") or {}).get("effective_model_input") or {}
    bounds = effective.get("element_bounds_wt_percent") or {}
    bounds_text = "；".join(
        f"{element} {value[0]}–{value[1]} wt.%"
        for element, value in bounds.items()
        if isinstance(value, (list, tuple)) and len(value) == 2
    ) or "以任务输入为准"
    sampling = result.get("sampling") or {}
    stages = [item for item in (sampling.get("funnel_stages") or []) if isinstance(item, dict)]
    if not stages:
        stages = [
            {"label": "经验成分适用域内或边界", "count": len(all_candidates)},
            {"label": "训练成分邻域内", "count": level_counts["inside"]},
            {"label": "强度—延性综合优先", "count": len(priority_candidates)},
        ]
    stage_explanations = {
        "元素 wt.% 边界采样": "各元素在当前 wt.% 上下限内取样。",
        "Fe 平衡可行（溶质总量 10–60 wt.%）": "其余质量分数由 Fe 平衡，保留奥氏体不锈钢成分窗口。",
        "经验成分适用域内或边界": "剔除远离现有训练成分的组合。",
        "训练成分邻域内": "优先保留与可追溯训练成分接近的组合。",
        "强度—延性综合优先": "按屈服、UTS、延伸率及其验证误差综合排序。",
    }
    stage_rows = [f"| {item.get('label', '筛选阶段')} | {int(item.get('count', 0))} | {stage_explanations.get(str(item.get('label')), '按当前筛选条件保留。')} |" for item in stages]
    lines = [
        "### 5. 筛选结果与候选卡",
        f"针对 {effective.get('component', '可回收火箭承压壳体')}，在 **{conditions.get('test_temperature_K', '-')} K** 固溶退火母材工况下，筛选可进入后续验证的 Fe–Cr–Ni–Mn 奥氏体不锈钢候选。",
        f"共保留 **{len(priority_candidates)}** 个训练成分邻域内候选进入优先验证队列。",
        "", "### 5.1 筛选过程", "| 阶段 | 数量 | 说明 |", "|---|---:|---|", *stage_rows,
        "", "{{VISUAL:rocket_screening_funnel}}", "", "### 5.2 优先候选",
        "| 排名 | 成分（wt.%） | 0.2% 屈服强度 | UTS（最大承载） | 延伸率 | 成分覆盖情况 |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for rank, item in enumerate(priority_candidates, 1):
        tensile = item["short_time_tensile"]
        domain = {"inside": "训练成分邻域内", "boundary": "训练边界附近"}.get((item.get("applicability_domain") or {}).get("level"), "范围外")
        lines.append(f"| {rank} | {_composition_text(item.get('composition_wt_percent') or {})} | {tensile['yield_0p2_MPa']['mean']:.0f} MPa | {tensile['uts_MPa']['mean']:.0f} MPa | {tensile['elongation_pct']['mean']:.1f}% | {domain} |")
    top = priority_candidates[0] if priority_candidates else None
    if top:
        tensile = top["short_time_tensile"]
        domain = top.get("applicability_domain") or {}
        domain_label = {"inside": "训练成分邻域内", "boundary": "训练边界附近，需复核", "outside": "训练范围外"}.get(domain.get("level"), "当前未记录")
        lines += [
            "", "{{VISUAL:rocket_strength_ductility_tradeoff}}", "", "#### 优先候选的完整性质卡", "| 项目 | 当前结果 |", "|---|---|",
            f"| 候选编号 | {top['candidate_id']} |",
            f"| 成分 | {_composition_text(top.get('composition_wt_percent') or {})}（wt.%） |",
            f"| 适用工况 | {conditions.get('test_temperature_K', '-')} K；{_rocket_process_text(conditions.get('processing'))} |",
            f"| 0.2% 屈服强度 | {tensile['yield_0p2_MPa']['mean']:.0f} MPa；分组验证 MAE {tensile['yield_0p2_MPa']['screening_MAE']:.1f} MPa |",
            f"| UTS（最大承载） | {tensile['uts_MPa']['mean']:.0f} MPa；分组验证 MAE {tensile['uts_MPa']['screening_MAE']:.1f} MPa |",
            f"| 延伸率 | {tensile['elongation_pct']['mean']:.1f}%；分组验证 MAE {tensile['elongation_pct']['screening_MAE']:.2f}% |",
            f"| 成分覆盖情况 | {domain_label}；最近训练成分距离 {domain.get('nearest_training_composition_distance', '-')} |",
            "", "{{VISUAL:rocket_composition_comparison}}", "", "#### 结论",
            f"在上述工况下，优先评估 **{top['candidate_id']}**。下一步按实际冷作量、板厚和焊接状态完成母材/焊缝拉伸，并开展低温韧性、疲劳与 LOX 相容性验证。",
        ]
    return "\n".join(lines)


def _embed_rocket_visuals(narrative: str, visual_assets: list[dict[str, str]] | None) -> str:
    assets = {str(item.get("name")): item for item in visual_assets or []}
    for name in ("rocket_screening_funnel", "rocket_strength_ductility_tradeoff", "rocket_composition_comparison"):
        token = f"{{{{VISUAL:{name}}}}}"; item = assets.get(name)
        if not item:
            narrative = narrative.replace(token, ""); continue
        title = str(item.get("title") or name); description = str(item.get("description") or "")
        narrative = narrative.replace(token, f"#### {title}\n\n{description}\n\n![{title}]({item['url']})")
    return narrative
