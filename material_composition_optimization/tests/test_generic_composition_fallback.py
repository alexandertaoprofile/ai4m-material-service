from pathlib import Path

from src.alloy_workflow.fallback import generic_fallback_result
from src.alloy_workflow.presentation import generic_composition_fallback_summary_block, planned_alloy_method_block
from src.alloy_workflow.runtime import AlloyRuntime


PAYLOAD = {
    "taskid": "generic-fallback-contract",
    "idea": "针对环氧树脂、连续玻璃纤维和导热填料的复合体系，给出一个材料配比方案。",
}


def test_untrained_material_family_returns_a_visible_generic_formulation_instead_of_error():
    result, constraints = generic_fallback_result(PAYLOAD, "未命中已验证专项模型", "alloy-composition-optimization")
    assert constraints["taskid"] == "generic-fallback-contract"
    assert result["status"] == "completed_with_fallback"
    assert result["model_domain"] == "generic_composition_design_fallback_v1"
    assert len(result["supported_model_capabilities"]) == 8
    assert result["initial_candidates"][0]["candidate_kind"] == "rule_assisted_experiment_template"
    report = generic_composition_fallback_summary_block(result)
    assert report.startswith("### 1. 问题描述")
    assert "本次输入尚未匹配到可执行的预测模型" in report
    assert "当前材料类型没有已训练完成的专项模型" not in report
    assert "| 支持的材料类别 |" not in report
    headings = ["### 1.", "### 2.", "### 3.", "### 4.", "### 5.", "### 5.1", "### 5.2", "#### 结论"]
    assert [report.index(heading) for heading in headings] == sorted(report.index(heading) for heading in headings)
    assert "复合材料/多相体系" in report
    assert "必须同步锁定的工艺变量" in report
    assert "每个样品至少记录" in report
    assert "{{VISUAL:" not in report
    assert "\n- " in report and "\n\n- " in report
    assert "$x_i$" in report and "$l_i$" in report and "$u_i$" in report


def test_titanium_fallback_uses_a_family_specific_first_round_design():
    result, _ = generic_fallback_result(
        {"taskid": "generic-titanium", "idea": "设计一种高强韧钛合金配比并给出试验方案"},
        "未命中已验证专项模型",
        "alloy-composition-optimization",
    )
    candidate = result["initial_candidates"][0]
    assert candidate["material_family"] == "钛合金探索体系"
    assert candidate["composition_basis"] == "wt.%；Ti 作为平衡元素"
    assert any("β 稳定元素" == item["role"] for item in candidate["formulation_components"])
    assert "β 稳定元素" in candidate["first_round_strategy"]
    report = generic_composition_fallback_summary_block(result)
    assert "相区与热处理" in report
    assert "固溶/时效参数" in report


def test_plastic_fallback_uses_polymer_components_and_process_controls():
    payload = {
        "taskid": "generic-plastic",
        "idea": "针对塑料配比筛选，明确塑料基体、目标性能、填料和助剂及加工约束。",
    }
    result, _ = generic_fallback_result(payload, "未命中已验证专项模型", "alloy-composition-optimization")
    candidate = result["initial_candidates"][0]
    assert candidate["material_family"] == "热塑性塑料/聚合物配方"
    assert candidate["composition_basis"].startswith("wt.%")
    assert [item["role"] for item in candidate["formulation_components"]][:3] == [
        "基础树脂", "共混树脂/增韧剂", "功能填料",
    ]
    assert any(item["variable"] == "混炼过程" for item in candidate["process_variables"])
    report = generic_composition_fallback_summary_block(result)
    assert "热塑性塑料/聚合物配方" in report
    assert "原料含湿量与干燥制度" in report
    preview = planned_alloy_method_block(payload)
    assert preview.startswith("### 配方设计：任务准备")
    assert "当前专项模型支持范围" not in preview
    assert "单因素试验设计" in preview
    assert "Transformer" not in preview and "NSGA-II" not in preview


def test_fallback_publishes_the_plan_without_fabricated_screening_chart(tmp_path: Path):
    result, _ = generic_fallback_result(PAYLOAD, "未命中已验证专项模型", "alloy-composition-optimization")
    runtime = AlloyRuntime()
    runtime.results_root = tmp_path
    assets = runtime._render(result)
    assert "generic_composition_funnel" not in assets
    assert not any(path.suffix == ".png" for path in assets.values())
    assert assets["summary_markdown"].is_file()
