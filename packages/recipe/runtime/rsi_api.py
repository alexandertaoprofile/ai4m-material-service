"""Internal evaluate/optimize API for frozen material models.

No text-to-goal guessing, training, Memory updates, production routing or uploads.
Test labels are outside this importable runtime. All results are predictions.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import copy
import hashlib
import json
import math
import os
import sys
import time
import uuid
import warnings
from functools import lru_cache
from sklearn.exceptions import InconsistentVersionWarning
warnings.filterwarnings('error',category=InconsistentVersionWarning)

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from candidate_model_stack import NativeSpace,ModelStack,DOMAINS,UNITS
from bounded_nsga import Limits,objectives,search
from optimization_contract import map_resolved_objectives

API_VERSION='material_rsi_v1_20260928'
# These describe a later experiment/application, not a learned safety model.
CONTEXT_ONLY={'application','component','weld_state','low_temperature_verification_K','verification_focus'}
THRESHOLDS={
 'uts_min_MPa':('UTS_MPa','min'), 'proof_strength_min_MPa':('YS_MPa','min'),
 'rupture_life_min_h':('rupture_hours','min'),
 'CTE_linear_0_to_300C_max_ppm_per_K':('cte_0_300_ppm_K','max'),
 'density_g_per_cm3_max':('density_g_cm3','max'),
 'young_modulus_GPa_min':('young_modulus_GPa','min'),
 'stress_optical_coefficient_nm_cm_per_MPa_max':('stress_optical_coefficient_nm_cm_per_MPa','max'),
}

class RequestError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def finite_number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise RequestError('INVALID_NUMBER',name+' must be a finite JSON number')
    return float(value)

@lru_cache(maxsize=1)
def verify_specialists():
    package=ROOT.parent
    entries=json.loads((package/'SPECIALIST_FILES.json').read_text())
    for item in entries:
        path=(package/item['bundle_path']).resolve()
        if not path.is_relative_to(package.resolve()) or not path.is_file():
            raise RequestError('ARTIFACT_PATH','missing or unsafe specialist artifact path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise RequestError('ARTIFACT_HASH','specialist hash mismatch: '+item['bundle_path'])
    return {x['bundle_path']:x['sha256'] for x in entries if x['bundle_path'].endswith('.joblib')}

def load_space(payload):
    allowed={'operation','request_id','domain','composition_basis','scope','material_context','candidates','objectives','limits','seed','reference_policy','model_stack'}
    if not isinstance(payload,dict) or set(payload)-allowed:
        raise RequestError('UNKNOWN_FIELD','unsupported top-level request fields')
    domain=payload.get('domain');scope=copy.deepcopy(payload.get('scope'));context=copy.deepcopy(payload.get('material_context'))
    if domain not in DOMAINS or not isinstance(scope,dict) or not isinstance(context,dict):
        raise RequestError('DOMAIN_OR_CONTEXT','explicit domain, resolved scope and material_context required')
    reference_policy=payload.get('reference_policy','evaluation')
    if not isinstance(reference_policy,str) or reference_policy not in {'evaluation','production'}:raise RequestError('REFERENCE_POLICY','reference_policy must be evaluation or production')
    model_stack=payload.get('model_stack','transformer_plus_specialist')
    if not isinstance(model_stack,str) or model_stack not in {'specialist','transformer_plus_specialist'}:
        raise RequestError('MODEL_STACK','unknown model_stack')
    if scope.get('model_domain')!=domain or context.get('model_domain')!=domain:
        raise RequestError('MIXED_DOMAIN','scope and context must refer to the same domain')
    if set(context)-{'model_domain','screening_conditions','search_space','all_candidates'}:
        raise RequestError('CONTEXT_FIELDS','unsupported material_context fields')
    cond=context.get('screening_conditions',{})
    if not isinstance(cond,dict):raise RequestError('CONTEXT_FIELDS','screening_conditions must be an object')
    for name in ('test_temperature_K','test_temperature_C','processing','processing_category','heat_treatment','manufacturing_route','applied_stress_MPa','element_bounds_wt_percent'):
        if name in scope and name in cond and scope[name]!=cond[name]:
            raise RequestError('CONDITION_CONFLICT','scope and actual context differ: '+name)
    common={'model_domain','num_candidates','candidate_count','random_seed','objectives','screening_thresholds','screening_mode','composition_basis'} | CONTEXT_ONLY
    fields={
      'reusable_rocket_stainless':{'test_temperature_K','processing','element_bounds_wt_percent'},
      'aerospace_aluminum_composition_v1':{'test_temperature_C','processing_category','element_bounds_wt_percent'},
      'ni_superalloy_hot_end':{'test_temperature_C','heat_treatment','manufacturing_route','applied_stress_MPa','element_bounds_wt_percent'},
      'copper_hot_end_local_composition_v1':{'test_temperature_C','composition_family','processing_state','element_bounds_wt_percent','ambient_process'},
      'hea_mpea':{'allowed_elements','element_bounds_at_pct','processing_method','test_temperature_C','constraints'},
      'chip_glass_thermomechanical_family_v1':{'oxide_bounds_mol_percent','poisson_ratio','service_options'},
      'short_cf_thermomechanical_rve_v1':{'matrix_name','matrix_properties','candidate_windows','search_space','target_vf','fiber_length_mm','target_a11'},
    }
    if set(scope)-common-fields[domain]:
        raise RequestError('UNSUPPORTED_CONSTRAINT','unsupported scope fields: '+','.join(sorted(set(scope)-common-fields[domain])))
    if scope.get('constraints'):
        raise RequestError('UNSUPPORTED_CONSTRAINT','arbitrary HEA constraints need an explicit implemented rule adapter')
    for name in ('element_bounds_wt_percent','element_bounds_at_pct','oxide_bounds_mol_percent'):
        bounds=scope.get(name,{})
        if not isinstance(bounds,dict):raise RequestError('BOUNDS','element bounds must be an object')
        for e,interval in bounds.items():
            if not isinstance(interval,list) or len(interval)!=2:raise RequestError('BOUNDS','bounds require [lower, upper]')
            lo,hi=[finite_number(v,name+'.'+e) for v in interval]
            if not 0<=lo<=hi<=100:raise RequestError('BOUNDS','bounds must satisfy 0<=lower<=upper<=100')
    if domain=='reusable_rocket_stainless' and not 293<=finite_number(cond.get('test_temperature_K'),'test_temperature_K')<=1273:
        raise RequestError('TEMPERATURE_SCOPE','stainless composition model covers 293-1273 K')
    if domain=='aerospace_aluminum_composition_v1' and abs(finite_number(cond.get('test_temperature_C',scope.get('test_temperature_C',25.)),'test_temperature_C')-25)>1e-6:
        raise RequestError('TEMPERATURE_SCOPE','aluminum model uses 25 C convention')
    if domain=='hea_mpea':
        search_context=context.get('search_space',{})
        for requested,actual in [('element_bounds_at_pct','elements_at_pct'),('test_temperature_C','test_temperature_C'),('processing_method','processing_method')]:
            if requested in scope and actual in search_context and scope[requested]!=search_context[actual]:
                raise RequestError('CONDITION_CONFLICT','scope differs from actual HEA context: '+requested)
    if domain=='short_cf_thermomechanical_rve_v1':
        if scope.get('matrix_properties'):
            raise RequestError('UNSUPPORTED_CONSTRAINT','matrix properties are read from the frozen context, not replaced by scope')
        for field,value in cond.get('target_centre',{}).items():
            if field in scope and scope[field]!=value:raise RequestError('CONDITION_CONFLICT','scope and actual RVE centre differ: '+field)
    try:space=NativeSpace(domain,scope,context,reference_policy=reference_policy)
    except ValueError as exc:
        if 'starting anchors' in str(exc):raise RequestError('NO_ELIGIBLE_STARTING_REFERENCE',str(exc)) from exc
        raise
    basis='volume_and_geometry' if space.family=='cf' else 'at_percent' if space.family=='hea_mpea' else 'mol_percent' if space.family=='glass' else 'wt_percent'
    if payload.get('composition_basis')!=basis:
        raise RequestError('COMPOSITION_UNIT','explicit domain composition_basis must be '+basis)
    return space

def input_usage(payload):
    return dict(application_context_only={k:v for k,v in payload['scope'].items() if k in CONTEXT_ONLY},
                reference_policy=payload.get('reference_policy','evaluation'),
                evaluation_isolation_enforced=payload.get('reference_policy','evaluation')=='evaluation',
                model_stack=payload.get('model_stack','transformer_plus_specialist'),
                optimization_conditions_fixed=True,
                optimizer_does_not_train_predictor=True,
                later_verification_targets_are_not_predicted=True,
                unmodeled_process_detail={'ambient_process':payload['scope']['ambient_process']}
                    if payload['domain']=='copper_hot_end_local_composition_v1' and 'ambient_process' in payload['scope'] else {})

def constraint_report(space,composition,row):
    c=composition['values'];basis='volume_and_geometry' if space.family=='cf' else 'at_percent' if space.family=='hea_mpea' else 'mol_percent' if space.family=='glass' else 'wt_percent'
    constraints=[]
    if space.family!='cf':
        error=abs(sum(c.values())-100)
        constraints.append(dict(name='composition_closure',passed=error<=1e-5,violation=error,unit=basis))
    for e,bounds in space.bounds.items():
        value=c.get(e,0.);lo,hi=bounds
        violation=max(lo-value,value-hi,0.)
        constraints.append(dict(name='bound:'+e,passed=violation<=1e-8,violation=violation,lower=lo,upper=hi,unit=basis))
    if 'UTS_MPa' in row['values']:
        violation=max(row['values']['YS_MPa']-row['values']['UTS_MPa'],0.)
        constraints.append(dict(name='UTS_ge_YS',passed=violation==0,violation=violation,unit='MPa'))
    for name,limit in (space.scope.get('screening_thresholds') or {}).items():
        key,mode=THRESHOLDS[name];value=row['values'][key]
        violation=max(float(limit)-value,0.) if mode=='min' else max(value-float(limit),0.)
        constraints.append(dict(name=name,passed=violation==0,violation=violation,limit=float(limit),unit=UNITS[key]))
    shared=row.get('support',{}).get('shared')
    if shared:
        violation=max(shared['distance']-shared['threshold_p99'],0.)
        constraints.append(dict(name='shared_composition_support',passed=shared['admitted'],violation=violation,unit='standardized_distance',
                                outside_element_ranges=shared['outside_element_ranges']))
    else:
        constraints.append(dict(name='specialist_support',passed=bool(row['support_admitted']),violation=None,unit=None))
    constraints.append(dict(name='physical_validity',passed=bool(row['physical_valid']),violation=None,unit=None))
    return constraints

def weight_hashes():
    return {name:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for name,path in
            [('alloy_v10','models/alloy_v10/model_state.pt'),('glass_v1_strict','models/glass_v1/model_state.pt')]}

def code_hash():
    names=['rsi_api.py','rsi_client.py','bounded_nsga.py','candidate_model_stack.py','specialist_bridge.py','specialist_worker.py',
           'alloy_v10_shadow.py','oxide_glass_shadow_contract.py','oxide_glass_shadow_runner.py',
           'stainless_support.py','stainless_input_adapter.py','optimization_contract.py','private_shadow_dispatcher.py']
    paths=[ROOT/name for name in names]+list((ROOT/'models').rglob('*.py'))+list((ROOT.parent/'specialists/src').rglob('*.py'))
    entries={str(p.relative_to(ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(paths) if '__pycache__' not in p.parts}
    return hashlib.sha256(json.dumps(entries,sort_keys=True).encode()).hexdigest()

def evaluate(payload):
    space=load_space(payload);entries=payload.get('candidates')
    if not isinstance(entries,list) or not 1<=len(entries)<=256:
        raise RequestError('BATCH_SIZE','evaluate requires 1-256 candidates')
    inputs=[];ids=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)-{'candidate_id','values','anchor_index','composition_status'}:
            raise RequestError('CANDIDATE_SCHEMA','unsupported candidate fields')
        identifier=entry.get('candidate_id')
        if not isinstance(identifier,str) or not identifier or len(identifier)>128 or identifier in ids:
            raise RequestError('CANDIDATE_ID','unique candidate_id of 1-128 characters required')
        ids.append(identifier)
        c=entry.get('values')
        if not isinstance(c,dict) or not c:raise RequestError('COMPOSITION','candidate values required')
        clean={e:finite_number(v,'values.'+e) for e,v in c.items()}
        if any(v<0 for v in clean.values()):raise RequestError('COMPOSITION','negative composition')
        if space.family=='aluminum':allowed=set(space.artifact['elements'])
        elif space.family=='glass':allowed=set(space.features)
        elif space.family=='nickel_superalloy':
            from candidate_model_stack import ni
            allowed=set(ni.ELEMENTS)
        elif space.family=='copper':allowed=set(space.artifact['elements'])
        elif space.family=='stainless':allowed={'Fe','Cr','Ni','Mn','Si','C','N','P','S'}
        elif space.family=='cf':allowed=set(space.variable)
        else:allowed=set(space.bounds)|{space.balance}
        if set(clean)-allowed:raise RequestError('ELEMENT_SCOPE','candidate contains unsupported elements or variables')
        if space.family=='stainless':
            from stainless_support import canonical_composition
            clean,_=canonical_composition(clean,entry.get('composition_status'))
        elif entry.get('composition_status'):
            raise RequestError('STATUS_SCOPE','reporting statuses are implemented for stainless only')
        if space.family!='cf' and abs(sum(clean.values())-100)>1e-5:
            raise RequestError('COMPOSITION_CLOSURE','composition must sum to 100 in the declared domain basis')
        index=entry.get('anchor_index',0)
        if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(space.anchors):
            raise RequestError('ANCHOR_INDEX','invalid anchor_index')
        inputs.append(dict(values=clean,anchor_index=index))
    started=time.perf_counter();stack=ModelStack(space,mode=payload.get('model_stack','transformer_plus_specialist'))
    try:rows=stack(inputs)
    finally:stack.close()
    latency=time.perf_counter()-started
    output=[]
    for entry,composition,row in zip(entries,inputs,rows):
        constraints=constraint_report(space,composition,row)
        output.append(dict(candidate_id=entry['candidate_id'],values=composition['values'],
            composition_basis='at_percent' if space.family=='hea_mpea' else 'mol_percent' if space.family=='glass' else 'wt_percent' if space.family!='cf' else 'volume_and_geometry',
            material_conditions=space.base_conditions,
            properties={k:dict(prediction=v,unit=UNITS[k],source=row['property_sources'][k],uncertainty=None,
                               uncertainty_status='not_calibrated',evaluation_type='model_prediction') for k,v in row['values'].items()},
            support=row['support'],constraints=constraints,feasible=all(x['passed'] for x in constraints) and row['feasible'],
            p_feasible=None,inference_seconds=latency/len(rows),experiment_verified=False))
    return dict(candidates=output,model_stack='specialist' if stack.mode=='specialist' else 'transformer_plus_property_specialists',elapsed_seconds=latency)

def optimize(payload):
    if 'candidates' in payload:raise RequestError('OPERATION_FIELDS','optimize does not accept pre-selected candidates')
    space=load_space(payload);stack=ModelStack(space,mode=payload.get('model_stack','transformer_plus_specialist'))
    try:
        specs=objectives(payload.get('objectives') or map_resolved_objectives(space.scope,stack.allowed),stack.allowed)
        limits=Limits(**(payload.get('limits') or {}));seed=payload.get('seed',20260928)
        if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<2**32:raise RequestError('SEED','invalid seed')
        out=search(space,stack,specs,limits,seed)
        for row in out['history']:
            row['constraints']=constraint_report(space,row['composition'],row)
        eligible=[r for r in out['history'] if r.get('admissible') is True
                  and all(c['passed'] for c in r['constraints'] if c['name'] not in THRESHOLDS)]
        out['nearest_candidates']=(sorted(eligible,key=lambda r:(r['constraint_violation'],json.dumps(r['composition'],sort_keys=True)))[:3]
                                   if not out['pareto_front'] else [])
        out['nearest_candidate_basis']='sum_relative_performance_shortfall'
        out['reference_policy']=space.reference_policy;out['model_stack']=stack.mode
        out['eligible_for_blind_recovery_benchmark']=space.reference_policy=='evaluation'
        out['domain']=space.domain;out['scope']=space.scope;out['material_conditions']=space.base_conditions
        out['search_variables']=space.variable;out['anchor_count']=len(space.anchors)
        out['resume_supported']=False
        return out
    finally:stack.close()

def execute(payload):
    started=time.perf_counter();operation=payload.get('operation') if isinstance(payload,dict) else None
    if operation not in {'evaluate','optimize'}:raise RequestError('OPERATION','operation must be evaluate or optimize')
    request_id=payload.get('request_id')
    if not isinstance(request_id,str) or not request_id or len(request_id)>128:raise RequestError('REQUEST_ID','request_id required')
    specialist_hashes=verify_specialists()
    result=evaluate(payload) if operation=='evaluate' else optimize(payload)
    return dict(ok=True,api_version=API_VERSION,request_id=request_id,response_id=str(uuid.uuid4()),
                called_utc=datetime.now(timezone.utc).isoformat(),operation=operation,
                weights_sha256=weight_hashes(),preprocessor_sha256=hashlib.sha256((ROOT/'models/alloy_v10/preprocessor.json').read_bytes()).hexdigest(),
                code_tree_sha256=code_hash(),elapsed_seconds=time.perf_counter()-started,
                specialist_weights_sha256=specialist_hashes,
                code_version='rsi_prelaunch_20260928_nicu_fix_20260929',input_usage=input_usage(payload),result=result,public_effect=False,
                display_allowed=False,mode='shadow',production_release_authorized=False)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--response',type=Path,required=True)
    args=parser.parse_args();exit_code=0
    try:
        if args.request.stat().st_size>16*1024*1024:raise RequestError('REQUEST_SIZE','request exceeds 16 MiB')
        payload=json.loads(args.request.read_text(),parse_constant=lambda v: (_ for _ in ()).throw(ValueError('nonfinite JSON constant')))
        response=execute(payload)
    except Exception as exc:
        exit_code=2;response=dict(ok=False,api_version=API_VERSION,error=dict(code=getattr(exc,'code','MODEL_OR_INPUT_ERROR'),message=str(exc)),
                                 public_effect=False,display_allowed=False,mode='shadow',production_release_authorized=False)
    args.response.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.response.with_name(args.response.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(response,ensure_ascii=False,indent=2,allow_nan=False)+'\n');os.replace(temporary,args.response)
    print(json.dumps(dict(ok=response['ok'],response=str(args.response),error=response.get('error')),ensure_ascii=False))
    return exit_code

if __name__=='__main__':raise SystemExit(main())
