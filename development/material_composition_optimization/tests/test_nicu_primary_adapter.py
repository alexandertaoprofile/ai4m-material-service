from copy import deepcopy
from types import SimpleNamespace
import asyncio
import json

import pytest

from src.alloy_workflow import recipe_primary as p
from src.alloy_workflow import protocol


@pytest.mark.parametrize('domain', sorted(p.NICU))
def test_new_adapter_needs_its_own_domain_acceptance(tmp_path, monkeypatch, domain):
    path = tmp_path / 'release.json'
    config = {'approved_after_acceptance': True, 'domains': {domain: 'primary'}}
    monkeypatch.setenv('MATERIAL_RECIPE_RELEASE_CONFIG', str(path))
    path.write_text(json.dumps(config))
    assert p.mode_for(domain) == 'shadow'
    config['domain_acceptance'] = {domain: {'approved_after_acceptance': True,
        'adapter_version': p.NICU_ADAPTER_VERSION, 'model_stack': 'specialist', 'authorization': 'isolated-test'}}
    path.write_text(json.dumps(config))
    assert p.mode_for(domain) == 'primary'
    for key, value in [('approved_after_acceptance', False), ('adapter_version', 'old'),
                       ('model_stack', 'transformer_plus_specialist'), ('authorization', '')]:
        changed = deepcopy(config)
        changed['domain_acceptance'][domain][key] = value
        path.write_text(json.dumps(changed))
        assert p.mode_for(domain) == 'shadow'


def recipe(domain, *, nearest=False):
    nickel = domain == 'ni_superalloy_hot_end'
    values = {'YS_MPa': 600. if nickel else 280., 'UTS_MPa': 950. if nickel else 420.}
    if nickel:
        values.update(elongation_pct=22., rupture_hours=440.)
    comp = {'Ni': 90., 'Cr': 10.} if nickel else {'Cu': 98., 'Cr': 1.5, 'Nb': .5}
    conditions = {'test_temperature_C': 950., 'manufacturing_route': 'single_crystal',
        'applied_stress_MPa': 250., 'heat_treatment': 'solution_stage_1_temp_C=1300 solution_stage_1_time_h=2'} if nickel else {
        'test_temperature_C': 25., 'processing_state': 'as_received', 'ambient_process': {'cold_reduction_pct': 50}}
    gaps = {'uts_min_MPa': {'property': 'UTS_MPa', 'target': 1e6, 'observed': values['UTS_MPa'],
        'shortfall': 1e6 - values['UTS_MPa'], 'relative_shortfall': (1e6 - values['UTS_MPa']) / 1e6,
        'direction': 'min', 'unit': 'MPa'}} if nearest else {}
    row = {'display_id': ('NI' if nickel else 'CU') + ('-N01' if nearest else '-001'),
        'composition': {'values': comp, 'anchor_index': 0}, 'values': values,
        'property_sources': {k: 'specialist' for k in values}, 'admissible': True, 'feasible': not nearest,
        'support': {'specialist': 'inside' if nickel else 'local'}, 'constraint_gaps': gaps,
        'constraint_violation': sum(g['relative_shortfall'] for g in gaps.values()), 'preference_score': .2}
    return {'domain': domain, 'conditions': conditions, 'candidates': [] if nearest else [row],
            'nearest_candidates': [row] if nearest else []}


class Runner:
    def __init__(self, report):
        self.report = report
        self.calls = []

    def run(self, taskid, operation, conditions, requests):
        self.calls.append((taskid, operation, deepcopy(conditions), deepcopy(requests)))
        row = (self.report['candidates'] or self.report['nearest_candidates'])[0]
        v = row['values']
        if self.report['domain'] == 'ni_superalloy_hot_end':
            result = {'model_version': 'ni-frozen', 'inputs': {**self.report['conditions'],
                'composition_wt_percent': requests[0]['composition_wt_percent']},
                'short_time_strength': {'ultimate_tensile_strength_MPa': {'mean': v['UTS_MPa'], 'screening_MAE_MPa': 111},
                                       'proof_strength_0p2_MPa': {'mean': v['YS_MPa'], 'screening_MAE_MPa': 86}},
                'tensile_ductility': {'elongation_percent': v['elongation_pct'], 'status': 'auxiliary_indicator'},
                'creep_rupture': {'predicted_hours': v['rupture_hours'], 'predicted_log10_hours': 2.64,
                                 'applicability_domain': {'level': 'inside'}}}
        else:
            result = {'model_version': 'cu-frozen', 'material_state': conditions['processing_state'],
                'screening_conditions': deepcopy(conditions), 'source_anchor': {'alloy_name': 'GRCop-84'},
                'short_time_tensile': {'ultimate_tensile_strength_MPa': {'mean': v['UTS_MPa']},
                                      'yield_0p2_MPa': {'mean': v['YS_MPa']}},
                'additional_properties': {'electrical_conductivity_percent_IACS': {'mean': 75., 'condition': '室温'}},
                'applicability_domain': {'level': 'inside'}}
        return {'results': [result]}


@pytest.mark.parametrize('domain', sorted(p.NICU))
@pytest.mark.parametrize('nearest', [False, True])
def test_exact_selected_composition_gets_its_own_full_properties(domain, nearest):
    report = recipe(domain, nearest=nearest)
    saved = deepcopy(report)
    runner = Runner(report)
    candidate = p.compatible_candidates(runner, {'taskid': 'adapter-test', 'model_domain': domain,
        'initial_candidates': [{'composition_wt_percent': {'wrong_old_recipe': 100}}]}, report,
        {'test_temperature_C': -100, 'screening_thresholds': {'uts_min_MPa': 1e6} if nearest else {}}, nearest=nearest)[0]
    selected = (report['candidates'] or report['nearest_candidates'])[0]
    assert report == saved
    assert runner.calls[0][1] == 'evaluate_batch'
    assert runner.calls[0][3] == [{'composition_wt_percent': selected['composition']['values']}]
    assert runner.calls[0][2]['test_temperature_C'] == report['conditions']['test_temperature_C']
    assert candidate['composition_wt_percent'] == selected['composition']['values']
    assert candidate['strict_screening']['passed'] is (not nearest)
    assert candidate['feasible'] is (not nearest)
    assert candidate['specialist_model_version'].endswith('frozen')
    if nearest:
        assert candidate['selection_score'] is None
        assert candidate['strict_screening']['constraint_gaps'] == selected['constraint_gaps']
    if domain == 'ni_superalloy_hot_end':
        assert candidate['creep_rupture']['predicted_hours'] == 440
        assert candidate['elongation_percent_auxiliary']['elongation_percent'] == 22
        assert candidate['inputs']['applied_stress_MPa'] == 250
    else:
        assert candidate['additional_properties']['electrical_conductivity_percent_IACS']['mean'] == 75
        assert candidate['material_state'] == 'as_received'


@pytest.mark.parametrize('domain', sorted(p.NICU))
def test_foreign_prediction_cannot_be_promoted_as_specialist(domain):
    report = recipe(domain)
    report['candidates'][0]['property_sources']['UTS_MPa'] = 'alloy_v10'
    with pytest.raises(ValueError, match='requires specialist'):
        p.compatible_candidates(Runner(report), {'taskid': 'reject', 'model_domain': domain}, report, {})


@pytest.mark.parametrize('domain', sorted(p.NICU))
def test_selected_prediction_must_match_recalculation(domain):
    report = recipe(domain)
    runner = Runner(report)
    original = runner.run
    def wrong(*args):
        result = original(*args)
        key = 'short_time_strength' if domain == 'ni_superalloy_hot_end' else 'short_time_tensile'
        result['results'][0][key]['ultimate_tensile_strength_MPa']['mean'] += 1
        return result
    runner.run = wrong
    with pytest.raises(ValueError, match='differs from frozen optimizer'):
        p.compatible_candidates(runner, {'taskid': 'reject', 'model_domain': domain}, report, {})


@pytest.mark.parametrize('domain', sorted(p.NICU))
def test_nearest_summary_keeps_recipe_conditions_and_actual_shortfall(domain):
    report = recipe(domain, nearest=True)
    display = SimpleNamespace(DOMAINS={domain: ('材料', 'wt.%', 'ID')},
        component_groups=lambda report, selected: [('配方组成', selected['composition']['values'], 'wt.%')],
        condition_rows=lambda report: [('评价温度', str(report['conditions']['test_temperature_C']) + '°C')],
        number=lambda v: str(v), property_value=lambda k, v: (k, str(v), 'MPa'))
    text = p.downstream_summary(report, 'near-test', display)
    assert '没有全部达标配方' in text and '未满足项' in text
    assert str(1e6 - report['nearest_candidates'][0]['values']['UTS_MPa']) in text
    assert '优选配方' not in text and 'wt.%' in text
    assert protocol.result_event_data({'taskid': 'near-test', 'initial_candidates': [],
        'nearest_candidates': [{'candidate_id': 'NI-N01'}], 'user_conclusion': text})['priority_candidate_id'] == 'NI-N01'


def test_nearest_public_asset_captions_do_not_claim_passed(monkeypatch, tmp_path):
    async def publish(taskid, assets):
        return {item['name']: 'https://example.test/' + item['name'] + '.png' for item in assets}
    monkeypatch.setattr(protocol, 'publish_png_assets', publish)
    originals = deepcopy((protocol.ASSET_DOCS, protocol.ASSET_TITLES))
    result = {'_recipe_report': {'candidates': [], 'nearest_candidates': [{}]},
        'presentation': {'assets': [{'name': 'recipe_tradeoff', 'url': '/tasks/x/tradeoff.png'},
                                   {'name': 'recipe_composition_0', 'url': '/tasks/x/composition.png'}]}}
    urls, docs, titles, visuals = asyncio.run(protocol.prepare_public_assets(None, 'x', result, tmp_path))
    assert len(urls) == 2 and all('接近' in asset['title'] for asset in visuals)
    assert all('优选' not in asset['title'] for asset in visuals)
    assert (protocol.ASSET_DOCS, protocol.ASSET_TITLES) == originals


@pytest.mark.parametrize('domain', sorted(p.NICU))
def test_preflight_does_not_claim_an_unused_transformer(domain):
    assert 'NSGA-II' in p.primary_preflight(domain)
    assert '专项' in p.primary_preflight(domain)
    assert 'Transformer' not in p.primary_preflight(domain)
