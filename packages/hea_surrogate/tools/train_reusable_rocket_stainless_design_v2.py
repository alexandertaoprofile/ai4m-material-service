#!/usr/bin/env python3
"""Train the selected group-validated Extra Trees v2 model.

Run with /data/mamba/envs/crabnet-py310/bin/python3.10.
"""
from __future__ import annotations
import json
from pathlib import Path
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/processed/reusable_rocket_stainless_design/20260902_v1/map_model_training_observations.csv'
OUT=ROOT/'data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees'
ELS=["Cr","Ni","Mo","Mn","Si","Nb","Ti","Zr","Ta","V","W","Cu","N","C","B","P","S","Co","Al","Sn","Pb"]
NUM=ELS+["Solution_treatment_temperature","Solution_treatment_time(s)","Water_Quenched_after_s.t.","Air_Quenched_after_s.t.","Grains mm-2","Size of ingot","Temperature (K)"]
CAT=["Type of melting","Product form"]
TARGETS=["yield_0p2_MPa","uts_MPa","elongation_pct"]

def make_model():
    return Pipeline([("features",ColumnTransformer([
        ("num",SimpleImputer(strategy="median",add_indicator=True),NUM),
        ("cat",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),
            ("onehot",OneHotEncoder(handle_unknown="ignore",sparse_output=False))]),CAT)])),
        ("regressor",ExtraTreesRegressor(n_estimators=500,min_samples_leaf=2,
            max_features=.85,n_jobs=-1,random_state=20260902))])

def dataset_for(data,target):
    d=data.dropna(subset=[target]).copy()
    # Two source rows have 435% / 505% elongation. Preserve them in
    # the raw parsed file and exclude only from this target until page review.
    return d[d[target].between(0,150)].copy() if target=="elongation_pct" else d

def group_cv(data,target):
    d=dataset_for(data,target); y=d[target].to_numpy(); pred=y.copy()*float("nan")
    for train,test in GroupKFold(n_splits=3).split(d,y,d.state_group):
        m=make_model().fit(d.iloc[train][NUM+CAT],y[train])
        pred[test]=m.predict(d.iloc[test][NUM+CAT])
    return {"n":len(d),"group_kfold_splits":3,"MAE":float(mean_absolute_error(y,pred)),"R2":float(r2_score(y,pred))}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    raw=pd.read_csv(DATA)
    models={}; metrics={}
    for target in TARGETS:
        metrics[target]=group_cv(raw,target)
        d=dataset_for(raw,target)
        models[target]=make_model().fit(d[NUM+CAT],d[target])
    joblib.dump({"model_version":"reusable_rocket_austenitic_design_v2_extratrees",
                 "features":{"numeric":NUM,"categorical":CAT},"models":models,
                 "temperature_domain_K":[293,1273],
                 "low_temperature_rule":"Below 293 K use the separate 301/304L reference layer; do not extrapolate this model."},
                OUT/"austenitic_design_v2_extratrees.joblib")
    outlier_rows=raw.loc[raw.elongation_pct.gt(150),["source_locator","elongation_pct","Temperature (K)"]].copy()
    outlier_rows["source_locator"]=outlier_rows["source_locator"].where(outlier_rows["source_locator"].notna(),None)
    outliers=outlier_rows.to_dict("records")
    card={"service_name":"可回收火箭不锈钢配方设计",
          "artifact":"reusable_rocket_austenitic_design_v2_extratrees",
          "training_rows":{"yield_0p2_MPa":2180,"uts_MPa":2180,"elongation_pct":2083},
          "independent_thermomechanical_groups":259,
          "validation":{"method":"3-fold GroupKFold by full composition + thermomechanical state",**metrics},
          "data_quality_action":{"elongation_rows_held_for_source_review":outliers,
             "missing_process_fields":"模型以中位数填补并添加缺失指示符；后续补数优先覆盖固溶时间、晶粒度和铸锭尺寸。"},
          "formal_scope":"293–1273 K、公开数据适用域内的奥氏体不锈钢短时拉伸候选筛选。",
          "cryo_scope":"20/77/90/111 K 使用独立 301/304L 参考层，作为试验优先级而非自由配方预测。",
          "applicability_domain_file":"applicability_domain.json（组装服务时必须执行其中的温度和成分邻域门槛）",
          "not_predicted":["30X 专属性能","疲劳","断裂韧性","焊接性能","LOX 相容性"],
          "release_status":"conditional_ready_for_first_service"}
    (OUT/"model_card.json").write_text(json.dumps(card,ensure_ascii=False,indent=2)+"\n")
    (OUT/"README.md").write_text("# 可回收火箭不锈钢配方设计：v2 Extra Trees 训练包\n\n"
        "采用按完整热机械状态分组的交叉验证。适用范围为 293–1273 K 短时拉伸筛选；低温火箭壳体由独立参考层约束。\n",encoding="utf-8")
    print(json.dumps({"metrics":metrics,"release":card["release_status"]},ensure_ascii=False))
if __name__=="__main__": main()
