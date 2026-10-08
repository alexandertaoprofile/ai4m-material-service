"""Real frozen-model optimization and retained-grid display acceptance."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os
import sys
import time
from urllib.request import urlopen

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from recipe_display import DOMAINS,from_rsi,from_perovskite,render_private,validate_markdown
from shadow_recipe_runner import execute,build_request
from recipe_shadow import RecipeShadowDispatcher

RSI=Path('/data/se42/experiments/material_rsi_prelaunch_20260928')
RUNTIME=RSI/'package/runtime'
SERVICE=Path('/data/se42/alpha_project/material_service_hub/material_composition_optimization')
FAMILY={'hea_mpea':'hea_mpea','ni_superalloy_hot_end':'nickel_superalloy','copper_hot_end_local_composition_v1':'copper',
        'reusable_rocket_stainless':'stainless','aerospace_aluminum_composition_v1':'aluminum',
        'chip_glass_thermomechanical_family_v1':'glass','short_cf_thermomechanical_rve_v1':'cf'}


def latest_tasks():
    records={}
    for path in sorted((SERVICE/'results/alloy_composition_optimization').glob('*/manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:task=json.loads(path.read_text())
        except Exception:continue
        domain=task.get('model_domain') or ('hea_mpea' if 'search_space' in task else '')
        if domain not in DOMAINS or domain in records or not task.get('initial_candidates'):continue
        task['model_domain']=domain;records[domain]=(path,task)
    return records


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    records=latest_tasks();saved={str(p):sha(p) for p,_ in records.values()}
    report={'display_version':'material_recipe_display_v2_20260928','domains':{},'production_results_unchanged':False,
            'public_effect':False,'display_allowed':False,'production_release_authorized':False}
    for domain,(source,task) in records.items():
        directory=HERE/'validation'/DOMAINS[domain][2];start=time.perf_counter()
        if domain in FAMILY:
            # These are the frozen, already audited raw requests, not test labels.
            request=json.loads((RSI/'package/examples'/(FAMILY[domain]+'_probe_optimize_request.json')).read_text())
            request['limits']={'population':16,'model_evaluations':64,'proposals':8192,'seconds':30}
            response=execute(task,RUNTIME,directory,request=request)
            actual=json.loads((directory/'optimization_response.json').read_text())
            front=actual['result']['pareto_front']
            # Replay the native production request separately; its domain may
            # legitimately have no eligible references. Never widen bounds.
            native_request=build_request(task,limits={'population':8,'model_evaluations':16,'proposals':4096,'seconds':20})
            try:
                import rsi_api
                native=rsi_api.execute(native_request)
                native_status=native['result']['status']
                (directory/'native_response.json').write_text(json.dumps(native,ensure_ascii=False,indent=2,allow_nan=False))
            except Exception as exc:native_status={'error_type':type(exc).__name__,'message':str(exc)}
            status={'actual_method':actual['result']['method'],'evaluations':actual['result']['counters']['model_evaluations'],
                    'pareto_count':len(front),'native_request_status':native_status}
        else:
            response=execute(task,RUNTIME,directory)
            status={'actual_method':'documented_grid','retained_candidates':len(task['initial_candidates'])}
        assert response['ok'] and response['public_effect'] is False and response['display_allowed'] is False
        directory=directory/'presentation';text=(directory/'preview.md').read_text()
        validate_markdown(text)
        assert '{{VISUAL:' not in text and '随机种子' not in text and '种群规模' not in text
        manifest=json.loads((directory/'display_manifest.json').read_text())
        assert all((directory/a['filename']).is_file() and sha(directory/a['filename'])==a['sha256'] for a in manifest['assets'])
        report['domains'][domain]={**status,'format_verified':True,'assets':len(manifest['assets']),
            'seconds':time.perf_counter()-start,'preview':str(directory/'preview.md'),'source_manifest':str(source)}
        print(json.dumps({domain:report['domains'][domain]},ensure_ascii=False),flush=True)
    assert len(report['domains'])==8
    report['production_results_unchanged']=all(sha(Path(path))==digest for path,digest in saved.items())
    assert report['production_results_unchanged']
    report['health_after']=json.loads(urlopen('http://127.0.0.1:1111/health',timeout=10).read())
    (HERE/'ALL_DOMAINS_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
