#!/usr/bin/env python3
"""Fresh endpoint challenge using groups unused by the prior endpoint evaluation."""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def groups_and_endpoints(formal, frame, groups):
    cte = frame[frame.task.eq(formal.CTE_TASK)].copy(); cte["_group"] = formal.cte_group_key(cte)
    out = {}
    for group in groups:
        subset = cte[cte._group.eq(group)]
        out[group] = subset.index[subset.condition_temperature_C.eq(subset.condition_temperature_C.max())].to_numpy(dtype=np.int64)
    return out


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--data",type=Path,required=True); parser.add_argument("--base-script",type=Path,required=True); parser.add_argument("--formal-script",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--max-epochs",type=int,default=35); parser.add_argument("--patience",type=int,default=6); parser.add_argument("--batch-size",type=int,default=512); parser.add_argument("--eval-batch-size",type=int,default=2048); parser.add_argument("--pair-batch-size",type=int,default=256); parser.add_argument("--d-model",type=int,default=128); parser.add_argument("--layers",type=int,default=3); parser.add_argument("--heads",type=int,default=8); parser.add_argument("--dropout",type=float,default=0.1); parser.add_argument("--lr",type=float,default=3e-4); parser.add_argument("--weight-decay",type=float,default=2e-4); parser.add_argument("--seed",type=int,default=20260928)
    args=parser.parse_args()
    if args.output_dir.exists(): raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True); started=time.time(); device=torch.device("cuda")
    base=load("glass_base_fresh",args.base_script); base.BOUNDED_TASKS={"POISSON_RATIO":(0.0,0.5)}
    formal=load("glass_formal_fresh",args.formal_script); frame=pd.read_csv(args.data,low_memory=False); frame["source_kod"]=frame.source_kod.astype(str)
    compositions=base.parse_compositions(frame); species=sorted({n for v in compositions.values() for n,_ in v}); vocab={n:i+1 for i,n in enumerate(species)}; tasks=sorted(frame.task.unique().tolist()); max_tokens=max(len(v) for v in compositions.values())
    cte=frame[frame.task.eq(formal.CTE_TASK)].copy(); cte["_group"]=formal.cte_group_key(cte); counts=cte.groupby("_group").condition_temperature_C.nunique(); eligible=np.asarray(sorted(counts[counts>=3].index.tolist()))
    # Reconstruct the prior split exactly, then take the fresh test only from the
    # groups that were not prior endpoint validation/test groups.
    prior_rng=np.random.default_rng(20260924); prior_rng.shuffle(eligible)
    n_prior_test=max(1,int(round(0.30*len(eligible)))); n_prior_val=max(1,int(round(0.20*len(eligible))))
    prior_test=set(eligible[:n_prior_test]); prior_val=set(eligible[n_prior_test:n_prior_test+n_prior_val]); unused=np.asarray(sorted(set(eligible)-prior_test-prior_val))
    fresh_rng=np.random.default_rng(20260928); fresh_rng.shuffle(unused); fresh_test=set(unused[:max(1,int(round(0.50*len(unused))))]); validation=prior_val
    endpoints=groups_and_endpoints(formal,frame,validation|fresh_test); val_idx=np.concatenate([endpoints[g] for g in sorted(validation)]); test_idx=np.concatenate([endpoints[g] for g in sorted(fresh_test)])
    held=set(val_idx.tolist())|set(test_idx.tolist()); train_idx=np.asarray([i for i in range(len(frame)) if i not in held],dtype=np.int64)
    val_anchor=formal.anchors(frame,val_idx,train_idx); test_anchor=formal.anchors(frame,test_idx,train_idx)
    methods={"baseline_full_multitask":(False,0.0),"cte_adapter_pair_response":(True,formal.PAIR_WEIGHT)}; predictions={k:[] for k in methods}; anchors={k:[] for k in methods}; per_seed={k:[] for k in methods}; rows=[]
    for name,(enabled,weight) in methods.items():
        for seed in formal.SEEDS:
            model,pre,encoded,training=formal.train_model(base,frame,compositions,vocab,tasks,max_tokens,train_idx,val_idx,args,device,seed,enabled,weight)
            _,pred=formal.predict_indices(base,model,encoded,test_idx,pre,frame,args.eval_batch_size,device); _,anchor_pred=formal.predict_indices(base,model,encoded,test_anchor,pre,frame,args.eval_batch_size,device)
            predictions[name].append(pred); anchors[name].append(anchor_pred); per_seed[name].append({"seed":seed,"training":training,"test":formal.endpoint_metrics(frame,test_idx,pred,test_anchor,anchor_pred)})
            print(json.dumps({"method":name,"seed":seed,"test":per_seed[name][-1]["test"]},ensure_ascii=False),flush=True)
    ensemble={}
    for name in methods:
        pred=np.mean(np.stack(predictions[name]),axis=0); anchor_pred=np.mean(np.stack(anchors[name]),axis=0); ensemble[name]=formal.endpoint_metrics(frame,test_idx,pred,test_anchor,anchor_pred)
        part=frame.iloc[test_idx][["source_kod","canonical_composition","task","condition_temperature_C","target"]].copy(); part["prediction"]=pred; part["method"]=name; part["anchor_row_index"]=test_anchor; rows.append(part)
    with gzip.open(args.output_dir/"fresh_endpoint_test_predictions.csv.gz","wt",encoding="utf-8",newline="") as handle: pd.concat(rows,ignore_index=True).to_csv(handle,index=False)
    report={"status":"offline_fresh_endpoint_challenge_not_deployed","prior_endpoint_groups":{"validation":len(prior_val),"test":len(prior_test)},"previously_unused_endpoint_groups":len(unused),"fresh_test_groups":len(fresh_test),"fresh_test_rows":len(test_idx),"validation_groups_reused_for_early_stopping":len(validation),"pair_weight_frozen":formal.PAIR_WEIGHT,"test_group_overlap_with_prior_endpoint_validation_or_test":len(fresh_test&(prior_val|prior_test)),"per_seed":per_seed,"ensemble_test":ensemble,"elapsed_seconds":time.time()-started}
    path=args.output_dir/"FRESH_ENDPOINT_RESULTS.json"; path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); (args.output_dir/"COMPLETED.json").write_text(json.dumps({"complete":True,"report":str(path),"elapsed_seconds":report["elapsed_seconds"]},indent=2),encoding="utf-8"); print(json.dumps({"complete":True,"report":str(path),"ensemble":ensemble},ensure_ascii=False),flush=True)


if __name__=="__main__": main()
