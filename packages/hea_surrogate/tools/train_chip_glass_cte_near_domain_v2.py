#!/usr/bin/env python3
"""Train the traceable multi-source 0--300 C near-domain CTE candidate.

Validation leaves one complete document source out.  Descriptors are pure,
deterministic transformations of the oxide mol% input and are stored with the
artifact so inference can reproduce them exactly.
"""
from __future__ import annotations
import argparse, csv, json, re
from pathlib import Path
import joblib
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

CORE=("SiO2","Al2O3","B2O3","Na2O","K2O","Li2O","MgO","CaO","BaO","ZnO","TiO2","ZrO2","P2O5")
MASS=np.asarray((60.0843,101.9613,69.6202,61.9789,94.196,29.8814,40.3044,56.0774,153.326,81.379,79.866,123.222,141.9445))

def rows(paths:list[Path])->list[dict[str,str]]:
    return [row for path in paths for row in csv.DictReader(path.open(encoding="utf-8",newline=""))]

def vector(row:dict[str,str])->tuple[np.ndarray,np.ndarray]:
    c=np.asarray([float(row.get(oxide,"") or 0) for oxide in CORE]);si,al,b,na,k,li,mg,ca,ba,zn,ti,zr,p=c
    modifier=na+k+li+mg+ca+ba+zn;charge=na+k+li+2*(mg+ca+ba+zn);network=si+al+b+p+ti+zr
    descriptors=np.asarray((np.dot(c,MASS)/100,modifier,charge,network,charge-2*al,(charge-2*al)/max(network,1e-9),(mg+ca+ba+zn)/max(al,1e-9),b/max(si,1e-9)))
    return c,np.r_[c,descriptors]

def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--inputs",type=Path,nargs="+",required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--algorithm",choices=("extra_trees","ridge"),default="extra_trees");a=p.parse_args()
    if a.output_dir.exists():raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    data=rows(a.inputs);x_comp,x=zip(*(vector(row) for row in data));x_comp=np.asarray(x_comp);x=np.asarray(x);y=np.asarray([float(row['property_value'])*1e6 for row in data]);groups=np.asarray([row['source_id'] for row in data])
    def build(): return make_pipeline(StandardScaler(),Ridge(alpha=3.0)) if a.algorithm=="ridge" else ExtraTreesRegressor(n_estimators=1600,min_samples_leaf=2,max_features=.9,random_state=20260903,n_jobs=-1)
    pred=np.zeros(len(y));per_source={}
    for held in sorted(set(groups)):
        test=groups==held;model=build().fit(x[~test],y[~test]);pred[test]=model.predict(x[test]);per_source[held]={"n":int(test.sum()),"mae_ppm_per_K":float(mean_absolute_error(y[test],pred[test])),"rmse_ppm_per_K":float(mean_squared_error(y[test],pred[test])**.5),"r2":float(r2_score(y[test],pred[test]))}
    final=build().fit(x,y)
    errors=np.abs(y-pred)
    for row,value,error in zip(data,pred,errors):row['source_held_out_prediction_ppm_per_K']=f'{value:.6f}';row['absolute_error_ppm_per_K']=f'{error:.6f}'
    a.output_dir.mkdir(parents=True)
    with (a.output_dir/'training_rows.csv').open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(data[0])+['source_held_out_prediction_ppm_per_K','absolute_error_ppm_per_K']);w.writeheader();w.writerows(data)
    scale=x.std(axis=0); scale[scale < 1e-9]=1.0; normalized=x/scale
    nearest=NearestNeighbors(n_neighbors=2).fit(normalized).kneighbors(normalized)[0][:,1]
    uncertainty={"source_held_out_absolute_residual_quantiles_ppm_per_K":{"p50":float(np.quantile(errors,.50)),"p80":float(np.quantile(errors,.80)),"p90":float(np.quantile(errors,.90)),"p95":float(np.quantile(errors,.95))},"domain_distance":"nearest training composition+descriptor distance after feature standardization","domain_distance_thresholds":{"p95":float(np.quantile(nearest,.95)),"max":float(nearest.max())}}
    report={"model_version":f"chip_glass_cte_near_domain_v2_{a.algorithm}_20260903","status":"candidate_multisource_near_domain_screening","model":"StandardScaler+Ridge(alpha=3.0)" if a.algorithm=="ridge" else "ExtraTreesRegressor","task":"oxide mol% plus deterministic oxide-chemistry descriptors to linear CTE at 0-300 C","unit":"ppm/K","training_rows":len(data),"sources":{source:int((groups==source).sum()) for source in sorted(set(groups))},"validation":"leave-one-complete-document-source-out; no tuning on held source","metrics":{"mae_ppm_per_K":float(mean_absolute_error(y,pred)),"rmse_ppm_per_K":float(mean_squared_error(y,pred)**.5),"r2":float(r2_score(y,pred)),"max_absolute_error_ppm_per_K":float(errors.max())},"per_held_source":per_source,"uncertainty_and_domain":uncertainty,"composition_features_mol_percent":list(CORE),"derived_descriptors":["mean_oxide_molar_mass","modifier_mol_percent","modifier_charge","network_former_mol_percent","charge_minus_2Al","NBO_T_like_proxy","alkaline_earth_to_Al_proxy","B2O3_to_SiO2_ratio"],"training_min_max_mol_percent":{oxide:[float(x_comp[:,i].min()),float(x_comp[:,i].max())] for i,oxide in enumerate(CORE)},"scope":"0-300 C CTE near-domain screening across four traceable patent sources. Descriptors are oxide bookkeeping proxies, not thermodynamic, molecular-dynamics, fictive-temperature or residual-stress constraints."}
    (a.output_dir/'training_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (a.output_dir/'MODEL_CARD.md').write_text('# 芯片玻璃基板 0–300°C 近域 CTE 候选模型 v2\n\n该候选模型以四份可追溯专利的 0–300°C 线性 CTE 记录训练，并以完整文献来源留出验证。输入为完整氧化物 mol%；模型内部增加确定性氧化物化学计量描述符。输出是 CTE 筛选值，不是残余应力或封装可靠性预测。\n',encoding='utf-8')
    joblib.dump({"model":final,"composition_features":list(CORE),"training_vectors":x,"training_targets_ppm_per_K":y,"feature_scale":scale,"uncertainty":uncertainty,"report":report},a.output_dir/'model.joblib')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
