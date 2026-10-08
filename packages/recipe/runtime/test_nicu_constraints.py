from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

import candidate_model_stack as models
from bounded_nsga import constraint_cost
from rsi_api import load_space, RequestError


def test_performance_shortfall_is_distinct_from_invalid_input():
    assert constraint_cost({'feasible': True}) == 0
    assert constraint_cost({'feasible': False, 'admissible': True, 'constraint_violation': .05}) < constraint_cost({'feasible': False, 'admissible': True, 'constraint_violation': .5})
    assert constraint_cost({'feasible': False, 'admissible': False}) > 1e12


def test_final_predictor_is_gated_once_not_by_specialist_performance(monkeypatch):
    old = {'values': {'UTS_MPa': 600., 'YS_MPa': 500.}, 'feasible': False,
           'support_admitted': True, 'physical_valid': True, 'support': {'specialist': 'local'}}
    space = SimpleNamespace(family='copper', domain='copper_hot_end_local_composition_v1',
        base_conditions={'test_temperature_C': 25., 'processing_state': 'as_received'}, task={},
        scope={'screening_thresholds': {'uts_min_MPa': 700.}})
    stack = models.ModelStack.__new__(models.ModelStack)
    stack.space = space; stack.mode = 'transformer_plus_specialist'; stack.apply_thresholds = True
    stack.bridge = SimpleNamespace(evaluate=lambda rows: [deepcopy(old) for _ in rows]); stack.loaded = None
    def predictions(requests, loaded, mode):
        return {'results': [{'status': 'predicted_diagnostic', 'prediction': {
            'yield_strength_0p2_MPa': 650., 'ultimate_tensile_strength_MPa': 750.}} for _ in requests]}
    monkeypatch.setattr(models.alloy, 'predict_many', predictions)
    row = stack([{'values': {'Cu': 90., 'Cr': 5., 'Nb': 5.}, 'anchor_index': 0}])[0]
    assert row['feasible'] and row['admissible'] and row['support_admitted']
    assert row['constraint_gaps']['uts_min_MPa']['observed'] == 750.
    space.scope['screening_thresholds']['uts_min_MPa'] = 800.
    row = stack([{'values': {'Cu': 90., 'Cr': 5., 'Nb': 5.}, 'anchor_index': 0}])[0]
    assert not row['feasible'] and row['admissible'] and row['support_admitted']
    assert row['constraint_violation'] == pytest.approx(50 / 800)


def test_copper_production_policy_does_not_change_blind_default():
    payload = json.loads((Path(__file__).parent.parent / 'examples/copper_probe_optimize_request.json').read_text())
    original = deepcopy(payload)
    payload['reference_policy'] = 'production'; payload['model_stack'] = 'specialist'
    space = load_space(payload)
    assert space.reference_policy == 'production' and space.anchors
    assert 'reference_policy' not in original
    assert models.restrict_to_training_anchors('copper', space.anchors, lambda a: a['composition_wt_percent'], reference_policy='production') == space.anchors


@pytest.mark.parametrize('field,value', [('reference_policy', 'relaxed'), ('model_stack', 'latest'), ('reference_policy', True)])
def test_unknown_policy_or_model_rejected(field, value):
    payload = json.loads((Path(__file__).parent.parent / 'examples/copper_probe_optimize_request.json').read_text())
    payload[field] = value
    with pytest.raises(RequestError): load_space(payload)
