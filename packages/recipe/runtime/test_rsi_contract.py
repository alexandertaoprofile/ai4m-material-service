from copy import deepcopy
from pathlib import Path
import json
import numpy as np
import pytest
import stainless_support as support
from stainless_input_adapter import prepare_stainless_input
from bounded_nsga import Limits,search,objectives,objective_vector
from rsi_api import load_space,RequestError

RECIPE={'Fe':72.4,'Cr':17.5,'Ni':9.0,'Mn':.5,'Si':.5,'C':.08,'N':.02}

def test_missing_and_explicit_zero_have_different_comparison_masks():
    absent=support.evaluate(RECIPE,20.)
    zero=support.evaluate({**RECIPE,'P':0.,'S':0.},20.)
    assert absent['composition_status']['P']=='not_specified'
    assert zero['composition_status']['P']=='specified'
    assert 'P' not in absent['compared_elements'] and 'P' in zero['compared_elements']
    assert absent['nitrogen_checked'] and zero['nitrogen_checked']

def test_nitrogen_is_not_removed_to_manufacture_admission():
    composition={**RECIPE,'N':.1};composition['Fe']-=.08
    result=support.evaluate(composition,20.)
    assert result['admitted'] is False and 'N' in result['outside_element_ranges']

def test_obviously_different_major_alloy_is_rejected():
    composition={**RECIPE,'Cr':45.};composition['Fe']-=27.5
    assert support.evaluate(composition,20.)['admitted'] is False

@pytest.mark.parametrize('change',[
    {'N':True},{'N':float('nan')},{'C':-.1},{'Fe':10.},
])
def test_invalid_recipe_rejected(change):
    with pytest.raises(ValueError):support.evaluate({**RECIPE,**change},20.)

def test_unknown_nitrogen_is_not_zero():
    recipe=dict(RECIPE);recipe.pop('N')
    with pytest.raises(ValueError):support.canonical_composition(recipe)

def test_numerical_amount_and_missing_status_conflict():
    with pytest.raises(ValueError):support.canonical_composition({**RECIPE,'P':0.},{'P':'not_specified'})

def test_generated_and_user_recipe_adapter_do_not_mutate_original():
    supplied={'composition_wt_percent':{**RECIPE,'P':0.,'S':0.}}
    original=deepcopy(supplied)
    generated=prepare_stainless_input(supplied,generated_design=True,declared_bounds={'N':[.01,.03]})
    reported=prepare_stainless_input(supplied)
    assert generated['composition_status']['P']=='not_specified'
    assert reported['composition_status']['P']=='specified'
    assert supplied==original

def test_train_only_profile_contains_no_performance_labels():
    p=support.load_profile()
    assert p['fit_role']=='train' and p['independent_compositions']==150
    assert not {'target','UTS','YS','prediction'}&set(p)

def test_temperature_scope_rejected():
    assert support.evaluate(RECIPE,-183.15)['admitted'] is False

class ToySpace:
    lower=[0.];upper=[1.]
    def sample(self,rng,n):return rng.uniform(0,1,(n,1))
    def decode(self,x):return {'x':float(x[0])} if x[0]>.3 else None
    def key(self,x):return x['x']

def test_full_proposal_and_evaluation_log():
    spec=objectives([dict(property='value',mode='max',unit='1')],{'value':'1'})
    def predictor(rows):return [dict(values={'value':r['x']},feasible=r['x']>.6) for r in rows]
    result=search(ToySpace(),predictor,spec,Limits(population=8,model_evaluations=17,proposals=256,seconds=5),17)
    assert len(result['proposal_log'])==result['counters']['proposals']
    assert len(result['history'])==result['counters']['model_evaluations']==17
    assert any(r['status']=='invalid_composition' for r in result['proposal_log'])
    assert any(r['status']=='evaluated_infeasible' for r in result['proposal_log'])
    assert result['batch_log'][-1]['model_evaluations_total']==17

def test_last_bit_prediction_differences_do_not_change_dominance():
    spec=[dict(property='elongation_pct',unit='%',mode='max',scale=10)]
    assert objective_vector({'elongation_pct':60.82},spec)==objective_vector({'elongation_pct':60.820000000000014},spec)

def test_mixed_domains_and_unknown_top_fields_rejected():
    for payload in ({'domain':'unknown'}, {'domain':'reusable_rocket_stainless','invented_rule':True}):
        with pytest.raises(RequestError):load_space(payload)

def test_raw_schema_rejects_wrong_composition_unit():
    payload=json.loads((Path(__file__).parent.parent/'examples/stainless_optimize_request.json').read_text())
    payload['composition_basis']='at_percent'
    with pytest.raises(RequestError):load_space(payload)

def test_later_verification_context_is_not_model_temperature():
    payload=json.loads((Path(__file__).parent.parent/'examples/stainless_optimize_request.json').read_text())
    space=load_space(payload)
    assert space.base_conditions['test_temperature_K']==293
    assert payload['scope']['low_temperature_verification_K']==[90,111]

def test_aluminum_context_cannot_silently_change_room_temperature():
    payload=json.loads((Path(__file__).parent.parent/'examples/aluminum_optimize_request.json').read_text())
    payload['material_context']['screening_conditions']['test_temperature_C']=100.
    with pytest.raises(RequestError):load_space(payload)

def test_copper_bounds_cannot_differ_between_declared_and_actual_context():
    payload=json.loads((Path(__file__).parent.parent/'examples/copper_probe_optimize_request.json').read_text())
    payload['scope']['element_bounds_wt_percent']['Cu']=[99.,100.]
    with pytest.raises(RequestError):load_space(payload)

@pytest.mark.parametrize('change',[{'required_elements':['Au']},{'safety_constraint':'LOX_safe'},
                                 {'element_bounds_wt_percent':{'Cr':['16','20']}},
                                 {'test_temperature_K':500.}])
def test_unimplemented_rules_or_inconsistent_context_rejected(change):
    payload=json.loads((Path(__file__).parent.parent/'examples/stainless_optimize_request.json').read_text())
    payload['scope'].update(change)
    with pytest.raises(RequestError):load_space(payload)
