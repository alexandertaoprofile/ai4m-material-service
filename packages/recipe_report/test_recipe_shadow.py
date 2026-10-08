from copy import deepcopy
from pathlib import Path
import json
import time
import pytest

from recipe_shadow import RecipeShadowDispatcher
from shadow_recipe_runner import build_request


def fake_runner(private=True):
    def run(command,timeout):
        response=Path(command[command.index('--response')+1])
        incoming=json.loads(Path(command[command.index('--request')+1]).read_text())
        row={'status':'completed','domain':incoming['items'][0]['task']['model_domain'],
             'public_effect':False,'display_allowed':not private,'production_release_authorized':False}
        response.write_text(json.dumps({'ok':True,'results':[row],'public_effect':False,
            'display_allowed':not private,'production_release_authorized':False}))
        return 0
    return run


def test_private_run_never_mutates_formal_result(tmp_path):
    task={'model_domain':'hea_mpea','initial_candidates':[{'x':1}]};original=deepcopy(task)
    dispatcher=RecipeShadowDispatcher(tmp_path,process_runner=fake_runner())
    try:result=dispatcher.run_sync('task-01',task)
    finally:dispatcher.close()
    assert result['status']=='completed' and task==original
    assert result['public_effect'] is False and result['formal_result_mutated'] is False
    assert list(tmp_path.glob('task-01/shadow/recipe_v2/*/manifest.json'))
    assert not list(tmp_path.glob('task-01/presentation/*'))


def test_public_flag_blocks_private_response(tmp_path):
    dispatcher=RecipeShadowDispatcher(tmp_path,process_runner=fake_runner(private=False))
    try:result=dispatcher.run_sync('task-02',{'model_domain':'hea_mpea'})
    finally:dispatcher.close()
    assert result['status']=='failed' and result['error_type']=='ValueError'


@pytest.mark.parametrize('taskid',['../escape','a/b','..','C:/escape','',None])
def test_path_traversal_rejected(tmp_path,taskid):
    dispatcher=RecipeShadowDispatcher(tmp_path,process_runner=fake_runner())
    try:
        with pytest.raises(ValueError):dispatcher.run_sync(taskid,{'model_domain':'hea_mpea'})
    finally:dispatcher.close()


def test_domain_not_guessed(tmp_path):
    dispatcher=RecipeShadowDispatcher(tmp_path,process_runner=fake_runner())
    try:
        with pytest.raises(ValueError):dispatcher.run_sync('task-03',{'model_domain':'unknown'})
    finally:dispatcher.close()


def test_stainless_scope_preserves_temperature_and_microelement_bounds():
    task={'model_domain':'reusable_rocket_stainless','screening_conditions':{'test_temperature_K':293,
        'processing':{'quench':'water'}},'requirement_interpretation':{'effective_model_input':{
            'element_bounds_wt_percent':{'Cr':[16,20],'N':[.01,.08]},'low_temperature_verification_K':[90,111],
            'objectives':{'yield_strength':{'goal':'maximize'}}}}}
    original=deepcopy(task);request=build_request(task)
    assert request['scope']['test_temperature_K']==293
    assert request['scope']['element_bounds_wt_percent']['N']==[.01,.08]
    assert request['scope']['low_temperature_verification_K']==[90,111]
    assert task==original and request['composition_basis']=='wt_percent'


def test_mixed_explicit_optimization_domain_rejected():
    with pytest.raises(ValueError):build_request({'model_domain':'hea_mpea','_recipe_optimization_request':{'domain':'other'}})


def test_no_objective_is_silently_dropped():
    task={'model_domain':'hea_mpea','search_space':{'elements_at_pct':{'Fe':[0,100]}},
          'requirement_interpretation':{'effective_model_input':{'objectives':{'invented_property':{'goal':'maximize'}}}}}
    assert 'invented_property' in build_request(task)['scope']['objectives']


def test_shadow_disabled_never_submits(tmp_path,monkeypatch):
    monkeypatch.setenv('RECIPE_SHADOW_ENABLED','0')
    dispatcher=RecipeShadowDispatcher(tmp_path,process_runner=fake_runner())
    try:assert dispatcher.submit('task-04',{'model_domain':'hea_mpea'}) is None
    finally:dispatcher.close()


def test_virtualenv_python_symlink_is_preserved(tmp_path):
    interpreter=tmp_path/'system-python';interpreter.write_text('test interpreter')
    virtual=tmp_path/'venv'/'python';virtual.parent.mkdir();virtual.symlink_to(interpreter)
    dispatcher=RecipeShadowDispatcher(tmp_path,python=virtual,process_runner=fake_runner())
    try:assert dispatcher.python==virtual.absolute() and dispatcher.python!=virtual.resolve()
    finally:dispatcher.close()
