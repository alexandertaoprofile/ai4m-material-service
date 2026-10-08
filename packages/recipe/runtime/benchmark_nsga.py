"""Five-seed comparison with native search spaces, fixed prediction stack,
identical INITIAL draws, objective definitions and true model budgets.
"""
from pathlib import Path
import argparse
import json
import time
from bounded_nsga import Limits,objectives,search
from candidate_model_stack import NativeSpace,ModelStack,DOMAINS
from replay_production_stopping import recent_manifests
from pymoo.indicators.hv import HV
import numpy as np

ROOT=Path(__file__).resolve().parent
# Fixed physical-unit reference scales; never normalize on two almost
# coincident Pareto endpoints (which spuriously produced >10000% ratios).
HV_RANGES={'YS_MPa':[-3000,0],'UTS_MPa':[-3500,0],'elongation_pct':[-100,0],
           'rupture_hours':[-10000,0],'phase_risk':[0,1],'density_g_cm3':[1,10],
           'young_modulus_GPa':[-250,0],'E11_MPa':[-60000,0],'fiber_volume_fraction':[0,1]}
PLANS={
 'aluminum':[('YS_MPa','max'),('elongation_pct','max')],
 'stainless':[('YS_MPa','max'),('elongation_pct','max')],
 'nickel_superalloy':[('UTS_MPa','max'),('rupture_hours','max')],
 'copper':[('YS_MPa','max'),('UTS_MPa','max')],
 'hea_mpea':[('YS_MPa','max'),('phase_risk','min')],
 'glass':[('density_g_cm3','min'),('young_modulus_GPa','max')],
 'cf':[('E11_MPa','max'),('fiber_volume_fraction','min')]}
parser=argparse.ArgumentParser()
parser.add_argument('--domains',nargs='+',choices=list(DOMAINS))
args=parser.parse_args()
tasks=recent_manifests();report=dict(production_release_authorized=False,scope='private frozen-model diagnostics; not measured materials',
    valid_evaluation_budget=256,population=32,soft_wall_seconds=30,seeds=[17,29,43,73,97],domains={})
if args.domains:
    report=json.loads((ROOT/'NSGA_FAIR_BENCHMARK.json').read_text())
for domain in args.domains or DOMAINS:
    stack=None
    try:
        path,task=tasks[domain];scope=task['requirement_interpretation']['effective_model_input']
        space=NativeSpace(domain,scope,task);stack=ModelStack(space)
        specs=objectives([dict(property=k,mode=m,unit=stack.allowed[k]) for k,m in PLANS[space.family]],stack.allowed)
        runs=[]
        for seed in report['seeds']:
            random=search(space,stack,specs,Limits(),seed,'random');nsga=search(space,stack,specs,Limits(),seed,'nsga2')
            fs=[np.asarray([r['F'] for r in x['pareto_front']],float).reshape(-1,2) for x in (random,nsga)]
            if any(len(x)==0 for x in fs):hvs=[0.,0.]
            else:
                ranges=np.asarray([HV_RANGES[x['property']] for x in specs]);low=ranges[:,0];width=ranges[:,1]-low
                hvs=[float(HV(ref_point=np.array([1.1,1.1]))((f-low)/width)) for f in fs]
            equal=random['counters']['model_evaluations']==nsga['counters']['model_evaluations']==256
            runs.append(dict(seed=seed,equal_model_evaluation_budget_reached=equal,
                random={k:random[k] for k in ('counters','stop_reason','elapsed_seconds','status')},
                nsga2={k:nsga[k] for k in ('counters','stop_reason','elapsed_seconds','status')},
                random_hypervolume=hvs[0],nsga_hypervolume=hvs[1],
                relative_hv_change_percent=100*(hvs[1]/hvs[0]-1) if hvs[0] and equal else None))
            (ROOT/f'NSGA_EXAMPLE_{space.family}_{seed}.json').write_text(json.dumps({k:v for k,v in nsga.items() if k!='history'},indent=2))
            print(domain,seed,runs[-1],flush=True)
        gain=[r['relative_hv_change_percent'] for r in runs if r['relative_hv_change_percent'] is not None]
        report['domains'][domain]=dict(source_manifest=str(path),predictor_stack='transformer plus specialist except CF',
            variables=space.variable,anchors=len(space.anchors),search_space_policy='full native generator domain; fixed process; no reduced four-element surrogate box',
            benchmark_objectives_are_not_user_defaults=True,objectives=specs,runs=runs,
            hypervolume_reference_ranges=HV_RANGES,
            median_hv_gain_percent=float(np.median(gain)) if gain else None,
            nsga_wins=sum(g>0 for g in gain),equal_budget_trials=sum(r['equal_model_evaluation_budget_reached'] for r in runs),
            comparable_hypervolume_trials=len(gain),
            superiority_proven_for_all_requests=False)
    except Exception as exc:
        report['domains'][domain]=dict(error=f'{type(exc).__name__}: {exc}')
        print(domain,'ERROR',report['domains'][domain],flush=True)
    finally:
        if stack:stack.close()
    (ROOT/'NSGA_FAIR_BENCHMARK.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False))
print('benchmark completed',flush=True)
