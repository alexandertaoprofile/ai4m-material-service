"""Presentation changes must preserve specialist values and strict eligibility."""
from copy import deepcopy

import pytest

from src.alloy_workflow.presentation import (
    _embed_copper_local_visuals,
    _embed_hot_end_visuals,
    _recipe_composition_table,
    copper_hot_end_local_summary_block,
    hot_end_summary_block,
    planned_alloy_method_block,
)


def nickel_result():
    return {
        "model_domain": "ni_superalloy_hot_end",
        "screening_conditions": {
            "manufacturing_route": "single_crystal",
            "test_temperature_C": 950,
            "applied_stress_MPa": 250,
            "heat_treatment": "solution_stage_1:1315C/4h",
        },
        "sampling": {"generated": 120, "funnel_stages": [
            {"label": "满足成分约束", "count": 120},
            {"label": "严格门槛通过", "count": 1},
        ]},
        "initial_candidates": [{
            "candidate_id": "NIH-021",
            "composition_wt_percent": {"Cr": 8, "Co": 7, "Al": 5.5, "Ta": 6.5, "W": 8, "Re": 4, "Ni": 61},
            "source_anchor": {"alloy_name": "RENE N5"},
            "ultimate_tensile_strength_MPa": {"mean": 960, "screening_MAE_MPa": 111},
            "proof_strength_0p2_MPa": {"mean": 630, "screening_MAE_MPa": 86},
            "creep_rupture": {"predicted_hours": 414.7, "screening_error_factor": 1.81},
            "elongation_percent_auxiliary": {"elongation_percent": 8.2},
            "applicability_domain": {"level": "inside"},
        }],
    }


def copper_result():
    return {
        "model_domain": "copper_hot_end_local_composition_v1",
        "screening_conditions": {
            "processing_state": "solution_aged", "test_temperature_C": 25,
            "element_bounds_wt_percent": {"Cr": [4, 7], "Nb": [2, 4]},
        },
        "sampling": {"funnel_stages": [{"label": "条件核对通过", "count": 1}]},
        "initial_candidates": [{
            "rank": 1, "candidate_id": "CUL-021",
            "composition_wt_percent": {"Cr": 6, "Nb": 2.98, "Zr": .02, "Cu": 91},
            "source_anchor": {"alloy_name": "GRCop-84", "alloy_state": "solution_aged", "test_temperature_C": 25},
            "applicability_domain": {"level": "inside"},
            "short_time_tensile": {
                "ultimate_tensile_strength_MPa": {"mean": 630, "screening_MAE_MPa": 23, "evidence_level": "B"},
                "yield_0p2_MPa": {"mean": 420, "screening_MAE_MPa": 19, "evidence_level": "B"},
            },
            "additional_properties": {
                "electrical_conductivity_percent_IACS": {"mean": 74, "unit": "%IACS"},
                "thermal_conductivity_room_temperature_W_mK": {"mean": 313, "low": 282, "high": 344, "unit": "W/(m·K)"},
                "hardness_HV": {"mean": 128, "unit": "HV"},
                "density_g_cm3": {"mean": 8.82, "unit": "g/cm³"},
                "CTE_room_temperature_ppm_per_K": {"mean": 17.1, "unit": "ppm/K"},
            },
        }],
        "user_conclusion": "优先评估CUL-021，按同一工艺状态安排拉伸试验。",
    }


def test_nickel_card_has_element_table_and_all_four_existing_properties():
    result = nickel_result()
    before = deepcopy(result)
    report = hot_end_summary_block(result)
    assert "### 高温镍基合金｜本次优选配方：NIH-021" in report
    assert "| 镍（平衡元素） | Ni | 61 |" in report
    assert "| 铼 | Re | 4 |" in report
    assert report.index("| 镍（平衡元素）") < report.index("| 铬 |")
    for value in ("960 MPa", "630 MPa", "414.7 h", "8.2%", "1.81×", "RENE N5", "单晶", "950 °C", "250 MPa"):
        assert value in report
    assert "Transformer" not in report and "NSGA-II" not in report
    assert result == before


def test_nickel_nearest_candidate_is_not_relabelled_as_a_feasible_recipe():
    result = nickel_result()
    candidate = result["initial_candidates"].pop()
    candidate["strict_screening"] = {"gaps": {"uts_min_MPa": {"shortfall": 40}}}
    result["nearest_candidates"] = [candidate]
    report = hot_end_summary_block(result)
    assert "当前筛选门槛通过数为 0" in report
    assert "UTS差 40 MPa" in report
    assert "### 高温镍基合金｜本次优先评估配方" in report
    assert "｜本次优选配方" not in report


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_nickel_missing_elongation_is_not_displayed_as_zero(missing):
    result = nickel_result()
    result["initial_candidates"][0]["elongation_percent_auxiliary"]["elongation_percent"] = missing
    report = hot_end_summary_block(result)
    assert "| 延伸率（辅助） | 当前未收录 |" in report
    assert "nan" not in report.lower() and "| 延伸率（辅助） | 0.0%" not in report


def test_copper_card_keeps_all_specialist_and_auxiliary_results():
    result = copper_result()
    before = deepcopy(result)
    report = copper_hot_end_local_summary_block(result)
    assert "### 铜基热壁合金｜本次优选配方：CUL-021" in report
    assert "| 铜（平衡元素） | Cu | 91 |" in report
    assert "| 锆 | Zr | 0.02 |" in report
    for value in ("630 MPa", "420 MPa", "74.00 %IACS", "313.00（282.00–344.00", "128.00 HV", "8.82 g/cm³", "17.10 ppm/K"):
        assert value in report
    assert "Transformer" not in report and "NSGA-II" not in report
    assert result == before


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_copper_missing_iacs_has_no_fabricated_zero_or_interval(missing):
    result = copper_result()
    result["initial_candidates"][0]["additional_properties"]["electrical_conductivity_percent_IACS"]["mean"] = missing
    report = copper_hot_end_local_summary_block(result)
    assert "| 电导率（C） | 当前未收录 |" in report
    assert "nan" not in report.lower()
    assert "74.00（0.00–0.00" not in report


def test_trace_elements_are_preserved_and_absent_elements_are_not_invented():
    table = "\n".join(_recipe_composition_table({"Fe": 67.06451, "Cr": 18.71, "N": .02136, "P": None, "S": 0}, "Fe"))
    assert "| 铁（平衡元素） | Fe | 67.06451 |" in table
    assert "| 氮 | N | 0.02136 |" in table
    assert "| P |" not in table and "| S |" not in table


@pytest.mark.parametrize("factory,render,embed,names", [
    (nickel_result, hot_end_summary_block, _embed_hot_end_visuals,
     ("hot_end_screening_funnel", "hot_end_strength_life_tradeoff", "hot_end_composition_traceability")),
    (copper_result, copper_hot_end_local_summary_block, _embed_copper_local_visuals,
     ("copper_local_screening_funnel", "copper_local_strength_tradeoff", "copper_local_composition_traceability")),
])
def test_existing_visual_embedding_contract_is_preserved(factory, render, embed, names):
    assets = [{"name": name, "url": f"https://images.example.test/{name}.png", "title": name} for name in names]
    report = embed(render(factory()), assets)
    assert "{{VISUAL:" not in report
    assert report.count("![") == 3
    assert report.rfind("#### 结论") > report.rfind("![")
    assert "{{VISUAL:" not in embed(render(factory()), [])


def test_copper_page_has_one_model_definition_and_one_ordered_heading_sequence():
    payload = {
        "idea": "对铜基热壁合金进行Cr、Nb、Zr局部配比优化",
        "alloy_optimization": {"processing_state": "solution_aged", "test_temperature_C": 25},
    }
    report = planned_alloy_method_block(payload) + "\n\n" + copper_hot_end_local_summary_block(copper_result())
    for heading in ("### 1.", "### 2.", "### 3.", "### 4.", "### 5."):
        assert report.count(heading + " ") == 1
    assert "$$k\\approx L_0\\sigma_eT_{abs}$$" in report
    assert "$L_0$为洛伦兹数" in report
