#!/usr/bin/env python3
"""Source-held-out benchmarks for composition-only glass property candidates."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

CORE=("SiO2","Al2O3","B2O3","Na2O","K2O","Li2O","MgO","CaO","BaO","ZnO","TiO2","ZrO2","P2O5")
MASS=np.asarray((60.0843,101.9613,69.6202,61.9789,94.196,29.8814,40.3044,56.0774,153.326,81.379,79.866,123.222,141.9445))
CONVERSION={"young_modulus_Mpsi": ("young_modulus_GPa", 6.894757293168), "young_modulus_GPa": ("young_modulus_GPa", 1.0)}
TARGETS=("density_g_per_cm3","strain_point_fibre_C","annealing_point_fibre_C","young_modulus_GPa","poisson_ratio")

def vector(row):
    c=np.asarray([float(row.get(k) or 0) for k in CORE]); si,al,b,na,k,li,mg,ca,ba,zn,ti,zr,p=c
    modifier=na+k+li+mg+ca+ba+zn; charge=na+k+li+2*(mg+ca+ba+zn); network=si+al+b+p+ti+zr
    d=np.asarray((np.dot(c,MASS)/100,modifier,charge,network,charge-2*al,(charge-2*al)/max(network,1e-9),(mg+ca+ba+zn)/max(al,1e-9),b/max(si,1e-9)))
    return np.r_[c,d]

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    raw=list(csv.DictReader(a.input.open(encoding='utf-8',newline=''))); grouped={}
    for r in raw:
        prop,factor=CONVERSION.get(r['property_name'],(r['property_name'],1.0))
        if factor is None or prop not in TARGETS: continue
        r=dict(r);r['canonical_property']=prop;r['canonical_value']=str(float(r['property_value'])*factor);grouped.setdefault(prop,[]).append(r)
    report={'input':str(a.input),'validation':'leave-one-complete-document-source-out; composition-only Ridge(alpha=3) with deterministic oxide descriptors; no tuning on held sources','properties':{}}
    for prop,rows in grouped.items():
        sources=sorted({r['source_id'] for r in rows}); x=np.asarray([vector(r) for r in rows]);y=np.asarray([float(r['canonical_value']) for r in rows]);g=np.asarray([r['source_id'] for r in rows])
        if len(sources)<2: continue
        pred=np.empty(len(y));per={}
        for held in sources:
            test=g==held; model=make_pipeline(StandardScaler(),Ridge(alpha=3.0)).fit(x[~test],y[~test]);pred[test]=model.predict(x[test])
            per[held]={'n':int(test.sum()),'mae':float(mean_absolute_error(y[test],pred[test])),'rmse':float(mean_squared_error(y[test],pred[test])**.5),'r2':float(r2_score(y[test],pred[test]))}
        baseline=np.empty(len(y))
        for held in sources: baseline[g==held]=np.mean(y[g!=held])
        unit = 'GPa' if prop=='young_modulus_GPa' else ('g/cm3' if prop=='density_g_per_cm3' else ('1' if prop=='poisson_ratio' else 'C'))
        report['properties'][prop]={'unit': unit,'rows':len(rows),'sources':{s:int((g==s).sum()) for s in sources},'metrics':{'mae':float(mean_absolute_error(y,pred)),'rmse':float(mean_squared_error(y,pred)**.5),'r2':float(r2_score(y,pred)),'baseline_source_mean_mae':float(mean_absolute_error(y,baseline))},'per_held_source':per,'decision':'candidate only if it improves source-held-out mean baseline; otherwise keep as traceable data pool'}
    if a.output.exists(): raise SystemExit(f'refusing to overwrite existing output: {a.output}')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
