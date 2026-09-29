"""Tests run from the 1111 service with its actual application/runner imports."""
from copy import deepcopy
from pathlib import Path
import pytest

from src.alloy_workflow import application as app_module
from src.alloy_workflow import recipe_primary
from src.alloy_workflow.runner import HEASurrogateRunner
from src.alloy_workflow.recipe_shadow import DOMAINS


@pytest.mark.parametrize('domain',sorted(DOMAINS))
def test_private_hook_gets_enriched_copy_without_public_changes(domain,monkeypatch):
    # Exercise shadow mode explicitly; a deployed primary release must not
    # determine this unit test's expectations or invoke real numerical work.
    monkeypatch.setattr(recipe_primary,'mode_for',lambda requested:'shadow')
    class FakeRunner:
        def run(self,*args):return {'model_domain':domain,'initial_candidates':[]}
        def submit_tensile_shadow(self,*args):pass
        def submit_recipe_shadow(self,taskid,result):self.private=deepcopy(result)
    plan={'effective_model_input':{'model_domain':domain},'field_provenance':{'test_temperature_C':'user_input'}}
    monkeypatch.setattr(app_module,'requirement_plan',lambda payload:({'model_domain':domain},plan))
    monkeypatch.setattr(app_module,'contract',lambda payload:{'taskid':'recipe-test'})
    runner=FakeRunner();app=app_module.AlloyOptimizationApplication(runner,'材料配比')
    monkeypatch.setattr(app,'_enrich',lambda result,plan:result.update(requirement_interpretation=plan))
    result,_=app.propose_space({})
    assert result==runner.private and result is not runner.private
    assert result['requirement_interpretation']==plan
    assert not any(key.startswith('recipe_') for key in result)


@pytest.mark.parametrize('mode', ['legacy', 'primary'])
def test_non_shadow_modes_do_not_submit_the_private_recipe_hook(mode, monkeypatch):
    domain = 'reusable_rocket_stainless'
    monkeypatch.setattr(recipe_primary, 'mode_for', lambda requested: mode)
    promoted = {'model_domain': domain, 'recipe_release': {'mode': 'primary'}, 'initial_candidates': []}
    monkeypatch.setattr(recipe_primary, 'promote', lambda *args: promoted)
    plan = {'effective_model_input': {'model_domain': domain}}
    monkeypatch.setattr(app_module, 'requirement_plan', lambda payload: ({'model_domain': domain}, plan))
    monkeypatch.setattr(app_module, 'contract', lambda payload: {'taskid': 'recipe-mode-test'})

    class FakeRunner:
        def run(self, *args):
            return {'model_domain': domain, 'initial_candidates': []}

        def submit_tensile_shadow(self, *args):
            pass

        def submit_recipe_shadow(self, *args):
            raise AssertionError('non-shadow mode submitted a recipe shadow hook')

    app = app_module.AlloyOptimizationApplication(FakeRunner(), '材料配比')
    monkeypatch.setattr(app, '_enrich', lambda result, plan: result.update(requirement_interpretation=plan))
    result, _ = app.propose_space({})
    if mode == 'primary':
        assert result is promoted
    else:
        assert 'recipe_release' not in result


def test_private_setup_failure_does_not_break_runner(tmp_path,monkeypatch):
    runner=HEASurrogateRunner(results_root=tmp_path,surrogate_root=tmp_path,
                              environment_prefix=tmp_path,executable='unused')
    class BrokenDispatcher:
        def submit(self,*args):raise RuntimeError('test private-only failure')
    runner._recipe_dispatcher=BrokenDispatcher()
    try:
        result={'model_domain':'hea_mpea','initial_candidates':[]};original=deepcopy(result)
        assert runner.submit_recipe_shadow('recipe-test',result) is None
        assert result==original
        assert list(tmp_path.glob('recipe-test/shadow/notice-*.json'))
    finally:runner._shadow_pool.shutdown(wait=True)
