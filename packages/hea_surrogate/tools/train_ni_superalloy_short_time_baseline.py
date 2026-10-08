#!/usr/bin/env python3
"""Fit a leakage-safe, explicitly limited baseline for Ni hot-end alloys.

This is a research baseline: grade specifications (not measured heats) plus
test temperature predict short-time yield/UTS.  It never consumes creep,
rupture or CALPHAD data, and uses the frozen grade-disjoint split.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def value(text: str) -> float:
    try: return float(text)
    except (ValueError, TypeError): return float("nan")


def condition_features(row: dict[str, str]) -> tuple[list[float], dict[str, object]]:
    """Conservatively recover model features from retained source conditions.

    Values are emitted only when their source field/text explicitly carries a
    unit or a conventional condition token.  Original `condition` remains the
    authoritative evidence and is copied to predictions.
    """
    text=' '.join(str(row.get(k,'')) for k in ('condition','source_caption','source_footnote')).casefold()
    def first(pattern, converter=lambda x: float(x)):
        m=re.search(pattern,text,re.I)
        return converter(m.group(1)) if m else float('nan')
    # Source expansion preserves original column names, e.g.
    # heat_treatment_deg_f_1_hr=1200.  Older reviewed rows still contribute
    # categorical state flags but do not acquire invented numerical treatment.
    ht_c=first(r'heat_treatment_deg_c[^=;]*=\s*(-?\d+(?:\.\d+)?)')
    ht_f=first(r'heat_treatment_deg_f[^=;]*=\s*(-?\d+(?:\.\d+)?)')
    ht_k=ht_c+273.15 if not np.isnan(ht_c) else ((ht_f-32)*5/9+273.15 if not np.isnan(ht_f) else float('nan'))
    duration=first(r'(?:heat_treatment|aging|age)[^=;]*(?:hr|hour)[^=;]*=\s*(\d+(?:\.\d+)?)')
    cold=first(r'cold_reduction[^=;]*=\s*(\d+(?:\.\d+)?)')
    thickness=first(r'thickness[^=;]*=\s*(\d+(?:\.\d+)?)')
    diameter=first(r'(?:diameter|diam)[^=;]*=\s*(\d+(?:\.\d+)?)')
    # Units are known only when the column explicitly encodes them.
    if not np.isnan(thickness):
        if re.search(r'thickness[^=;]*_in\b',text): thickness*=25.4
        elif not re.search(r'thickness[^=;]*_(?:mm|millimeter)',text): thickness=float('nan')
    if not np.isnan(diameter):
        if re.search(r'(?:diameter|diam)[^=;]*_in\b',text): diameter*=25.4
        elif not re.search(r'(?:diameter|diam)[^=;]*_(?:mm|millimeter)',text): diameter=float('nan')
    states=['solution_annealed','annealed','aged','as_drawn','cold_drawn','as_rolled']
    forms=['plate','sheet','bar','wire','tube','forging']
    flags=[float(bool(re.search(s.replace('_',r'[ -]?'),text))) for s in states+forms]
    metadata={'heat_treatment_temperature_K':ht_k,'heat_treatment_duration_h':duration,'cold_reduction_percent':cold,
              'thickness_mm':thickness,'diameter_mm':diameter,
              **{f'state__{x}':flags[i] for i,x in enumerate(states)},
              **{f'form__{x}':flags[len(states)+i] for i,x in enumerate(forms)}}
    return [ht_k,duration,cold,thickness,diameter]+flags,metadata


def make_composition_features(rows: list[dict[str, str]]):
    """Return grade -> feature map and a reproducible, source-derived schema."""
    elements = sorted({r["element"] for r in rows})
    specs = {"minimum_wt_percent", "maximum_wt_percent", "nominal_wt_percent"}
    by_grade: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_grade[r["material_id"]].append(r)
    feature_names=[]
    for e in elements:
        feature_names.extend([f"{e}__listed", f"{e}__min", f"{e}__max", f"{e}__nominal"])
    features={}
    for material_id, comp in by_grade.items():
        index={r["element"]:r for r in comp}
        row=[]
        for e in elements:
            r=index.get(e)
            row.append(1.0 if r else 0.0)
            for field in specs:
                row.append(value(r[field]) if r and r["numeric_feature_admission"] == "admitted" else float("nan"))
        features[material_id]=row
    return features, feature_names


def metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    y=np.array([float(r['observed_MPa']) for r in rows])
    pred=np.array([float(r['predicted_MPa']) for r in rows])
    result={"n":len(rows), "mae_MPa":float(mean_absolute_error(y,pred)), "rmse_MPa":float(math.sqrt(mean_squared_error(y,pred)))}
    result["r2"] = float(r2_score(y,pred)) if len(rows) >= 2 and len(set(y)) > 1 else None
    return result


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--package",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args(); p=args.package
    if args.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    contract=json.loads((p/'model_input_contract_v1.json').read_text())
    readiness=json.loads((p/'fit_readiness_manifest.json').read_text())
    if contract['model_fit_started'] or readiness['model_fit_started']: raise SystemExit('package is not a frozen pre-fit package')
    comp=read_csv(p/'composition_specification_envelopes.csv')
    labels=read_csv(p/'short_time_strength_observations.csv')
    split={r['material_id']:r['split'] for r in read_csv(p/'splits/grade_disjoint_split_v1.csv')}
    feature_map, composition_feature_names=make_composition_features(comp)
    condition_names=['heat_treatment_temperature_K','heat_treatment_duration_h','cold_reduction_percent','thickness_mm','diameter_mm',
                     'state__solution_annealed','state__annealed','state__aged','state__as_drawn','state__cold_drawn','state__as_rolled',
                     'form__plate','form__sheet','form__bar','form__wire','form__tube','form__forging']
    parsed_conditions=[]; per_property=defaultdict(list)
    for row in labels:
        material_id=row['material_id']
        if material_id not in split or material_id not in feature_map: continue
        condition_vector, parsed=condition_features(row)
        parsed_conditions.append({'material_id':material_id,'property':row['property'],'source_locator':row.get('source_locator',''), **parsed})
        vector=[value(row['temperature_K'])] + feature_map[material_id] + condition_vector
        per_property[row['property']].append((row, vector, split[material_id]))
    feature_names=['temperature_K']+composition_feature_names+condition_names
    models={}; predictions=[]; metric_report={}; retained_features={}; feature_masks={}
    for property_name, records in sorted(per_property.items()):
        train=[r for r in records if r[2]=='train']
        if len({r[0]['material_id'] for r in train}) < 3: raise SystemExit(f'insufficient training grades for {property_name}')
        x_train=np.array([r[1] for r in train],dtype=float)
        y_train=np.array([value(r[0]['value']) for r in train],dtype=float)
        # A component field unseen in *all* training grades cannot be learned.
        # Drop it explicitly rather than letting the imputer warn or silently
        # manufacture a value; the full schema remains recorded in the report.
        feature_mask=np.any(~np.isnan(x_train),axis=0)
        feature_masks[property_name]=feature_mask.tolist()
        x_train=x_train[:,feature_mask]
        retained_features[property_name]=[name for name, keep in zip(feature_names,feature_mask) if keep]
        # Median imputation is fitted on training grades only. Missingness is
        # explicitly exposed by an indicator; it is not interpreted as zero or
        # as a range midpoint.
        pipeline=Pipeline([
            ('impute',SimpleImputer(strategy='median',add_indicator=True)),
            ('model',HistGradientBoostingRegressor(
                learning_rate=0.06,max_leaf_nodes=8,l2_regularization=2.0,
                min_samples_leaf=8,max_iter=250,random_state=20260831)),
        ])
        pipeline.fit(x_train,y_train)
        models[property_name]=pipeline
        by_split=defaultdict(list)
        for observation, vector, split_name in records:
            pred=float(pipeline.predict(np.array([vector],dtype=float)[:,feature_mask])[0])
            rec={
                'material_id':observation['material_id'], 'property':property_name,
                'split':split_name, 'temperature_K':observation['temperature_K'],
                'condition':observation['condition'], 'source_locator':observation['source_locator'],
                'observed_MPa':observation['value'], 'predicted_MPa':pred,
                'residual_MPa':pred-value(observation['value']),
            }
            predictions.append(rec); by_split[split_name].append(rec)
        metric_report[property_name]={name:metrics(items) for name,items in sorted(by_split.items())}
    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir/'predictions.csv',list(predictions[0]),predictions)
    write_csv(args.output_dir/'parsed_condition_features.csv',list(parsed_conditions[0]),parsed_conditions)
    joblib.dump({'models':models,'feature_names':feature_names,'composition_feature_names':composition_feature_names,
                 'retained_features':retained_features,'feature_masks':feature_masks},args.output_dir/'baseline_models.joblib')
    train_grade_ids=sorted(k for k,v in split.items() if v=='train')
    validation_grade_ids=sorted(k for k,v in split.items() if v=='validation')
    test_grade_ids=sorted(k for k,v in split.items() if v=='test')
    report={
      'model':'HistGradientBoostingRegressor baseline, one model per target',
      'model_fit_started':True,
      'research_status':'baseline only; not approved for product inference or material-selection claims',
      'training_input_package':str(p),
      'composition_representation':'supplier grade specification envelope; not measured heat chemistry',
      'features':{'temperature_K':1,'composition_features':len(composition_feature_names),'structured_condition_features':condition_names,'total_before_imputation':len(feature_names),'retained_after_train_support':{k:len(v) for k,v in retained_features.items()},'imputation':'training-only median plus missingness indicators; no zero or midpoint imputation'},
      'targets':'yield_strength and ultimate_tensile_strength, MPa',
      'split':{'rule':'material_id grade-disjoint fixed v1','train':train_grade_ids,'validation':validation_grade_ids,'test':test_grade_ids},
      'metrics_MPa':metric_report,
      'boundaries':[
        'Only two independent test grades: numerical test metrics are exploratory and not a generalization claim.',
        'A conservative subset of heat-treatment, product-state, geometry and cold-reduction fields is parsed; unstructured residual condition text remains traceability-only.',
        'No creep/rupture, CALPHAD, melting, oxidation, weld/filler or manufacturability prediction is produced.',
        'Predictions must be limited to the composition/state/temperature applicability domain in the input package.'
      ]
    }
    (args.output_dir/'training_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# 镍基高温合金短时强度基线训练结果','','这是研究基线，不是可上线的材料结论。','', '## 固定评估设置','',f"- 训练/验证/测试牌号：{len(train_grade_ids)} / {len(validation_grade_ids)} / {len(test_grade_ids)}（严格按牌号隔离）。",'- 成分输入为供应商牌号规范区间；不等同于炉次实测成分。','- 特征含测试温度、元素是否列出、成分下限/上限/标称值；缺失值只按训练集统计量填补，并加入缺失标记。','', '## 指标（MPa）','']
    for target, by_split in metric_report.items():
        lines.append(f'### {target}')
        for split_name, m in by_split.items(): lines.append(f"- {split_name}: n={m['n']}，MAE={m['mae_MPa']:.2f}，RMSE={m['rmse_MPa']:.2f}，R²={m['r2'] if m['r2'] is not None else 'N/A'}")
        lines.append('')
    lines.extend(['## 解释边界','',*['- '+x for x in report['boundaries']]])
    (args.output_dir/'training_report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
