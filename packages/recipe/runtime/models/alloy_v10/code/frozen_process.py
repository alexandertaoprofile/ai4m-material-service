"""Exact v2 training parser. Do not silently change semantics."""
import json
import re
import numpy as np
import pandas as pd

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
