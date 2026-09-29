import pytest

from src.alloy_workflow.contracts import is_reusable_rocket_stainless_intent, requirement_plan


@pytest.mark.parametrize('text', [
    '针对可回收不锈钢材料做一个配比优化',
    '针对可回收的不锈钢做配方优化',
    '优化可回收型不锈钢的组分',
    '设计可回收用不锈钢配方',
    '可回收 不锈钢配比优化',
])
def test_confirmed_product_alias_routes_to_the_existing_30x_workflow(text):
    effective, plan = requirement_plan({'idea': text})
    assert effective['model_domain'] == 'reusable_rocket_stainless'
    assert plan['template'] == 'reusable_rocket_stainless_screening'
    assert effective['test_temperature_K'] == 293
    assert effective['processing']['material_state'] == 'solution_annealed'
    assert set(effective['element_bounds_wt_percent']) == {'Cr', 'Ni', 'Mn', 'Si', 'C', 'N'}


@pytest.mark.parametrize('text,domain', [
    ('不考虑可回收不锈钢，改用7075航空铝合金做配比优化', 'aerospace_aluminum_composition_v1'),
    ('排除可回收不锈钢，针对芯片玻璃基板做配方优化', 'chip_glass_thermomechanical_family_v1'),
])
def test_excluded_alias_does_not_override_the_current_material(text, domain):
    assert requirement_plan({'idea': text})[0]['model_domain'] == domain


@pytest.mark.parametrize('text', ['废不锈钢回收料配方', '再生不锈钢原料回收率', '回收不锈钢废料'])
def test_alias_does_not_expand_to_unconfirmed_scrap_feedstock_model(text):
    assert not is_reusable_rocket_stainless_intent(text, {})


def test_explicit_model_route_remains_supported_without_the_alias():
    effective, _ = requirement_plan({'idea': '优化这一配方', 'constraints': {'model_domain': 'reusable_rocket_stainless'}})
    assert effective['model_domain'] == 'reusable_rocket_stainless'
