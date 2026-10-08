"""Versioned PRIVATE composition optimization adapter for frozen material models.

The composition domain is the deployed generator's domain, not the bounds of
an earlier reduced-variable trial. Fixed processing conditions are not optimized.
"""
from __future__ import annotations
from functools import lru_cache
import itertools
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT.parent/'specialists')]
from src.api import aerospace_aluminum_runtime as al, ni_superalloy_hotend_runtime as ni
from src.api import copper_hotend_local_runtime as cu, reusable_rocket_stainless_runtime as ss
from src.api import chip_glass_thermomechanical_runtime as glass, runtime as hea
from src.api import short_cf_thermomechanical_runtime as cf
import alloy_v10_shadow as alloy

DOMAINS={'aerospace_aluminum_composition_v1':'aluminum','ni_superalloy_hot_end':'nickel_superalloy',
         'reusable_rocket_stainless':'stainless','copper_hot_end_local_composition_v1':'copper',
         'hea_mpea':'hea_mpea','chip_glass_thermomechanical_family_v1':'glass',
         'short_cf_thermomechanical_rve_v1':'cf'}
UNITS={'YS_MPa':'MPa','UTS_MPa':'MPa','elongation_pct':'%', 'rupture_hours':'h',
       'phase_risk':'1','density_g_cm3':'g/cm3','young_modulus_GPa':'GPa',
       'cte_0_300_ppm_K':'ppm/K','E11_MPa':'MPa','fiber_volume_fraction':'1'}
UNITS.update({k:'MPa' for k in ('E22_MPa','E33_MPa','G12_MPa','G13_MPa','G23_MPa')})
UNITS.update({k:'1' for k in ('V12','V13','V23','anisotropy_ratio_E11_E22')})
UNITS.update(stress_optical_coefficient_nm_cm_per_MPa='nm/(cm MPa)',
             viscosity_temperature_200_poise_C='C',viscosity_temperature_35kpoise_C='C')


@lru_cache(maxsize=1)
def load_alloy():
    base=ROOT/'models/alloy_v10'; code=base/'code'
    alloy.FOUNDATION_DIR=base/'foundation'
    sys.path.insert(0,str(base/'foundation'))
    return alloy.load_frozen(SimpleNamespace(checkpoint=base/'model_state.pt',preprocessor=base/'preprocessor.json',
        descriptor_properties=code/'descriptor_properties.json',code_dir=code,support_code_dir=code,
        v2_runtime=code,threads=1))


@lru_cache(maxsize=1)
def load_glass():
    from oxide_glass_shadow_runner import load_frozen
    d=json.loads((ROOT/'GLASS_V1_MANIFEST.json').read_text())
    return load_frozen(ROOT/'models/glass_v1/model_state.pt',ROOT/'models/glass_v1/code',d['checkpoint_sha256'],
                       d['infer_sha256'],d['train_sha256'])

def restrict_to_training_anchors(family,anchors,composition_of,reference_policy='evaluation'):
    """Keep blind recovery separate from service use of known formulations."""
    if reference_policy not in {'evaluation','production'}:
        raise ValueError('unknown reference policy')
    if reference_policy=='production':
        return list(anchors)
    policy=json.loads((ROOT/'rsi_training_compositions.json').read_text())
    excluded=set(policy['excluded_evaluation_composition_hashes'][family])
    properties=json.loads((ROOT/'models/alloy_v10/code/descriptor_properties.json').read_text())
    admitted=[]
    for anchor in anchors:
        c=composition_of(anchor)
        mol={e:v/properties[e]['AtomicWeight'] for e,v in c.items() if v>0}
        total=sum(mol.values());atomic={e:v/total for e,v in mol.items()}
        key=';'.join(f'{e}:{v:.7f}' for e,v in sorted(atomic.items()) if v>0)
        if hashlib.sha256(key.encode()).hexdigest() in excluded:continue
        # External specialist references may be used if they are not a frozen
        # evaluation composition. No performance labels are read here.
        admitted.append(anchor)
    if not admitted:raise ValueError('all requested starting anchors are frozen evaluation compositions')
    return admitted


class NativeSpace:
    def __init__(self,domain,scope,task,*,reference_policy='evaluation'):
        if domain not in DOMAINS:raise ValueError('unsupported optimization domain')
        if reference_policy not in {'evaluation','production'}:raise ValueError('unknown reference policy')
        self.reference_policy=reference_policy
        self.domain=domain;self.family=DOMAINS[domain];self.scope=dict(scope);self.task=task
        self.bounds={};self.balance=None;self.anchors=[{}];self.variable=[];self.base_conditions=task.get('screening_conditions',{})
        if self.family=='aluminum':
            self.artifact=(al._artifact() if os.environ.get('MATERIAL_SPECIALIST_LOCAL')=='1'
                           else json.loads((ROOT/'aluminum_generator_metadata.json').read_text()))
            p=self.base_conditions['processing_category']
            self.anchors=[a for a in self.artifact['anchors'] if a['processing_category']==p] or self.artifact['anchors']
            self.variable=[e for e in al._VARIABLES if e in self.artifact['elements']]
            self.bounds=scope.get('element_bounds_wt_percent') or {};self.balance='Al'
        elif self.family=='nickel_superalloy':
            self.bounds=ni._bounds(scope);route=ni.ROUTE_CODES[str(scope['manufacturing_route']).casefold()]
            self.anchors=ni._anchors(route,self.bounds);self.variable=[e for e in self.bounds if e!='Ni'];self.balance='Ni'
        elif self.family=='copper':
            self.artifact=cu._artifact();c=self.base_conditions
            self.anchors=cu._contextual_references(self.artifact,c['composition_family'],c['processing_state'],c['test_temperature_C'])
            self.variable=[e for e in self.artifact['elements'] if e!='Cu'];self.bounds=c['element_bounds_wt_percent'];self.balance='Cu'
        elif self.family=='stainless':
            self.bounds=ss._bounds(scope);self.variable=list(self.bounds);self.balance='Fe'
        elif self.family=='hea_mpea':
            self.bounds=task['search_space']['elements_at_pct'];self.balance=list(self.bounds)[-1]
            self.variable=[e for e in self.bounds if e!=self.balance]
        elif self.family=='glass':
            art=glass._artifacts()[0];features=art['features'];self.features=features
            self.bounds,*_=glass._proposal_constraints(scope,features,art['report']['training_min_max_mol_percent'])
            self.anchors=glass._family_anchors(features,art['report']['training_min_max_mol_percent'])
            self.pairs=list(itertools.combinations(('Al2O3','B2O3','MgO','CaO','BaO','SrO'),2))
            self.variable=['pair','delta_1','delta_2'];self.balance='SiO2'
        else:
            self.variable=['target_vf','fiber_length_mm','target_a11']
            self.bounds=task['screening_conditions']['local_candidate_windows']
            self.anchors=[{'matrix_name':r['matrix_name'],'matrix_properties':{
                k:v for k,v in r['inputs'].items() if k not in self.variable}}
                for r in task['all_candidates']]
            # Use actual stored RVE inputs, not a fabricated matrix property.
            unique={r['matrix_name']:r for r in self.anchors};self.anchors=list(unique.values())
        if self.family in {'aluminum','nickel_superalloy','copper'}:
            def amounts(a):
                if self.family=='aluminum':return {e:float(a.get(e,0))*100 for e in self.artifact['elements']}
                return a['composition' if self.family=='nickel_superalloy' else 'composition_wt_percent']
            self.anchors=restrict_to_training_anchors(self.family,self.anchors,amounts,self.reference_policy)
        if not self.anchors:raise ValueError('no traceable anchor in requested domain')
        self.lower=np.zeros(len(self.variable)+1);self.upper=np.ones(len(self.variable)+1)
        self.reset()

    def reset(self):self.sample_offset=0

    def base(self,i):
        a=self.anchors[i]
        if self.family=='aluminum':return {e:float(a.get(e,0))*100 for e in self.artifact['elements']}
        if self.family=='nickel_superalloy':return dict(a['composition'])
        if self.family in {'copper','glass'}:return dict(a['composition_mol_percent' if self.family=='glass' else 'composition_wt_percent'])
        return {}

    def ranges(self,i):
        b=self.base(i);ranges=[]
        for e in self.variable:
            if self.family=='aluminum':r=self.bounds.get(e,[max(0,b.get(e,0)-.5),b.get(e,0)+.5])
            elif self.family=='nickel_superalloy':
                r=[max(self.bounds[e][0],b.get(e,0)-.35),min(self.bounds[e][1],b.get(e,0)+.35)] if b.get(e,0)>0 else [0,0]
            elif self.family=='copper':r=self.bounds.get(e,[0,max(.2,b.get(e,0)+1)])
            else:r=self.bounds[e]
            if len(r)!=2 or not all(math.isfinite(float(x)) for x in r) or r[0]>r[1]:raise ValueError('invalid resolved variable interval')
            ranges.append(r)
        return np.asarray(ranges,float)

    def decode(self,x):
        if not np.isfinite(x).all():return None
        i=min(int(float(x[0])*len(self.anchors)),len(self.anchors)-1)
        b=self.base(i)
        if self.family=='glass':
            pair=self.pairs[min(int(float(x[1])*len(self.pairs)),len(self.pairs)-1)]
            for e,u in zip(pair,x[2:]):
                delta=.24*float(u)-.12;b[e]+=delta;b['SiO2']-=delta
        else:
            ranges=self.ranges(i)
            b.update({e:float(l+(h-l)*u) for e,(l,h),u in zip(self.variable,ranges,x[1:])})
            if self.balance:b[self.balance]=100-sum(v for e,v in b.items() if e!=self.balance)
        if self.family in {'aluminum','nickel_superalloy','copper'}:
            # Same 5-decimal wt.% resolution as the actual native generator;
            # avoid artificial 1e-20 solutes introduced by continuous mutation.
            b={e:round(v,5) for e,v in b.items()}
            b[self.balance]=round(100-sum(v for e,v in b.items() if e!=self.balance),5)
        if self.family!='cf':
            if any(v < -1e-8 for v in b.values()) or abs(sum(b.values())-100)>1e-5:return None
            if any(not (l-1e-8<=b.get(e,0)<=h+1e-8) for e,(l,h) in self.bounds.items()):return None
            if self.family=='stainless' and not 10<=100-b['Fe']<=60:return None
        return dict(values=b,anchor_index=i)

    def key(self,c):return (c['anchor_index'],tuple(sorted((k,round(v,9)) for k,v in c['values'].items())))

    def sample(self,rng,n):
        X=rng.uniform(0,1,size=(n,len(self.lower)))
        # Native random proposal law is retained for the control. NSGA's
        # initial population uses exactly the same draw, then evolves it.
        for j in range(n):
            i=(self.sample_offset+j)%len(self.anchors) if self.family in {'aluminum','copper'} else int(rng.integers(len(self.anchors)))
            X[j,0]=(i+.5)/len(self.anchors)
            c=None
            if self.family=='aluminum':c=al._composition(self.anchors[i],self.bounds,rng,self.artifact['elements'])
            elif self.family=='nickel_superalloy':c=ni._candidate_from_anchor(self.anchors[i],self.bounds,rng)
            elif self.family=='copper':c=cu._composition_from_anchor(self.anchors[i],self.bounds,rng,self.artifact['elements'])
            elif self.family=='hea_mpea':
                names=list(self.bounds)
                for _ in range(4096):
                    raw=rng.dirichlet(np.ones(len(names)))*100;c=dict(zip(names,raw))
                    if all(l<=c[e]<=h for e,(l,h) in self.bounds.items()):break
                else:raise ValueError('native Dirichlet law found no candidate within bounded proposal retries')
            if c is not None:
                ranges=self.ranges(i)
                X[j,1:]=[(c.get(e,0)-l)/(h-l) if h>l else 0 for e,(l,h) in zip(self.variable,ranges)]
        self.sample_offset+=n
        return X


class ModelStack:
    def __init__(self,space,mode='transformer_plus_specialist',specialist_local=False,*,apply_thresholds=True):
        if mode not in {'transformer_plus_specialist','specialist'}:raise ValueError('unknown model stack')
        self.space=space;self.mode=mode;self.apply_thresholds=apply_thresholds
        self.bridge=None
        if not specialist_local:
            from specialist_bridge import SpecialistBridge
            self.bridge=SpecialistBridge(space)
        if mode!='specialist' and space.family not in {'cf'}:
            self.loaded=load_glass() if space.family=='glass' else load_alloy()
        if space.family=='cf':self.allowed={k:UNITS[k] for k in ('E11_MPa','E22_MPa','E33_MPa','G12_MPa','G13_MPa','G23_MPa','V12','V13','V23','fiber_volume_fraction','anisotropy_ratio_E11_E22')}
        elif space.family=='glass':self.allowed={k:UNITS[k] for k in ('density_g_cm3','young_modulus_GPa','cte_0_300_ppm_K','stress_optical_coefficient_nm_cm_per_MPa','viscosity_temperature_200_poise_C','viscosity_temperature_35kpoise_C')}
        else:
            fields=['YS_MPa','UTS_MPa']
            if space.family in {'aluminum','stainless','nickel_superalloy'}:fields.append('elongation_pct')
            if space.family=='nickel_superalloy':fields.append('rupture_hours')
            if space.family=='hea_mpea':fields.append('phase_risk')
            self.allowed={k:UNITS[k] for k in fields}

    def __call__(self,candidates):
        s=self.space;cond=s.base_conditions;rows=[];requests=[]
        legacy=self.bridge.evaluate(candidates) if self.bridge else None
        batch_al=[]
        batch_ss=[];batch_ss_ad=[]
        batch_hea=[];batch_glass=[]
        if s.family=='glass' and legacy is None:
            batch_glass=glass._predict_properties_many([item['values'] for item in candidates])
        if s.family=='hea_mpea' and legacy is None:
            batch_hea=hea._evaluate_many(s.task['search_space'],[item['values'] for item in candidates])
        if s.family=='stainless' and legacy is None:
            comps=[{e:item['values'].get(e,0) for e in ss.ELS} for item in candidates]
            batch_ss=ss._predict_many(comps,cond['test_temperature_K'],cond['processing'])
            batch_ss_ad=ss._applicability_many(comps,cond['test_temperature_K'])
        if s.family=='aluminum' and legacy is None:
            a=s.artifact
            frame=pd.DataFrame([{**{e:item['values'].get(e,0)/100 for e in a['elements']},
                                 'processing_category':cond['processing_category']} for item in candidates])
            cols={k:m.predict(frame) for k,m in a['models'].items()}
            batch_al=[{k:float(values[i]) for k,values in cols.items()} for i in range(len(candidates))]
        for candidate_index,item in enumerate(candidates):
            c=item['values'];p={};okay=True;support=None;physical=True
            if legacy is not None:
                old=legacy[candidate_index];p=old['values'];okay=old['support_admitted'];physical=old['physical_valid'] if s.family=='cf' else True;support=old['support']['specialist']
            elif s.family=='aluminum':
                a=s.artifact;p0=batch_al[candidate_index]
                p={'YS_MPa':p0['yield_strength_MPa'],'UTS_MPa':p0['uts_MPa'],'elongation_pct':p0['elongation_pct']}
                distance=min(np.linalg.norm([c.get(e,0)/100-float(a0.get(e,0)) for e in a['elements']]) for a0 in s.anchors)
                support='inside' if distance<=.025 else 'boundary'
            elif s.family=='stainless':
                c0={e:c.get(e,0) for e in ss.ELS};temp=cond['test_temperature_K']
                ad=batch_ss_ad[candidate_index];p0=batch_ss[candidate_index]
                support=ad['level'];okay=support!='outside'
                p={'YS_MPa':p0['yield_0p2_MPa']['mean'],'UTS_MPa':p0['uts_MPa']['mean'],'elongation_pct':p0['elongation_pct']['mean']}
            elif s.family=='nickel_superalloy':
                p0=ni.evaluate({**cond,'composition_wt_percent':c});strength=p0['short_time_strength'];creep=p0['creep_rupture']
                p={'YS_MPa':strength['proof_strength_0p2_MPa']['mean'],'UTS_MPa':strength['ultimate_tensile_strength_MPa']['mean'],
                   'elongation_pct':p0['tensile_ductility']['elongation_percent']}
                if 'predicted_hours' in creep:p['rupture_hours']=creep['predicted_hours']
                support=creep.get('applicability_domain',{}).get('level');okay=support!='outside'
            elif s.family=='copper':
                uts,ys=cu._predict(s.artifact,c,cond['test_temperature_C'],cond['processing_state']);p={'YS_MPa':ys,'UTS_MPa':uts}
                matching=[r for r in s.anchors if cu._context_distance(r,cond['processing_state'],cond['test_temperature_C'],tuple(s.artifact['process_tokens']))==(0,0)]
                distance=min((np.linalg.norm([c.get(e,0)-r['composition_wt_percent'].get(e,0) for e in s.artifact['elements']]) for r in matching),default=float('inf'))
                support='local' if distance<=2.5 else 'outside';okay=support!='outside'
            elif s.family=='hea_mpea':
                p0=batch_hea[candidate_index]
                p={'YS_MPa':p0['yield_strength_MPa']['mean'],'phase_risk':p0['phase_probabilities'].get('IM',0)+p0['phase_probabilities'].get('SS+IM',0)}
                support=p0['applicability_domain']['level'];okay=support!='outside'
            elif s.family=='glass':
                p0=batch_glass[candidate_index]
                p={'cte_0_300_ppm_K':p0['CTE_linear_0_to_300C']['prediction_ppm_per_K'],
                   'density_g_cm3':p0['density_g_per_cm3']['prediction'],'young_modulus_GPa':p0['young_modulus_GPa']['prediction']}
                for key in ('stress_optical_coefficient_nm_cm_per_MPa','viscosity_temperature_200_poise_C','viscosity_temperature_35kpoise_C'):
                    if key in p0:p[key]=p0[key]['prediction']
                support='patent_family_local_anchor'
            else:
                a=s.anchors[item['anchor_index']];p0=cf.evaluate({'matrix_name':a['matrix_name'],'matrix_properties':a['matrix_properties'],**c})
                p={key:value for key,value in p0['predicted_engineering_constants'].items() if key in UNITS}
                p['fiber_volume_fraction']=c['target_vf'];p['anisotropy_ratio_E11_E22']=p['E11_MPa']/p['E22_MPa']
                support=p0['applicability_domain']['level'];okay=support!='outside';physical=p0['physical_consistency']['positive_definite_stiffness']
            shared_support=None
            if s.family=='stainless':
                from stainless_support import evaluate as evaluate_support
                shared_support=evaluate_support(c,cond['test_temperature_K']-273.15)
                okay=shared_support['admitted']
                support=shared_support['level']
            rows.append(dict(values=p,feasible=bool(okay),support_admitted=bool(okay),physical_valid=bool(physical),support=support,property_sources={k:'specialist' for k in p},
                             shared_support=shared_support))
            if self.mode!='specialist' and s.family not in {'cf','glass'}:
                if s.family in {'aluminum','stainless','nickel_superalloy'}:
                    from service_adapters import adapt
                    if s.family=='aluminum':actual={'composition_wt_percent':c,'processing_category':cond['processing_category']}
                    elif s.family=='stainless':actual={'composition_wt_percent':c,'test_temperature_K':cond['test_temperature_K'],'processing':cond['processing']}
                    else:actual={'composition_wt_percent':c,**cond}
                    request,_=adapt(s.domain,actual)
                    if s.family=='stainless':request['process']['parameters']={'solution_temp_C':cond['processing']['solution_treatment_temperature_K']-273.15,'solution_time_h':cond['processing']['solution_treatment_time_s']/3600}
                    if s.family=='nickel_superalloy':request['process']['v6_stages']=cond['heat_treatment']
                else:
                    temp=cond.get('test_temperature_C',s.task.get('search_space',{}).get('test_temperature_C'))
                    label=cond.get('processing_state',s.task.get('search_space',{}).get('processing_method','unresolved'))
                    request=dict(domain=s.family,composition=c,composition_basis='at_percent' if s.family=='hea_mpea' else 'wt_percent',
                                 temperature=dict(value=temp,unit='C'),process=dict(text=label))
                requests.append(request)
        if self.mode!='specialist' and s.family=='glass':
            state,model,research,torch,_=self.loaded
            from oxide_glass_shadow_runner import infer_many
            tasks=(('DENSITY_20C','density_g_cm3'),('YOUNG_MODULUS','young_modulus_GPa'))
            requests=[{'composition_mol_percent':item['values'],'task':task}
                      for item in candidates for task,_ in tasks]
            predictions=infer_many(requests,state,model,research,torch)
            for i,row in enumerate(rows):
                for j,(_,key) in enumerate(tasks):
                    row['values'][key]=float(predictions[2*i+j]['prediction'])
                    row['property_sources'][key]='glass_v1_strict'
        elif requests:
            result=alloy.predict_many(requests,self.loaded,mode='research')
            for row,p in zip(rows,result['results']):
                if p['status']!='predicted_diagnostic':raise ValueError('unified input rejected: '+str(p))
                for key in ('YS_MPa','UTS_MPa'):
                    mapped={'YS_MPa':'yield_strength_0p2_MPa','UTS_MPa':'ultimate_tensile_strength_MPa'}[key]
                    row['values'][key]=float(p['prediction'][mapped]);row['property_sources'][key]='alloy_v10'
        for row in rows:
            p=row['values'];row['physical_valid'] &= all(math.isfinite(float(v)) for v in p.values())
            if 'UTS_MPa' in p:row['physical_valid'] &= p['UTS_MPa']>=p['YS_MPa']>=0
            row['admissible']=bool(row['support_admitted'] and row['physical_valid'])
            thresholds=(s.scope.get('screening_thresholds') or {}) if self.apply_thresholds else {}
            threshold_map={'uts_min_MPa':('UTS_MPa','min'),'proof_strength_min_MPa':('YS_MPa','min'),
                           'rupture_life_min_h':('rupture_hours','min'),
                           'CTE_linear_0_to_300C_max_ppm_per_K':('cte_0_300_ppm_K','max'),
                           'density_g_per_cm3_max':('density_g_cm3','max'),
                           'young_modulus_GPa_min':('young_modulus_GPa','min'),
                           'stress_optical_coefficient_nm_cm_per_MPa_max':('stress_optical_coefficient_nm_cm_per_MPa','max')}
            gaps={}
            for name,value in thresholds.items():
                if name not in threshold_map:raise ValueError('unsupported threshold: '+name)
                if isinstance(value,bool) or not math.isfinite(float(value)):raise ValueError('nonfinite threshold')
                key,mode=threshold_map[name]
                if key not in p:raise ValueError('requested threshold property not predicted: '+key)
                target=float(value);observed=float(p[key])
                shortfall=max(target-observed,0.) if mode=='min' else max(observed-target,0.)
                gaps[name]=dict(property=key,target=target,observed=observed,shortfall=shortfall,
                                relative_shortfall=shortfall/max(abs(target),1.),direction=mode,unit=UNITS[key])
            row['constraint_gaps']=gaps
            row['constraint_violation']=sum(g['relative_shortfall'] for g in gaps.values())
            row['performance_feasible']=all(g['shortfall']==0 for g in gaps.values())
            row['feasible']=bool(row['admissible'] and row['performance_feasible'])
            # All legacy support labels are preserved as diagnostics. They
            # are NOT a calibrated support approval for the new Transformer.
            if row['shared_support'] is not None:
                row['support']={'specialist':row['support'],'transformer':row['support'],
                                'same_semantics':True,'shared':row['shared_support']}
            else:
                row['support']={'specialist':row['support'],'transformer':'not_calibrated' if self.mode!='specialist' else 'not_used'}
        return rows

    def close(self):
        if self.bridge:self.bridge.close()
