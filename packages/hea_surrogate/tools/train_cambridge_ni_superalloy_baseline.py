#!/usr/bin/env python3
"""Fit the research-only Cambridge cast/DS/SC tensile baselines.

Composition, route, heat-treatment summary and test temperature are used only
for UTS and 0.2% proof stress.  The 24 explicit-yield records are intentionally
retained but not fitted because their independent coverage is too small.
"""
from __future__ import annotations
import argparse, csv, json, math, re
from collections import defaultdict
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

TARGETS={"ultimate_tensile_strength","proof_strength_0p2"}
ELEMENTS=["Ni","Cr","Co","Re","Ru","Al","Ta","W","Ti","Mo","Fe","V","Ir","Mn","Y","C","B","Zr","Si","Nb","Hf"]
ROUTES=["cc","ds","sc","am/sebm","am/lc"]
def read(p):
    with p.open(encoding="utf8",newline="") as f:return list(csv.DictReader(f))
def f(x):
    try:return float(x)
    except (ValueError,TypeError):return float("nan")
def ht_stage_features(s):
    """Keep every recorded heat-treatment stage; do not collapse its order."""
    out=[]
    for family, stages in (("solution",3),("precipitation",4)):
        for stage in range(1,stages+1):
            for suffix in ("temp_C","time_h","heating_C_per_h"):
                m=re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)",s)
                out.append(f(m.group(1)) if m else float("nan"))
    return out
def features(r):
    route=r["casting_code"]
    return [f(r["temperature_K"]), *[f(r.get(f"{e}_wt_percent")) for e in ELEMENTS],
            *[float(route==x) for x in ROUTES], f(r.get("casting_gradient_K_per_mm")), *ht_stage_features(r["heat_treatment"])]
def score(rows):
    y=np.array([r["observed_MPa"] for r in rows]); p=np.array([r["predicted_MPa"] for r in rows])
    return {"n":len(rows),"mae_MPa":float(mean_absolute_error(y,p)),"rmse_MPa":float(math.sqrt(mean_squared_error(y,p))),"r2":float(r2_score(y,p)) if len(rows)>1 and len(set(y))>1 else None}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--package",type=Path,required=True);ap.add_argument("--output-dir",type=Path,required=True);a=ap.parse_args()
    if a.output_dir.exists():raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    p=a.package; readiness=json.loads((p/"fit_readiness_manifest.json").read_text())
    if not readiness.get("fit_ready") or readiness.get("model_fit_started"):raise SystemExit("input package is not a frozen pre-fit package")
    split={r["tag"]:r["split"] for r in read(p/"splits/tag_disjoint_split_v1.csv")}
    rows=[r for r in read(p/"cast_ds_sc_short_time_strength_admitted.csv") if r["property"] in TARGETS]
    names=["temperature_K",*[f"{e}_wt_percent" for e in ELEMENTS],*[f"route__{x}" for x in ROUTES],"casting_gradient_K_per_mm",*[f"solution_stage_{i}_{x}" for i in range(1,4) for x in ("temp_C","time_h","heating_C_per_h")],*[f"precipitation_stage_{i}_{x}" for i in range(1,5) for x in ("temp_C","time_h","heating_C_per_h")]]
    a.output_dir.mkdir(parents=True); preds=[]; models={}; report={}
    for target in sorted(TARGETS):
        rs=[r for r in rows if r["property"]==target]
        train=[r for r in rs if split[r["tag"]]=="train"]
        if len({r["tag"] for r in train})<8:raise SystemExit(f"insufficient train coverage for {target}")
        x=np.array([features(r) for r in train]); y=np.array([f(r["value_MPa"]) for r in train])
        keep=np.any(~np.isnan(x),axis=0)
        model=Pipeline([("imputer",SimpleImputer(strategy="median",add_indicator=True)),("regressor",HistGradientBoostingRegressor(learning_rate=.05,max_leaf_nodes=10,min_samples_leaf=8,l2_regularization=3,max_iter=350,random_state=20260831))])
        model.fit(x[:,keep],y);models[target]={"model":model,"feature_mask":keep.tolist()}
        by=defaultdict(list)
        for r in rs:
            pred=float(model.predict(np.array([features(r)])[:,keep])[0]);rec={"tag":r["tag"],"alloy_name":r["alloy_name"],"manufacturing_route":r["manufacturing_route"],"property":target,"split":split[r["tag"]],"temperature_K":f(r["temperature_K"]),"observed_MPa":f(r["value_MPa"]),"predicted_MPa":pred,"residual_MPa":pred-f(r["value_MPa"]),"source_row":r["source_row"],"reference_1_doi":r["reference_1_doi"]};preds.append(rec);by[rec["split"]].append(rec)
        report[target]={k:score(v) for k,v in sorted(by.items())};report[target]["independent_tags"]={k:len({x["tag"] for x in v}) for k,v in by.items()}
    with (a.output_dir/"predictions.csv").open("w",encoding="utf8",newline="") as o:
        w=csv.DictWriter(o,fieldnames=list(preds[0]));w.writeheader();w.writerows(preds)
    joblib.dump({"models":models,"feature_names":names},a.output_dir/"baseline_models.joblib")
    result={"model":"HistGradientBoostingRegressor, separate UTS and 0.2% proof-stress models","research_status":"exploratory research baseline only; not approved for product/service prediction","training_package":str(p),"split":"Cambridge Tag-disjoint v1","features":"test temperature, source-record wt.% composition, casting route/gradient, and every recorded solution/precipitation heat-treatment stage","metrics_MPa":report,"not_fitted":{"yield_strength":{"observations":sum(r["property"]=="yield_strength" for r in read(p/"cast_ds_sc_short_time_strength_admitted.csv")),"reason":"24 observations is insufficient for a defensible independent benchmark"}},"boundaries":["The training lane covers cast, DS, single-crystal and two additive records; it is not a wrought-alloy model.","A test Tag is excluded entirely from fit, but limited test-tag counts mean metrics remain exploratory.","Predictions do not establish creep life, oxidation resistance, phase stability, melting range, manufacturability or certification."]}
    (a.output_dir/"training_report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    md=["# 剑桥高温镍基合金短时强度研究基线","","该模型用于铸造、定向凝固和单晶高温镍基候选的早期排序，不用于产品承诺。","","## 独立 Tag 测试指标（MPa）",""]
    for t,m in report.items():
        q=m.get("test",{});md.append(f"- {t}：n={q.get('n',0)}，独立 Tag={m['independent_tags'].get('test',0)}，MAE={q.get('mae_MPa','N/A'):.2f}，RMSE={q.get('rmse_MPa','N/A'):.2f}，R²={q.get('r2')}")
    md += ["","## 边界","",*['- '+x for x in result['boundaries']]]
    (a.output_dir/"training_report.md").write_text("\n".join(md)+"\n",encoding="utf8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
