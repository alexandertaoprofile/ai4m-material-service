from pathlib import Path
import hashlib
import json
import sys
import numpy as np
import pandas as pd
import torch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'models/glass_v1/code'))
import train_oxide_glass_transformer as t
from oxide_glass_shadow_runner import load_frozen,run_batch,verify_health

torch.set_num_threads(1)
frame=pd.read_csv(ROOT/'frozen_data/glass_v0.csv.gz',low_memory=False)
frame['source_kod']=frame.source_kod.astype(str);comps=t.parse_compositions(frame)
manifest=json.loads((ROOT/'GLASS_V1_MANIFEST.json').read_text())
loaded=load_frozen(Path(manifest['checkpoint']),Path(manifest['model_code_dir']),manifest['checkpoint_sha256'],
                   manifest['infer_sha256'],manifest['train_sha256'])
state,model,research,torch,digest=loaded
folds=[]
for fold in (1,2,3):
    cp=torch.load(ROOT/f'glass_v1_source_purged/fold_{fold}/checkpoint.pt',weights_only=True)
    pre=cp['preprocessor'];train=np.asarray(cp['train_indices']);val=np.asarray(cp['validation_indices']);test=np.asarray(cp['test_indices'])
    fitted=t.Preprocessor(frame,train,comps,pre['vocab'],pre['tasks'],pre['max_tokens'])
    stats_ok=all(np.allclose(fitted.state()[key],pre[key],atol=1e-10) for key in ('descriptor_mean','descriptor_std'))
    assert stats_ok
    source_overlap=len(set(frame.iloc[train].source_kod)&set(frame.iloc[test].source_kod))
    comp_overlap=len(set(frame.iloc[train].canonical_composition)&set(frame.iloc[test].canonical_composition))
    val_overlap=len(set(frame.iloc[train].canonical_composition)&set(frame.iloc[val].canonical_composition))
    assert source_overlap==comp_overlap==val_overlap==0
    folds.append(dict(fold=fold,training_statistics_parity=True,train_test_source_overlap=source_overlap,
                      train_test_composition_overlap=comp_overlap,train_validation_composition_overlap=val_overlap,
                      descriptor_training_compositions=pre['descriptor_training_compositions']))
pre=state['preprocessor'];fit=t.Preprocessor(frame,np.arange(len(frame)),comps,pre['vocab'],pre['tasks'],pre['max_tokens'])
indices=[]
for task in ('DENSITY_20C','YOUNG_MODULUS'):
    subset=frame.index[frame.task.eq(task)&frame.composition_json.str.contains('SiO2')]
    indices.extend(subset[:64])
sub=frame.loc[indices];enc=fit.encode(sub)
batch={k:torch.as_tensor(getattr(enc,v)) for k,v in {
    'species':'species_ids','fractions':'fractions','descriptors':'descriptors','task':'task_ids','condition':'condition','condition_mask':'condition_mask'}.items()}
with torch.inference_mode():pred_z=model(batch).cpu().numpy()
direct=t.decode_predictions(sub,np.arange(len(sub)),pred_z,fit)
requests=[dict(request_id=f'parity-{i}',composition_mol_percent={k:100*v for k,v in comps[row.canonical_composition]},task=row.task)
          for i,row in enumerate(sub.itertuples())]
out=run_batch({'requests':requests},*loaded)
actual=np.asarray([r['prediction']['value'] for r in out['results']]);diff=float(np.max(np.abs(actual-direct)))
assert np.allclose(actual,direct,rtol=2e-5,atol=1e-4)
payload={'requests':[requests[0],requests[64]]+[dict(request_id='unsupported-thermal',composition_mol_percent=requests[0]['composition_mol_percent'],task='THERMAL_CONDUCTIVITY')]}
health=run_batch(payload,*loaded)
expected={r['request_id']:({'task':r['prediction']['task'],'unit':r['prediction']['unit'],'value':r['prediction']['value']}
                          if r['status']=='predicted_diagnostic' else {'status':r['status']}) for r in health['results']}
verify_health(health,expected)
(ROOT/'glass_shadow_example_batch.json').write_text(json.dumps(payload,indent=2))
(ROOT/'glass_shadow_health_expected.json').write_text(json.dumps(expected,indent=2))
oof=pd.read_csv(ROOT/'glass_v1_source_purged/oof_predictions.csv.gz')
assert len(oof)==len(frame) and oof.row_index.nunique()==len(frame) and np.isfinite(oof.prediction).all()
report=dict(all_rows=len(frame),independent_compositions=len(comps),source_groups=frame.source_kod.nunique(),
            folds=folds,all_oof_rows_present_once=True,all_oof_outputs_finite=True,
            wrapper_prediction_parity_rows=len(sub),maximum_wrapper_difference=diff,
            health_expected_origin='cross-checked raw inference against independent training encoder',
            properties_admitted_to_shadow=['DENSITY_20C','YOUNG_MODULUS'],
            cte_interval_semantics_match=False,thermal_conductivity_primary_admitted=False,
            license_review_required=True,production_release_authorized=False)
(ROOT/'GLASS_MODEL_AUDIT.json').write_text(json.dumps(report,indent=2,default=int))
print(json.dumps(report,default=int))
