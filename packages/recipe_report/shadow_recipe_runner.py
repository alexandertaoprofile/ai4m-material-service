"""Owned private subprocess: frozen prediction + optimization + report.

Never publishes images or writes the formal manifest/Markdown/result.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from recipe_display import DOMAINS,from_rsi,from_perovskite,from_input_issue,render_private

FIELDS={
 'reusable_rocket_stainless':{'test_temperature_K','processing','element_bounds_wt_percent'},
 'aerospace_aluminum_composition_v1':{'test_temperature_C','processing_category','element_bounds_wt_percent'},
 'ni_superalloy_hot_end':{'test_temperature_C','heat_treatment','manufacturing_route','applied_stress_MPa','element_bounds_wt_percent'},
 'copper_hot_end_local_composition_v1':{'test_temperature_C','composition_family','processing_state','element_bounds_wt_percent','ambient_process'},
 'hea_mpea':{'allowed_elements','element_bounds_at_pct','processing_method','test_temperature_C','constraints'},
 'chip_glass_thermomechanical_family_v1':{'oxide_bounds_mol_percent','poisson_ratio','service_options'},
 'short_cf_thermomechanical_rve_v1':{'matrix_name','matrix_properties','candidate_windows','search_space','target_vf','fiber_length_mm','target_a11'},
}
COMMON={'model_domain','num_candidates','candidate_count','random_seed','objectives','screening_thresholds','screening_mode',
        'application','component','weld_state','low_temperature_verification_K','verification_focus'}
DEFAULT_GOALS={
 'reusable_rocket_stainless':{'yield_strength':{'goal':'maximize'},'uts':{'goal':'maximize'},'elongation':{'goal':'maximize'}},
 'aerospace_aluminum_composition_v1':{'yield_strength':{'goal':'maximize'},'elongation':{'goal':'maximize'}},
 'ni_superalloy_hot_end':{'proof_strength_0p2_MPa':{'goal':'maximize'},'ultimate_tensile_strength_MPa':{'goal':'maximize'},'rupture_life':{'goal':'maximize'}},
 'copper_hot_end_local_composition_v1':{'yield_strength':{'goal':'maximize'},'uts':{'goal':'maximize'}},
 'hea_mpea':{'yield_strength':{'goal':'maximize'},'phase_risk':{'goal':'minimize'}},
 'chip_glass_thermomechanical_family_v1':{'CTE_linear_0_to_300C':{'goal':'minimize'},'young_modulus_GPa':{'goal':'maximize'},'density_g_per_cm3':{'goal':'minimize'}},
 'short_cf_thermomechanical_rve_v1':{'E11_MPa':{'goal':'maximize'},'fiber_volume_fraction':{'goal':'minimize'}},
}
SPECIALIST_SERVICE_DOMAINS={'ni_superalloy_hot_end','copper_hot_end_local_composition_v1'}


def service_policies(request):
    """Known references are allowed in a service; blind RSI defaults stay strict."""
    if request['domain'] in SPECIALIST_SERVICE_DOMAINS:
        request.setdefault('reference_policy','production')
        request.setdefault('model_stack','specialist')
    return request


def build_request(task, *, limits=None):
    domain=task['model_domain']
    if domain not in FIELDS:raise ValueError('domain does not have a continuous optimization adapter')
    if '_recipe_optimization_request' in task:
        request=deepcopy(task['_recipe_optimization_request'])
        if request['domain']!=domain:raise ValueError('mixed explicit optimization domain')
        if limits:request['limits']=deepcopy(limits)
        return service_policies(request)
    interpretation=task.get('requirement_interpretation') or {}
    original=interpretation.get('effective_model_input') or task.get('_resolved_scope') or {}
    scope={k:deepcopy(v) for k,v in original.items() if k in COMMON|FIELDS[domain]}
    scope['model_domain']=domain
    conditions=deepcopy(task.get('screening_conditions') or {})
    for key in FIELDS[domain]:
        if key in conditions:scope[key]=deepcopy(conditions[key])
    context={k:deepcopy(task[k]) for k in ('screening_conditions','search_space') if k in task}
    context['model_domain']=domain
    if domain=='hea_mpea':
        search=context['search_space']
        scope['element_bounds_at_pct']=deepcopy(search['elements_at_pct'])
        scope['allowed_elements']=list(search['elements_at_pct'])
        for k in ('processing_method','test_temperature_C'):
            if k in search:scope[k]=deepcopy(search[k])
    if domain=='short_cf_thermomechanical_rve_v1':
        context['all_candidates']=[{k:deepcopy(row[k]) for k in ('matrix_name','inputs')} for row in task.get('all_candidates',task.get('initial_candidates',[]))]
        windows=scope.get('candidate_windows') or {}
        if windows:
            import math
            resolved=context['screening_conditions']['local_candidate_windows']
            for key,interval in windows.items():
                if key not in {'target_vf','fiber_length_mm','target_a11'} or not isinstance(interval,list) or len(interval)!=2:
                    raise ValueError('invalid requested RVE window')
                lo,hi=map(float,interval)
                if not (math.isfinite(lo) and math.isfinite(hi) and 0<=lo<=hi):raise ValueError('invalid RVE bounds')
                if key in {'target_vf','target_a11'} and hi>1:raise ValueError('RVE fraction must not exceed 1')
                resolved[key]=[lo,hi]
    if not scope.get('objectives'):scope['objectives']=deepcopy(DEFAULT_GOALS[domain])
    basis='at_percent' if domain=='hea_mpea' else 'mol_percent' if domain=='chip_glass_thermomechanical_family_v1' else 'volume_and_geometry' if domain=='short_cf_thermomechanical_rve_v1' else 'wt_percent'
    return service_policies({'operation':'optimize','request_id':'private-recipe-'+DOMAINS[domain][2],
        'domain':domain,'composition_basis':basis,'scope':scope,'material_context':context,
        'seed':20260928,'limits':deepcopy(limits or {'population':32,'model_evaluations':256,'proposals':20480,'seconds':30})})


def execute(task, runtime, directory, *, request=None):
    domain=task['model_domain'];directory=Path(directory)
    original=deepcopy(task);started=time.perf_counter();response=None
    if domain=='perovskite_transport_stability_v2':
        report=from_perovskite(task)
    else:
        request=request or build_request(task)
        runtime=Path(runtime).resolve()
        sys.path.insert(0,str(runtime))
        os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        import rsi_api
        # The frozen glass space builder validates legacy min/max objectives
        # while setting composition bounds. Pass actual target objectives to
        # the optimizer's separate public objective field, preserving every
        # target, unit and hard threshold. No frozen numerical code is changed.
        original_request = deepcopy(request)
        target_adapter = domain == 'chip_glass_thermomechanical_family_v1' and any(
            isinstance(value, dict) and value.get('goal') == 'target'
            for value in request['scope'].get('objectives', {}).values())
        if target_adapter:
            from optimization_contract import map_resolved_objectives
            from candidate_model_stack import UNITS
            resolved = map_resolved_objectives(request['scope'], UNITS)
            if request.get('objectives') and request['objectives'] != resolved:
                raise ValueError('conflicting glass optimization objective representations')
            request = deepcopy(request)
            request['objectives'] = resolved
            request['scope'].pop('objectives')
        try:
            space=rsi_api.load_space(request)
            response=rsi_api.execute(request)
            response['resolved_design']={'bounds':deepcopy(space.bounds),'variables':list(space.variable)}
        except rsi_api.RequestError as exc:
            if exc.code not in {'NO_ELIGIBLE_STARTING_REFERENCE','TEMPERATURE_SCOPE','BOUNDS',
                'CONDITION_CONFLICT','UNSUPPORTED_CONSTRAINT','COMPOSITION_UNIT'}:raise
            response={'ok':False,'error_code':exc.code,'error':str(exc),
                'public_effect':False,'display_allowed':False,'production_release_authorized':False}
        response['shadow_wrapper_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        directory.mkdir(parents=True,exist_ok=True)
        if target_adapter:
            (directory/'requested_optimization_request.json').write_text(json.dumps(original_request,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            response['objective_adapter'] = {'version':'glass_target_request_v1',
                'requested_objectives':original_request['scope']['objectives'],
                'executed_objectives':request['objectives']}
        (directory/'optimization_request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        (directory/'optimization_response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        report=(from_rsi(response,task,request=request) if response['ok'] else
                from_input_issue(task,request,response['error_code']))
    if task!=original:raise RuntimeError('private report mutated source result')
    report['elapsed_seconds']=time.perf_counter()-started
    manifest=render_private(report,directory/'presentation')
    return {'ok':True,'results':[{'status':'input_requires_revision' if report['method']=='input_validation' else 'completed' if report['candidates'] else 'no_feasible_candidate',
        'domain':domain,'display_version':manifest['display_version'],
        'selected_count':len(report['candidates']),'nearest_count':len(report.get('nearest_candidates',[])),
        'presentation_directory':str(directory/'presentation'),
        'actual_method':report['method'],'public_effect':False,'display_allowed':False,'production_release_authorized':False}],
        'elapsed_seconds':time.perf_counter()-started,'public_effect':False,'display_allowed':False,
        'production_release_authorized':False,'formal_result_mutated':False,'mode':'shadow'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--response',type=Path,required=True)
    p.add_argument('--runtime',type=Path,required=True);a=p.parse_args();status=0
    try:
        if a.request.is_symlink() or a.request.stat().st_size>16*1024*1024:raise ValueError('unsafe or oversized private request')
        incoming=json.loads(a.request.read_text(encoding='utf-8'));items=incoming['items']
        if not isinstance(items,list) or len(items)!=1:raise ValueError('one resolved material task required')
        response=execute(items[0]['task'],a.runtime,a.response.parent)
    except Exception as exc:
        response={'ok':False,'error_type':type(exc).__name__,'error':str(exc),
            'public_effect':False,'display_allowed':False,'production_release_authorized':False,'mode':'shadow'}
        status=2
    a.response.write_text(json.dumps(response,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'ok':response['ok'],'response':str(a.response),'error_type':response.get('error_type')},ensure_ascii=False),flush=True)
    return status


if __name__=='__main__':raise SystemExit(main())
