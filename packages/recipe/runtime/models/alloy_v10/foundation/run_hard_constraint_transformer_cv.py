"""Transformer-only alloy benchmark with a hard tensile-order parameterization.

No tree predictions, blending, residual correction, or teacher distillation are
used.  The joint output is parameterized as UTS = YS + softplus(delta), which
guarantees UTS >= YS.  Five strict composition folds and three random seeds per
fold quantify and reduce neural-model variance.
"""
from __future__ import annotations

import copy
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, WeightedRandomSampler

from run_family_adapter_multitask import FractionEncoder
from run_physics_informed_cv import (
    IndexedDataset,
    PhysicsPreprocessor,
    add_explicit_physics_descriptors,
    full_metrics,
)


ROOT = Path("/data/se42/experiments/alloy_foundation_v0_20260916")
DATA = ROOT / "unified_alloy_tensile_strength_v0.csv"
OUT = ROOT / "hard_constraint_transformer_cv_v3"

BASE_SEED = 20260917
FOLD_COUNT = 5
SEEDS_PER_FOLD = 3
MAX_EPOCHS = 120
PATIENCE = 20
BATCH_SIZE = 256
MAX_ELEMENTS = 20
YS_OUTPUT = 0
UTS_OUTPUT = 1


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


class HardConstraintTransformer(nn.Module):
    def __init__(
        self,
        *,
        element_features: np.ndarray,
        n_numeric: int,
        n_domains: int,
        n_processes: int,
        ys_mean: np.ndarray,
        ys_std: np.ndarray,
        delta_scale: np.ndarray,
        d_model: int = 128,
    ):
        super().__init__()
        element_tensor = torch.as_tensor(element_features, dtype=torch.float32)
        self.register_buffer("element_features", element_tensor)
        self.register_buffer("ys_mean", torch.as_tensor(ys_mean, dtype=torch.float32))
        self.register_buffer("ys_std", torch.as_tensor(ys_std, dtype=torch.float32))
        self.register_buffer("delta_scale", torch.as_tensor(delta_scale, dtype=torch.float32))
        self.element_projection = nn.Sequential(
            nn.Linear(element_tensor.shape[1], d_model), nn.LayerNorm(d_model), nn.SiLU()
        )
        self.learned_element = nn.Embedding(element_tensor.shape[0], d_model, padding_idx=0)
        self.fraction_encoder = FractionEncoder(d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=4,
            dim_feedforward=384,
            dropout=0.12,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(layer, num_layers=3)
        self.numeric_encoder = nn.Sequential(
            nn.Linear(n_numeric, 96), nn.LayerNorm(96), nn.SiLU(), nn.Dropout(0.10),
            nn.Linear(96, 64),
        )
        self.process_embedding = nn.Embedding(n_processes + 1, 24, padding_idx=0)
        self.domain_embedding = nn.Embedding(n_domains, 16)
        self.fusion = nn.Sequential(
            nn.Linear(d_model + 64 + 24 + 16, 160), nn.LayerNorm(160), nn.SiLU(),
            nn.Dropout(0.12), nn.Linear(160, 128), nn.LayerNorm(128), nn.SiLU(),
        )
        self.ys_head = nn.Sequential(
            nn.Linear(128, 64), nn.SiLU(), nn.Dropout(0.08), nn.Linear(64, 1)
        )
        self.delta_head = nn.Sequential(
            nn.Linear(128, 64), nn.SiLU(), nn.Dropout(0.08), nn.Linear(64, 1)
        )

    def forward(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        ids = batch["element_ids"]
        fractions = batch["fractions"]
        padding = ids.eq(0)
        fixed = self.element_projection(self.element_features[ids])
        x = fixed + self.learned_element(ids) + self.fraction_encoder(fractions)
        x = self.transformer(x, src_key_padding_mask=padding)
        weight = fractions.unsqueeze(-1)
        composition = (x * weight).sum(dim=1) / weight.sum(dim=1).clamp_min(1e-8)
        fused = self.fusion(torch.cat([
            composition,
            self.numeric_encoder(batch["numeric"]),
            self.process_embedding(batch["process"]),
            self.domain_embedding(batch["domain"]),
        ], dim=1))
        domain = batch["domain"]
        ys = self.ys_mean[domain] + self.ys_std[domain] * self.ys_head(fused).squeeze(1)
        delta = torch.nn.functional.softplus(self.delta_head(fused).squeeze(1)) * self.delta_scale[domain]
        uts = ys + delta
        return torch.stack([ys, uts], dim=1)


def domain_scales(pre: PhysicsPreprocessor) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ys_mean, ys_std, delta_scale = [], [], []
    for domain in pre.vocab.domains:
        mean_y, std_y = pre.target_stats[("YS", domain)]
        mean_u, _ = pre.target_stats[("UTS", domain)]
        ys_mean.append(mean_y)
        ys_std.append(std_y)
        delta_scale.append(float(np.clip(mean_u - mean_y, 40.0, 300.0)))
    return (
        np.asarray(ys_mean, dtype=np.float32),
        np.asarray(ys_std, dtype=np.float32),
        np.asarray(delta_scale, dtype=np.float32),
    )


def make_loaders(
    frame: pd.DataFrame,
    encoded: dict[str, np.ndarray],
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    seed: int,
) -> tuple[DataLoader, DataLoader, DataLoader]:
    pairs = [f"{frame.iloc[i].domain}/{frame.iloc[i].task}" for i in train_idx]
    counts = pd.Series(pairs).value_counts().to_dict()
    weights = np.asarray([1.0 / math.sqrt(counts[pair]) for pair in pairs], dtype=np.float64)
    sampler = WeightedRandomSampler(
        weights,
        num_samples=len(weights),
        replacement=True,
        generator=torch.Generator().manual_seed(seed),
    )
    return (
        DataLoader(IndexedDataset(encoded, train_idx), batch_size=BATCH_SIZE, sampler=sampler,
                   num_workers=0, pin_memory=True),
        DataLoader(IndexedDataset(encoded, val_idx), batch_size=512, shuffle=False,
                   num_workers=0, pin_memory=True),
        DataLoader(IndexedDataset(encoded, test_idx), batch_size=512, shuffle=False,
                   num_workers=0, pin_memory=True),
    )


@torch.no_grad()
def predict(
    model: HardConstraintTransformer,
    loader: DataLoader,
    frame: pd.DataFrame,
    device: torch.device,
) -> pd.DataFrame:
    model.eval()
    rows, outputs = [], []
    for batch in loader:
        batch = move_batch(batch, device)
        physical = model(batch)
        rows.append(batch["row_index"].cpu().numpy())
        outputs.append(physical.cpu().numpy())
    row_idx = np.concatenate(rows)
    both = np.concatenate(outputs)
    output_index = frame.iloc[row_idx].task.map({"YS": YS_OUTPUT, "UTS": UTS_OUTPUT}).to_numpy(
        dtype=np.int64
    )
    selected = both[np.arange(len(both)), output_index]
    result = frame.iloc[row_idx][
        ["record_id", "composition_key", "domain", "task", "target"]
    ].copy()
    result["prediction"] = selected
    result["predicted_YS"] = both[:, YS_OUTPUT]
    result["predicted_UTS"] = both[:, UTS_OUTPUT]
    result["order_violation_mpa"] = result.predicted_YS - result.predicted_UTS
    result["row_index"] = row_idx
    return result.reset_index(drop=True)


def train_one(
    *,
    fold: int,
    seed_index: int,
    seed: int,
    frame: pd.DataFrame,
    pre: PhysicsPreprocessor,
    encoded: dict[str, np.ndarray],
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    device: torch.device,
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    seed_everything(seed)
    train_loader, val_loader, test_loader = make_loaders(
        frame, encoded, train_idx, val_idx, test_idx, seed
    )
    ys_mean, ys_std, delta_scale = domain_scales(pre)
    model = HardConstraintTransformer(
        element_features=pre.element_features,
        n_numeric=len(pre.numeric_cols),
        n_domains=len(pre.vocab.domains),
        n_processes=len(pre.vocab.processes),
        ys_mean=ys_mean,
        ys_std=ys_std,
        delta_scale=delta_scale,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=2e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=MAX_EPOCHS, eta_min=3e-5
    )
    criterion = nn.SmoothL1Loss(beta=0.5)
    task_ids = pre.vocab.task_to_id
    best_state, best_epoch, best_score = None, -1, float("inf")
    history, stale = [], 0
    started = time.monotonic()
    for epoch in range(MAX_EPOCHS):
        model.train()
        epoch_losses = []
        for batch in train_loader:
            batch = move_batch(batch, device)
            optimizer.zero_grad(set_to_none=True)
            physical = model(batch)
            output_index = torch.where(
                batch["task"].eq(task_ids["YS"]),
                torch.full_like(batch["task"], YS_OUTPUT),
                torch.full_like(batch["task"], UTS_OUTPUT),
            )
            chosen = physical.gather(1, output_index.unsqueeze(1)).squeeze(1)
            normalized_error = (chosen - batch["target"]) / batch["target_std"]
            loss = criterion(normalized_error, torch.zeros_like(normalized_error))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        scheduler.step()
        if epoch % 2 and epoch != MAX_EPOCHS - 1:
            continue
        val_predictions = predict(model, val_loader, frame, device)
        val_metrics = full_metrics(val_predictions)
        score = float(val_metrics["macro_domain_task_MAE"])
        history.append({
            "epoch": epoch,
            "loss": float(np.mean(epoch_losses)),
            "validation_macro_MAE": score,
        })
        if score < best_score - 0.1:
            best_score = score
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 2
        if stale >= PATIENCE:
            break
    if best_state is None:
        raise RuntimeError("No checkpoint selected")
    model.load_state_dict(best_state)
    val_predictions = predict(model, val_loader, frame, device)
    test_predictions = predict(model, test_loader, frame, device)
    torch.save({
        "model_state": model.state_dict(),
        "fold": fold,
        "seed_index": seed_index,
        "seed": seed,
        "architecture": "UTS = YS + softplus(delta) * domain_scale",
        "domains": pre.vocab.domains,
        "processes": pre.vocab.processes,
        "numeric_cols": pre.numeric_cols,
        "ys_mean": ys_mean,
        "ys_std": ys_std,
        "delta_scale": delta_scale,
    }, OUT / f"fold_{fold}_seed_{seed_index}.pt")
    return ({
        "seed": seed,
        "parameter_count": int(sum(p.numel() for p in model.parameters())),
        "best_epoch": best_epoch,
        "elapsed_seconds": time.monotonic() - started,
        "history": history,
        "validation": full_metrics(val_predictions),
        "test": full_metrics(test_predictions),
    }, val_predictions, test_predictions)


def ensemble_predictions(parts: list[pd.DataFrame]) -> pd.DataFrame:
    base = parts[0][
        ["record_id", "composition_key", "domain", "task", "target", "row_index"]
    ].copy()
    pred = np.stack([part.prediction.to_numpy() for part in parts])
    ys = np.stack([part.predicted_YS.to_numpy() for part in parts])
    uts = np.stack([part.predicted_UTS.to_numpy() for part in parts])
    base["prediction"] = pred.mean(axis=0)
    base["prediction_std"] = pred.std(axis=0)
    base["predicted_YS"] = ys.mean(axis=0)
    base["predicted_UTS"] = uts.mean(axis=0)
    base["order_violation_mpa"] = base.predicted_YS - base.predicted_UTS
    return base


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    OUT.mkdir(parents=True, exist_ok=True)
    frame = add_explicit_physics_descriptors(pd.read_csv(DATA, low_memory=False))
    groups = frame.composition_key.to_numpy()
    outer = GroupKFold(n_splits=FOLD_COUNT)
    device = torch.device("cuda")
    fold_reports, oof_parts, split_parts = [], [], []

    for fold, (train_val_idx, test_idx) in enumerate(outer.split(frame, groups=groups), start=1):
        inner = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=BASE_SEED + fold)
        train_rel, val_rel = next(inner.split(train_val_idx, groups=groups[train_val_idx]))
        train_idx, val_idx = train_val_idx[train_rel], train_val_idx[val_rel]
        pre = PhysicsPreprocessor(frame, train_idx)
        encoded = pre.encode_rows(frame)
        seed_reports, val_predictions, test_predictions = [], [], []
        for seed_index in range(1, SEEDS_PER_FOLD + 1):
            seed = BASE_SEED + fold * 100 + seed_index
            report, val_pred, test_pred = train_one(
                fold=fold, seed_index=seed_index, seed=seed, frame=frame, pre=pre,
                encoded=encoded, train_idx=train_idx, val_idx=val_idx,
                test_idx=test_idx, device=device,
            )
            seed_reports.append(report)
            val_predictions.append(val_pred)
            test_predictions.append(test_pred)
        val_ensemble = ensemble_predictions(val_predictions)
        test_ensemble = ensemble_predictions(test_predictions)
        test_ensemble["fold"] = fold
        oof_parts.append(test_ensemble)
        for role, indices in (("train", train_idx), ("validation", val_idx), ("test", test_idx)):
            split = frame.iloc[indices][["record_id", "composition_key", "domain", "task"]].copy()
            split["fold"] = fold
            split["role"] = role
            split_parts.append(split)
        fold_report = {
            "fold": fold,
            "split": {
                "train_records": int(len(train_idx)),
                "validation_records": int(len(val_idx)),
                "test_records": int(len(test_idx)),
                "train_compositions": int(frame.iloc[train_idx].composition_key.nunique()),
                "validation_compositions": int(frame.iloc[val_idx].composition_key.nunique()),
                "test_compositions": int(frame.iloc[test_idx].composition_key.nunique()),
            },
            "seeds": seed_reports,
            "validation_ensemble": full_metrics(val_ensemble),
            "test_ensemble": full_metrics(test_ensemble),
        }
        fold_reports.append(fold_report)
        (OUT / "progress.json").write_text(
            json.dumps({"completed_folds": fold, "fold_reports": fold_reports}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({
            "fold": fold,
            "ensemble": fold_report["test_ensemble"]["by_task"],
            "violation_rate": fold_report["test_ensemble"]["predicted_order_violation_rate"],
        }, ensure_ascii=False), flush=True)

    oof = pd.concat(oof_parts).sort_values("row_index").reset_index(drop=True)
    oof.to_csv(OUT / "oof_hard_constraint_ensemble_predictions.csv", index=False)
    pd.concat(split_parts).to_csv(OUT / "five_fold_split_assignments.csv", index=False)
    report = {
        "experiment": "alloy_foundation_hard_constraint_transformer_cv_v3_20260917",
        "status": "offline_benchmark_only_not_for_service",
        "tree_used_in_training_or_inference": False,
        "architecture": "joint Transformer; UTS = YS + softplus(delta) * domain_scale",
        "records": int(len(frame)),
        "independent_compositions": int(frame.composition_key.nunique()),
        "folds": FOLD_COUNT,
        "seeds_per_fold": SEEDS_PER_FOLD,
        "oof_metrics": full_metrics(oof),
        "fold_reports": fold_reports,
    }
    (OUT / "benchmark_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "complete", "oof_metrics": report["oof_metrics"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
