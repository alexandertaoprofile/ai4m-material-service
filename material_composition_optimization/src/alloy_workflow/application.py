"""Application use cases for HEA/MPEA candidate evaluation and proposal."""
from __future__ import annotations

import time
from typing import Any

from src.alloy_workflow.contracts import contract, requirement_plan
from src.alloy_workflow.runner import HEASurrogateRunner


class AlloyOptimizationApplication:
    """Coordinates standard requests with the isolated numerical runner.

    This layer owns use-case ordering only.  HTTP/WebSocket protocol belongs
    to ``main.py``/``protocol.py``; charts, files and object storage belong to
    infrastructure adapters.
    """

    def __init__(self, runner: HEASurrogateRunner, service_name: str) -> None:
        self.runner = runner
        self.service_name = service_name

    def propose_space(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        effective, plan = requirement_plan(payload)
        if plan.get("requires_domain_confirmation"):
            raise ValueError("请先确认采用高温镍基合金还是 HEA/MPEA 路线后开始配方筛选")
        normalized = dict(payload)
        normalized["alloy_optimization"] = effective
        constraints = contract(normalized)
        constraints["raw_scope"] = effective
        started = time.perf_counter()
        operation = "propose_space" if effective.get("model_domain") == "chip_glass_thermomechanical_family_v1" else "propose"
        result = self.runner.run(constraints["taskid"], operation, constraints)
        result.update({"taskid": constraints["taskid"], "status": "completed", "service": self.service_name, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        self._enrich(result, plan)
        return result, constraints

    def evaluate(self, payload: dict[str, Any], candidates: list[Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        constraints = contract(payload)
        operation = "evaluate_batch" if candidates is not None else "evaluate"
        return self.runner.run(constraints["taskid"], operation, constraints, candidates), constraints

    def _enrich(self, result: dict[str, Any], plan: dict[str, Any]) -> None:
        if result.get("model_domain") == "chip_glass_thermomechanical_family_v1":
            self._enrich_chip_glass(result, plan)
            return
        if result.get("model_domain") == "ni_superalloy_hot_end":
            self._enrich_hot_end(result, plan)
            return
        if result.get("model_domain") == "reusable_rocket_stainless":
            self._enrich_rocket_stainless(result, plan)
            return
        result["requirement_interpretation"] = plan
        result["model_evidence"] = self.runner.model_evidence()
        result["next_actions"] = ["确认或修改模板假设", "查看候选表和图表", "由上游架构决定是否继续数学优化"]
        result["nonlinear_response_function"] = {"name": "hea_mpea_surrogate_response_v0_1", "meaning": "成分、工艺和温度到材料性质的非线性响应关系；不是固定线性配比公式。", "mathematical_form": "F(composition_at_pct, processing_method, test_temperature_C) -> {yield_strength_MPa_mean_std, hardness_HV_mean_std, phase_probabilities, applicability_domain}", "batch_call": "POST /alloy/evaluate-batch", "input": {"composition_at_pct": "元素原子百分比，总和为 100", "processing_method": "如 CAST", "test_temperature_C": "测试或目标服役温度（°C）"}, "output": {"yield_strength_MPa": "预测均值和集成标准差", "hardness_HV": "预测均值和集成标准差", "phase_probabilities": "AM/IM/SS/SS+IM 概率", "applicability_domain": "训练域内、边界或域外"}}
        sampling, candidates = result.get("sampling", {}), result.get("initial_candidates", [])
        if candidates:
            top = candidates[0]; phase = "较低" if top["phase_risk"] == "low" else "较高"; domain = {"inside": "训练数据范围内", "boundary": "训练数据边界附近", "outside": "训练数据范围外"}.get(top["applicability_domain"]["level"], top["applicability_domain"]["level"])
            result["user_conclusion"] = f"在 {int(sampling.get('generated', 0))} 个满足成分边界的候选中，有 {int(sampling.get('feasible', 0))} 个通过当前模型的初步筛选。排在前面的候选预测屈服强度为 {top['yield_strength_MPa']['mean']:.0f} ± {top['yield_strength_MPa']['std']:.0f} MPa、硬度为 {top['hardness_HV']['mean']:.0f} ± {top['hardness_HV']['std']:.0f} HV；析出相相关风险{phase}，但它位于{domain}。它适合进入下一步验证，不等同于已确认的工程用材。"
        else:
            result["user_conclusion"] = f"在当前目标和约束下，{int(sampling.get('generated', 0))} 个候选均未通过初筛。建议检查温度/工艺假设、元素边界或目标阈值。"
        result["downstream_handoff_text"] = "本服务提供可批量调用的非线性成分—性能评价函数。上游架构如需继续数学优化，可读取搜索范围、初始候选和筛选条件，并对新成分调用该评价函数。"
        result["downstream_handoff"] = {"decision_variables": "search_space", "initial_population": "initial_candidates", "evaluation_contract": "HEA runner evaluate/evaluate_batch", "do_not_treat_as_hard_bounds": "derived_candidate_percentiles_at_pct"}

    def _enrich_chip_glass(self, result: dict[str, Any], plan: dict[str, Any]) -> None:
        result["requirement_interpretation"] = plan
        result["model_evidence"] = {"model_version": result.get("model_version"), "data_type": "US20250026678 低硼无碱铝硼硅酸盐玻璃的可追溯组分与热机械记录；候选仅作同家族局部扰动。", "validation": {"CTE_0_300C": "MAE 0.048 ppm/K", "density": "MAE 0.0045 g/cm³", "young_modulus": "MAE 0.244 GPa", "SOC": "MAE 0.112 nm/cm/MPa"}}
        result["next_actions"] = ["确认候选与来源锚点的制样窗口", "实测同批泊松比、k(T)、Cp(T)", "输入实际层堆、厚度、边界和热历史后计算残余应力与翘曲"]
        result["downstream_handoff_text"] = "候选保留氧化物 mol% 配方、来源锚点、六项玻璃侧预测和适用域，可作为实验设计与后续受限优化的初始池。"

    def _enrich_rocket_stainless(self, result: dict[str, Any], plan: dict[str, Any]) -> None:
        result["requirement_interpretation"] = plan
        result["model_evidence"] = {"model_version": result.get("model_version", "reusable_rocket_austenitic_design_v2_extratrees"), "data_type": "公开奥氏体不锈钢成分—固溶处理—温度短时拉伸数据；低温 301/304L 记录单独保留为参考层。", "validation": {"0.2%_yield": "3-fold 完整热机械状态 GroupKFold：R² 0.769，MAE 16.2 MPa", "UTS": "R² 0.922，MAE 16.3 MPa", "elongation": "R² 0.833，MAE 3.29%"}}
        result["nonlinear_response_function"] = {"name": "reusable_rocket_austenitic_design_v2_extratrees", "meaning": "成分 wt.%、固溶处理和温度到短时拉伸性能的条件筛选。", "mathematical_form": "F(composition_wt_pct, solution_treatment, T) -> {yield, UTS, elongation, applicability_domain}", "input": {"composition_wt_percent": "Fe 平衡的元素 wt.%", "test_temperature_K": "293–1273 K", "processing": "固溶温度、时间、淬冷、产品形态和熔炼路线代码"}, "output": {"yield/UTS/elongation": "预测均值与 GroupKFold MAE", "applicability_domain": "成分邻域状态"}}
        if result.get("mode") == "cryogenic_reference":
            result["user_conclusion"] = "该目标温度已进入低温验证优先模式：服务返回 301/304L 可追溯参考与试验路径，不将公开代理材料写成自由配方或 30X 性能预测。"
            result["next_actions"] = ["确定 301/304L 基准状态", "开展目标温度母材拉伸", "补齐焊缝/HAZ 韧性和疲劳验证", "评估 LOX 相容性"]
            return
        candidates = result.get("initial_candidates") or []
        if candidates:
            top = candidates[0]; tensile = top["short_time_tensile"]
            result["user_conclusion"] = f"针对 {result.get('screening_conditions', {}).get('test_temperature_K', '-'):.0f} K 的指定固溶处理条件，优先评估候选 {top['candidate_id']}：0.2% 屈服强度筛选值 {tensile['yield_0p2_MPa']['mean']:.0f} MPa、UTS {tensile['uts_MPa']['mean']:.0f} MPa、延伸率 {tensile['elongation_pct']['mean']:.1f}%。默认火箭场景还会把 90 K（LOX）与 111 K（LCH4）列为低温验证关卡；该候选用于下一轮材料与工艺验证排序。"
        else:
            result["user_conclusion"] = "当前边界内未保留可比较候选；请收窄至公开奥氏体不锈钢的成分邻域，或补充新的可追溯数据。"
        result["next_actions"] = ["确认实际固溶处理、冷作量、晶粒度与板厚", "开展目标温度拉伸验证", "按构件需要开展焊接、低温韧性、疲劳和 LOX 相容性验证"]
        result["downstream_handoff_text"] = "候选保留 wt.% 配方、工艺条件、短时拉伸筛选值和适用域，可作为后续试验设计与数学优化的受限初始池。"

    def _enrich_hot_end(self, result: dict[str, Any], plan: dict[str, Any]) -> None:
        result["requirement_interpretation"] = plan
        m1 = str(result.get("model_version") or "").endswith("20260902")
        result["model_evidence"] = {
            "model_version": result.get("model_version", "hot_end_ni_superalloy_screening_v0 / 20260901"),
            "data_type": "剑桥高温合金数据库的可追溯文献/行业数据汇编；按合金 Tag 分组保留独立测试。",
            "validation": {"UTS": ("独立 Tag 测试 MAE 92 MPa（M1 候选）" if m1 else "独立 Tag 测试 MAE 111 MPa"), "0.2% proof": ("独立 Tag 测试 MAE 51 MPa（M1 候选）" if m1 else "独立 Tag 测试 MAE 86 MPa"), "rupture_life": "独立 Tag 测试 MAE 0.259 log10(h)，约 1.81 倍因子（维持 M0）", "elongation": "仅辅助展示，验证集波动较大"},
        }
        result["nonlinear_response_function"] = {"name": ("hot_end_ni_superalloy_screening_m1_candidate" if m1 else "hot_end_ni_superalloy_screening_v0"), "meaning": "wt.% 成分、制造路线、热处理、温度和载荷到短时强度与蠕变寿命的条件预测。", "mathematical_form": "F(composition_wt_pct, route, heat_treatment, T, stress) -> {UTS, proof, rupture_life, elongation_auxiliary}", "input": {"composition_wt_percent": "元素质量百分比，总和为 100", "manufacturing_route": "cast / directionally_solidified / single_crystal", "heat_treatment": "来源数据格式的热处理阶段串", "test_temperature_C": "目标温度（°C）", "applied_stress_MPa": "蠕变载荷"}, "output": {"UTS/proof": "预测均值及独立测试 MAE", "rupture_life": "预测小时数及约 1.81 倍筛选误差因子", "hardness": "当前目录未收录，不输出"}}
        candidates = result.get("initial_candidates", [])
        nearest_candidates = result.get("nearest_candidates", [])
        reference_candidates = result.get("reference_candidates", [])
        if candidates:
            top = candidates[0]
            uts = top["ultimate_tensile_strength_MPa"]; proof = top["proof_strength_0p2_MPa"]; life = top["creep_rupture"]["predicted_hours"]
            anchor = top.get("source_anchor") or {}
            anchor_name = str(anchor.get("alloy_name") or anchor.get("tag") or "当前来源参考")
            composition = top.get("composition_wt_percent") or {}
            composition_text = "；".join(
                f"{element} {float(value):.5f}" for element, value in composition.items()
                if float(value) > 0
            )
            elements = [str(element) for element, value in composition.items() if float(value) > 0]
            # The action return value is a compact downstream handoff. A label
            # alone cannot be turned back into a MatterGen element system.
            result["user_conclusion"] = (
                f"针对 {result['screening_conditions']['manufacturing_route']} 路线、"
                f"{result['screening_conditions']['test_temperature_C']:.0f} °C、"
                f"{result['screening_conditions']['applied_stress_MPa']:.0f} MPa 与指定热处理条件，"
                f"优先评估候选 {top['candidate_id']}（基于 {anchor_name} 的镍基高温合金研发候选；"
                f"元素体系：{'-'.join(elements)}；名义成分：{composition_text} wt.%）："
                f"短时抗拉强度初筛为 {uts['mean']:.0f} MPa（独立测试 MAE {uts['screening_MAE_MPa']:.0f} MPa），"
                f"预测蠕变断裂寿命为 {life:.1f} h（约 1.81 倍筛选误差因子）。"
                "该成分可作为后续新相结构生成的元素输入，结果仍用于确定 DFT/CALPHAD、氧化与力学试验的优先级。"
            )
        elif nearest_candidates:
            top = nearest_candidates[0]
            gaps = (top.get("strict_screening") or {}).get("gaps") or {}
            missing = [key for key, value in gaps.items() if float(value.get("shortfall", 0)) > 1e-9]
            result["user_conclusion"] = f"当前工况与目标组合下，暂未配比出同时严格满足全部需求的材料；候选 {top['candidate_id']} 最接近当前目标，作为下一轮配比迭代与验证的优先方向。建议重点围绕 {('、'.join(missing) or '当前指标')} 继续优化。"
        elif reference_candidates:
            top = reference_candidates[0]
            result["user_conclusion"] = f"针对 {result['screening_conditions']['test_temperature_C']:.0f} °C、{result['screening_conditions']['applied_stress_MPa']:.0f} MPa 的边界或外推工况，当前门槛通过数为 0；结果页保留综合排序最高的参考候选 {top['candidate_id']}，用于比较配方调整方向。"
        else:
            result["user_conclusion"] = "当前元素边界与工况下未形成可比较的候选；请放宽边界或检查路线、热处理、温度与载荷是否落在数据支持范围。"
        result["next_actions"] = ["确认优先候选的成分与热处理", "开展 CALPHAD 相稳定性和氧化风险筛查", "对前 2–3 个候选进行蠕变/拉伸验证"]
        result["downstream_handoff_text"] = "候选由已有高温镍基合金锚点的局部扰动生成，保留来源锚点和适用域信息；可用于下一步严格热力学与试验计划。"
