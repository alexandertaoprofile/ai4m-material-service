"""All-row raw input parity, immutable-checkpoint replay and matched controls."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'): os.environ[key]='1'
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

ROOT = Path('/data/se42/experiments/material_release_validation_20260928')
PATENT = Path('/data/se42/experiments/patent_optimization_v0_20260922')
V10 = PATENT/'process_semantics_canonical_v10_20260923'
FOUNDATION = Path('/data/se42/experiments/alloy_foundation_v0_20260916')
RUNTIME = Path('/data/se42/hea_surrogate/models/unified_alloy_tensile_v2_20260921/runtime')
sys.path[:0]=[str(ROOT), str(V10/'code_snapshot'), str(PATENT), str(RUNTIME)]
import alloy_v10_shadow as adapter
import train_v8_canonical as train
import frozen_process
import frozen_descriptors

def metric(target,pred):
    target=np.asarray(target,float); pred=np.asarray(pred,float)
    return dict(n=len(target),MAE=float(mean_absolute_error(target,pred)),
                RMSE=float(np.sqrt(mean_squared_error(target,pred))),
                R2=float(r2_score(target,pred)) if len(target)>1 else None,
                median_AE=float(np.median(np.abs(target-pred))),
                p95_AE=float(np.quantile(np.abs(target-pred),.95)))

def summarise(frame,pred):
    result={}
    for key,group in frame.assign(prediction=pred).groupby(['role','domain','task']):
        result['/'.join(key)]=metric(group.target,group.prediction)
    return result

def raw_request(row,raw,pre):
    source_audit=raw.get(row.record_id,{})
    text=source_audit.get('raw_process')
    if not isinstance(text,str): text='unresolved'
    params={}
    for name in frozen_process.PARAMS:
        col='desc_v2_'+name
        if col in row.index and pd.notna(row[col]): params[name]=float(row[col])
    # Preserve conflicting original explicit fields too. Reconstructing from
    # cleaned NaNs would discard the conflict and silently prefer text.
    if pd.notna(source_audit.get('conflicts')) and source_audit.get('conflicts'):
        for item in json.loads(source_audit.get('evidence') or '[]'):
            if item.get('method')=='explicit_field' and item.get('field') in frozen_process.PARAMS:
                params[item['field']]=item['value']
    process=dict(text=text,parameters=params)
    v6_stages=[c for c in pre['numeric_cols'] if c.startswith(('desc_v6_solution_stage','desc_v6_precipitation_stage')) and not c.endswith('_missing')]
    if row.domain=='nickel_superalloy' and row[v6_stages].notna().any():
        process['v6_stages']=str(row.repair_process_raw)
    measurement={}
    for name in ('test_strain_rate_per_s','grain_size_um','grain_areal_density_per_mm2','phase_FCC','phase_BCC','phase_HCP','phase_B2','phase_L12'):
        col='desc_v6_'+name
        if col in row.index and pd.notna(row[col]): measurement[name]=float(row[col])
    return dict(request_id=str(row.record_id),domain=row.domain,composition=json.loads(row.composition_json),
                composition_basis='at_fraction',temperature=None if bool(row.temperature_missing)
                else dict(value=float(row.test_temperature_C),unit='C'),process=process,measurements=measurement)

def main():
    start=time.perf_counter()
    args=SimpleNamespace(checkpoint=ROOT/'models/alloy_v10/model_state.pt',
                         preprocessor=ROOT/'models/alloy_v10/preprocessor.json',
                         descriptor_properties=RUNTIME/'assets/descriptor_properties.json',
                         code_dir=V10/'code_snapshot',support_code_dir=PATENT,v2_runtime=RUNTIME,threads=1)
    loaded=adapter.load_frozen(args)
    pre,properties,parser,descriptors,model,torch=loaded
    frame,_=train.view(train.prepare_frame(ROOT/'frozen_data/alloy_v8.csv'),pre['config'])
    fitted=train.base.PhysicsPreprocessor(frame,np.flatnonzero(frame.role.eq('train')))
    reference=fitted.encode_rows(frame)
    statistics_match=all(all(np.isclose(getattr(fitted,key)[name],value,atol=1e-10,rtol=1e-10)
                             for name,value in pre[key].items())
                         for key in ('numeric_mean','numeric_std','numeric_median'))
    assert statistics_match and fitted.numeric_cols==pre['numeric_cols']
    process_vocab_refit_equal = list(fitted.vocab.processes)==pre['processes']
    # The saved token mapping is authoritative. pandas value_counts tie
    # ordering is not a serialization format and differs across environments.
    fitted.vocab.processes=pre['processes']
    fitted.vocab.domains=pre['domains']
    fitted.vocab.tasks=pre['tasks']
    reference=fitted.encode_rows(frame)
    raw=pd.read_csv(FOUNDATION/'process_adapter_v2_20260920/process_parsing_audit.csv',usecols=['record_id','raw_process','evidence','conflicts'],low_memory=False)
    raw=raw.drop_duplicates('record_id').set_index('record_id').to_dict('index')
    failures=[]; counts={}; serving_numeric=[]; requests=[]
    for i,row in frame.iterrows():
        key=f'{row.domain}/{row.role}'; count=counts.setdefault(key,dict(checked=0,failures=0,max_numeric_z_error=0))
        request=raw_request(row,raw,pre); requests.append(request)
        try:
            encoded,audit=adapter.encode_request(request,pre,properties,parser,descriptors)
            numeric_error=np.abs(encoded['numeric']-reference['numeric'][i]); largest=float(numeric_error.max())
            okay=(np.array_equal(encoded['element_ids'],reference['element_ids'][i])
                  and np.allclose(encoded['fractions'],reference['fractions'][i],atol=1e-7,rtol=1e-6)
                  and np.allclose(encoded['numeric'],reference['numeric'][i],atol=1e-5,rtol=1e-5)
                  and encoded['process']==reference['process'][i] and encoded['domain']==reference['domain'][i])
            serving_numeric.append(encoded['numeric']); count['max_numeric_z_error']=max(count['max_numeric_z_error'],largest)
            if not okay:
                names=np.asarray(pre['numeric_cols']); difference=dict(zip(names[numeric_error>1e-5], numeric_error[numeric_error>1e-5].tolist()))
                if sum(f['domain']==row.domain for f in failures)<6: failures.append(dict(record_id=row.record_id,domain=row.domain,role=row.role,
                    expected_label=row.process_label,actual_label=audit['process_label_encoded'],differences=difference,
                    expected_process_id=int(reference['process'][i]),actual_process_id=int(encoded['process'])))
        except Exception as exc:
            okay=False; serving_numeric.append(np.full(113,np.nan,dtype=np.float32))
            if sum(f['domain']==row.domain for f in failures)<6: failures.append(dict(record_id=row.record_id,domain=row.domain,role=row.role,error=str(exc)))
        count['checked']+=1; count['failures']+=not okay
    prediction_parts=[]
    with torch.inference_mode():
        for offset in range(0,len(frame),256):
            batch={key:torch.as_tensor(value[offset:offset+256]) for key,value in reference.items()}
            prediction_parts.append(model(batch).cpu().numpy())
    paired=np.concatenate(prediction_parts); assert np.isfinite(paired).all() and (paired[:,1]>=paired[:,0]).all()
    predicted=np.where(frame.task.eq('YS'),paired[:,0],paired[:,1])
    metrics=summarise(frame,predicted)
    final=json.loads((V10/'FINAL_RESULTS.json').read_text())['selected_single_metrics']
    parity={}
    for role in ('test','calibration','source_challenge'):
        for task in ('YS','UTS'):
            idx=frame.role.eq(role)&frame.task.eq(task)
            if idx.any():
                actual=metric(frame.loc[idx,'target'],predicted[idx]); saved=final[role]['by_task'][task]
                parity[f'{role}/{task}']=dict(passed=all(np.isclose(actual[key],saved[key],atol=1e-3,rtol=1e-6) for key in ('MAE','RMSE','R2')),
                    actual=actual,saved=saved,cpu_gpu_tolerance_MPa=0.001)
    # No checkpoint/hyperparameter is selected using these comparison results.
    # Controls receive the same explicit input quantities, masks and train rows.
    atomic=np.zeros((len(frame),max(pre['symbol_to_z'].values())+1),np.float32)
    for i in range(len(frame)):
        atomic[i,reference['element_ids'][i]]=reference['fractions'][i]
    process=np.eye(len(pre['processes'])+1,dtype=np.float32)[reference['process']]
    X=np.concatenate([atomic,reference['numeric'],process],axis=1)
    controls=np.full(len(frame),np.nan); control_time={}
    for domain in pre['domains']:
        for task in ('YS','UTS'):
            idx=frame.domain.eq(domain)&frame.task.eq(task); train_idx=np.flatnonzero(idx&frame.role.eq('train'))
            eval_idx=np.flatnonzero(idx&~frame.role.eq('train'))
            if not len(train_idx) or not len(eval_idx): continue
            t0=time.perf_counter()
            control=ExtraTreesRegressor(n_estimators=300,min_samples_leaf=2,max_features=1.0,
                                        n_jobs=2,random_state=20260928)
            control.fit(X[train_idx],frame.target.to_numpy()[train_idx]); controls[eval_idx]=control.predict(X[eval_idx])
            control_time[f'{domain}/{task}']=time.perf_counter()-t0
    control_metrics=summarise(frame.loc[~frame.role.eq('train')],controls[~frame.role.eq('train')])
    holdout_rows=frame.loc[~frame.role.eq('train'),['record_id','composition_key','source','role','domain','task','target','test_temperature_C','temperature_missing','process_label']].copy()
    holdout_rows['prediction']=predicted[~frame.role.eq('train')]; holdout_rows['matched_control_prediction']=controls[~frame.role.eq('train')]
    holdout_rows.to_json(ROOT/'ALLOY_HELDOUT_PREDICTIONS.json',orient='records',indent=2,force_ascii=False)
    role_comp=frame.groupby('composition_key').role.nunique(); source_role=frame.groupby(['domain','source']).role.nunique()
    report=dict(checked_rows=len(frame),independent_composition_groups=int(frame.composition_key.nunique()),
                split_composition_overlap=int((role_comp>1).sum()),source_groups_shared_between_roles=int((source_role>1).sum()),
                source_challenge_domains=sorted(frame.loc[frame.role.eq('source_challenge'),'domain'].unique()),
                preprocessor_training_statistics_parity=statistics_match,all_row_input_parity=counts,
                process_vocabulary_refit_equal=process_vocab_refit_equal,
                process_vocabulary_policy='saved mapping only; never regenerate by runtime value_counts',
                input_parity_failures=failures,checkpoint_metric_parity=parity,metrics=metrics,
                matched_extratrees_control=dict(config=dict(n_estimators=300,min_samples_leaf=2,random_state=20260928,
                    feature_policy='atomic fractions + same 113 normalized numeric features + process one-hot'),
                    metrics=control_metrics,fit_seconds=control_time),
                finite_ordered_outputs=True,elapsed_seconds=time.perf_counter()-start,
                limitations=['frozen historical holdouts have been inspected previously; not a new blind test',
                             'source challenge exists only for steel and titanium',
                             'matched controls are audit baselines, not production models'],production_release_authorized=False)
    (ROOT/'ALLOY_MODEL_AUDIT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps(dict(checked_rows=len(frame),input_failures=sum(c['failures'] for c in counts.values()),
                          metric_parity=parity,elapsed_seconds=report['elapsed_seconds']),ensure_ascii=False),flush=True)

if __name__=='__main__': main()
