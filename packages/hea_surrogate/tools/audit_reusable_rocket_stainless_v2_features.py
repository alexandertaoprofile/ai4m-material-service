#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import joblib
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/"data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees/austenitic_design_v2_extratrees.joblib"
OUT=ROOT/"reports/reusable_rocket_stainless/20260902_v2_feature_audit.json"
def main():
    artifact=joblib.load(MODEL); report={}
    for target,pipeline in artifact["models"].items():
        names=pipeline.named_steps["features"].get_feature_names_out().tolist()
        values=pipeline.named_steps["regressor"].feature_importances_
        ordered=sorted(({"feature":n,"importance":float(v)} for n,v in zip(names,values)),
                       key=lambda x:x["importance"],reverse=True)
        report[target]={"top_features":ordered[:20]}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({k:v["top_features"][:6] for k,v in report.items()},ensure_ascii=False))
if __name__=="__main__": main()
