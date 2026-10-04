#!/usr/bin/env python3
"""Offline controlled ablations for oxide-glass physical constraints.

This script never modifies deployed artifacts. It evaluates three independent
questions on frozen splits:
1) positive output parameterization for intrinsically positive properties;
2) Poisson-ratio stability bounds;
3) a CTE temperature-response adapter on a highest-temperature endpoint holdout.
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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler


POSITIVE_TASKS = ["DENSITY_20C", "TG", "THERMAL_CONDUCTIVITY", "YOUNG_MODULUS"]
SEEDS = [20260924, 20260925, 20260926]


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_base_module(path: Path):
    spec = importlib.util.spec_from_file_location("glass_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import base module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Transform:
    positive_log: frozenset[str] = frozenset()
    bounded: tuple[tuple[str, float, float], ...] = ()

    def bounds(self) -> dict[str, tuple[float, float]]:
        return {task: (lo, hi) for task, lo, hi in self.bounded}

    def forward(self, task: str, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        if task in self.positive_log:
            return np.log(np.clip(values, 1e-12, None))
        if task in self.bounds():
            lo, hi = self.bounds()[task]
            scaled = np.clip((values - lo) / (hi - lo), 1e-6, 1.0 - 1e-6)
            return np.log(scaled / (1.0 - scaled))
        return values

    def inverse(self, task: str, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        if task in self.positive_log:
            return np.exp(np.clip(values, -20.0, 20.0))
        if task in self.bounds():
            lo, hi = self.bounds()[task]
            sigmoid = 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))
            return lo + (hi - lo) * sigmoid
        return values


class FlexiblePreprocessor:
    def __init__(self, base, frame: pd.DataFrame, train_idx: np.ndarray, compositions, vocab, tasks, max_tokens, transform: Transform):
        self.base = base
        self.vocab = vocab
        self.tasks = tasks
        self.task_to_id = {task: index for index, task in enumerate(tasks)}
        self.max_tokens = max_tokens
        self.compositions = compositions
        self.transform = transform
        train = frame.iloc[train_idx]
        self.target_stats: dict[str, dict[str, float]] = {}
        for task, group in train.groupby("task"):
            transformed = transform.forward(str(task), group.target.to_numpy(dtype=float))
            self.target_stats[str(task)] = {"mean": float(transformed.mean()), "std": max(float(transformed.std()), 1e-6)}
        train_compositions = train.canonical_composition.drop_duplicates().tolist()
        unique_desc = np.asarray([base.descriptor_vector(compositions[key]) for key in train_compositions], dtype=np.float64)
        self.descriptor_mean = unique_desc.mean(axis=0)
        self.descriptor_std = np.maximum(unique_desc.std(axis=0), 1e-8)
        finite_temp = pd.to_numeric(train.condition_temperature_C, errors="coerce").dropna().to_numpy(dtype=float)
        self.condition_mean = float(finite_temp.mean()) if len(finite_temp) else 0.0
        self.condition_std = max(float(finite_temp.std()), 1.0) if len(finite_temp) else 1.0

    def encode(self, frame: pd.DataFrame):
        n = len(frame)
        ids = np.zeros((n, self.max_tokens), dtype=np.int64)
        fractions = np.zeros((n, self.max_tokens), dtype=np.float32)
        desc = np.zeros((n, 7 + len(self.base.EXPLICIT_SPECIES)), dtype=np.float32)
        task_ids = np.zeros(n, dtype=np.int64)
        condition = np.zeros(n, dtype=np.float32)
        condition_mask = np.zeros(n, dtype=np.float32)
        target_z = np.zeros(n, dtype=np.float32)
        for row_index, row in enumerate(frame.itertuples(index=False)):
            components = self.compositions[row.canonical_composition]
            for token_index, (name, value) in enumerate(components):
                ids[row_index, token_index] = self.vocab[name]
                fractions[row_index, token_index] = value
            desc[row_index] = (np.asarray(self.base.descriptor_vector(components)) - self.descriptor_mean) / self.descriptor_std
            task = str(row.task)
            task_ids[row_index] = self.task_to_id[task]
            has_temp = pd.notna(row.condition_temperature_C)
            temp = float(row.condition_temperature_C) if has_temp else 0.0
            condition_mask[row_index] = float(has_temp)
            condition[row_index] = (temp - self.condition_mean) / self.condition_std if has_temp else 0.0
            transformed = self.transform.forward(task, np.asarray([float(row.target)]))[0]
            stats = self.target_stats[task]
            target_z[row_index] = (transformed - stats["mean"]) / stats["std"]
        return self.base.Encoding(ids, fractions, desc, task_ids, condition, condition_mask, target_z)

    def decode(self, frame: pd.DataFrame, indices: np.ndarray, pred_z: np.ndarray) -> np.ndarray:
        output = np.zeros(len(indices), dtype=np.float64)
        rows = frame.iloc[indices]
        for task in self.tasks:
            mask = rows.task.to_numpy() == task
            if not mask.any():
                continue
            stats = self.target_stats[task]
            transformed = pred_z[mask] * stats["std"] + stats["mean"]
            output[mask] = self.transform.inverse(task, transformed)
        return output


def source_composition_purged_split(frame: pd.DataFrame, seed: int = 20260924):
    rng = np.random.default_rng(seed)
    sources = np.asarray(sorted(frame.source_kod.astype(str).unique()))
    rng.shuffle(sources)
    n_test = max(1, int(round(0.20 * len(sources))))
    test_sources = set(sources[:n_test])
    test_mask = frame.source_kod.astype(str).isin(test_sources).to_numpy()
    test_idx = np.flatnonzero(test_mask)
    test_comps = set(frame.iloc[test_idx].canonical_composition)
    pool_mask = ~test_mask & ~frame.canonical_composition.isin(test_comps).to_numpy()
    pool_sources = np.asarray(sorted(frame.loc[pool_mask, "source_kod"].astype(str).unique()))
    rng.shuffle(pool_sources)
    n_val = max(1, int(round(0.15 * len(pool_sources))))
    val_sources = set(pool_sources[:n_val])
    val_mask = pool_mask & frame.source_kod.astype(str).isin(val_sources).to_numpy()
    val_idx = np.flatnonzero(val_mask)
    val_comps = set(frame.iloc[val_idx].canonical_composition)
    train_mask = pool_mask & ~frame.source_kod.astype(str).isin(val_sources).to_numpy() & ~frame.canonical_composition.isin(val_comps).to_numpy()
    train_idx = np.flatnonzero(train_mask)
    return train_idx, val_idx, test_idx


def counts_by_task(frame: pd.DataFrame, indices: np.ndarray) -> dict[str, int]:
    return {str(k): int(v) for k, v in frame.iloc[indices].task.value_counts().sort_index().items()}


def sampler(frame: pd.DataFrame, indices: np.ndarray, seed: int):
    labels = frame.iloc[indices].task.astype(str).tolist()
    counts = Counter(labels)
    weights = np.asarray([1.0 / math.sqrt(counts[label]) for label in labels], dtype=np.float64)
    return WeightedRandomSampler(torch.from_numpy(weights), num_samples=len(weights), replacement=True, generator=torch.Generator().manual_seed(seed))


def metrics(actual: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    return {
        "n": int(len(actual)),
        "MAE": float(mean_absolute_error(actual, prediction)),
        "RMSE": float(math.sqrt(mean_squared_error(actual, prediction))),
        "R2": float(r2_score(actual, prediction)) if len(actual) >= 2 else float("nan"),
    }


def by_task_metrics(frame: pd.DataFrame, indices: np.ndarray, prediction: np.ndarray):
    rows = frame.iloc[indices].copy()
    rows["prediction"] = prediction
    out = {}
    for task, group in rows.groupby("task"):
        task = str(task)
        item = metrics(group.target.to_numpy(float), group.prediction.to_numpy(float))
        if task in POSITIVE_TASKS:
            item["nonpositive_predictions"] = int((group.prediction <= 0).sum())
            item["nonpositive_rate"] = float((group.prediction <= 0).mean())
        if task == "POISSON_RATIO":
            item["outside_general_stability"] = int(((group.prediction <= -1.0) | (group.prediction >= 0.5)).sum())
            item["outside_oxide_domain_0_0p5"] = int(((group.prediction <= 0.0) | (group.prediction >= 0.5)).sum())
        out[task] = item
    return out


@torch.no_grad()
def predict_z(base, model, encoded, indices, batch_size, device):
    loader = DataLoader(base.IndexedDataset(encoded, indices), batch_size=batch_size, shuffle=False, num_workers=0)
    rows, pred = base.predict(model, loader, device)
    if not np.array_equal(rows, indices):
        raise RuntimeError("prediction order mismatch")
    return pred


def train_model(base, frame, train_idx, val_idx, compositions, vocab, tasks, max_tokens, transform, seed, args, device):
    seed_everything(seed)
    pre = FlexiblePreprocessor(base, frame, train_idx, compositions, vocab, tasks, max_tokens, transform)
    encoded = pre.encode(frame)
    train_loader = DataLoader(base.IndexedDataset(encoded, train_idx), batch_size=args.batch_size, sampler=sampler(frame, train_idx, seed), num_workers=0, pin_memory=True)
    val_loader = DataLoader(base.IndexedDataset(encoded, val_idx), batch_size=args.eval_batch_size, shuffle=False, num_workers=0, pin_memory=True)
    model = base.OxideGlassTransformer(len(vocab), len(tasks), encoded.descriptors.shape[1], args.d_model, args.layers, args.heads, args.dropout).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.max_epochs, eta_min=args.lr * 0.08)
    criterion = nn.SmoothL1Loss(beta=0.5)
    best_loss, best_state, best_epoch, patience = float("inf"), None, -1, 0
    history = []
    for epoch in range(args.max_epochs):
        model.train(); losses = []
        for batch in train_loader:
            batch = base.move(batch, device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch), batch["target_z"])
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step()
        _, val_z = base.predict(model, val_loader, device)
        val_loss = float(np.mean(np.abs(val_z - encoded.target_z[val_idx])))
        history.append({"epoch": epoch + 1, "train_loss": float(np.mean(losses)), "val_normalized_MAE": val_loss})
        if val_loss < best_loss - 1e-4:
            best_loss, best_epoch = val_loss, epoch + 1
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
        if patience >= args.patience:
            break
    if best_state is None:
        raise RuntimeError("training produced no checkpoint")
    model.load_state_dict(best_state)
    return model, pre, encoded, {"best_epoch": best_epoch, "best_val_normalized_MAE": best_loss, "history": history}


def prepare(frame: pd.DataFrame, base):
    compositions = base.parse_compositions(frame)
    species = sorted({name for values in compositions.values() for name, _ in values})
    vocab = {name: index + 1 for index, name in enumerate(species)}
    tasks = sorted(frame.task.unique().tolist())
    max_tokens = max(len(value) for value in compositions.values())
    return compositions, vocab, tasks, max_tokens


def run_positive_ablation(base, full: pd.DataFrame, args, device):
    frame = full[full.task.isin(POSITIVE_TASKS)].reset_index(drop=True)
    train_idx, val_idx, test_idx = source_composition_purged_split(frame)
    split = {"train": counts_by_task(frame, train_idx), "validation": counts_by_task(frame, val_idx), "test": counts_by_task(frame, test_idx)}
    for part, values in split.items():
        missing = set(POSITIVE_TASKS) - set(values)
        if missing:
            raise RuntimeError(f"positive {part} split missing tasks: {missing}")
    compositions, vocab, tasks, max_tokens = prepare(frame, base)
    variants = {
        "unconstrained_identity": Transform(),
        "positive_log_parameterization": Transform(positive_log=frozenset(POSITIVE_TASKS)),
    }
    result = {"split": split, "variants": {}}
    prediction_rows = []
    for name, transform in variants.items():
        seed_reports, test_predictions = [], []
        for seed in SEEDS:
            model, pre, encoded, train_report = train_model(base, frame, train_idx, val_idx, compositions, vocab, tasks, max_tokens, transform, seed, args, device)
            pred_z = predict_z(base, model, encoded, test_idx, args.eval_batch_size, device)
            pred = pre.decode(frame, test_idx, pred_z)
            seed_reports.append({"seed": seed, "training": train_report, "test": by_task_metrics(frame, test_idx, pred)})
            test_predictions.append(pred)
            print(json.dumps({"stage": "positive", "variant": name, "seed": seed, "test": seed_reports[-1]["test"]}, ensure_ascii=False), flush=True)
        ensemble = np.mean(np.stack(test_predictions), axis=0)
        result["variants"][name] = {"seeds": seed_reports, "ensemble_test": by_task_metrics(frame, test_idx, ensemble)}
        part = frame.iloc[test_idx][["source_kod", "canonical_composition", "task", "target"]].copy()
        part["prediction"] = ensemble; part["variant"] = name; prediction_rows.append(part)
    return result, pd.concat(prediction_rows, ignore_index=True)


def run_poisson_ablation(base, full: pd.DataFrame, args, device):
    frame = full[full.task.eq("POISSON_RATIO")].reset_index(drop=True)
    train_idx, val_idx, test_idx = source_composition_purged_split(frame)
    compositions, vocab, tasks, max_tokens = prepare(frame, base)
    variants = {
        "unconstrained_identity": Transform(),
        "legacy_loose_minus0p2_to_0p6": Transform(bounded=(("POISSON_RATIO", -0.2, 0.6),)),
        "oxide_stable_0_to_0p5": Transform(bounded=(("POISSON_RATIO", 0.0, 0.5),)),
    }
    result = {"split": {"train": len(train_idx), "validation": len(val_idx), "test": len(test_idx)}, "variants": {}}
    prediction_rows = []
    for name, transform in variants.items():
        seed_reports, test_predictions = [], []
        for seed in SEEDS:
            model, pre, encoded, train_report = train_model(base, frame, train_idx, val_idx, compositions, vocab, tasks, max_tokens, transform, seed, args, device)
            pred_z = predict_z(base, model, encoded, test_idx, args.eval_batch_size, device)
            pred = pre.decode(frame, test_idx, pred_z)
            seed_reports.append({"seed": seed, "training": train_report, "test": by_task_metrics(frame, test_idx, pred)["POISSON_RATIO"]})
            test_predictions.append(pred)
            print(json.dumps({"stage": "poisson", "variant": name, "seed": seed, "test": seed_reports[-1]["test"]}, ensure_ascii=False), flush=True)
        ensemble = np.mean(np.stack(test_predictions), axis=0)
        ensemble_metrics = by_task_metrics(frame, test_idx, ensemble)["POISSON_RATIO"]
        nu = ensemble
        # With E > 0, these counts directly determine whether derived G and K are positive.
        ensemble_metrics["derived_G_nonpositive_if_E_positive"] = int((nu <= -1.0).sum())
        ensemble_metrics["derived_K_nonpositive_if_E_positive"] = int((nu >= 0.5).sum())
        result["variants"][name] = {"seeds": seed_reports, "ensemble_test": ensemble_metrics}
        part = frame.iloc[test_idx][["source_kod", "canonical_composition", "task", "target"]].copy()
        part["prediction"] = ensemble; part["variant"] = name; prediction_rows.append(part)
    return result, pd.concat(prediction_rows, ignore_index=True)


def cte_group_key(frame: pd.DataFrame) -> pd.Series:
    return frame.source_kod.astype(str) + "||" + frame.canonical_composition.astype(str)


def cte_endpoint_split(frame: pd.DataFrame, seed: int = 20260924):
    keys = cte_group_key(frame)
    work = frame.assign(_group=keys)
    nunique = work.groupby("_group").condition_temperature_C.nunique()
    eligible = np.asarray(sorted(nunique[nunique >= 3].index.tolist()))
    rng = np.random.default_rng(seed); rng.shuffle(eligible)
    n_test = max(1, int(round(0.30 * len(eligible))))
    n_val = max(1, int(round(0.20 * len(eligible))))
    test_groups = set(eligible[:n_test]); val_groups = set(eligible[n_test:n_test + n_val])
    endpoints = {}
    for group in val_groups | test_groups:
        idx = np.flatnonzero(keys.to_numpy() == group)
        max_temp = frame.iloc[idx].condition_temperature_C.max()
        endpoints[group] = idx[frame.iloc[idx].condition_temperature_C.to_numpy(float) == float(max_temp)]
    val_idx = np.concatenate([endpoints[g] for g in sorted(val_groups)]).astype(np.int64)
    test_idx = np.concatenate([endpoints[g] for g in sorted(test_groups)]).astype(np.int64)
    held = set(val_idx.tolist()) | set(test_idx.tolist())
    train_idx = np.asarray([i for i in range(len(frame)) if i not in held], dtype=np.int64)
    return train_idx, val_idx, test_idx, val_groups, test_groups


def build_pairs(frame: pd.DataFrame, allowed_idx: np.ndarray, minimum_delta: float = 10.0):
    allowed = set(allowed_idx.tolist())
    work = frame.copy(); work["_group"] = cte_group_key(frame)
    low, high = [], []
    for _, group in work.groupby("_group", sort=False):
        group = group.loc[[i in allowed for i in group.index]]
        if group.empty:
            continue
        representatives = []
        for _, at_temp in group.groupby("condition_temperature_C", sort=True):
            representatives.append(int(at_temp.index[0]))
        for i, j in zip(representatives[:-1], representatives[1:]):
            if float(frame.loc[j, "condition_temperature_C"] - frame.loc[i, "condition_temperature_C"]) >= minimum_delta:
                low.append(i); high.append(j)
    return np.asarray(low, dtype=np.int64), np.asarray(high, dtype=np.int64)


def endpoint_anchors(frame: pd.DataFrame, endpoint_idx: np.ndarray, train_idx: np.ndarray):
    train_set = set(train_idx.tolist())
    keys = cte_group_key(frame)
    anchors = []
    for idx in endpoint_idx:
        key = keys.iloc[idx]; temp = float(frame.iloc[idx].condition_temperature_C)
        candidates = [j for j in np.flatnonzero(keys.to_numpy() == key) if j in train_set and float(frame.iloc[j].condition_temperature_C) < temp]
        if not candidates:
            raise RuntimeError(f"no lower-temperature anchor for endpoint {idx}")
        max_temp = max(float(frame.iloc[j].condition_temperature_C) for j in candidates)
        anchors.append(next(j for j in candidates if float(frame.iloc[j].condition_temperature_C) == max_temp))
    return np.asarray(anchors, dtype=np.int64)


def endpoint_metrics(frame, endpoint_idx, endpoint_pred, anchor_idx, anchor_pred):
    actual = frame.iloc[endpoint_idx].target.to_numpy(float)
    anchor_actual = frame.iloc[anchor_idx].target.to_numpy(float)
    dt = frame.iloc[endpoint_idx].condition_temperature_C.to_numpy(float) - frame.iloc[anchor_idx].condition_temperature_C.to_numpy(float)
    actual_slope = (actual - anchor_actual) / dt * 100.0
    predicted_slope = (endpoint_pred - anchor_pred) / dt * 100.0
    out = metrics(actual, endpoint_pred)
    out.update({
        "slope_MAE_per_100C": float(mean_absolute_error(actual_slope, predicted_slope)),
        "slope_RMSE_per_100C": float(math.sqrt(mean_squared_error(actual_slope, predicted_slope))),
        "slope_direction_accuracy": float(np.mean(np.sign(actual_slope) == np.sign(predicted_slope))),
        "temperature_span_median_C": float(np.median(dt)),
    })
    return out


class CTEResponseAdapter(nn.Module):
    def __init__(self, n_descriptors: int):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(n_descriptors + 2, 64), nn.SiLU(), nn.Linear(64, 32), nn.SiLU(), nn.Linear(32, 1))
        nn.init.zeros_(self.net[-1].weight); nn.init.zeros_(self.net[-1].bias)

    def forward(self, descriptors: torch.Tensor, condition: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([descriptors, condition[:, None], (condition * condition)[:, None]], dim=1)).squeeze(1)


def fit_adapter(encoded, base_pred_z: np.ndarray, train_idx, val_idx, val_anchor_idx, pair_low, pair_high, pair_weight, seed, args, device):
    seed_everything(seed + int(pair_weight * 10000) + 71)
    adapter = CTEResponseAdapter(encoded.descriptors.shape[1]).to(device)
    optimizer = torch.optim.AdamW(adapter.parameters(), lr=1e-3, weight_decay=1e-4)
    desc = torch.from_numpy(encoded.descriptors).to(device)
    cond = torch.from_numpy(encoded.condition).to(device)
    target = torch.from_numpy(encoded.target_z).to(device)
    base_z = torch.from_numpy(base_pred_z.astype(np.float32)).to(device)
    train_t = torch.from_numpy(train_idx).long().to(device)
    val_t = torch.from_numpy(val_idx).long().to(device)
    val_anchor_t = torch.from_numpy(val_anchor_idx).long().to(device)
    low_t = torch.from_numpy(pair_low).long().to(device)
    high_t = torch.from_numpy(pair_high).long().to(device)
    criterion = nn.SmoothL1Loss(beta=0.5)
    best, best_state, best_epoch, patience = float("inf"), None, -1, 0
    history = []
    for epoch in range(args.adapter_epochs):
        adapter.train(); optimizer.zero_grad(set_to_none=True)
        correction = adapter(desc, cond)
        pred = base_z + correction
        point_loss = criterion(pred[train_t], target[train_t])
        pair_loss = criterion(pred[high_t] - pred[low_t], target[high_t] - target[low_t]) if len(pair_low) else torch.zeros((), device=device)
        loss = point_loss + pair_weight * pair_loss
        loss.backward(); nn.utils.clip_grad_norm_(adapter.parameters(), 1.0); optimizer.step()
        adapter.eval()
        with torch.no_grad():
            correction = adapter(desc, cond); pred = base_z + correction
            val_point = torch.mean(torch.abs(pred[val_t] - target[val_t]))
            val_slope = torch.mean(torch.abs((pred[val_t] - pred[val_anchor_t]) - (target[val_t] - target[val_anchor_t])))
            objective = float((val_point + val_slope).cpu())
        history.append({"epoch": epoch + 1, "train_point": float(point_loss.detach().cpu()), "train_pair": float(pair_loss.detach().cpu()), "val_objective_z": objective})
        if objective < best - 1e-5:
            best, best_epoch = objective, epoch + 1
            best_state = {k: v.detach().cpu().clone() for k, v in adapter.state_dict().items()}
            patience = 0
        else:
            patience += 1
        if patience >= args.adapter_patience:
            break
    adapter.load_state_dict(best_state)
    adapter.eval()
    with torch.no_grad():
        prediction = (base_z + adapter(desc, cond)).detach().cpu().numpy()
    return prediction, {"best_epoch": best_epoch, "best_val_objective_z": best, "history": history}


def run_cte_ablation(base, full: pd.DataFrame, args, device):
    frame = full[full.task.eq("CTE_FIXED_T")].reset_index(drop=True)
    train_idx, val_idx, test_idx, val_groups, test_groups = cte_endpoint_split(frame)
    val_anchor = endpoint_anchors(frame, val_idx, train_idx)
    test_anchor = endpoint_anchors(frame, test_idx, train_idx)
    pair_low, pair_high = build_pairs(frame, train_idx)
    compositions, vocab, tasks, max_tokens = prepare(frame, base)
    transform = Transform()
    pair_weights = [0.0, 0.05, 0.2, 0.5]
    seed_artifacts = []
    for seed in SEEDS:
        model, pre, encoded, train_report = train_model(base, frame, train_idx, val_idx, compositions, vocab, tasks, max_tokens, transform, seed, args, device)
        all_idx = np.arange(len(frame), dtype=np.int64)
        base_z = predict_z(base, model, encoded, all_idx, args.eval_batch_size, device)
        candidates = {}
        for weight in pair_weights:
            pred_z, adapter_report = fit_adapter(encoded, base_z, train_idx, val_idx, val_anchor, pair_low, pair_high, weight, seed, args, device)
            val_pred = pre.decode(frame, val_idx, pred_z[val_idx]); val_anchor_pred = pre.decode(frame, val_anchor, pred_z[val_anchor])
            candidates[str(weight)] = {"pred_z": pred_z, "training": adapter_report, "validation": endpoint_metrics(frame, val_idx, val_pred, val_anchor, val_anchor_pred)}
        base_val = pre.decode(frame, val_idx, base_z[val_idx]); base_val_anchor = pre.decode(frame, val_anchor, base_z[val_anchor])
        seed_artifacts.append({"seed": seed, "pre": pre, "base_z": base_z, "base_training": train_report, "base_validation": endpoint_metrics(frame, val_idx, base_val, val_anchor, base_val_anchor), "candidates": candidates})
        print(json.dumps({"stage": "cte", "seed": seed, "base_validation": seed_artifacts[-1]["base_validation"], "candidate_validation": {k: v["validation"] for k, v in candidates.items()}}, ensure_ascii=False), flush=True)
    candidate_summary = {}
    for weight in pair_weights:
        key = str(weight)
        vals = [item["candidates"][key]["validation"] for item in seed_artifacts]
        candidate_summary[key] = {
            "mean_endpoint_MAE": float(np.mean([v["MAE"] for v in vals])),
            "mean_slope_MAE_per_100C": float(np.mean([v["slope_MAE_per_100C"] for v in vals])),
            "mean_direction_accuracy": float(np.mean([v["slope_direction_accuracy"] for v in vals])),
        }
    baseline_val_mae = float(np.mean([item["base_validation"]["MAE"] for item in seed_artifacts]))
    eligible_pair = [w for w in pair_weights if w > 0 and candidate_summary[str(w)]["mean_endpoint_MAE"] <= baseline_val_mae * 1.02]
    if eligible_pair:
        selected_weight = min(eligible_pair, key=lambda w: candidate_summary[str(w)]["mean_slope_MAE_per_100C"])
        selection_reason = "minimum validation slope MAE among positive pair weights with endpoint MAE <= baseline * 1.02"
    else:
        selected_weight = min([w for w in pair_weights if w > 0], key=lambda w: candidate_summary[str(w)]["mean_endpoint_MAE"])
        selection_reason = "no pair weight met point guardrail; selected minimum validation endpoint MAE for diagnostic test only"
    methods = {"baseline_point_only": None, "adapter_point_only": 0.0, "adapter_pair_response_selected": selected_weight}
    method_test_predictions = {name: [] for name in methods}
    method_anchor_predictions = {name: [] for name in methods}
    per_seed_test = {name: [] for name in methods}
    prediction_rows = []
    for item in seed_artifacts:
        pre = item["pre"]
        for method, weight in methods.items():
            pred_z = item["base_z"] if weight is None else item["candidates"][str(weight)]["pred_z"]
            endpoint_pred = pre.decode(frame, test_idx, pred_z[test_idx])
            anchor_pred = pre.decode(frame, test_anchor, pred_z[test_anchor])
            method_test_predictions[method].append(endpoint_pred); method_anchor_predictions[method].append(anchor_pred)
            per_seed_test[method].append({"seed": item["seed"], **endpoint_metrics(frame, test_idx, endpoint_pred, test_anchor, anchor_pred)})
    ensemble_test = {}
    for method in methods:
        endpoint_pred = np.mean(np.stack(method_test_predictions[method]), axis=0)
        anchor_pred = np.mean(np.stack(method_anchor_predictions[method]), axis=0)
        ensemble_test[method] = endpoint_metrics(frame, test_idx, endpoint_pred, test_anchor, anchor_pred)
        part = frame.iloc[test_idx][["source_kod", "canonical_composition", "task", "condition_temperature_C", "target"]].copy()
        part["anchor_row_index"] = test_anchor
        part["prediction"] = endpoint_pred; part["method"] = method; prediction_rows.append(part)
    result = {
        "split": {"rows": len(frame), "train_rows": len(train_idx), "validation_endpoint_rows": len(val_idx), "test_endpoint_rows": len(test_idx), "validation_groups": len(val_groups), "test_groups": len(test_groups), "training_adjacent_pairs_deltaT_ge_10C": len(pair_low)},
        "selection": {"pair_weight_candidates": pair_weights, "validation_summary": candidate_summary, "baseline_mean_validation_endpoint_MAE": baseline_val_mae, "selected_pair_weight": selected_weight, "reason": selection_reason},
        "per_seed_test": per_seed_test,
        "ensemble_test": ensemble_test,
    }
    return result, pd.concat(prediction_rows, ignore_index=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-script", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--adapter-epochs", type=int, default=250)
    parser.add_argument("--adapter-patience", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--eval-batch-size", type=int, default=2048)
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--heads", type=int, default=8)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=2e-4)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    device = torch.device("cuda")
    started = time.time()
    base = load_base_module(args.base_script)
    full = pd.read_csv(args.data, low_memory=False)
    full["source_kod"] = full.source_kod.astype(str)
    report = {
        "status": "offline_ablation_not_deployed",
        "data": str(args.data),
        "data_sha256": sha256(args.data),
        "base_script": str(args.base_script),
        "base_script_sha256": sha256(args.base_script),
        "seeds": SEEDS,
        "protocol": "validation-only model/weight selection; frozen test evaluated after selection",
    }
    positive, positive_predictions = run_positive_ablation(base, full, args, device)
    report["positive_output_ablation"] = positive
    poisson, poisson_predictions = run_poisson_ablation(base, full, args, device)
    report["poisson_bound_ablation"] = poisson
    cte, cte_predictions = run_cte_ablation(base, full, args, device)
    report["cte_response_ablation"] = cte
    report["elapsed_seconds"] = time.time() - started
    report_path = args.output_dir / "GLASS_CONSTRAINT_ABLATION_RESULTS.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, frame in [("positive_test_predictions.csv.gz", positive_predictions), ("poisson_test_predictions.csv.gz", poisson_predictions), ("cte_endpoint_test_predictions.csv.gz", cte_predictions)]:
        with gzip.open(args.output_dir / name, "wt", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
    (args.output_dir / "COMPLETED.json").write_text(json.dumps({"complete": True, "report": str(report_path), "elapsed_seconds": report["elapsed_seconds"]}, indent=2), encoding="utf-8")
    print(json.dumps({"complete": True, "report": str(report_path), "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
