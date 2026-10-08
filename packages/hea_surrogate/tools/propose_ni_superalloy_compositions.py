#!/usr/bin/env python3
"""Conservative inverse search for Ni-superalloy composition candidates.

The search does not invent a broad composition space.  It perturbs source
whole-alloy compositions only within a small neighbourhood, retains candidates
inside a route-specific empirical distance envelope, then ranks the surviving
compositions with the frozen Cambridge UTS/proof models.
"""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
import joblib
import numpy as np

ELEMENTS=["Ni","Cr","Co","Re","Ru","Al","Ta","W","Ti","Mo","Fe","V","Ir","Mn","Y","C","B","Zr","Si","Nb","Hf"]
ROUTES={"cc":"cast","ds":"directionally_solidified","sc":"single_crystal"}
MODEL_TARGETS=("ultimate_tensile_strength","proof_strength_0p2")
TEST_MAE={"ultimate_tensile_strength":111.0157643473382,"proof_strength_0p2":85.8476861599025}

def read(p):
    with p.open(encoding="utf8",newline="") as f:return list(csv.DictReader(f))
def f(x):
    try:return float(x)
    except (ValueError,TypeError):return float("nan")
def ht_stage_features(s):
    import re
    out=[]
    for family, stages in (("solution",3),("precipitation",4)):
        for stage in range(1,stages+1):
            for suffix in ("temp_C","time_h","heating_C_per_h"):
                m=re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)",s)
                out.append(f(m.group(1)) if m else float("nan"))
    return out
def features(row,temp_k):
    route=row["casting_code"]
    return [temp_k,*[f(row.get(f"{e}_wt_percent")) for e in ELEMENTS],*[float(route==x) for x in ("cc","ds","sc","am/sebm","am/lc")],f(row.get("casting_gradient_K_per_mm")),*ht_stage_features(row["heat_treatment"])]
def write_csv(p,rows):
    with p.open("w",encoding="utf8",newline="") as o:
        w=csv.DictWriter(o,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--package",type=Path,required=True)
    ap.add_argument("--model-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--route",choices=sorted(ROUTES),default="cc")
    ap.add_argument("--temperature-C",type=float,default=900)
    ap.add_argument("--num-proposals",type=int,default=3000)
    ap.add_argument("--seed",type=int,default=20260831)
    a=ap.parse_args()
    if a.output_dir.exists():raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    if not 20 <= a.temperature_C <= 1100:raise SystemExit("temperature must be within the v1 conservative service range 20..1100 °C")
    rows=read(a.package/"cast_ds_sc_short_time_strength_admitted.csv")
    # One row per Tag preserves composition, route and an actual source HT.
    anchors={r["tag"]:r for r in rows if r["casting_code"]==a.route}
    anchors=list(anchors.values())
    if len(anchors)<8:raise SystemExit("insufficient route anchors")
    temp_k=a.temperature_C+273.15
    model=joblib.load(a.model_dir/"baseline_models.joblib")["models"]
    # Values in this source are fractions (sum 1), although headers identify
    # wt.%.  A small subset of literature rows are composition-incomplete;
    # exclude those instead of normalising them into a fictional recipe.
    anchors=[r for r in anchors if .97 <= sum(f(r[f"{e}_wt_percent"]) for e in ELEMENTS) <= 1.03]
    if len(anchors)<8:raise SystemExit("too few composition-complete route anchors")
    base=np.array([[f(r[f"{e}_wt_percent"]) for e in ELEMENTS] for r in anchors])
    if not np.allclose(base.sum(axis=1),1.0,atol=.03):raise SystemExit("unexpected source composition scale")
    # Require an anchor with tensile coverage near the requested temperature;
    # an incomplete-composition row cannot act as its process analogue.
    eligible=[r for r in anchors if any(x["tag"]==r["tag"] and abs(f(x["temperature_K"])-temp_k)<=400 for x in rows)]
    if not eligible: raise SystemExit("no route anchor has tensile coverage near requested temperature")
    scale=np.maximum(np.nanstd(base,axis=0),.003)
    # Empirical support radius: train-anchor nearest-neighbour P90, measured in
    # standardized source-composition space.  New proposals cannot exceed it.
    dist=np.sqrt((((base[:,None,:]-base[None,:,:])/scale)**2).sum(axis=2));np.fill_diagonal(dist,np.inf)
    support=float(np.quantile(np.min(dist,axis=1),.90))
    rng=np.random.default_rng(a.seed); candidates=[]
    # A small perturbation (max 1.0 wt.% for every non-Ni element) is deliberate.
    for n in range(a.num_proposals):
        anchor=eligible[n%len(eligible)] if n<len(eligible) else eligible[int(rng.integers(len(eligible)))]
        c=np.array([f(anchor[f"{e}_wt_percent"]) for e in ELEMENTS])
        non_ni=[i for i,e in enumerate(ELEMENTS) if e!="Ni" and c[i]>0]
        # perturb 1--3 elements by at most 1 wt.% (0.01 source fraction), then
        # let Ni remain the balance; no previously absent strategic element added.
        for i in rng.choice(non_ni,size=min(len(non_ni),int(rng.integers(1,4))),replace=False): c[i]=max(0,c[i]+rng.uniform(-.01,.01))
        ni=ELEMENTS.index("Ni");c[ni]=1-c.sum()+c[ni]
        if c[ni]<=0:continue
        nearest=int(np.argmin(np.sqrt((((base-c)/scale)**2).sum(axis=1))))
        distance=float(np.sqrt((((base[nearest]-c)/scale)**2).sum()))
        if distance>support:continue
        candidate=dict(anchor)
        for i,e in enumerate(ELEMENTS):candidate[f"{e}_wt_percent"]=float(c[i])
        vector=np.array([features(candidate,temp_k)])
        prediction={}
        for target in MODEL_TARGETS:
            item=model[target]; mask=np.array(item["feature_mask"],dtype=bool)
            prediction[target]=float(item["model"].predict(vector[:,mask])[0])
        candidates.append({"candidate_id":f"NIHT-{a.route.upper()}-{len(candidates)+1:04d}","manufacturing_route":ROUTES[a.route],"test_temperature_C":a.temperature_C,
                           "composition_wt_percent":{e:round(float(c[i])*100,4) for i,e in enumerate(ELEMENTS) if c[i]>1e-5},
                           "predicted_UTS_MPa":prediction["ultimate_tensile_strength"],"UTS_screening_uncertainty_MPa":TEST_MAE["ultimate_tensile_strength"],
                           "predicted_0p2_proof_MPa":prediction["proof_strength_0p2"],"proof_screening_uncertainty_MPa":TEST_MAE["proof_strength_0p2"],
                           "nearest_source_tag":anchors[nearest]["tag"],"nearest_source_alloy":anchors[nearest]["alloy_name"],"nearest_source_row":anchors[nearest]["source_row"],"nearest_source_heat_treatment":anchors[nearest]["heat_treatment"],"standardized_composition_distance":distance,"empirical_support_radius":support})
    # Strong UTS plus proof score, never treating a model prediction as a measured property.
    candidates.sort(key=lambda x:(x["predicted_UTS_MPa"]+x["predicted_0p2_proof_MPa"],-x["standardized_composition_distance"]),reverse=True)
    # Retain diverse source anchors, not five trivial variants of one grade.
    selected=[];used=set()
    for x in candidates:
        if x["nearest_source_tag"] in used:continue
        selected.append(x);used.add(x["nearest_source_tag"])
        if len(selected)>=10:break
    if not selected:raise SystemExit("no candidate retained in empirical support domain")
    a.output_dir.mkdir(parents=True)
    flat=[]
    for x in selected:
        row={k:v for k,v in x.items() if k!="composition_wt_percent"}
        row.update({f"{e}_wt_percent":x["composition_wt_percent"].get(e,0.0) for e in ELEMENTS});flat.append(row)
    write_csv(a.output_dir/"ranked_candidates.csv",flat)
    result={"model_version":"ni_superalloy_cast_ds_sc_strength_v0 / conservative_inverse_search_v1","task":"route-specific Ni-superalloy wt.% composition screening","route":ROUTES[a.route],"temperature_C":a.temperature_C,"source_package":str(a.package),"model_dir":str(a.model_dir),"generated_within_small_local_perturbations":a.num_proposals,"empirical_support_radius_standardized":support,"retained_diverse_candidates":selected,"boundaries":["wt.% candidates only; Ni is calculated balance.","All candidates remain inside the empirical composition-support radius of the selected manufacturing route.","Predicted UTS and proof values are screening estimates; displayed uncertainty is independent-test MAE, not a confidence interval.","No creep, oxidation, phase-stability, density, processability or certification result is produced.","Each candidate must enter CALPHAD/creep/oxidation and experimental validation before use."]}
    (a.output_dir/"proposal_manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    lines=["# 镍基高温合金配比候选 v1","",f"工艺路线：{ROUTES[a.route]}；目标温度：{a.temperature_C:g} °C。","","## 优先进入验证的候选","", "| 候选 | 配比（wt.%） | 预测 UTS | 预测 0.2% proof | 最近来源合金 |", "|---|---|---:|---:|---|"]
    for x in selected:
        comp=" · ".join(f"{e} {v:g}" for e,v in x["composition_wt_percent"].items())
        lines.append(f"| {x['candidate_id']} | {comp} | {x['predicted_UTS_MPa']:.0f} ± {x['UTS_screening_uncertainty_MPa']:.0f} MPa | {x['predicted_0p2_proof_MPa']:.0f} ± {x['proof_screening_uncertainty_MPa']:.0f} MPa | {x['nearest_source_alloy']} ({x['nearest_source_tag']}) |")
    lines += ["","结果仅用于后续验证优先级：误差带为独立测试 MAE，不是已验证性能区间；首先需核验热处理、CALPHAD 相稳定性、蠕变/持久和氧化行为。"]
    (a.output_dir/"proposal_report.md").write_text("\n".join(lines)+"\n",encoding="utf8")
    print(json.dumps({"retained":len(selected),"output":str(a.output_dir),"top":selected[0]},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
