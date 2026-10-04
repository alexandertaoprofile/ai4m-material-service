from copy import deepcopy
from pathlib import Path
import json
import pytest

from src.alloy_workflow import recipe_primary as p


@pytest.mark.parametrize('approved', [True, False])
@pytest.mark.parametrize('domain', ['reusable_rocket_stainless', 'ni_superalloy_hot_end', 'copper_hot_end_local_composition_v1'])
def test_only_accepted_domain_can_be_promoted(tmp_path, monkeypatch, domain, approved):
    path = tmp_path / 'release.json'
    path.write_text(json.dumps({'approved_after_acceptance': approved, 'domains': {domain: 'primary'}}))
    monkeypatch.setenv('MATERIAL_RECIPE_RELEASE_CONFIG', str(path))
    assert p.mode_for(domain) == ('primary' if approved and domain in p.ELIGIBLE and domain not in p.NICU else 'shadow')


def test_unknown_or_broken_config_is_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv('MATERIAL_RECIPE_RELEASE_CONFIG', str(tmp_path / 'missing.json'))
    assert p.mode_for('reusable_rocket_stainless') == 'shadow'


def test_cryogenic_conditions_do_not_claim_transformer_or_free_recipe(monkeypatch):
    monkeypatch.setattr(p, 'mode_for', lambda domain: 'primary')
    assert p.effective_mode({'model_domain': 'reusable_rocket_stainless', 'test_temperature_K': 90}) == 'legacy'
    assert p.effective_mode({'model_domain': 'reusable_rocket_stainless', 'test_temperature_K': 293}) == 'primary'


def test_cryogenic_missing_properties_are_json_null_not_zero():
    from src.alloy_workflow.application import AlloyOptimizationApplication
    result = {'mode': 'cryogenic_reference', 'cryogenic_reference': {'nearest_records': [
        {'material_id': '301', 'elongation_pct': float('nan'), 'yield_MPa': 1758.164},
        {'material_id': '304L', 'elongation_pct': 35.7, 'yield_MPa': 856.0},
    ]}}
    AlloyOptimizationApplication(None, 'test')._enrich_rocket_stainless(result, {})
    records = result['cryogenic_reference']['nearest_records']
    assert records[0]['elongation_pct'] is None
    assert records[0]['yield_MPa'] == 1758.164 and records[1]['elongation_pct'] == 35.7
    assert '"elongation_pct": null' in json.dumps(result, allow_nan=False)


def test_single_target_is_preserved_for_optimizer_not_cast_as_legacy_weight(monkeypatch):
    monkeypatch.setattr(p, 'mode_for', lambda domain: 'primary')
    original = {'model_domain': 'reusable_rocket_stainless', 'objectives': {'yield_strength': {'goal': 'target', 'target': 1000}},
                'raw_scope': {'objectives': {'yield_strength': {'goal': 'target', 'target': 1000}}, 'test_temperature_K': 293}}
    saved = deepcopy(original)
    seed = p.bootstrap_constraints(original)
    assert seed['objectives'] == {} and seed['raw_scope']['objectives'] == {}
    assert original == saved and original['objectives']['yield_strength']['target'] == 1000
    assert seed['raw_scope']['test_temperature_K'] == 293


def row():
    return {'display_id': 'RSS-001', 'composition': {'values': {'Fe': 70, 'Cr': 18, 'Ni': 12}, 'anchor_index': 0},
        'values': {'YS_MPa': 250, 'UTS_MPa': 600, 'elongation_pct': 40},
        'property_sources': {'YS_MPa': 'alloy_v10', 'UTS_MPa': 'alloy_v10', 'elongation_pct': 'specialist'},
        'support': {'shared': {'admitted': True, 'level': 'inside', 'distance': .2}}, 'preference_score': .3}


def test_stainless_uses_exact_composition_and_sources_without_fabricated_error():
    r = row()
    report = {'candidates': [r], 'conditions': {}}
    candidate = p.compatible_candidates(None, {'model_domain': 'reusable_rocket_stainless'}, report, {})[0]
    assert candidate['composition_wt_percent'] == r['composition']['values']
    assert 'P' not in candidate['composition_wt_percent'] and 'S' not in candidate['composition_wt_percent']
    assert candidate['short_time_tensile']['yield_0p2_MPa']['mean'] == 250
    assert candidate['short_time_tensile']['yield_0p2_MPa']['source'] == 'alloy_v10'
    assert set(candidate['short_time_tensile']['yield_0p2_MPa']).isdisjoint({'std', 'screening_MAE'})
    assert candidate['short_time_tensile']['elongation_pct']['source'] == 'specialist'


def test_shared_support_rejection_is_not_silently_relaxed():
    r = row()
    r['support']['shared']['admitted'] = False
    with pytest.raises(ValueError, match='outside shared'):
        p.support(r)


def test_aluminum_keeps_original_property_keys():
    r = row()
    r['composition']['values'] = {'Al': 97, 'Mg': 2, 'Si': 1}
    report = {'candidates': [r], 'conditions': {'processing_category': 'peak_aged'}}
    result = p.compatible_candidates(None, {'model_domain': 'aerospace_aluminum_composition_v1'}, report, {})[0]
    assert set(result['predicted_properties']) == {'yield_strength_MPa', 'uts_MPa', 'elongation_pct'}
    assert result['processing_category'] == 'peak_aged'


def test_cte_comparison_is_in_ppm_not_inverse_kelvin():
    p.require_matching({'cte': 3.5}, {'CTE': {'prediction': 3.5e-6, 'prediction_ppm_per_K': 3.5}}, {'cte': 'CTE'})
    with pytest.raises(ValueError, match='differs'):
        p.require_matching({'cte': 3.5}, {'CTE': {'prediction_ppm_per_K': 3.9}}, {'cte': 'CTE'})


def test_action_summary_contains_actual_composition_and_conditions_not_just_id():
    from types import SimpleNamespace
    display = SimpleNamespace(DOMAINS={'reusable_rocket_stainless': ('奥氏体不锈钢', 'wt.%', 'RSS')},
        component_groups=lambda report, selected: [('配方组成', selected['composition']['values'], 'wt.%')],
        condition_rows=lambda report: [('评价温度', '293 K', '用户指定')],
        number=lambda v: str(v), property_value=lambda k, v: (k, str(v), 'MPa'))
    text = p.downstream_summary({'domain': 'reusable_rocket_stainless', 'candidates': [row()]}, 'handoff-case', display)
    assert 'Fe为70%' in text and 'Cr为18%' in text and 'Ni为12%' in text
    assert '293 K' in text and 'YS_MPa为250 MPa' in text
    assert '/alloy/tasks/handoff-case' in text and '见上表' not in text


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_no_nonfinite_production_prediction(value):
    with pytest.raises(ValueError):
        p.predicted(value, 'alloy_v10', 'MPa')
