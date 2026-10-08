"""Exercise the actual merged 1111 runner, without restarting the service."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import time

HERE=Path(__file__).resolve().parent
SERVICE=Path('/data/se42/alpha_project/material_service_hub/material_composition_optimization')
sys.path.insert(0,str(SERVICE))
from src.alloy_workflow.runner import HEASurrogateRunner
from verify_all_domains import latest_tasks


def main():
    source,task=latest_tasks()['reusable_rocket_stainless']
    original=deepcopy(task);digest=hashlib.sha256(source.read_bytes()).hexdigest()
    runner=HEASurrogateRunner(results_root=HERE/'installed_hook_validation/results',
        surrogate_root=Path('/data/se42/hea_surrogate'),
        environment_prefix=Path('/data/mamba/envs/mattergen-py310'),executable='unused')
    started=time.perf_counter()
    try:
        future=runner.submit_recipe_shadow('installed-hook-RSS',task)
        assert future is not None
        record=future.result(timeout=90)
        assert record['status']=='completed',record
        assert record['results'][0]['selected_count']>0
        assert record['public_effect'] is False and record['formal_result_mutated'] is False
    finally:
        runner._shadow_pool.shutdown(wait=True)
        if runner._recipe_dispatcher:runner._recipe_dispatcher.close()
    assert task==original and hashlib.sha256(source.read_bytes()).hexdigest()==digest
    installed=SERVICE/'src/alloy_workflow/recipe_shadow.py'
    assert installed.read_bytes()==(HERE/'recipe_shadow.py').read_bytes()
    output={'installed_hook_verified':True,'formal_result_mutated':False,'production_results_unchanged':True,
        'seconds':time.perf_counter()-started,'model_domain':'reusable_rocket_stainless',
        'helper_sha256':hashlib.sha256(installed.read_bytes()).hexdigest(),
        'runner_sha256':hashlib.sha256((SERVICE/'src/alloy_workflow/runner.py').read_bytes()).hexdigest(),
        'application_sha256':hashlib.sha256((SERVICE/'src/alloy_workflow/application.py').read_bytes()).hexdigest(),
        'record':record,'public_effect':False,'display_allowed':False,'production_release_authorized':False}
    (HERE/'INSTALLED_HOOK_VALIDATION.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in output.items() if k!='record'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
