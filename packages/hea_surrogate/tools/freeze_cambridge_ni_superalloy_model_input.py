#!/usr/bin/env python3
"""Freeze a deterministic Tag-disjoint split for the Cambridge tensile lane."""
from __future__ import annotations
import argparse, csv, hashlib, json
from collections import Counter
from pathlib import Path

def read(p):
    with p.open(encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def write(p, rows):
    with p.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def bucket(x): return int(hashlib.sha256(("cambridge-ni-v1:"+x).encode()).hexdigest()[:8],16)%10
def main():
    ap=argparse.ArgumentParser();ap.add_argument("package",type=Path);a=ap.parse_args();p=a.package
    inp=p/"cast_ds_sc_short_time_strength_admitted.csv"; rows=read(inp)
    groups=sorted({r["tag"] for r in rows})
    split={g:("test" if bucket(g)==0 else "validation" if bucket(g)==1 else "train") for g in groups}
    # ensure held-out coverage while avoiding any manual selection by property values
    if not any(v=="test" for v in split.values()): split[groups[-1]]="test"
    if not any(v=="validation" for v in split.values()): split[groups[-2]]="validation"
    out=[{"tag":g,"split":split[g],"hash_bucket":bucket(g),"rule":"deterministic SHA256 Tag-disjoint split v1"} for g in groups]
    d=p/"splits";d.mkdir(exist_ok=False);write(d/"tag_disjoint_split_v1.csv",out)
    manifest={"fit_ready":True,"model_fit_started":False,"task":"route-specific short-time tensile regression","unit":"MPa","input":"cast_ds_sc_short_time_strength_admitted.csv","split":"splits/tag_disjoint_split_v1.csv","split_rule":"all observations for the same Cambridge Tag occur in one split","counts":{"tags":len(groups),"observations":len(rows),"split_tags":dict(Counter(split.values())),"targets":dict(Counter(r["property"] for r in rows))},"boundaries":["Research-only baseline; no product/service inference.","Supplier/experimental dataset composition and process data are source records, not a manufacturing release.","Creep, fatigue and phase-transition targets are separate tasks."]}
    (p/"fit_readiness_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
