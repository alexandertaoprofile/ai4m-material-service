#!/usr/bin/env python3
"""Family-scoped thermo-mechanical models for US20250026678 glass samples.

The validation unit is a complete patent table, not a random sample.  These
models intentionally apply only to the low-B alkali-free family represented by
that document; they are not cross-family glass-property models.
"""
from __future__ import annotations
import argparse, csv, json, re
from pathlib import Path
import joblib, numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from benchmark_chip_glass_thermomechanical_models import CORE, vector

PROPS={
 "density_g_per_cm3":"g/cm3", "young_modulus_GPa":"GPa", "shear_modulus_GPa":"GPa", "poisson_ratio":"1", "stress_optical_coefficient_nm_cm_per_MPa":"nm/cm/MPa", "strain_point_fibre_C":"C", "annealing_point_fibre_C":"C", "softening_point_fibre_C":"C", "viscosity_temperature_200_poise_C":"C", "viscosity_temperature_35kpoise_C":"C", "liquidus_viscosity_kpoise":"kpoise"}
RELEASE_ALLOWED={"density_g_per_cm3","young_modulus_GPa","stress_optical_coefficient_nm_cm_per_MPa","viscosity_temperature_200_poise_C","viscosity_temperature_35kpoise_C"}

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
 if a.output_dir.exists():raise SystemExit(f'refusing to overwrite existing output: {a.output_dir}')
 rows=[r for r in csv.DictReader(a.input.open(encoding='utf-8',newline='')) if r['source_id']=='SRC-PATENT-US20250026678' and r['property_name'] in PROPS]
 all_models={};summary={}
 for prop,unit in PROPS.items():
  rr=[r for r in rows if r['property_name']==prop]; groups=np.asarray([re.search(r'Table (\d+)',r['source_locator']).group(1) for r in rr])
  if len(rr)<20 or len(set(groups))<2: continue
  x=np.asarray([vector(r) for r in rr]);y=np.asarray([float(r['property_value']) for r in rr]);pred=np.zeros(len(y));per={}
  for held in sorted(set(groups)):
   test=groups==held;m=make_pipeline(StandardScaler(),Ridge(alpha=3.0)).fit(x[~test],y[~test]);pred[test]=m.predict(x[test]);per[f'Table_{held}']={'n':int(test.sum()),'mae':float(mean_absolute_error(y[test],pred[test])),'rmse':float(mean_squared_error(y[test],pred[test])**.5),'r2':float(r2_score(y[test],pred[test])) if test.sum()>1 else None}
  baseline=np.zeros(len(y))
  for held in set(groups):baseline[groups==held]=np.mean(y[groups!=held])
  m=make_pipeline(StandardScaler(),Ridge(alpha=3.0)).fit(x,y);metrics={'mae':float(mean_absolute_error(y,pred)),'rmse':float(mean_squared_error(y,pred)**.5),'r2':float(r2_score(y,pred)),'group_mean_baseline_mae':float(mean_absolute_error(y,baseline))}
  release=prop in RELEASE_ALLOWED and metrics['mae'] < metrics['group_mean_baseline_mae']
  summary[prop]={'unit':unit,'rows':len(rr),'patent_table_groups':{f'Table_{g}':int((groups==g).sum()) for g in sorted(set(groups))},'metrics':metrics,'per_held_table':per,'status':'family_screening_candidate' if release else 'evidence_only_no_prediction'}
  if release:all_models[prop]={'estimator':m,'unit':unit,'training_vectors':x,'targets':y,'summary':summary[prop]}
 artifact={'model_version':'chip_glass_family_thermomechanical_v1_20260904','scope':'US20250026678 low-B alkali-free alumino(boro)silicate family only; composition mol% inputs; no extrapolation to other glass families','models':all_models,'all_property_assessments':summary,'not_in_model':'thermal conductivity and specific heat are not present in the retained same-identity evidence; residual stress, warp, strength and fracture toughness have no composition-paired target.'}
 a.output_dir.mkdir(parents=True);joblib.dump(artifact,a.output_dir/'model.joblib')
 (a.output_dir/'training_report.json').write_text(json.dumps({k:v for k,v in artifact.items() if k!='models'},ensure_ascii=False,indent=2),encoding='utf-8')
 (a.output_dir/'MODEL_CARD.md').write_text('# 低硼无碱玻璃基板热机械族内候选模型 v1\n\n输入为完整氧化物 mol%。输出仅适用于 US20250026678A1 所覆盖的低硼无碱铝硼硅酸盐近域；验证按整张专利表留出。该模型用于封装仿真前筛选，不输出热导率、比热、残余应力、翘曲、强度或断裂韧性。\n',encoding='utf-8')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
