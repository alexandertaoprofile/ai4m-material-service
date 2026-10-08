#!/usr/bin/env python3
"""Compare regression algorithms under complete-source holdout for CTE."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor,RandomForestRegressor,HistGradientBoostingRegressor,RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OX=("SiO2","Al2O3","B2O3","Na2O","K2O","Li2O","MgO","CaO","BaO","ZnO","TiO2","ZrO2","P2O5")
M=np.asarray((60.0843,101.9613,69.6202,61.9789,94.196,29.8814,40.3044,56.0774,153.326,81.379,79.866,123.222,141.9445))
def vec(r):
 c=np.asarray([float(r.get(k) or 0) for k in OX]);si,al,b,na,k,li,mg,ca,ba,zn,ti,zr,p=c;mod=na+k+li+mg+ca+ba+zn;charge=na+k+li+2*(mg+ca+ba+zn);net=si+al+b+p+ti+zr
 return np.r_[c,np.dot(c,M)/100,mod,charge,net,charge-2*al,(charge-2*al)/max(net,1e-9),(mg+ca+ba+zn)/max(al,1e-9),b/max(si,1e-9)]
def models():
 return {"ridge":lambda:make_pipeline(StandardScaler(),Ridge(alpha=3.0)),"random_forest":lambda:RandomForestRegressor(n_estimators=1200,min_samples_leaf=2,max_features=.9,n_jobs=-1,random_state=20260903),"extra_trees_leaf_1":lambda:ExtraTreesRegressor(n_estimators=1200,min_samples_leaf=1,max_features=.9,n_jobs=-1,random_state=20260903),"extra_trees_leaf_2":lambda:ExtraTreesRegressor(n_estimators=1200,min_samples_leaf=2,max_features=.9,n_jobs=-1,random_state=20260903),"extra_trees_leaf_3":lambda:ExtraTreesRegressor(n_estimators=1200,min_samples_leaf=3,max_features=.9,n_jobs=-1,random_state=20260903),"hist_gradient_boosting":lambda:HistGradientBoostingRegressor(max_iter=250,learning_rate=.05,max_leaf_nodes=12,l2_regularization=.2,random_state=20260903)}
def main():
 p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output}')
 rows=[r for path in a.inputs for r in csv.DictReader(path.open(encoding='utf-8'))];x=np.asarray([vec(r) for r in rows]);y=np.asarray([float(r['property_value'])*1e6 for r in rows]);g=np.asarray([r['source_id'] for r in rows]);report={"rows":len(rows),"validation":"leave one complete document source out; fixed hyperparameters selected before this comparison","features":"oxide mol% plus eight deterministic chemistry descriptors","algorithms":{}}
 for name,create in models().items():
  pred=np.zeros(len(y));held={}
  for source in sorted(set(g)):
   te=g==source;pred[te]=create().fit(x[~te],y[~te]).predict(x[te]);held[source]={"n":int(te.sum()),"mae_ppm_per_K":float(mean_absolute_error(y[te],pred[te])),"r2":float(r2_score(y[te],pred[te]))}
  report['algorithms'][name]={"mae_ppm_per_K":float(mean_absolute_error(y,pred)),"rmse_ppm_per_K":float(mean_squared_error(y,pred)**.5),"r2":float(r2_score(y,pred)),"per_held_source":held}
 report['selected_by_lowest_source_held_out_mae']=min(report['algorithms'],key=lambda k:report['algorithms'][k]['mae_ppm_per_K'])
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
