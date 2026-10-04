import pytest

from src.alloy_workflow.contracts import is_composite_material_request, requirement_plan


def test_existing_copper_grade_query_is_redirected_to_mature_material_catalogue():
    payload = {
        "idea": "GRCop-42、GRCop-84、CuCrZr、NARloy-Z 铜基单合金热端筛选；"
        "体系内不含树脂、纤维、填料、涂层或复合相；900 K 热导率不低于 250 W/(m·K)。"
    }
    assert not is_composite_material_request(payload["idea"], {})
    with pytest.raises(ValueError, match="1105"):
        requirement_plan(payload)


def test_copper_named_composition_design_routes_to_conditioned_local_screening():
    payload = {
        "idea": "以 GRCop-84/CuCrZr 的铜基热壁合金为基础做 Cr、Nb、Zr 局部配比优化，室温固溶时效状态。",
        "alloy_optimization": {"processing_state": "solution_aged", "test_temperature_C": 25, "num_candidates": 4},
    }
    effective, plan = requirement_plan(payload)
    assert effective["model_domain"] == "copper_hot_end_local_composition_v1"
    assert plan["template"] == "copper_hot_end_conditioned_local_composition_screening"
    assert effective["ambient_process"]["cold_reduction_pct"] == 50


def test_copper_default_process_allows_partial_user_override():
    effective, _ = requirement_plan({
        "idea": "对铜基热壁合金进行 Cr、Nb、Zr 局部配比优化。",
        "alloy_optimization": {"ambient_process": {"cold_reduction_pct": 65}},
    })
    assert effective["ambient_process"]["cold_reduction_pct"] == 65
    assert effective["ambient_process"]["aging_temperature_K"] == 723
    assert effective["composition_family"] == "GRCop_type_Cu_Cr_Nb"
    assert effective["processing_state"] == "as_received"
    assert effective["element_bounds_wt_percent"]["Cr"][0] == 4
