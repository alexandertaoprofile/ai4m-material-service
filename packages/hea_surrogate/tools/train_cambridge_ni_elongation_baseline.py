#!/usr/bin/env python3
"""Train the admitted Cambridge Ni-superalloy elongation screening baseline."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline

ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = ["cc", "ds", "sc"]


def num(value: str) -> float:
    try: return float(value)
    except (TypeError, ValueError): return float("nan")


def split_for(tag: str) -> str:
    bucket = int(hashlib.sha256(tag.encode("utf8")).hexdigest(), 16) % 10
    return "test" if bucket == 0 else "validation" if bucket == 1 else "train"


def heat_features(value: str) -> list[float]:
    result: list[float] = []
    for family, stages in (("solution", 3), ("precipitation", 4)):
        for stage in range(1, stages + 1):
            for suffix in ("temp_C", "time_h", "heating_C_per_h"):
                found = re.search(fr"{family}_stage_{stage}_{suffix}=([-+0-9.eE]+)", value or "")
                result.append(num(found.group(1)) if found else float("nan"))
    return result


def feature(row: dict) -> list[float]:
    route = row["casting_code"]
    return [num(row["temperature_K"]), *[num(row.get(f"{e}_wt_percent")) for e in ELEMENTS], *[float(route == x) for x in ROUTES], num(row.get("casting_gradient_K_per_mm")), *heat_features(row.get("heat_treatment", ""))]


def summary(items: list[dict]) -> dict:
    y = np.asarray([item["observed_percent"] for item in items]); p = np.asarray([item["predicted_percent"] for item in items])
    return {"n":len(items), "mae_percent":float(mean_absolute_error(y,p)), "rmse_percent":float(math.sqrt(mean_squared_error(y,p))), "r2":float(r2_score(y,p)) if len(items)>1 and len(set(y))>1 else None}


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--package",type=Path,required=True); ap.add_argument("--output-dir",type=Path,required=True); args=ap.parse_args()
    if args.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    with (args.package / "tensile_auxiliary_property_queue.csv").open(encoding="utf8",newline="") as h: raw=list(csv.DictReader(h))
    rows=[]; review=0
    for row in raw:
        total=sum(num(row.get(f"{e}_wt_percent")) for e in ELEMENTS)
        value=num(row.get("value")); temp=num(row.get("temperature_K"))
        if row.get("property") == "elongation" and row.get("casting_code") in ROUTES and value>0 and temp>0 and .97<=total<=1.03:
            rows.append({**row,"split":split_for(row["tag"])})
        elif row.get("property") == "elongation": review += 1
    train=[r for r in rows if r["split"]=="train"]
    if len({r["tag"] for r in train}) < 20: raise SystemExit("insufficient training tags")
    x=np.asarray([feature(r) for r in train]); y=np.asarray([num(r["value"]) for r in train]); keep=np.any(~np.isnan(x),axis=0)
    model=Pipeline([("imputer",SimpleImputer(strategy="median",add_indicator=True)),("regressor",HistGradientBoostingRegressor(learning_rate=.05,max_leaf_nodes=10,min_samples_leaf=8,l2_regularization=3,max_iter=350,random_state=20260901))])
    model.fit(x[:,keep],y)
    predictions=[]; grouped=defaultdict(list)
    for row in rows:
        pred=float(model.predict(np.asarray([feature(row)])[:,keep])[0])
        item={"tag":row["tag"],"alloy_name":row["alloy_name"],"split":row["split"],"manufacturing_route":row["manufacturing_route"],"temperature_C":num(row["test_temperature_C"]),"observed_percent":num(row["value"]),"predicted_percent":pred,"source_row":row["source_row"],"reference_1_doi":row["reference_1_doi"]}
        predictions.append(item); grouped[item["split"]].append(item)
    metrics={key:summary(value) for key,value in sorted(grouped.items())}; metrics["independent_tags"]={key:len({x["tag"] for x in value}) for key,value in grouped.items()}
    args.output_dir.mkdir(parents=True)
    with (args.output_dir/"predictions.csv").open("w",encoding="utf8",newline="") as h:
        writer=csv.DictWriter(h,fieldnames=list(predictions[0]));writer.writeheader();writer.writerows(predictions)
    joblib.dump({"model":model,"feature_mask":keep.tolist()},args.output_dir/"elongation_model.joblib")
    report={"model":"HistGradientBoostingRegressor for tensile elongation percent","research_status":"exploratory screening baseline; not approved for engineering ductility release","training_package":str(args.package),"target":"tensile elongation percent","admitted_records":len(rows),"review_only_records":review,"inputs":"test temperature, source wt.% composition, cast/DS/SC route, casting gradient and recorded heat-treatment stages","split":"Tag-disjoint v1","metrics":metrics,"boundaries":["Elongation is a tensile ductility measurement under its recorded test condition; it is not a fracture-toughness or creep-ductility prediction.","Hardness is not a label in this source database and is intentionally absent from this service model."]}
    (args.output_dir/"training_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    test=metrics.get("test",{})
    (args.output_dir/"training_report.md").write_text(f"# 镍基高温合金延伸率研究基线\n\n独立 Tag 测试：n={test.get('n',0)}，MAE={test.get('mae_percent',float('nan')):.2f}%，RMSE={test.get('rmse_percent',float('nan')):.2f}%，R²={test.get('r2')}。\n",encoding="utf8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
