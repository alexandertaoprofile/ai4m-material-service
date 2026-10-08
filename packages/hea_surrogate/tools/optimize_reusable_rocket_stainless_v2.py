#!/usr/bin/env python3
"""Tune Extra Trees using the same no-state-leakage validation as v2."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data/processed/reusable_rocket_stainless_design/20260902_v1/map_model_training_observations.csv"
OUT=ROOT/"reports/reusable_rocket_stainless/20260902_v2_optimization.json"
ELS=["Cr","Ni","Mo","Mn","Si","Nb","Ti","Zr","Ta","V","W","Cu","N","C","B","P","S","Co","Al","Sn","Pb"]
BASE=ELS+["Solution_treatment_temperature","Solution_treatment_time(s)","Water_Quenched_after_s.t.","Air_Quenched_after_s.t.","Grains mm-2","Size of ingot","Temperature (K)"]
CAT=["Type of melting","Product form"]

def enrich(d):
    x=d.copy()
    c=x["C"].fillna(0); n=x["N"].fillna(0); mn=x["Mn"].fillna(0)
    cr=x["Cr"].fillna(0); ni=x["Ni"].fillna(0); mo=x["Mo"].fillna(0); si=x["Si"].fillna(0); nb=x["Nb"].fillna(0)
    x["Fe_balance_wt_pct"]=100-x[ELS].fillna(0).sum(axis=1)
    x["Ni_equivalent_proxy"]=ni+.5*mn+30*c+30*n
    x["Cr_equivalent_proxy"]=cr+mo+1.5*si+.5*nb
    x["test_temperature_sq"]=(x["Temperature (K)"]/1000)**2
    return x

def model(num,leaf,max_features,n_estimators=80):
    return Pipeline([("features",ColumnTransformer([
       ("numeric",SimpleImputer(strategy="median",add_indicator=True),num),
       ("categorical",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),
        ("onehot",OneHotEncoder(handle_unknown="ignore",sparse_output=False))]),CAT)])),
       ("regressor",ExtraTreesRegressor(n_estimators=n_estimators,min_samples_leaf=leaf,
         max_features=max_features,n_jobs=-1,random_state=20260902))])

def score(d,target,num,leaf,max_features):
    d=d.dropna(subset=[target]).copy()
    if target=="elongation_pct": d=d[d[target].between(0,150)].copy()
    y=d[target].to_numpy(); p=np.full(len(d),np.nan)
    for tr,te in GroupKFold(n_splits=3).split(d,y,d.state_group):
        m=model(num,leaf,max_features).fit(d.iloc[tr][num+CAT],y[tr])
        p[te]=m.predict(d.iloc[te][num+CAT])
    return {"n":len(d),"MAE":float(mean_absolute_error(y,p)),"R2":float(r2_score(y,p))}

def main():
    d=enrich(pd.read_csv(DATA))
    enriched=BASE+["Fe_balance_wt_pct","Ni_equivalent_proxy","Cr_equivalent_proxy","test_temperature_sq"]
    configs=[]
    for descriptor_set,num in [("base",BASE),("metallurgical_descriptors",enriched)]:
      for leaf,maxf in [(1,.85),(2,.85)]:
        metrics={t:score(d,t,num,leaf,maxf) for t in ["yield_0p2_MPa","uts_MPa","elongation_pct"]}
        configs.append({"descriptor_set":descriptor_set,"min_samples_leaf":leaf,"max_features":maxf,"metrics":metrics})
    for item in configs:
      item["selection_score"]=sum(item["metrics"][t]["R2"] for t in item["metrics"])
    configs.sort(key=lambda x:x["selection_score"],reverse=True)
    report={"validation":"3-fold GroupKFold by full composition + thermomechanical state",
      "best":configs[0],"all_configs":configs,
      "data_diagnostics":{"temperature_counts":d["Temperature (K)"].value_counts().sort_index().to_dict(),
        "process_missing_rate":{k:float(d[k].isna().mean()) for k in ["Solution_treatment_time(s)","Grains mm-2","Size of ingot"]}}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report["best"],ensure_ascii=False))
if __name__=="__main__": main()
