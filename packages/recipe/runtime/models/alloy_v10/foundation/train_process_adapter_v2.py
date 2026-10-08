"""Offline matched controls: grouped train/validation/calibration/test + source challenge.

Does not write serving repositories. Missing/ambiguous process values remain
missing. Source challenge is retrospective, not a blind prospective test.
"""
from __future__ import annotations
import copy, hashlib, json, math, re, time
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader
from scipy.spatial.distance import cdist
import run_hard_constraint_transformer_cv as old
from run_physics_informed_cv import PhysicsPreprocessor, IndexedDataset, full_metrics

ROOT=Path('/data/se42/experiments/alloy_foundation_v0_20260916')
OUT=ROOT/'process_adapter_v2_20260920'
SEEDS=[20260920,20260921,20260922]
MAX_EPOCHS=80
FLAGS={
 'cast':r'\bcast\b|as.cast|casting', 'wrought':r'forg|roll|extrud|extrus|wrought|swag',
 'additive':r'\bslm\b|lpbf|lded|\bded\b|additive|electron.beam|laser',
 'powder':r'powder|sinter|\bsps\b|\bpm\b',
 'solution':r'solution|\bst(?:[- ](?:wq|ac|fc))?\b|\bsta\b',
 'age':r'ag[ei]ng|\baged?\b|\bsta\b', 'anneal':r'anneal|\bann\b',
 'homogenize':r'homog|\bhom\b', 'coldwork':r'cold.work|cold.roll|cold.reduc|\bcr\b',
 'quench':r'quench|water.cool|\bwq\b', 'aircool':r'air.cool|\bac\b',
 'temper':r'temper(?!ature)', 'single_crystal':r'single.crystal',
 'directional':r'directionally.solidified',
}
PARAMS=['solution_temp_C','solution_time_h','aging_temp_C','aging_time_h',
 'annealing_temp_C','annealing_time_h','homogenization_temp_C','homogenization_time_h',
 'tempering_temp_C','tempering_time_h','cold_reduction_pct']

def clean(v):
 return '' if pd.isna(v) else str(v)
def num(v):
 try:
  f=float(v);return f if np.isfinite(f) else None
 except (ValueError,TypeError):return None
def digest(s):return int(hashlib.sha256(str(s).encode()).hexdigest()[:12],16)
def canonical(comp):
 total=sum(comp.values());return ';'.join(f'{e}:{v/total:.7f}' for e,v in sorted(comp.items()) if v/total>1e-8)
def source_id(s):
 s=clean(s).lower(); n=re.search(r'(19\d{9}|20\d{9})',s)
 return 'ntrs:'+n.group(1) if n else re.sub(r'https?://(?:dx\.)?doi.org/','',s).strip()

def parse_process(text, structured):
 """Conservative numeric extraction with span provenance and conflict masking."""
 text=clean(text).lower().replace('_',' ')
 flags={f'desc_v2_flag_{k}':float(bool(re.search(p,text))) for k,p in FLAGS.items()}
 vals={p:None for p in PARAMS}; evidence=[]; conflicts=[]
 for p,v in structured.items():
  if p in vals and num(v) is not None:
   vals[p]=num(v);evidence.append({'field':p,'value':vals[p],'method':'explicit_field'})
 stages={'solution':r'solution(?:ized|ised|izing|ing|treated)?|\bst\b',
         'aging':r'aging|ageing|aged|\bage\b', 'annealing':r'anneal(?:ing|ed)?',
         'homogenization':r'homogeniz(?:ation|ed|ing)|homogenis(?:ation|ed|ing)',
         'tempering':r'temper(?:ing|ed)?'}
 # JSON process steps preserve ranges as review; do not midpoint them.
 try:
  obj=json.loads(text)
  for step in obj.get('proc_para',[]):
   typ=str(step.get('type',''))
   for stage,pattern in stages.items():
    if not re.search(pattern,typ):continue
    for field,unitout in [('temperature','temp_C'),('time','time_h')]:
     item=step.get(field,{})
     if not isinstance(item,dict):continue
     v=item.get('value');vs=v if isinstance(v,list) else [v]
     vs=[num(x) for x in vs]
     if len(vs)!=1 or vs[0] is None:continue
     unit=str(item.get('unit','')).lower();value=vs[0]
     if field=='temperature':
      if unit=='k':value-=273.15
      elif unit not in ['°c','c','degc']:continue
     elif unit in ['min','minutes']:value/=60
     elif unit in ['s','seconds']:value/=3600
     elif unit not in ['h','hr','hours']:continue
     p=f'{stage}_{unitout}';vals[p]=value;evidence.append({'field':p,'value':value,'method':'json_step'})
 except (ValueError,TypeError,AttributeError):pass
 for stage,pattern in stages.items():
  rg=rf'(?:{pattern})\s*(?:at|:|=)?\s*(\d{{2,4}}(?:\.\d+)?)\s*(?:°\s*)?(c|k)\b(?:\s*(?:for|/|,)?\s*(\d+(?:\.\d+)?)\s*(h|hr|min)\b)?'
  matches=list(re.finditer(rg,text))
  for field in ['temp_C','time_h']:
   p=f'{stage}_{field}';found=[]
   for m in matches:
    if field=='temp_C':v=float(m.group(1))-(273.15 if m.group(2)=='k' else 0)
    elif m.group(3):v=float(m.group(3))/(60 if m.group(4)=='min' else 1)
    else:continue
    found.append(v);evidence.append({'field':p,'value':v,'method':'text_span','span':m.group(0)})
   if vals[p] is not None:found.append(vals[p])
   if found:
    if max(found)-min(found)>max(.02,abs(found[0])*.002):vals[p]=None;conflicts.append(p)
    else:vals[p]=found[0]
 m=re.search(r'(\d+(?:\.\d+)?)\s*(?:pct|%)\s*(?:cold|reduction)',text)
 if m and vals['cold_reduction_pct'] is None:vals['cold_reduction_pct']=float(m.group(1))
 for p,v in vals.items():
  flags['desc_v2_'+p]=np.nan if v is None else v
  flags['desc_v2_'+p+'_missing']=float(v is None)
 flags['desc_v2_text_conflict']=float(bool(conflicts))
 # A field label STA alone is coarse; an explicit aging step takes precedence in flags.
 known=[k for k in FLAGS if flags['desc_v2_flag_'+k]]
 if 'cluster ' in text or re.search(r'form\s*=',text):label=text.strip() # no invented cluster mapping
 elif known:label='+'.join(known)
 else:label='unresolved'
 return flags,label,evidence,conflicts

def prepare():
 base_path=ROOT/'hea_enriched_hard_constraint_transformer_cv_v1/unified_alloy_tensile_strength_v2.csv'
 inc_path=ROOT/'round3_strict_intake_20260920/unified_alloy_tensile_strength_round3_strict.csv'
 base=pd.read_csv(base_path);base=base[base.task.isin(['YS','UTS'])].copy()
 inc=pd.read_csv(inc_path);inc=inc[inc.record_id.str.startswith('round3-')].copy()
 df=pd.concat([base,inc],ignore_index=True)
 df=df.drop(columns=[c for c in df if c.startswith('desc_process_')],errors='ignore')
 steel=pd.read_csv(ROOT/'round3_strict_intake_20260920/steel_admitted_increment.csv').set_index('record_id')
 ti=pd.read_csv(ROOT/'round3_strict_intake_20260920/titanium_admitted_increment.csv').set_index('record_id')
 features=[]; audit=[]; labels=[];sources=[]
 for _,r in df.iterrows():
  text=clean(r.process_label);params={};src=clean(r.source);rid=r.record_id.rsplit(':',1)[0]
  if rid.startswith('round3-'):
   rid=rid[7:]; raw=steel.loc[rid] if r.domain=='steel' else ti.loc[rid]
   if isinstance(raw,pd.DataFrame):raise ValueError('duplicate source record id')
   if r.domain=='steel':text=clean(raw.process_raw);src=source_id(raw.source_id)
   else:
    text='; '.join(clean(raw.get(k)) for k in ['condition_state','heat_treatment_detail','specimen_geometry','specimen_orientation'])
    src=source_id(raw.source_doi)
    for out,k in [('solution_temp_C','solution_temp_c'),('solution_time_h','solution_time_h'),('aging_temp_C','aging_temp_c'),('aging_time_h','aging_time_h')]:params[out]=raw.get(k)
  if r.domain=='hea_mpea':
   src=clean(r.get('source_reference')) or clean(r.get('source_dataset'))
   for out,k in [('homogenization_temp_C','homogenization_temp_K'),('annealing_temp_C','annealing_temp_K'),('annealing_time_h','annealing_time_h'),('aging_temp_C','aging_temp_K'),('aging_time_h','aging_time_h'),('cold_reduction_pct','cold_rolling_reduction_pct')]:
    v=num(r.get('desc_proc_'+k));missing=num(r.get('desc_proc_'+k+'_missing'))
    if v is not None and missing==0:params[out]=v-273.15 if k.endswith('_K') else v
  f,label,ev,conf=parse_process(text,params)
  comp=json.loads(r.composition_json)
  # General physical proxies only; no invented phase fractions or empirical alloy equivalents.
  tm=num(r.get('desc_MeltingT_mean'));temp=num(r.test_temperature_C)
  f['desc_v2_homologous_temperature_proxy']=(temp+273.15)/tm if tm and temp is not None else np.nan
  f['desc_v2_test_temperature_known']=1-float(r.temperature_missing)
  features.append(f);labels.append(label);sources.append(src)
  audit.append({'record_id':r.record_id,'domain':r.domain,'raw_process':text,'process_canonical':label,'source_group':src,'evidence':json.dumps(ev),'conflicts':';'.join(conf)})
 # Remove inherited process columns: newly parsed features use uniform missing masks.
 df=df.drop(columns=[c for c in df if c.startswith('desc_proc_')])
 df=pd.concat([df,pd.DataFrame(features)],axis=1)
 df['process_original']=df.process_label;df['process_label']=labels;df['audit_source']=sources
 df['composition_key']=df.composition_json.map(lambda s:canonical(json.loads(s)))
 df=old.add_explicit_physics_descriptors(df)
 # Do not add raw text/DOI/grade identifiers as input features.
 challenges=set()
 for _,r in df[df.record_id.str.startswith('round3-')].iterrows():
  if r.audit_source and digest('source-split-v2:'+r.audit_source)%4==0:challenges.add(r.composition_key)
 roles=[]
 for key in df.composition_key:
  h=digest('group-split-v2:'+key)%100
  roles.append('source_challenge' if key in challenges else 'test' if h<15 else 'calibration' if h<25 else 'validation' if h<35 else 'train')
 df['role']=roles
 # Explicit dedupe of identical task and condition; provenance audit keeps every incoming row.
 cols=['composition_key','domain','task','test_temperature_C','process_label','target']+[c for c in df if c.startswith('desc_v2_')]
 before=len(df);df=df.drop_duplicates(subset=cols).reset_index(drop=True)
 assert df.groupby('composition_key').role.nunique().max()==1
 pd.DataFrame(audit).to_csv(OUT/'process_parsing_audit.csv',index=False)
 df.to_csv(OUT/'dataset_snapshot.csv',index=False)
 df[['record_id','domain','composition_key','audit_source','role']].to_csv(OUT/'split_manifest.csv',index=False)
 report={'rows':len(df),'duplicates_removed':before-len(df),'compositions':df.composition_key.nunique(),
  'roles':df.groupby(['role','domain']).size().to_dict(),
  'feature_known_counts':{p:int(df['desc_v2_'+p].notna().sum()) for p in PARAMS},
  'sources_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [base_path,inc_path]},
  'source_challenge_note':'Retrospective source-group challenge within new steel/titanium batches; every matching chemistry is removed globally from training. Full primary-publication lineage independence across legacy data is not asserted.'}
 report['roles']={str(k):int(v) for k,v in report['roles'].items()}
 (OUT/'data_audit.json').write_text(json.dumps(report,indent=2))
 return df

class ProcessAdapterTransformer(old.HardConstraintTransformer):
 def __init__(self,**kwargs):
  super().__init__(**kwargs)
  self.adapters=nn.ModuleList([nn.Sequential(nn.Linear(128,24),nn.SiLU(),nn.Linear(24,128)) for _ in range(kwargs['n_domains'])])
  for m in self.adapters:nn.init.zeros_(m[-1].weight);nn.init.zeros_(m[-1].bias)
 def forward(self,b):
  ids=b['element_ids'];fr=b['fractions'];x=self.element_projection(self.element_features[ids])+self.learned_element(ids)+self.fraction_encoder(fr)
  x=self.transformer(x,src_key_padding_mask=ids.eq(0));w=fr.unsqueeze(-1);comp=(x*w).sum(1)/w.sum(1).clamp_min(1e-8)
  z=self.fusion(torch.cat([comp,self.numeric_encoder(b['numeric']),self.process_embedding(b['process']),self.domain_embedding(b['domain'])],1))
  dz=torch.zeros_like(z)
  for i,a in enumerate(self.adapters):dz=dz+a(z)*b['domain'].eq(i).unsqueeze(1)
  z=z+dz;dom=b['domain']
  raw=self.ys_mean[dom]+self.ys_std[dom]*self.ys_head(z).squeeze(1)
  ys=10*torch.nn.functional.softplus(raw/10) # nonnegative strength, approximately identity at ordinary strengths
  uts=ys+self.delta_scale[dom]*torch.nn.functional.softplus(self.delta_head(z).squeeze(1))
  return torch.stack([ys,uts],1)

def coverage(cal,test):
 blocks={}
 for (domain,task),g in cal.groupby(['domain','task']):
  scores=(g.target-g.prediction).abs().groupby(g.composition_key).max().to_numpy()
  t=test[(test.domain==domain)&(test.task==task)]
  if not len(t):continue
  vals={}
  for nominal in [.8,.9,.95]:
   k=math.ceil((len(scores)+1)*nominal)
   q=float(np.sort(scores)[k-1]) if k<=len(scores) else None
   vals[str(nominal)]={'calibration_compositions':len(scores),'radius_MPa':q,
    'row_coverage':float(((t.target-t.prediction).abs()<=q).mean()) if q is not None else None,
    'group_all_rows_coverage':float(((t.target-t.prediction).abs()<=q).groupby(t.composition_key).all().mean()) if q is not None else None}
  blocks[domain+'/'+task]=vals
 return blocks

def applicability(df):
 elements=sorted(set(e for s in df.composition_json for e in json.loads(s)))
 x=np.zeros((len(df),len(elements)));lookup={e:i for i,e in enumerate(elements)}
 for i,s in enumerate(df.composition_json):
  for e,v in json.loads(s).items():x[i,lookup[e]]=math.sqrt(v)
 gate=np.zeros(len(df),bool);dist=np.full(len(df),np.nan);thresholds={}
 for dom,g in df.groupby('domain'):
  tr=g[g.role=='train'].drop_duplicates('composition_key');idx=tr.index.to_numpy()
  mat=cdist(x[idx],x[idx])/math.sqrt(2);np.fill_diagonal(mat,np.inf)
  threshold=float(np.quantile(mat.min(1),.95));thresholds[dom]=threshold
  gi=g.index.to_numpy();d=cdist(x[gi],x[idx]).min(1)/math.sqrt(2);dist[gi]=d
  train=df[(df.domain==dom)&(df.role=='train')];temps=train[train.temperature_missing==0].test_temperature_C
  known=g.process_label.isin(train.process_label)&g.process_label.ne('unresolved')
  temperature=g.test_temperature_C.between(temps.min(),temps.max())&g.temperature_missing.eq(0)
  gate[gi]=(d<=threshold)&known.to_numpy()&temperature.to_numpy()
 return gate,dist,thresholds

def train_variant(df,variant,seed):
 frame=df.copy()
 if variant=='control':frame=frame.drop(columns=[c for c in frame if c.startswith('desc_v2_')])
 idx={r:np.flatnonzero(frame.role.eq(r)) for r in ['train','validation','calibration','test','source_challenge']}
 old.seed_everything(seed);pre=PhysicsPreprocessor(frame,idx['train']);enc=pre.encode_rows(frame)
 train_loader,val_loader,_=old.make_loaders(frame,enc,idx['train'],idx['validation'],idx['test'],seed)
 eval_idx=np.concatenate([idx[r] for r in ['calibration','test','source_challenge']])
 eval_loader=DataLoader(IndexedDataset(enc,eval_idx),batch_size=512)
 mean,std,delta=old.domain_scales(pre)
 kwargs=dict(element_features=pre.element_features,n_numeric=len(pre.numeric_cols),n_domains=len(pre.vocab.domains),n_processes=len(pre.vocab.processes),ys_mean=mean,ys_std=std,delta_scale=delta)
 model=(ProcessAdapterTransformer if variant=='process_adapter' else old.HardConstraintTransformer)(**kwargs).cuda()
 opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=2e-4)
 sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt,MAX_EPOCHS,eta_min=3e-5)
 best=math.inf;state=None;best_epoch=0;stale=0;history=[];start=time.time()
 for epoch in range(MAX_EPOCHS):
  model.train();losses=[]
  for b in train_loader:
   b=old.move_batch(b,torch.device('cuda'));opt.zero_grad(set_to_none=True);out=model(b)
   task=torch.where(b['task']==pre.vocab.task_to_id['YS'],0,1)
   pred=out.gather(1,task[:,None]).squeeze(1);loss=nn.functional.smooth_l1_loss((pred-b['target'])/b['target_std'],torch.zeros_like(pred),beta=.5)
   loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);opt.step();losses.append(loss.item())
  sched.step()
  if epoch%2==0 or epoch==MAX_EPOCHS-1:
   vp=old.predict(model,val_loader,frame,torch.device('cuda'));m=full_metrics(vp);score=m['macro_domain_task_MAE']
   history.append({'epoch':epoch+1,'loss':float(np.mean(losses)),'val_macro_mae':score})
   if score<best-.05:best=score;state=copy.deepcopy(model.state_dict());best_epoch=epoch+1;stale=0
   else:stale+=2
   if epoch%10==0:print(json.dumps({'variant':variant,'seed':seed,**history[-1]}),flush=True)
   if stale>=16:break
 model.load_state_dict(state);pred=old.predict(model,eval_loader,frame,torch.device('cuda'))
 pred['role']=df.iloc[pred.row_index].role.to_numpy();pred['audit_source']=df.iloc[pred.row_index].audit_source.to_numpy()
 gate,dist,thresholds=applicability(df);pred['ad_accept']=gate[pred.row_index];pred['composition_distance']=dist[pred.row_index]
 path=OUT/f'{variant}_seed_{seed}';path.mkdir()
 torch.save({'model_state':{k:v.cpu() for k,v in state.items()},'constructor':kwargs,'preprocessor':pre,'seed':seed,'best_epoch':best_epoch,'variant':variant,'offline_only':True},path/'checkpoint.pt')
 pred.to_csv(path/'predictions.csv',index=False)
 report={'variant':variant,'seed':seed,'best_epoch':best_epoch,'validation_macro_mae':best,'seconds':time.time()-start,'parameter_count':sum(p.numel() for p in model.parameters()),'history':history,'ad_thresholds':thresholds,
 'metrics':{r:full_metrics(pred[pred.role==r]) for r in ['test','source_challenge']},
 'calibration':{r:coverage(pred[pred.role=='calibration'],pred[pred.role==r]) for r in ['test','source_challenge']}}
 (path/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps({'finished':variant,'seed':seed,'best_epoch':best_epoch,'seconds':report['seconds']}),flush=True)
 del model;torch.cuda.empty_cache();return report,pred

def main():
 if OUT.exists():raise FileExistsError(OUT)
 OUT.mkdir();torch.set_num_threads(4)
 # Pure parser tests prevent silent range averaging and conflict repair.
 assert parse_process('ST 810 C 1 h WQ; age 500 C 8 h AC',{})[0]['desc_v2_solution_time_h']==1
 assert parse_process('ST 810 C 1 h',{'solution_temp_C':900})[0]['desc_v2_solution_temp_C_missing']==1
 assert parse_process('solution 800-900 C',{})[0]['desc_v2_solution_temp_C_missing']==1
 df=prepare();results={}
 for variant in ['control','process_adapter']:
  reports=[];parts=[]
  for seed in SEEDS:
   r,p=train_variant(df,variant,seed);reports.append(r);parts.append(p)
  avg=parts[0].copy()
  for col in ['prediction','predicted_YS','predicted_UTS']:avg[col]=np.mean([p[col].to_numpy() for p in parts],axis=0)
  avg['prediction_seed_std']=np.std([p.prediction.to_numpy() for p in parts],axis=0);avg['order_violation_mpa']=avg.predicted_YS-avg.predicted_UTS
  avg.to_csv(OUT/f'{variant}_ensemble_predictions.csv',index=False)
  results[variant]={'runs':reports,'ensemble_metrics':{r:full_metrics(avg[avg.role==r]) for r in ['test','source_challenge']},
   'ensemble_calibration':{r:coverage(avg[avg.role=='calibration'],avg[avg.role==r]) for r in ['test','source_challenge']},
   'gated_metrics':{r:full_metrics(avg[(avg.role==r)&avg.ad_accept]) for r in ['test','source_challenge'] if len(avg[(avg.role==r)&avg.ad_accept])},
   'ad_acceptance':avg.groupby(['role','domain']).ad_accept.mean().to_dict()}
  results[variant]['ad_acceptance']={str(k):float(v) for k,v in results[variant]['ad_acceptance'].items()}
  (OUT/'benchmark_report.json').write_text(json.dumps(results,indent=2))
 (OUT/'COMPLETED.json').write_text(json.dumps({'status':'completed','serving_changed':False,'seeds':SEEDS,'test_note':'single frozen grouped split, not five-fold CV; source challenge retrospective'},indent=2))

if __name__=='__main__':main()
