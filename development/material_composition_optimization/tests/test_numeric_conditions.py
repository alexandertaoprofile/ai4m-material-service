from copy import deepcopy
from unittest.mock import Mock,patch
import pytest
from src.alloy_workflow.contracts import requirement_plan,contract
from src.alloy_workflow.application import AlloyOptimizationApplication

GL='chip_glass_thermomechanical_family_v1';SS='reusable_rocket_stainless';GEN='generic_composition_design_fallback_v1'

@pytest.mark.parametrize('goal',['CTE目标为3.8 ppm/K','CTE接近3.8×10^-6/K','CTE为3.8 ppm/K',
    '热膨胀系数接近3.8 ppm/℃','CTE接近3.8e-6/K','CTE接近3.8 10⁻⁶/K'])
def test_equivalent_cte_targets(goal):
    scope,_=requirement_plan({'idea':'优化芯片玻璃基板配方，'+goal})
    assert scope['model_domain']==GL
    assert scope['objectives']['CTE_linear_0_to_300C']=={'goal':'target','target':3.8,'unit':'ppm/K'}

@pytest.mark.parametrize('phrase,expected',[('500°C下评价强度',773.15),('温度500℃',773.15),
    ('测试温度−100°C',173.15),('测试温度500°C，改为600°C',873.15),('工作温度为773.15 K',773.15)])
def test_temperature_semantics(phrase,expected):
    scope,_=requirement_plan({'idea':'优化316L不锈钢配方，'+phrase})
    assert scope['test_temperature_K']==expected

def test_heat_treatment_temperature_is_not_test_temperature():
    scope,_=requirement_plan({'idea':'优化不锈钢配方，固溶温度1050°C，时效温度700°C'})
    assert scope['test_temperature_K']==293

@pytest.mark.parametrize('phrase',['E至少85 GPa','杨氏模量至少85000 MPa','弹性模量不低于85 gpa'])
def test_modulus_aliases(phrase):
    scope,_=requirement_plan({'idea':'优化芯片玻璃配方，'+phrase})
    assert scope['screening_thresholds']['young_modulus_GPa_min']==85

def test_partial_user_ranges_merge_with_other_user_ranges():
    scope,plan=requirement_plan({'idea':'优化不锈钢配比，Cr含量17到18 wt.%',
        'constraints':{'element_bounds_wt_percent':{'Cr':[16,19],'Ni':[9,10]}}})
    assert scope['element_bounds_wt_percent']['Cr']==[17,18]
    assert scope['element_bounds_wt_percent']['Ni']==[9,10]
    assert scope['element_bounds_wt_percent']['Mn']==[.8,2]
    assert plan['execution_notes']

@pytest.mark.parametrize('phrase',['Cr含量17到18 wt.%','铬含量17–18 wt.%','铬（Cr）含量17%至18%'])
def test_element_range_forms(phrase):
    scope,_=requirement_plan({'idea':'优化不锈钢配方，'+phrase})
    assert scope['element_bounds_wt_percent']['Cr']==[17,18]

@pytest.mark.parametrize('phrase',['Cr含量18到17 wt.%','Cr含量17到18 at.%','Cr含量17到180 wt.%'])
def test_bad_or_incompatible_ranges_are_not_silently_repaired(phrase):
    scope,plan=requirement_plan({'idea':'优化不锈钢配方，'+phrase})
    assert scope['model_domain']==SS and phrase in plan['raw_requirement']
    assert scope['_calculation_blockers']
    assert not plan.get('requires_domain_confirmation')

def test_cf_volume_range_is_carried_with_seed_centre():
    scope,_=requirement_plan({'idea':'优化PLA-CF配方，碳纤维体积分数20%到25%'})
    assert scope['candidate_windows']['target_vf']==[.2,.25]
    assert scope['target_vf']==.225


def test_cf_empty_seed_preserves_range_and_completes_design():
    runner=Mock()
    runner.run.return_value={'model_domain':'short_cf_thermomechanical_rve_v1','all_candidates':[],
        'screening_conditions':{'matrix_name':'PLA'}}
    result,_=AlloyOptimizationApplication(runner,'test').propose_space(
        {'idea':'优化PLA-CF配方，碳纤维体积分数20%到25%'})
    assert result['status']=='completed'
    assert result['model_domain']=='short_cf_thermomechanical_rve_v1'
    effective=result['requirement_interpretation']['effective_model_input']
    assert effective['candidate_windows']['target_vf']==[.2,.25]
    assert result['specialist_outcome']['kind']=='no_supported_reference'
    runner.submit_tensile_shadow.assert_not_called()

@pytest.mark.parametrize('idea',['优化PLA-CF配方，碳纤维质量分数20%到25%',
    '优化芯片玻璃配方，E至少85 psi','优化芯片玻璃配方，导热率至少5 W/mK',
    '优化不锈钢配比，测试温度-500°C'])
def test_unsupported_or_nonphysical_requests_finish_with_design(idea):
    scope,plan=requirement_plan({'idea':idea})
    assert scope['model_domain']!=GEN
    assert idea in plan['raw_requirement']
    assert scope.get('_calculation_blockers') or scope.get('_uncomputed_requirements')

@pytest.mark.parametrize('alias,domain',[('6061-T6','aerospace_aluminum_composition_v1'),('TC4',GEN),
    ('IN718','ni_superalloy_hot_end'),('CuCrNb','copper_hot_end_local_composition_v1'),
    ('high entropy alloy','hea_mpea'),('芯片封装的玻璃',GL)])
def test_new_aliases(alias,domain):
    from main import _safe_requirement_plan
    assert _safe_requirement_plan({'idea':alias+'，帮我优化一下配方'})[0]['model_domain']==domain

def test_selected_material_beats_other_paper_material():
    scope,_=requirement_plan({'idea':'优化上述配方','upstream_result':{'selected_material':{'name':'芯片玻璃基板'}},
        'literature_result':{'material_name':'镍基高温合金'}})
    assert scope['model_domain']==GL

def test_priority_clause_resolves_multiple_families():
    scope,plan=requirement_plan({'idea':'铝合金和钛合金都考虑，先优化航空铝合金配方'})
    assert scope['model_domain']=='aerospace_aluminum_composition_v1'
    assert plan['execution_notes']

def test_missing_identity_does_not_invent_a_metal():
    scope,_=requirement_plan({'idea':'做一个配方优化'})
    assert scope['model_domain']==GEN
    assert scope['material_family']=='材料体系待明确的配方设计'
