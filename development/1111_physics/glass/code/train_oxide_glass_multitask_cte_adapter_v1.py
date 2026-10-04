#!/usr/bin/env python3
"""Formal offline validation of a CTE response adapter in the full glass model.

Protocols:
1. five-fold composition-held-out OOF;
2. three-fold source-held-out with overlapping compositions purged;
3. three-seed highest-temperature endpoint replay;
4. full-data candidate fit only after the three evaluations are produced.

The CTE pair loss updates only a zero-initialized response adapter. The shared
Transformer receives ordinary point-loss gradients, not pair-loss gradients.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from torch import nn
from torch.utils.data import DataLoader


CTE_TASK = "CTE_FIXED_T"
PAIR_WEIGHT = 0.5
SEEDS = [20260924, 20260925, 20260926]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("glass_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_everything(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True; torch.backends.cudnn.benchmark = False


class GlassMultitaskCTEAdapter(nn.Module):
    def __init__(self, base, n_species, n_tasks, n_descriptors, cte_task_id, d_model=128, n_layers=3, n_heads=8, dropout=0.1, enabled=True):
        super().__init__()
        self.base = base.OxideGlassTransformer(n_species, n_tasks, n_descriptors, d_model, n_layers, n_heads, dropout)
        self.cte_task_id = int(cte_task_id)
        self.enabled = bool(enabled)
        self.response_adapter = nn.Sequential(
            nn.Linear(n_descriptors + 2, 64), nn.SiLU(),
            nn.Linear(64, 32), nn.SiLU(), nn.Linear(32, 1),
        )
        nn.init.zeros_(self.response_adapter[-1].weight)
        nn.init.zeros_(self.response_adapter[-1].bias)

    def adapter_value(self, batch):
        condition = batch["condition"]
        x = torch.cat([batch["descriptors"], condition[:, None], (condition * condition)[:, None]], dim=1)
        return self.response_adapter(x).squeeze(1)

    def forward(self, batch):
        prediction = self.base(batch)
        if not self.enabled:
            return prediction
        mask = batch["task"].eq(self.cte_task_id).to(prediction.dtype)
        return prediction + mask * self.adapter_value(batch)


def tensor_batch(encoded, indices: np.ndarray, device):
    idx = np.asarray(indices, dtype=np.int64)
    return {
        "species": torch.from_numpy(encoded.species_ids[idx]).to(device),
        "fractions": torch.from_numpy(encoded.fractions[idx]).to(device),
        "descriptors": torch.from_numpy(encoded.descriptors[idx]).to(device),
        "task": torch.from_numpy(encoded.task_ids[idx]).long().to(device),
        "condition": torch.from_numpy(encoded.condition[idx]).to(device),
        "condition_mask": torch.from_numpy(encoded.condition_mask[idx]).to(device),
        "target_z": torch.from_numpy(encoded.target_z[idx]).to(device),
    }


def cte_group_key(frame: pd.DataFrame) -> pd.Series:
    return frame.source_kod.astype(str) + "||" + frame.canonical_composition.astype(str)


def build_pairs(frame: pd.DataFrame, allowed_idx: np.ndarray, minimum_delta_C: float = 10.0):
    allowed = set(np.asarray(allowed_idx, dtype=np.int64).tolist())
    cte = frame[frame.task.eq(CTE_TASK)].copy()
    cte["_group"] = cte_group_key(cte)
    low, high = [], []
    for _, group in cte.groupby("_group", sort=False):
        group = group.loc[[int(i) in allowed for i in group.index]]
        if group.empty:
            continue
        representatives = []
        for _, at_temperature in group.groupby("condition_temperature_C", sort=True):
            representatives.append(int(at_temperature.index[0]))
        for i, j in zip(representatives[:-1], representatives[1:]):
            delta = float(frame.loc[j, "condition_temperature_C"] - frame.loc[i, "condition_temperature_C"])
            if delta >= minimum_delta_C:
                low.append(i); high.append(j)
    return np.asarray(low, dtype=np.int64), np.asarray(high, dtype=np.int64)


def pair_mae_z(model, encoded, low, high, device, batch_size=2048):
    if not len(low):
        return float("nan")
    errors = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(low), batch_size):
            lo = low[start:start + batch_size]; hi = high[start:start + batch_size]
            blo = tensor_batch(encoded, lo, device); bhi = tensor_batch(encoded, hi, device)
            predicted = model(bhi) - model(blo)
            actual = bhi["target_z"] - blo["target_z"]
            errors.append(torch.abs(predicted - actual).cpu().numpy())
    return float(np.concatenate(errors).mean())


def train_model(base, frame, compositions, vocab, tasks, max_tokens, train_idx, val_idx, args, device, seed, adapter_enabled, pair_weight):
    seed_everything(seed)
    pre = base.Preprocessor(frame, train_idx, compositions, vocab, tasks, max_tokens)
    encoded = pre.encode(frame)
    loader = DataLoader(base.IndexedDataset(encoded, train_idx), batch_size=args.batch_size, sampler=base.task_sampler(frame, train_idx, seed), num_workers=0, pin_memory=True)
    val_loader = DataLoader(base.IndexedDataset(encoded, val_idx), batch_size=args.eval_batch_size, shuffle=False, num_workers=0)
    model = GlassMultitaskCTEAdapter(base, len(vocab), len(tasks), encoded.descriptors.shape[1], tasks.index(CTE_TASK), args.d_model, args.layers, args.heads, args.dropout, adapter_enabled).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.max_epochs, eta_min=args.lr * 0.08)
    criterion = nn.SmoothL1Loss(beta=0.5)
    pair_low, pair_high = build_pairs(frame, train_idx)
    val_pair_low, val_pair_high = build_pairs(frame, val_idx)
    pair_rng = np.random.default_rng(seed + 701)
    best_loss, best_state, best_epoch, patience = float("inf"), None, -1, 0
    history = []
    for epoch in range(args.max_epochs):
        model.train(); losses = []; point_losses = []; pair_losses = []
        for batch in loader:
            batch = base.move(batch, device)
            optimizer.zero_grad(set_to_none=True)
            point_loss = criterion(model(batch), batch["target_z"])
            pair_loss = torch.zeros((), device=device)
            if adapter_enabled and pair_weight > 0 and len(pair_low):
                chosen = pair_rng.integers(0, len(pair_low), size=min(args.pair_batch_size, len(pair_low)))
                low = pair_low[chosen]; high = pair_high[chosen]
                blo = tensor_batch(encoded, low, device); bhi = tensor_batch(encoded, high, device)
                # Evaluate the evolving base deterministically and without a pair gradient.
                model.base.eval()
                with torch.no_grad():
                    base_low = model.base(blo); base_high = model.base(bhi)
                model.base.train()
                predicted_delta = (base_high + model.adapter_value(bhi)) - (base_low + model.adapter_value(blo))
                actual_delta = bhi["target_z"] - blo["target_z"]
                pair_loss = criterion(predicted_delta, actual_delta)
            loss = point_loss + pair_weight * pair_loss
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
            losses.append(float(loss.detach().cpu())); point_losses.append(float(point_loss.detach().cpu())); pair_losses.append(float(pair_loss.detach().cpu()))
        scheduler.step()
        _, val_z = base.predict(model, val_loader, device)
        val_loss = float(np.mean(np.abs(val_z - encoded.target_z[val_idx])))
        val_pair = pair_mae_z(model, encoded, val_pair_low, val_pair_high, device, args.eval_batch_size)
        history.append({"epoch": epoch + 1, "train_total": float(np.mean(losses)), "train_point": float(np.mean(point_losses)), "train_pair": float(np.mean(pair_losses)), "val_normalized_MAE": val_loss, "val_CTE_pair_MAE_z": val_pair})
        if val_loss < best_loss - 1e-4:
            best_loss, best_epoch = val_loss, epoch + 1
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
        if patience >= args.patience:
            break
    if best_state is None:
        raise RuntimeError("no checkpoint")
    model.load_state_dict(best_state)
    return model, pre, encoded, {
        "seed": seed, "adapter_enabled": adapter_enabled, "pair_weight": pair_weight,
        "train_pairs": int(len(pair_low)), "validation_pairs": int(len(val_pair_low)),
        "best_epoch": best_epoch, "best_validation_normalized_MAE": best_loss, "history": history,
    }


@torch.no_grad()
def predict_indices(base, model, encoded, indices, pre, frame, batch_size, device):
    loader = DataLoader(base.IndexedDataset(encoded, indices), batch_size=batch_size, shuffle=False, num_workers=0)
    rows, pred_z = base.predict(model, loader, device)
    if not np.array_equal(rows, indices):
        raise RuntimeError("prediction order mismatch")
    return pre, base.decode_predictions(frame, indices, pred_z, pre)


def aggregate_predictions(base, frame, indices, predictions):
    return base.fold_metrics(frame, indices, predictions)


def run_oof_regime(base, frame, compositions, vocab, tasks, max_tokens, regime, folds, args, device, output_dir):
    fold_reports, parts, best_epochs = [], [], []
    for fold, (pool_idx, test_idx) in enumerate(base.outer_splits(frame, regime, folds), start=1):
        seed = args.seed + fold * 101
        train_idx, val_idx = base.inner_split(frame, pool_idx, regime, seed)
        model, pre, encoded, training = train_model(base, frame, compositions, vocab, tasks, max_tokens, train_idx, val_idx, args, device, seed, True, PAIR_WEIGHT)
        _, prediction = predict_indices(base, model, encoded, test_idx, pre, frame, args.eval_batch_size, device)
        fold_report = {
            "fold": fold, "training": training,
            "train_rows": int(len(train_idx)), "validation_rows": int(len(val_idx)), "test_rows": int(len(test_idx)),
            "composition_overlap_train_test": int(len(set(frame.iloc[train_idx].canonical_composition) & set(frame.iloc[test_idx].canonical_composition))),
            "source_overlap_train_test": int(len(set(frame.iloc[train_idx].source_kod.astype(str)) & set(frame.iloc[test_idx].source_kod.astype(str)))),
            "metrics": base.fold_metrics(frame, test_idx, prediction),
        }
        fold_reports.append(fold_report); best_epochs.append(training["best_epoch"])
        parts.append(pd.DataFrame({"row_index": test_idx, "fold": fold, "prediction": prediction}))
        print(json.dumps({"stage": regime, "fold": fold, "best_epoch": training["best_epoch"], "metrics": fold_report["metrics"]["by_task"].get(CTE_TASK)}, ensure_ascii=False), flush=True)
    oof = pd.concat(parts, ignore_index=True).sort_values("row_index")
    with gzip.open(output_dir / f"{regime}_oof_predictions.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        oof.to_csv(handle, index=False)
    metrics = base.aggregate_oof(frame, oof)
    return {"regime": regime, "folds": folds, "fold_reports": fold_reports, "oof_metrics": metrics, "median_best_epoch": int(round(float(np.median(best_epochs))))}, oof


def endpoint_split(frame: pd.DataFrame, seed=20260924):
    cte = frame[frame.task.eq(CTE_TASK)].copy(); cte["_group"] = cte_group_key(cte)
    counts = cte.groupby("_group").condition_temperature_C.nunique()
    eligible = np.asarray(sorted(counts[counts >= 3].index.tolist()))
    rng = np.random.default_rng(seed); rng.shuffle(eligible)
    n_test = max(1, int(round(0.30 * len(eligible)))); n_val = max(1, int(round(0.20 * len(eligible))))
    test_groups = set(eligible[:n_test]); val_groups = set(eligible[n_test:n_test + n_val])
    keys = cte["_group"]
    endpoints = {}
    for group in val_groups | test_groups:
        subset = cte[keys.eq(group)]
        endpoints[group] = subset.index[subset.condition_temperature_C.eq(subset.condition_temperature_C.max())].to_numpy(dtype=np.int64)
    val_idx = np.concatenate([endpoints[g] for g in sorted(val_groups)]); test_idx = np.concatenate([endpoints[g] for g in sorted(test_groups)])
    held = set(val_idx.tolist()) | set(test_idx.tolist())
    train_idx = np.asarray([i for i in range(len(frame)) if i not in held], dtype=np.int64)
    return train_idx, val_idx.astype(np.int64), test_idx.astype(np.int64), val_groups, test_groups


def anchors(frame, endpoints, train_idx):
    cte = frame[frame.task.eq(CTE_TASK)].copy(); cte["_group"] = cte_group_key(cte)
    key_by_idx = cte["_group"].to_dict(); train = set(train_idx.tolist()); output = []
    for idx in endpoints:
        key = key_by_idx[int(idx)]; t = float(frame.loc[idx, "condition_temperature_C"])
        candidates = [int(i) for i in cte.index[cte._group.eq(key)] if int(i) in train and float(frame.loc[i, "condition_temperature_C"]) < t]
        target_t = max(float(frame.loc[i, "condition_temperature_C"]) for i in candidates)
        output.append(next(i for i in candidates if float(frame.loc[i, "condition_temperature_C"]) == target_t))
    return np.asarray(output, dtype=np.int64)


def endpoint_metrics(frame, endpoints, prediction, anchor_idx, anchor_prediction):
    actual = frame.iloc[endpoints].target.to_numpy(float); anchor_actual = frame.iloc[anchor_idx].target.to_numpy(float)
    dt = frame.iloc[endpoints].condition_temperature_C.to_numpy(float) - frame.iloc[anchor_idx].condition_temperature_C.to_numpy(float)
    actual_slope = (actual - anchor_actual) / dt * 100.0; predicted_slope = (prediction - anchor_prediction) / dt * 100.0
    return {
        "n": int(len(actual)), "MAE": float(mean_absolute_error(actual, prediction)),
        "RMSE": float(math.sqrt(mean_squared_error(actual, prediction))), "R2": float(r2_score(actual, prediction)),
        "slope_MAE_per_100C": float(mean_absolute_error(actual_slope, predicted_slope)),
        "slope_RMSE_per_100C": float(math.sqrt(mean_squared_error(actual_slope, predicted_slope))),
        "direction_accuracy": float(np.mean(np.sign(actual_slope) == np.sign(predicted_slope))),
        "median_delta_T_C": float(np.median(dt)),
    }


def run_endpoint(base, frame, compositions, vocab, tasks, max_tokens, args, device, output_dir):
    train_idx, val_idx, test_idx, val_groups, test_groups = endpoint_split(frame)
    val_anchor = anchors(frame, val_idx, train_idx); test_anchor = anchors(frame, test_idx, train_idx)
    methods = {"baseline_full_multitask": (False, 0.0), "cte_adapter_pair_response": (True, PAIR_WEIGHT)}
    predictions = {name: [] for name in methods}; anchor_predictions = {name: [] for name in methods}; per_seed = {name: [] for name in methods}
    for name, (enabled, weight) in methods.items():
        for seed in SEEDS:
            model, pre, encoded, training = train_model(base, frame, compositions, vocab, tasks, max_tokens, train_idx, val_idx, args, device, seed, enabled, weight)
            _, pred = predict_indices(base, model, encoded, test_idx, pre, frame, args.eval_batch_size, device)
            _, anchor_pred = predict_indices(base, model, encoded, test_anchor, pre, frame, args.eval_batch_size, device)
            predictions[name].append(pred); anchor_predictions[name].append(anchor_pred)
            per_seed[name].append({"seed": seed, "training": training, "test": endpoint_metrics(frame, test_idx, pred, test_anchor, anchor_pred)})
            print(json.dumps({"stage": "endpoint", "method": name, "seed": seed, "test": per_seed[name][-1]["test"]}, ensure_ascii=False), flush=True)
    ensemble = {}; rows = []
    for name in methods:
        pred = np.mean(np.stack(predictions[name]), axis=0); anchor_pred = np.mean(np.stack(anchor_predictions[name]), axis=0)
        ensemble[name] = endpoint_metrics(frame, test_idx, pred, test_anchor, anchor_pred)
        part = frame.iloc[test_idx][["source_kod", "canonical_composition", "task", "condition_temperature_C", "target"]].copy()
        part["prediction"] = pred; part["method"] = name; part["anchor_row_index"] = test_anchor; rows.append(part)
    with gzip.open(output_dir / "endpoint_test_predictions.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        pd.concat(rows, ignore_index=True).to_csv(handle, index=False)
    return {
        "train_rows": int(len(train_idx)), "validation_endpoints": int(len(val_idx)), "test_endpoints": int(len(test_idx)),
        "validation_groups": int(len(val_groups)), "test_groups": int(len(test_groups)),
        "pair_weight_frozen_from_prior_ablation": PAIR_WEIGHT, "per_seed": per_seed, "ensemble_test": ensemble,
    }


def fit_full(base, frame, compositions, vocab, tasks, max_tokens, epochs, args, device, output_dir):
    all_idx = np.arange(len(frame), dtype=np.int64)
    seed_everything(args.seed + 9001)
    pre = base.Preprocessor(frame, all_idx, compositions, vocab, tasks, max_tokens); encoded = pre.encode(frame)
    loader = DataLoader(base.IndexedDataset(encoded, all_idx), batch_size=args.batch_size, sampler=base.task_sampler(frame, all_idx, args.seed + 9001), num_workers=0, pin_memory=True)
    model = GlassMultitaskCTEAdapter(base, len(vocab), len(tasks), encoded.descriptors.shape[1], tasks.index(CTE_TASK), args.d_model, args.layers, args.heads, args.dropout, True).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=args.lr * 0.08); criterion = nn.SmoothL1Loss(beta=0.5)
    pair_low, pair_high = build_pairs(frame, all_idx); rng = np.random.default_rng(args.seed + 9701); history=[]
    for epoch in range(epochs):
        model.train(); total=[]; point_values=[]; pair_values=[]
        for batch in loader:
            batch=base.move(batch,device); optimizer.zero_grad(set_to_none=True); point=criterion(model(batch),batch["target_z"])
            chosen=rng.integers(0,len(pair_low),size=min(args.pair_batch_size,len(pair_low))); blo=tensor_batch(encoded,pair_low[chosen],device); bhi=tensor_batch(encoded,pair_high[chosen],device)
            model.base.eval()
            with torch.no_grad(): base_lo=model.base(blo); base_hi=model.base(bhi)
            model.base.train(); pair=criterion((base_hi+model.adapter_value(bhi))-(base_lo+model.adapter_value(blo)),bhi["target_z"]-blo["target_z"])
            loss=point+PAIR_WEIGHT*pair; loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); optimizer.step()
            total.append(float(loss.detach().cpu())); point_values.append(float(point.detach().cpu())); pair_values.append(float(pair.detach().cpu()))
        scheduler.step(); history.append({"epoch":epoch+1,"total":float(np.mean(total)),"point":float(np.mean(point_values)),"pair":float(np.mean(pair_values))})
    checkpoint={
        "model_name":"oxide_glass_multitask_cte_response_adapter_v1_20260928",
        "status":"offline_candidate_not_deployed",
        "state_dict":{k:v.detach().cpu() for k,v in model.state_dict().items()},
        "constructor":{"n_species":len(vocab),"n_tasks":len(tasks),"n_descriptors":encoded.descriptors.shape[1],"cte_task_id":tasks.index(CTE_TASK),"d_model":args.d_model,"n_layers":args.layers,"n_heads":args.heads,"dropout":args.dropout,"adapter_enabled":True},
        "preprocessor":pre.state(),"target_transforms":{"positive_log_tasks":sorted(base.POSITIVE_LOG_TASKS),"bounded_tasks":base.BOUNDED_TASKS},"pair_weight":PAIR_WEIGHT,"training_pairs":int(len(pair_low)),"epochs":int(epochs),"training_data_sha256":sha256(args.data),
        "limitations":["not deployed","SciGlass database observations require license review","thermal conductivity temperature response remains unsupported"],
    }
    path=output_dir/"oxide_glass_multitask_cte_response_adapter_v1.pt"; torch.save(checkpoint,path)
    return {"checkpoint":str(path),"sha256":sha256(path),"epochs":int(epochs),"training_pairs":int(len(pair_low)),"history":history}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--data",type=Path,required=True); parser.add_argument("--base-script",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--max-epochs",type=int,default=35); parser.add_argument("--patience",type=int,default=6); parser.add_argument("--batch-size",type=int,default=512); parser.add_argument("--eval-batch-size",type=int,default=2048); parser.add_argument("--pair-batch-size",type=int,default=256)
    parser.add_argument("--d-model",type=int,default=128); parser.add_argument("--layers",type=int,default=3); parser.add_argument("--heads",type=int,default=8); parser.add_argument("--dropout",type=float,default=0.1); parser.add_argument("--lr",type=float,default=3e-4); parser.add_argument("--weight-decay",type=float,default=2e-4); parser.add_argument("--seed",type=int,default=20260928); parser.add_argument("--smoke",action="store_true")
    args=parser.parse_args()
    if args.output_dir.exists(): raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True); device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type!="cuda": raise RuntimeError("CUDA required")
    if args.smoke: args.max_epochs=1; args.patience=1
    started=time.time(); base=load_module(args.base_script)
    # The current scope is conventional oxide glass; use the validated elastic
    # stability interval instead of the legacy loose [-0.2, 0.6] mapping.
    base.BOUNDED_TASKS={"POISSON_RATIO":(0.0,0.5)}
    frame=pd.read_csv(args.data,low_memory=False); frame["source_kod"]=frame.source_kod.astype(str)
    compositions=base.parse_compositions(frame); species=sorted({n for values in compositions.values() for n,_ in values}); vocab={n:i+1 for i,n in enumerate(species)}; tasks=sorted(frame.task.unique().tolist()); max_tokens=max(len(v) for v in compositions.values())
    report={"status":"offline_candidate_not_deployed","data":str(args.data),"data_sha256":sha256(args.data),"base_script":str(args.base_script),"base_script_sha256":sha256(args.base_script),"rows":len(frame),"compositions":int(frame.canonical_composition.nunique()),"sources":int(frame.source_kod.nunique()),"tasks":tasks,"pair_weight_frozen_from_prior_ablation":PAIR_WEIGHT,"pair_gradient_scope":"adapter only","poisson_output_interval":[0.0,0.5],"positive_log_tasks":sorted(base.POSITIVE_LOG_TASKS),"test_used_for_selection":False}
    comp, _=run_oof_regime(base,frame,compositions,vocab,tasks,max_tokens,"composition",2 if args.smoke else 5,args,device,args.output_dir); report["composition_oof"]=comp
    source, _=run_oof_regime(base,frame,compositions,vocab,tasks,max_tokens,"source",2 if args.smoke else 3,args,device,args.output_dir); report["source_purged_oof"]=source
    report["endpoint_replay"]=run_endpoint(base,frame,compositions,vocab,tasks,max_tokens,args,device,args.output_dir)
    if not args.smoke:
        epochs=int(round(np.median([comp["median_best_epoch"],source["median_best_epoch"]]))); report["full_fit"]=fit_full(base,frame,compositions,vocab,tasks,max_tokens,epochs,args,device,args.output_dir)
    report["elapsed_seconds"]=time.time()-started; path=args.output_dir/"FORMAL_VALIDATION_RESULTS.json"; path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); (args.output_dir/"COMPLETED.json").write_text(json.dumps({"complete":True,"report":str(path),"elapsed_seconds":report["elapsed_seconds"]},indent=2),encoding="utf-8"); print(json.dumps({"complete":True,"report":str(path),"elapsed_seconds":report["elapsed_seconds"]},ensure_ascii=False),flush=True)


if __name__=="__main__": main()
