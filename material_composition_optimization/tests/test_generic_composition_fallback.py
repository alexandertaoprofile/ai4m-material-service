from pathlib import Path

from src.alloy_workflow.fallback import generic_fallback_result
from src.alloy_workflow.presentation import generic_composition_fallback_summary_block
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
    assert result["initial_candidates"][0]["candidate_kind"] == "llm_assisted_exploration_template"
    report = generic_composition_fallback_summary_block(result)
    assert report.startswith("### 通用配比方案与验证路径")
    assert "没有已训练并通过验证的专项预测模型" in report
    assert "{{VISUAL:generic_composition_funnel}}" in report


def test_fallback_uses_the_standard_tokenized_png_render_path(tmp_path: Path):
    result, _ = generic_fallback_result(PAYLOAD, "未命中已验证专项模型", "alloy-composition-optimization")
    runtime = AlloyRuntime()
    runtime.results_root = tmp_path
    assets = runtime._render(result)
    assert assets["generic_composition_funnel"].is_file()
    assert assets["summary_markdown"].is_file()
