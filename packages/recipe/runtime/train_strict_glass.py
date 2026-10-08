#!/usr/bin/env python3
"""Strict grouped-CV training for a multi-task oxide-glass Transformer."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import os
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler


POSITIVE_LOG_TASKS = {"TG", "DENSITY_20C", "YOUNG_MODULUS", "THERMAL_CONDUCTIVITY"}
BOUNDED_TASKS = {"POISSON_RATIO": (-0.2, 0.6)}
ALKALI = {"Li2O", "Na2O", "K2O", "Rb2O", "Cs2O"}
ALKALINE_EARTH = {"MgO", "CaO", "SrO", "BaO"}
NETWORK_FORMERS = {"SiO2", "B2O3", "P2O5", "GeO2"}
INTERMEDIATES = {"Al2O3", "TiO2", "ZrO2", "Nb2O5", "Ta2O5"}
EXPLICIT_SPECIES = ["SiO2", "B2O3", "Al2O3", "Na2O", "K2O", "Li2O", "CaO", "MgO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5", "PbO"]


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


def target_forward(task: str, values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if task in POSITIVE_LOG_TASKS:
        return np.log(values)
    if task in BOUNDED_TASKS:
        lo, hi = BOUNDED_TASKS[task]
        scaled = np.clip((values - lo) / (hi - lo), 1e-5, 1.0 - 1e-5)
        return np.log(scaled / (1.0 - scaled))
    return values


def target_inverse(task: str, values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if task in POSITIVE_LOG_TASKS:
        return np.exp(np.clip(values, -20.0, 20.0))
    if task in BOUNDED_TASKS:
        lo, hi = BOUNDED_TASKS[task]
        sigmoid = 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))
        return lo + (hi - lo) * sigmoid
    return values


def parse_compositions(frame: pd.DataFrame):
    cache: dict[str, list[tuple[str, float]]] = {}
    for key, raw in frame[["canonical_composition", "composition_json"]].drop_duplicates("canonical_composition").itertuples(index=False):
        cache[key] = [(str(name), float(value)) for name, value in json.loads(raw)]
    return cache


def descriptor_vector(components: list[tuple[str, float]]) -> list[float]:
    values = dict(components)
    fractions = np.asarray([value for _, value in components], dtype=np.float64)
    entropy = float(-(fractions * np.log(np.clip(fractions, 1e-12, None))).sum())
    output = [
        float(len(components)), entropy, float(fractions.max()),
        sum(values.get(name, 0.0) for name in NETWORK_FORMERS),
        sum(values.get(name, 0.0) for name in INTERMEDIATES),
        sum(values.get(name, 0.0) for name in ALKALI),
        sum(values.get(name, 0.0) for name in ALKALINE_EARTH),
    ]
    output.extend(values.get(name, 0.0) for name in EXPLICIT_SPECIES)
    return output


@dataclass
class Encoding:
    species_ids: np.ndarray
    fractions: np.ndarray
    descriptors: np.ndarray
    task_ids: np.ndarray
    condition: np.ndarray
    condition_mask: np.ndarray
    target_z: np.ndarray


class Preprocessor:
    def __init__(self, frame: pd.DataFrame, train_idx: np.ndarray, compositions: dict[str, list[tuple[str, float]]], vocab: dict[str, int], tasks: list[str], max_tokens: int):
        self.vocab = vocab
        self.tasks = tasks
        self.task_to_id = {task: index for index, task in enumerate(tasks)}
        self.max_tokens = max_tokens
        self.compositions = compositions
        train = frame.iloc[train_idx]
        self.target_stats = {}
        for task, group in train.groupby("task"):
            transformed = target_forward(str(task), group.target.to_numpy(dtype=float))
            std = float(transformed.std())
            self.target_stats[str(task)] = {"mean": float(transformed.mean()), "std": max(std, 1e-6)}
        # Fit the numeric scaling on training compositions only. Vocabulary is
        # fixed from the audited species dictionary; no held-out values or
        # targets enter the fitted statistics.
        training_keys = sorted(train.canonical_composition.unique())
        unique_desc = np.asarray([descriptor_vector(compositions[key]) for key in training_keys], dtype=np.float64)
        self.descriptor_training_keys = training_keys
        self.descriptor_mean = unique_desc.mean(axis=0)
        self.descriptor_std = np.maximum(unique_desc.std(axis=0), 1e-8)
        finite_temp = pd.to_numeric(train.condition_temperature_C, errors="coerce").dropna().to_numpy(dtype=float)
        self.condition_mean = float(finite_temp.mean()) if len(finite_temp) else 0.0
        self.condition_std = max(float(finite_temp.std()), 1.0) if len(finite_temp) else 1.0

    def encode(self, frame: pd.DataFrame) -> Encoding:
        n = len(frame)
        ids = np.zeros((n, self.max_tokens), dtype=np.int64)
        fractions = np.zeros((n, self.max_tokens), dtype=np.float32)
        desc = np.zeros((n, 7 + len(EXPLICIT_SPECIES)), dtype=np.float32)
        task_ids = np.zeros(n, dtype=np.int64)
        condition = np.zeros(n, dtype=np.float32)
        condition_mask = np.zeros(n, dtype=np.float32)
        target_z = np.zeros(n, dtype=np.float32)
        for row_index, row in enumerate(frame.itertuples(index=False)):
            components = self.compositions[row.canonical_composition]
            if len(components) > self.max_tokens:
                raise ValueError(f"composition exceeds max tokens: {row.canonical_composition}")
            for token_index, (name, value) in enumerate(components):
                ids[row_index, token_index] = self.vocab[name]
                fractions[row_index, token_index] = value
            desc[row_index] = (np.asarray(descriptor_vector(components)) - self.descriptor_mean) / self.descriptor_std
            task = str(row.task)
            task_ids[row_index] = self.task_to_id[task]
            temp = float(row.condition_temperature_C) if pd.notna(row.condition_temperature_C) else 0.0
            condition_mask[row_index] = float(pd.notna(row.condition_temperature_C))
            condition[row_index] = (temp - self.condition_mean) / self.condition_std if condition_mask[row_index] else 0.0
            transformed = target_forward(task, np.asarray([float(row.target)]))[0]
            stats = self.target_stats[task]
            target_z[row_index] = (transformed - stats["mean"]) / stats["std"]
        return Encoding(ids, fractions, desc, task_ids, condition, condition_mask, target_z)

    def state(self) -> dict[str, object]:
        return {
            "vocab": self.vocab, "tasks": self.tasks, "max_tokens": self.max_tokens,
            "target_stats": self.target_stats,
            "descriptor_mean": self.descriptor_mean.tolist(),
            "descriptor_std": self.descriptor_std.tolist(),
            "condition_mean": self.condition_mean, "condition_std": self.condition_std,
            "descriptor_names": ["n_components", "mixing_entropy", "max_fraction", "network_former_fraction", "intermediate_fraction", "alkali_fraction", "alkaline_earth_fraction"] + EXPLICIT_SPECIES,
            "descriptor_fit_scope": "unique_training_compositions_only",
            "descriptor_training_compositions": len(self.descriptor_training_keys),
            "descriptor_training_keys_sha256": hashlib.sha256("\n".join(self.descriptor_training_keys).encode()).hexdigest(),
        }


class IndexedDataset(Dataset):
    def __init__(self, encoded: Encoding, indices: np.ndarray):
        self.encoded = encoded
        self.indices = np.asarray(indices, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, item: int):
        index = self.indices[item]
        return {
            "species": torch.from_numpy(self.encoded.species_ids[index]),
            "fractions": torch.from_numpy(self.encoded.fractions[index]),
            "descriptors": torch.from_numpy(self.encoded.descriptors[index]),
            "task": torch.tensor(self.encoded.task_ids[index], dtype=torch.long),
            "condition": torch.tensor(self.encoded.condition[index], dtype=torch.float32),
            "condition_mask": torch.tensor(self.encoded.condition_mask[index], dtype=torch.float32),
            "target_z": torch.tensor(self.encoded.target_z[index], dtype=torch.float32),
            "row_index": torch.tensor(index, dtype=torch.long),
        }


class OxideGlassTransformer(nn.Module):
    def __init__(self, n_species: int, n_tasks: int, n_descriptors: int, d_model: int = 128, n_layers: int = 3, n_heads: int = 8, dropout: float = 0.1):
        super().__init__()
        self.species_embedding = nn.Embedding(n_species + 1, d_model, padding_idx=0)
        self.fraction_encoder = nn.Sequential(nn.Linear(3, d_model), nn.SiLU(), nn.Linear(d_model, d_model))
        layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4, dropout=dropout, activation="gelu", batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=n_layers, norm=nn.LayerNorm(d_model))
        self.task_embedding = nn.Embedding(n_tasks, 32)
        self.numeric = nn.Sequential(nn.Linear(n_descriptors + 2, 64), nn.SiLU(), nn.LayerNorm(64), nn.Linear(64, 64), nn.SiLU())
        self.head = nn.Sequential(nn.Linear(d_model + 32 + 64, 128), nn.SiLU(), nn.Dropout(dropout), nn.Linear(128, 64), nn.SiLU(), nn.Linear(64, 1))

    def forward(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        species = batch["species"]
        fractions = batch["fractions"]
        mask = species.eq(0)
        frac_features = torch.stack([fractions, torch.sqrt(torch.clamp(fractions, min=0.0)), torch.log1p(fractions * 100.0)], dim=-1)
        x = self.species_embedding(species) + self.fraction_encoder(frac_features)
        x = self.encoder(x, src_key_padding_mask=mask)
        weights = fractions.masked_fill(mask, 0.0)
        pooled = (x * weights.unsqueeze(-1)).sum(dim=1) / weights.sum(dim=1, keepdim=True).clamp_min(1e-8)
        numeric_input = torch.cat([batch["descriptors"], batch["condition"].unsqueeze(1), batch["condition_mask"].unsqueeze(1)], dim=1)
        numeric = self.numeric(numeric_input)
        task = self.task_embedding(batch["task"])
        return self.head(torch.cat([pooled, task, numeric], dim=1)).squeeze(1)


def move(batch: dict[str, torch.Tensor], device: torch.device):
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


def metrics(actual: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    return {
        "n": int(len(actual)),
        "MAE": float(mean_absolute_error(actual, prediction)),
        "RMSE": float(math.sqrt(mean_squared_error(actual, prediction))),
        "R2": float(r2_score(actual, prediction)) if len(actual) >= 2 else float("nan"),
    }


def decode_predictions(frame: pd.DataFrame, indices: np.ndarray, pred_z: np.ndarray, pre: Preprocessor) -> np.ndarray:
    output = np.zeros(len(indices), dtype=np.float64)
    rows = frame.iloc[indices]
    for task in pre.tasks:
        mask = rows.task.to_numpy() == task
        if not mask.any():
            continue
        stats = pre.target_stats[task]
        transformed = pred_z[mask] * stats["std"] + stats["mean"]
        output[mask] = target_inverse(task, transformed)
    return output


@torch.no_grad()
def predict(model: nn.Module, loader: DataLoader, device: torch.device):
    model.eval()
    preds, rows = [], []
    for batch in loader:
        batch = move(batch, device)
        preds.append(model(batch).detach().cpu().numpy())
        rows.append(batch["row_index"].detach().cpu().numpy())
    return np.concatenate(rows), np.concatenate(preds)


def task_sampler(frame: pd.DataFrame, indices: np.ndarray, seed: int):
    labels = frame.iloc[indices].task.astype(str).tolist()
    counts = Counter(labels)
    weights = np.asarray([1.0 / math.sqrt(counts[label]) for label in labels], dtype=np.float64)
    return WeightedRandomSampler(torch.from_numpy(weights), num_samples=len(weights), replacement=True, generator=torch.Generator().manual_seed(seed))


def inner_split(frame: pd.DataFrame, pool_idx: np.ndarray, regime: str, seed: int):
    groups = frame.iloc[pool_idx].canonical_composition if regime == "composition" else frame.iloc[pool_idx].source_kod.astype(str)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.10, random_state=seed)
    rel_train, rel_val = next(splitter.split(pool_idx, groups=groups))
    train_idx, val_idx = pool_idx[rel_train], pool_idx[rel_val]
    if regime == "source":
        val_comps = set(frame.iloc[val_idx].canonical_composition)
        keep = ~frame.iloc[train_idx].canonical_composition.isin(val_comps).to_numpy()
        train_idx = train_idx[keep]
    return train_idx, val_idx


def outer_splits(frame: pd.DataFrame, regime: str, folds: int):
    groups = frame.canonical_composition if regime == "composition" else frame.source_kod.astype(str)
    splitter = GroupKFold(n_splits=folds)
    for train_rel, test_idx in splitter.split(frame, groups=groups):
        train_idx = np.asarray(train_rel, dtype=np.int64)
        test_idx = np.asarray(test_idx, dtype=np.int64)
        if regime == "source":
            test_comps = set(frame.iloc[test_idx].canonical_composition)
            keep = ~frame.iloc[train_idx].canonical_composition.isin(test_comps).to_numpy()
            train_idx = train_idx[keep]
        yield train_idx, test_idx


def fold_metrics(frame: pd.DataFrame, indices: np.ndarray, predictions: np.ndarray):
    work = frame.iloc[indices].copy()
    work["prediction"] = predictions
    result = {"overall": metrics(work.target.to_numpy(float), work.prediction.to_numpy(float)), "by_task": {}, "by_task_evidence": {}}
    for task, group in work.groupby("task"):
        result["by_task"][str(task)] = metrics(group.target.to_numpy(float), group.prediction.to_numpy(float))
    for (task, tier), group in work.groupby(["task", "source_evidence_tier"]):
        result["by_task_evidence"][f"{task}/{tier}"] = metrics(group.target.to_numpy(float), group.prediction.to_numpy(float))
    return result


def train_fold(frame: pd.DataFrame, compositions, vocab, tasks, max_tokens, pool_idx, test_idx, regime, fold, args, device):
    seed = args.seed + fold * 101
    seed_everything(seed)
    train_idx, val_idx = inner_split(frame, pool_idx, regime, seed)
    pre = Preprocessor(frame, train_idx, compositions, vocab, tasks, max_tokens)
    encoded = pre.encode(frame)
    train_loader = DataLoader(IndexedDataset(encoded, train_idx), batch_size=args.batch_size, sampler=task_sampler(frame, train_idx, seed), num_workers=0, pin_memory=True)
    val_loader = DataLoader(IndexedDataset(encoded, val_idx), batch_size=args.eval_batch_size, shuffle=False, num_workers=0, pin_memory=True)
    test_loader = DataLoader(IndexedDataset(encoded, test_idx), batch_size=args.eval_batch_size, shuffle=False, num_workers=0, pin_memory=True)
    model = OxideGlassTransformer(len(vocab), len(tasks), encoded.descriptors.shape[1], args.d_model, args.layers, args.heads, args.dropout).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.max_epochs, eta_min=args.lr * 0.08)
    criterion = nn.SmoothL1Loss(beta=0.5)
    best_loss = float("inf")
    best_state = None
    best_epoch = -1
    patience = 0
    history = []
    for epoch in range(args.max_epochs):
        model.train()
        losses = []
        for batch in train_loader:
            batch = move(batch, device)
            optimizer.zero_grad(set_to_none=True)
            pred = model(batch)
            loss = criterion(pred, batch["target_z"])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step()
        _, val_z = predict(model, val_loader, device)
        val_target = encoded.target_z[val_idx]
        val_loss = float(np.mean(np.abs(val_z - val_target)))
        history.append({"epoch": epoch + 1, "train_smooth_l1": float(np.mean(losses)), "val_normalized_MAE": val_loss})
        if val_loss < best_loss - 1e-4:
            best_loss = val_loss
            best_epoch = epoch + 1
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
        if patience >= args.patience:
            break
    model.load_state_dict(best_state)
    row_order, pred_z = predict(model, test_loader, device)
    if not np.array_equal(row_order, test_idx):
        raise RuntimeError("test prediction order mismatch")
    predictions = decode_predictions(frame, test_idx, pred_z, pre)
    fold_dir = args.output_dir / f"fold_{fold}"
    fold_dir.mkdir()
    torch.save({"state_dict": {key:value.detach().cpu() for key,value in model.state_dict().items()},
                "preprocessor": pre.state(), "train_indices": train_idx.tolist(),
                "validation_indices": val_idx.tolist(), "test_indices": test_idx.tolist(),
                "seed": seed, "best_epoch": best_epoch}, fold_dir / "checkpoint.pt")
    return {
        "fold": fold, "train_rows": len(train_idx), "validation_rows": len(val_idx), "test_rows": len(test_idx),
        "train_sources": int(frame.iloc[train_idx].source_kod.nunique()), "test_sources": int(frame.iloc[test_idx].source_kod.nunique()),
        "train_compositions": int(frame.iloc[train_idx].canonical_composition.nunique()), "test_compositions": int(frame.iloc[test_idx].canonical_composition.nunique()),
        "composition_overlap_train_test": int(len(set(frame.iloc[train_idx].canonical_composition) & set(frame.iloc[test_idx].canonical_composition))),
        "source_overlap_train_test": int(len(set(frame.iloc[train_idx].source_kod.astype(str)) & set(frame.iloc[test_idx].source_kod.astype(str)))),
        "best_epoch": best_epoch, "best_inner_validation_normalized_MAE": best_loss, "history": history,
        "descriptor_fit_scope": "unique_training_compositions_only",
        "descriptor_training_compositions": len(pre.descriptor_training_keys),
        "descriptor_scaling_overlap_heldout": len(set(pre.descriptor_training_keys) & set(frame.iloc[test_idx].canonical_composition)),
        "metrics": fold_metrics(frame, test_idx, predictions),
    }, test_idx, predictions, best_epoch


def aggregate_oof(frame: pd.DataFrame, oof: pd.DataFrame):
    work = frame.iloc[oof.row_index.to_numpy(dtype=int)].copy()
    work["prediction"] = oof.prediction.to_numpy(float)
    return fold_metrics(work.reset_index(drop=True), np.arange(len(work)), work.prediction.to_numpy(float))


def fit_full(frame, compositions, vocab, tasks, max_tokens, epochs, args, device):
    seed_everything(args.seed + 9001)
    all_idx = np.arange(len(frame), dtype=np.int64)
    pre = Preprocessor(frame, all_idx, compositions, vocab, tasks, max_tokens)
    encoded = pre.encode(frame)
    loader = DataLoader(IndexedDataset(encoded, all_idx), batch_size=args.batch_size, sampler=task_sampler(frame, all_idx, args.seed + 9001), num_workers=0, pin_memory=True)
    model = OxideGlassTransformer(len(vocab), len(tasks), encoded.descriptors.shape[1], args.d_model, args.layers, args.heads, args.dropout).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=args.lr * 0.08)
    criterion = nn.SmoothL1Loss(beta=0.5)
    history = []
    for epoch in range(epochs):
        model.train(); losses = []
        for batch in loader:
            batch = move(batch, device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch), batch["target_z"])
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step(); history.append({"epoch": epoch + 1, "train_smooth_l1": float(np.mean(losses))})
    return model, pre, history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--regime", choices=["composition", "source"], required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--max-epochs", type=int, default=35)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--eval-batch-size", type=int, default=2048)
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--heads", type=int, default=8)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=2e-4)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--fit-full", action="store_true")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this training run")
    device = torch.device("cuda")
    started = time.time()
    frame = pd.read_csv(args.data, low_memory=False)
    frame["source_kod"] = frame.source_kod.astype(str)
    compositions = parse_compositions(frame)
    species = sorted({name for values in compositions.values() for name, _ in values})
    vocab = {name: index + 1 for index, name in enumerate(species)}
    tasks = sorted(frame.task.unique().tolist())
    max_tokens = max(len(value) for value in compositions.values())
    oof_parts = []
    fold_reports = []
    best_epochs = []
    for fold, (pool_idx, test_idx) in enumerate(outer_splits(frame, args.regime, args.folds), start=1):
        report, indices, predictions, best_epoch = train_fold(frame, compositions, vocab, tasks, max_tokens, pool_idx, test_idx, args.regime, fold, args, device)
        fold_reports.append(report); best_epochs.append(best_epoch)
        oof_parts.append(pd.DataFrame({"row_index": indices, "fold": fold, "prediction": predictions}))
        print(json.dumps({"fold_complete": fold, "best_epoch": best_epoch, "metrics": report["metrics"]["by_task"]}, ensure_ascii=False), flush=True)
    oof = pd.concat(oof_parts, ignore_index=True).sort_values("row_index")
    with gzip.open(args.output_dir / "oof_predictions.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        oof.to_csv(handle, index=False)
    summary = {
        "model": "oxide_glass_multitask_transformer_v1_strict_20260928",
        "status": "offline_research_candidate_not_deployed",
        "regime": args.regime,
        "folds": args.folds,
        "split_policy": "canonical composition held out" if args.regime == "composition" else "source KOD held out and train rows sharing test compositions purged",
        "inner_validation_policy": "grouped inner 10% split; outer test untouched until selected epoch",
        "descriptor_fit_scope": "unique_training_compositions_only",
        "species_dictionary_policy": "fixed audited oxide vocabulary; no held-out targets or scaling statistics",
        "source_code_sha256": sha256(Path(__file__)),
        "data": str(args.data), "data_sha256": sha256(args.data), "rows": len(frame),
        "canonical_compositions": int(frame.canonical_composition.nunique()), "source_groups": int(frame.source_kod.nunique()),
        "tasks": tasks, "species_vocab_size": len(vocab), "max_tokens": max_tokens,
        "architecture": {"d_model": args.d_model, "layers": args.layers, "heads": args.heads, "dropout": args.dropout, "physics_outputs": {"positive_log_tasks": sorted(POSITIVE_LOG_TASKS), "bounded_tasks": BOUNDED_TASKS}},
        "task_balancing": "inverse square root task-frequency weighted sampling",
        "fold_reports": fold_reports,
        "oof_metrics": aggregate_oof(frame, oof),
        "median_best_epoch": int(round(float(np.median(best_epochs)))),
        "elapsed_seconds_before_full_fit": time.time() - started,
    }
    if args.fit_full:
        epochs = summary["median_best_epoch"]
        model, pre, history = fit_full(frame, compositions, vocab, tasks, max_tokens, epochs, args, device)
        checkpoint = {
            "model_name": summary["model"], "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
            "constructor": {"n_species": len(vocab), "n_tasks": len(tasks), "n_descriptors": 7 + len(EXPLICIT_SPECIES), "d_model": args.d_model, "n_layers": args.layers, "n_heads": args.heads, "dropout": args.dropout},
            "preprocessor": pre.state(), "target_transforms": {"positive_log_tasks": sorted(POSITIVE_LOG_TASKS), "bounded_tasks": BOUNDED_TASKS},
            "training_data_sha256": summary["data_sha256"], "training_rows": len(frame), "epochs": epochs,
            "limitations": ["SciGlass unmarked values are database observations, not asserted confirmed experiments", "commercial deployment requires license review", "not calibrated for current 0-300C or 25-300C online CTE targets without matching interval metadata"],
        }
        checkpoint_path = args.output_dir / "oxide_glass_multitask_transformer_v1_strict.pt"
        torch.save(checkpoint, checkpoint_path)
        summary["full_fit"] = {"epochs": epochs, "history": history, "checkpoint": str(checkpoint_path), "checkpoint_sha256": sha256(checkpoint_path)}
    summary["elapsed_seconds_total"] = time.time() - started
    (args.output_dir / "training_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"complete": True, "report": str(args.output_dir / "training_report.json"), "oof_metrics": summary["oof_metrics"], "elapsed_seconds": summary["elapsed_seconds_total"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
