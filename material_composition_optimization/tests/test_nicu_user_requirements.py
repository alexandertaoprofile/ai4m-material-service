from src.alloy_workflow.contracts import requirement_plan, contract


def test_nickel_default_has_no_implicit_performance_minima():
    scope, plan = requirement_plan({'idea': '镍基高温合金配方优化'})
    assert scope['model_domain'] == 'ni_superalloy_hot_end'
    assert scope['screening_thresholds'] == {}
    assert 'screening_thresholds' not in {row['field'] for row in plan['default_assumptions']}


def test_nickel_explicit_minima_preserved_without_other_defaults():
    scope, _ = requirement_plan({'idea': '镍基高温合金配方优化，抗拉强度至少 900 MPa，屈服强度不低于 500 MPa，蠕变寿命至少250小时'})
    assert scope['screening_thresholds'] == {'uts_min_MPa': 900., 'proof_strength_min_MPa': 500., 'rupture_life_min_h': 250.}


def test_copper_explicit_strength_requirement_reaches_runtime():
    scope, _ = requirement_plan({'idea': '铜基再生冷却热壁局部配比优化，抗拉强度至少 1000 MPa'})
    assert scope['screening_thresholds'] == {'uts_min_MPa': 1000.}
    assert contract({'idea': '铜基热壁局部配比优化', 'alloy_optimization': scope})['screening_thresholds'] == {'uts_min_MPa': 1000.}


def test_conflicting_text_and_structured_gate_uses_current_text():
    scope, plan = requirement_plan({'idea': '镍基高温合金，抗拉强度至少900 MPa，屈服强度至少500 MPa',
        'alloy_optimization': {'screening_thresholds': {'uts_min_MPa': 1000.}}})
    assert scope['model_domain'] == 'ni_superalloy_hot_end'
    assert scope['screening_thresholds']['uts_min_MPa'] == 900
    assert scope['screening_thresholds']['proof_strength_min_MPa'] == 500
    assert plan['execution_notes']


def test_consistent_structured_gate_retains_other_text_gate():
    scope, _ = requirement_plan({'idea': '镍基高温合金，抗拉强度至少900 MPa，屈服强度至少500 MPa',
        'alloy_optimization': {'screening_thresholds': {'uts_min_MPa': 900.}}})
    assert scope['screening_thresholds'] == {'uts_min_MPa': 900., 'proof_strength_min_MPa': 500.}
