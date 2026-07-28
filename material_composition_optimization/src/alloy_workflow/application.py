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
        normalized = dict(payload)
        normalized["alloy_optimization"] = effective
        constraints = contract(normalized)
        constraints["raw_scope"] = effective
        started = time.perf_counter()
        result = self.runner.run(constraints["taskid"], "propose", constraints)
        result.update({"taskid": constraints["taskid"], "status": "completed", "service": self.service_name, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        self._enrich(result, plan)
        return result, constraints

    def evaluate(self, payload: dict[str, Any], candidates: list[Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        constraints = contract(payload)
        operation = "evaluate_batch" if candidates is not None else "evaluate"
        return self.runner.run(constraints["taskid"], operation, constraints, candidates), constraints

    def _enrich(self, result: dict[str, Any], plan: dict[str, Any]) -> None:
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
