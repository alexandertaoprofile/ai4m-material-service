from copy import deepcopy
from recipe_display import from_rsi, build_markdown, validate_markdown
from shadow_recipe_runner import build_request


def test_service_reference_policy_is_explicit_and_inputs_unchanged():
    for domain in ('ni_superalloy_hot_end', 'copper_hot_end_local_composition_v1'):
        task = {'model_domain': domain, '_resolved_scope': {'model_domain': domain}, 'screening_conditions': {}}
        original = deepcopy(task)
        request = build_request(task)
        assert request['model_stack'] == 'specialist' and request['reference_policy'] == 'production'
        assert task == original


def test_nearest_recipe_keeps_optimizer_definition_and_no_false_pass():
    domain = 'copper_hot_end_local_composition_v1'
    flags = {'public_effect': False, 'display_allowed': False, 'production_release_authorized': False}
    row = {'composition': {'values': {'Cu': 90., 'Cr': 5., 'Nb': 5.}, 'anchor_index': 0},
        'values': {'UTS_MPa': 800., 'YS_MPa': 500.}, 'F': [-800., -500.],
        'feasible': False, 'admissible': True, 'property_sources': {'UTS_MPa': 'specialist', 'YS_MPa': 'specialist'},
        'constraint_violation': .2, 'constraint_gaps': {'uts_min_MPa': {'property': 'UTS_MPa', 'target': 1000., 'observed': 800., 'shortfall': 200., 'relative_shortfall': .2, 'direction': 'min', 'unit': 'MPa'}}}
    response = {'ok': True, 'operation': 'optimize', **flags, 'result': {
        'domain': domain, **flags, 'method': 'nsga2', 'stop_reason': 'model_evaluation_budget',
        'objectives': [{'property': 'UTS_MPa', 'mode': 'max', 'unit': 'MPa'}, {'property': 'YS_MPa', 'mode': 'max', 'unit': 'MPa'}],
        'pareto_front': [], 'nearest_candidates': [row], 'history': [row], 'counters': {'model_evaluations': 32},
        'scope': {'screening_thresholds': {'uts_min_MPa': 1000.}}, 'material_conditions': {'test_temperature_C': 25., 'processing_state': 'as_received'}}}
    report = from_rsi(response, {'model_domain': domain})
    text = build_markdown(report); validate_markdown(text)
    assert report['candidates'] == [] and len(report['nearest_candidates']) == 1
    assert 'NSGA-II在成分与工艺条件内迭代' in text
    assert '本次接近目标的配方' in text and '差 200.0 MPa' in text
    assert '本次优选配方：' not in text
