#!/usr/bin/env python3
"""Train only thermo-mechanical property candidates that pass source-LOO gates."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import joblib, numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from benchmark_chip_glass_thermomechanical_models import CORE, CONVERSION, TARGETS, vector

RELEASE = {"density_g_per_cm3", "annealing_point_fibre_C"}
def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--benchmark',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    if a.output_dir.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output_dir}')
    bench=json.loads(a.benchmark.read_text(encoding='utf-8'))['properties'];raw=list(csv.DictReader(a.input.open(encoding='utf-8',newline='')));props={}
    for r in raw:
        prop,factor=CONVERSION.get(r['property_name'],(r['property_name'],1.0))
        if prop in RELEASE: props.setdefault(prop,[]).append((r,float(r['property_value'])*factor))
    artifact={"model_version":"chip_glass_thermomechanical_v1_20260904","input_features_mol_percent":list(CORE),"models":{},"not_released":{"young_modulus_GPa":"two sources only and both held-source R2 values are negative; retain data but do not expose prediction","strain_point_fibre_C":"large source-dependent offsets; retain data but do not expose prediction","poisson_ratio":"one document source only; no cross-source validation"}}
    for prop,items in props.items():
        x=np.asarray([vector(r) for r,_ in items]);y=np.asarray([v for _,v in items]); final=make_pipeline(StandardScaler(),Ridge(alpha=3.0)).fit(x,y)
        scale=x.std(axis=0);scale[scale<1e-9]=1;near=NearestNeighbors(n_neighbors=2).fit(x/scale).kneighbors(x/scale)[0][:,1]
        artifact['models'][prop]={"estimator":final,"training_vectors":x,"targets":y,"feature_scale":scale,"unit":bench[prop]['unit'],"rows":len(items),"sources":bench[prop]['sources'],"source_held_out_metrics":bench[prop]['metrics'],"domain_distance_threshold_p95":float(np.quantile(near,.95)),"scope":"composition-only near-domain screening; source measurement state and thermal history remain unmodelled"}
    a.output_dir.mkdir(parents=True);joblib.dump(artifact,a.output_dir/'model.joblib')
    report={k:{m:v for m,v in d.items() if m not in {'estimator','training_vectors','targets','feature_scale'}} for k,d in artifact['models'].items()}
    (a.output_dir/'training_report.json').write_text(json.dumps({"status":"candidate_multisource_screening","released_properties":report,"not_released":artifact['not_released'],"validation":"complete-document source held out; model fitted after validation only","limits":"No residual-stress, fracture-strength, creep/viscoelastic-constitutive, SOC, or package-warp prediction. Density and annealing point are composition-only screening estimates."},ensure_ascii=False,indent=2),encoding='utf-8')
    (a.output_dir/'MODEL_CARD.md').write_text('# 芯片玻璃基板热机械候选模型 v1\n\n可输出：密度、纤维伸长法退火点。它们是组成近域筛选值，不是残余应力或本构模型。杨氏模量、应变点和泊松比的数据已归档，但尚未通过跨来源发布门槛，刻意不输出。\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
