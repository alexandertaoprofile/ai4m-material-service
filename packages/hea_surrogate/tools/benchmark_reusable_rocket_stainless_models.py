#!/usr/bin/env python3
"""Group-safe benchmark for the first austenitic stainless design dataset."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/processed/reusable_rocket_stainless_design/20260902_v1/map_model_training_observations.csv'
OUT=ROOT/'reports/reusable_rocket_stainless/20260902_v1_model_benchmark.json'
ELS=["Cr","Ni","Mo","Mn","Si","Nb","Ti","Zr","Ta","V","W","Cu","N","C","B","P","S","Co","Al","Sn","Pb"]
NUM=ELS+["Solution_treatment_temperature","Solution_treatment_time(s)","Water_Quenched_after_s.t.","Air_Quenched_after_s.t.","Grains mm-2","Size of ingot","Temperature (K)"]
CAT=["Type of melting","Product form"]
TARGETS=["yield_0p2_MPa","uts_MPa","elongation_pct"]

def build(kind:str)->Pipeline:
    prep=ColumnTransformer([
      ("num",SimpleImputer(strategy="median",add_indicator=True),NUM),
      ("cat",Pipeline([("imp",SimpleImputer(strategy="most_frequent")),
                       ("oh",OneHotEncoder(handle_unknown="ignore",sparse_output=False))]),CAT)])
    if kind=="extra_trees":
        estimator=ExtraTreesRegressor(n_estimators=350,min_samples_leaf=2,max_features=.85,n_jobs=-1,random_state=20260902)
    elif kind=="random_forest":
        estimator=RandomForestRegressor(n_estimators=350,min_samples_leaf=2,max_features=.85,n_jobs=-1,random_state=20260902)
    else:
        estimator=HistGradientBoostingRegressor(max_iter=300,learning_rate=.05,max_leaf_nodes=20,min_samples_leaf=12,l2_regularization=1.,random_state=20260902)
    return Pipeline([("features",prep),("model",estimator)])

def evaluate(data:pd.DataFrame, target:str, kind:str)->dict:
    clean=data.dropna(subset=[target]).copy()
    # Do not repair a questionable source value.  It is reported separately;
    # the long-tail elongation model is benchmarked only after a physically
    # transparent review window to avoid a single apparent transcription error.
    if target=="elongation_pct":
        clean=clean[clean[target].between(0,150)].copy()
    y=clean[target].to_numpy()
    pred=np.full(len(clean),np.nan)
    for tr,te in GroupKFold(n_splits=3).split(clean,y,clean.state_group):
        m=build(kind).fit(clean.iloc[tr][NUM+CAT],y[tr])
        pred[te]=m.predict(clean.iloc[te][NUM+CAT])
    return {"n":len(clean),"MAE":float(mean_absolute_error(y,pred)),"R2":float(r2_score(y,pred))}

def main():
    d=pd.read_csv(DATA)
    report={"dataset":{"rows":len(d),"groups":int(d.state_group.nunique()),
      "missing_rate":{c:float(d[c].isna().mean()) for c in NUM},
      "elongation_outliers_gt_150_pct":d.loc[d.elongation_pct.gt(150),["source_locator","elongation_pct","Temperature (K)"]].to_dict("records")},
      "validation":"3-fold GroupKFold by full composition + thermomechanical state; no state temperature point leaks across folds.",
      "models":{}}
    for name in ["extra_trees","random_forest","hist_gradient_boosting"]:
      report["models"][name]={target:evaluate(d,target,name) for target in TARGETS}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report["models"],ensure_ascii=False))
if __name__=="__main__": main()
