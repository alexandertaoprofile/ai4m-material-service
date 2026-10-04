from copy import deepcopy
from unittest.mock import Mock,patch
import pytest
from src.alloy_workflow.contracts import requirement_plan
from src.alloy_workflow.application import AlloyOptimizationApplication
from src.alloy_workflow.recipe_primary import helper,render_primary

CASES=[('高熵合金','hea_mpea'),('镍基高温合金','ni_superalloy_hot_end'),
    ('铜基合金','copper_hot_end_local_composition_v1'),('航空铝合金','aerospace_aluminum_composition_v1'),
    ('316L不锈钢','reusable_rocket_stainless'),('芯片封装玻璃','chip_glass_thermomechanical_family_v1'),
    ('PLA-CF','short_cf_thermomechanical_rve_v1'),('卤化物钙钛矿','perovskite_transport_stability_v2')]

@pytest.mark.parametrize('name,domain',CASES)
def test_unavailable_property_retains_all_eight_routes(name,domain):
    scope,plan=requirement_plan({'idea':f'优化{name}配方，导热率至少1000 W/mK'})
    assert scope['model_domain']==domain and scope['_uncomputed_requirements']
    assert '1000' in str(scope['_uncomputed_requirements'])

@pytest.mark.parametrize('name,domain',CASES)
def test_structured_unknown_goal_kept_for_disclosure(name,domain):
    scope,plan=requirement_plan({'idea':f'优化{name}配方','constraints':{
        'objectives':{'unknown_property':{'goal':'target','target':9999}}}})
    assert scope['model_domain']==domain
    assert scope['_uncomputed_structured_requirements']['objectives']['unknown_property']['target']==9999
    assert 'unknown_property' not in scope['objectives']

@pytest.mark.parametrize('name,domain',CASES)
def test_model_execution_failure_does_not_change_route_or_fake_success(name,domain,tmp_path):
    runner=Mock();runner.run.side_effect=RuntimeError('private diagnostic')
    result,_=AlloyOptimizationApplication(runner,'test').propose_space({'idea':f'优化{name}配方'})
    assert result['model_domain']==domain and result['status']=='completed'
    assert result['specialist_outcome']['kind']=='calculation_error'
    assert result['initial_candidates']==[]
    paths=render_primary(result,tmp_path/domain)
    page=paths['summary'].read_text()
    assert 'private diagnostic' not in page
    assert '### 6. 优化结论' in page and '本次仍由' in page
    assert '通用配方' not in page and '{{VISUAL:' not in page
    assert result['user_conclusion'].startswith(page.split('### 6. 优化结论\n\n')[1])

@pytest.mark.parametrize('goal',['屈服强度至少99999 MPa','UTS接近99999 MPa'])
def test_extreme_performance_goal_not_relaxed(goal):
    scope,plan=requirement_plan({'idea':'优化不锈钢配方，'+goal})
    assert scope['model_domain']=='reusable_rocket_stainless'
    assert '99999' in str(scope.get('objectives'))+str(scope.get('screening_thresholds'))
    assert not scope.get('_calculation_blockers')


def test_partial_property_request_reaches_specialist_and_discloses_missing():
    payload={'idea':'优化芯片玻璃配方，CTE接近3.8 ppm/K，导热率至少5 W/mK'}
    runner=Mock();runner.run.return_value={'model_domain':'chip_glass_thermomechanical_family_v1'}
    with patch('src.alloy_workflow.recipe_primary.mode_for',return_value='legacy'):
        result,_=AlloyOptimizationApplication(runner,'test').propose_space(payload)
    assert result['model_domain']=='chip_glass_thermomechanical_family_v1'
    assert result['requirement_interpretation']['effective_model_input']['objectives']['CTE_linear_0_to_300C']['target']==3.8
    runner.run.assert_called_once()


def test_unadapted_material_still_uses_design_route():
    runner=Mock()
    result,_=AlloyOptimizationApplication(runner,'test').propose_space({'idea':'优化TC4钛合金配方'})
    assert result['model_domain']=='generic_composition_design_fallback_v1'
    runner.run.assert_not_called()


def test_invalid_composition_same_route_without_inference():
    runner=Mock()
    result,_=AlloyOptimizationApplication(runner,'test').propose_space({'idea':'优化316L不锈钢配方，Cr含量18到17 wt.%'})
    assert result['model_domain']=='reusable_rocket_stainless'
    assert result['specialist_outcome']['kind']=='input_conditions'
    runner.run.assert_not_called()
