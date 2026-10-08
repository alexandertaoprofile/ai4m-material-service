#!/usr/bin/env python3
"""Strict source-held-out evaluation for same-temperature CTE evidence."""
from __future__ import annotations
import argparse,csv,json,re
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
CORE=("SiO2","Al2O3","B2O3","Na2O","K2O","Li2O","MgO","CaO","BaO","ZnO","TiO2","ZrO2","P2O5")
OTHER=re.compile(r"([A-Za-z][A-Za-z0-9]*)=([0-9]+(?:\.[0-9]+)?)\s+mol%")
def read(p:Path): return list(csv.DictReader(p.open(encoding='utf-8',newline='')))
def values(r):
 d={x:float(r.get(x,'0') or 0) for x in CORE};d.update({x:float(v) for x,v in OTHER.findall(r.get('other_oxides_text',''))});return d
def metrics(y,p): return {'mae_ppm_per_K':mean_absolute_error(y,p),'rmse_ppm_per_K':mean_squared_error(y,p)**.5,'r2':r2_score(y,p),'max_absolute_error_ppm_per_K':float(np.abs(y-p).max())}
def main():
 a=argparse.ArgumentParser();a.add_argument('--source-a',type=Path,required=True);a.add_argument('--source-b',type=Path,required=True);a.add_argument('--output',type=Path,required=True);x=a.parse_args()
 if x.output.exists():raise SystemExit(f'refusing to overwrite existing output: {x.output}')
 A,B=read(x.source_a),read(x.source_b);f=sorted(set().union(*(values(r) for r in A+B)))
 def arr(rows):return np.asarray([[values(r).get(k,0) for k in f] for r in rows]),np.asarray([float(r['property_value'])*1e6 for r in rows])
 XA,yA=arr(A);XB,yB=arr(B)
 def fit_predict(train_x,train_y,test_x):return ExtraTreesRegressor(n_estimators=1000,min_samples_leaf=2,max_features=.9,random_state=20260903,n_jobs=-1).fit(train_x,train_y).predict(test_x)
 loA,hiA=XA.min(0),XA.max(0);loB,hiB=XB.min(0),XB.max(0)
 pAB=fit_predict(XA,yA,XB);pBA=fit_predict(XB,yB,XA)
 report={'target':'linear CTE 0-300 C, ppm/K','features_mol_percent':f,'source_a_rows':len(A),'source_b_rows':len(B),'validation':'train one complete source, predict the other; no tuning on held-out source','a_to_b':{'metrics':metrics(yB,pAB),'records_inside_a_component_bounds':int(np.all((XB>=loA)&(XB<=hiA),axis=1).sum()),'records':len(B)},'b_to_a':{'metrics':metrics(yA,pBA),'records_inside_b_component_bounds':int(np.all((XA>=loB)&(XA<=hiB),axis=1).sum()),'records':len(A)}}
 x.output.parent.mkdir(parents=True,exist_ok=True);x.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
