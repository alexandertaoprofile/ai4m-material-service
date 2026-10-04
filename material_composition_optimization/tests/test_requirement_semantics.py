from unittest.mock import Mock
from unittest.mock import patch
from copy import deepcopy
from src.alloy_workflow.contracts import requirement_plan
from src.alloy_workflow.application import AlloyOptimizationApplication

GL='chip_glass_thermomechanical_family_v1'
SS='reusable_rocket_stainless'


def test_ambiguous_request_never_calls_model():
    runner=Mock()
    result,_=AlloyOptimizationApplication(runner,'test').propose_space({'idea':'分别优化玻璃基板和航空铝合金配方'})
    assert result['status']=='completed_with_fallback'
    assert '多材料' in result['screening_conditions']['material_family']
    runner.run.assert_not_called()
    runner.submit_tensile_shadow.assert_not_called()


def test_missing_cte_target_continues_with_visible_starting_goal():
    scope,plan=requirement_plan({'idea':'优化芯片玻璃基板配方，使其匹配相邻材料的热膨胀系数'})
    assert scope['model_domain']==GL
    assert scope['objectives']['CTE_linear_0_to_300C']['goal']=='minimize'
    assert 'target' not in scope['objectives']['CTE_linear_0_to_300C']
    assert plan['execution_notes'][0]['parameter']=='热膨胀匹配目标'
    assert not plan.get('requires_domain_confirmation')


def test_paper_values_are_not_new_design_constraints():
    scope,plan=requirement_plan({'idea':'优化上述材料配方','literature_result':{'role':'tool','result':{
        'material_name':'镍基高温合金','summary':'文献测试温度800°C，抗拉强度至少1200 MPa',
        'test_temperature_C':800}}})
    assert scope['test_temperature_C']==800
    assert scope['screening_thresholds']=={}
    assert plan['field_provenance']['test_temperature_C']=='upstream_context'


def test_current_temperature_overrides_inherited_condition():
    scope,plan=requirement_plan({'idea':'优化上述配方，测试温度500°C','simulation_result':{
        'role':'tool','result':{'material_name':'316L不锈钢','test_temperature_K':293}}})
    assert scope['model_domain']==SS
    assert scope['test_temperature_K']==773.15
    assert plan['field_provenance']['test_temperature_K']=='adopted_assumption'


def test_upstream_conflict_requires_choice():
    scope,plan=requirement_plan({'idea':'优化上述材料配方',
        'simulation_result':{'material_name':'316L不锈钢'},
        'literature_result':{'material_name':'芯片玻璃基板'}})
    assert scope['model_domain']=='generic_composition_design_fallback_v1'
    assert not plan.get('requires_domain_confirmation')
    assert '316L' in plan['raw_requirement'] and '芯片玻璃' in plan['raw_requirement']


def test_upstream_composite_keeps_matrix_identity():
    scope,plan=requirement_plan({'idea':'继续优化配方','simulation_result':{'material_name':'短碳纤维增强PC'}})
    assert scope['matrix_name']=='PC'
    assert plan['field_provenance']['matrix_name']=='upstream_context'


def test_structured_simulation_conditions_can_be_adopted():
    scope,_=requirement_plan({'idea':'优化上述材料的配方','simulation_result':{
        'material_name':'奥氏体不锈钢','simulation_conditions':{'test_temperature_K':773.15}}})
    assert scope['test_temperature_K']==773.15


def test_missing_temperature_unit_uses_visible_assumption():
    scope,plan=requirement_plan({'idea':'优化奥氏体不锈钢配方，测试温度500'})
    assert scope['test_temperature_K']==773.15
    assert plan['execution_notes'][0]['adopted']=='500°C'


def test_conflicting_objective_aliases_request_confirmation():
    scope,plan=requirement_plan({'idea':'优化玻璃基板配方，热膨胀系数接近3.8 ppm/K',
        'constraints':{'objectives':{'cte_0_300_ppm_K':{'goal':'target','target':3.5,'unit':'ppm/K'}}}})
    assert scope['objectives']['CTE_linear_0_to_300C']['target']==3.8
    assert plan['execution_notes']


def test_target_and_hard_limit_are_independent():
    scope,_=requirement_plan({'idea':'优化芯片玻璃基板配方，热膨胀系数接近3.3 ppm/K，弹性模量至少85 GPa'})
    assert scope['objectives']=={'CTE_linear_0_to_300C':{'goal':'target','target':3.3,'unit':'ppm/K'}}
    assert scope['screening_thresholds']=={'young_modulus_GPa_min':85}


def test_no_false_conversion_of_negative_goal():
    scope,plan=requirement_plan({'idea':'优化芯片玻璃基板配方，不要降低热膨胀系数'})
    assert scope['model_domain']==GL
    assert 'CTE_linear_0_to_300C' not in scope['objectives']
    assert '不要降低' in plan['raw_requirement']


def test_default_and_explicit_glass_goals_have_distinct_provenance():
    a,p=requirement_plan({'idea':'优化芯片玻璃基板配方'})
    b,q=requirement_plan({'idea':'优化芯片玻璃基板配方，降低热膨胀系数'})
    assert p['field_provenance']['objectives']=='platform_default'
    assert q['field_provenance']['objectives']=='text_extraction'
    assert len(a['objectives'])==3 and len(b['objectives'])==1


def test_glass_seed_copy_keeps_requested_target_untouched():
    from src.alloy_workflow.contracts import contract
    from src.alloy_workflow.recipe_primary import bootstrap_constraints
    scope,_=requirement_plan({'idea':'优化玻璃基板配方，热膨胀系数接近3.8 ppm/K，弹性模量至少82 GPa'})
    request=contract({'idea':'优化玻璃基板配方','alloy_optimization':scope})
    request['raw_scope']=deepcopy(scope)
    before=deepcopy(request)
    with patch('src.alloy_workflow.recipe_primary.mode_for',return_value='primary'):
        seed=bootstrap_constraints(request)
    assert seed['objectives']=={}
    assert seed['screening_thresholds']['young_modulus_GPa_min']==82
    assert request==before
    assert request['objectives']['CTE_linear_0_to_300C']['target']==3.8
