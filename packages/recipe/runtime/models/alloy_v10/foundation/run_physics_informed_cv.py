"""Repeated strict-composition CV for a physics-guided alloy Transformer.

Stage 3 adds three practical physics-informed layers to the admitted unified
YS/UTS benchmark:
1. explicit mismatch descriptors derived from elemental-property statistics;
2. a soft tensile consistency constraint UTS >= YS;
3. a validation-selected residual correction of ExtraTrees by the Transformer.

This remains an offline benchmark.  It never writes service artifacts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from run_family_adapter_multitask import AlloyFoundation, Preprocessor


ROOT = Path("/data/se42/experiments/alloy_foundation_v0_20260916")
DATA = ROOT / "unified_alloy_tensile_strength_v0.csv"
OUT = ROOT / "physics_informed_cv_v2"

SEED = 20260916
N_FOLDS = 5
MAX_EPOCHS = 120
PATIENCE = 20
BATCH_SIZE = 256
CONSTRAINT_WEIGHT = 0.12


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def add_explicit_physics_descriptors(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    specs = {
        "atomic_size_delta_pct": "CovalentRadius",
        "electronegativity_cv": "Electronegativity",
        "melting_temperature_cv": "MeltingT",
        "atomic_weight_cv": "AtomicWeight",
        "valence_electron_cv": "NValence",
        "atomic_volume_cv": "GSvolume_pa",
    }
    for name, prop in specs.items():
        mean = pd.to_numeric(frame[f"desc_{prop}_mean"], errors="coerce")
        std = pd.to_numeric(frame[f"desc_{prop}_std"], errors="coerce")
        ratio = std / mean.abs().clip(lower=1e-8)
        if name == "atomic_size_delta_pct":
            ratio = 100.0 * ratio
        frame[f"desc_phys_{name}"] = ratio.replace([np.inf, -np.inf], np.nan)
    frame["desc_phys_entropy_to_component_count"] = (
        pd.to_numeric(frame["desc_mixing_entropy_R"], errors="coerce")
        / pd.to_numeric(frame["desc_component_count"], errors="coerce").clip(lower=1.0)
    )
    frame["desc_phys_dilute_fraction"] = 1.0 - pd.to_numeric(
        frame["desc_max_atomic_fraction"], errors="coerce"
    )
    return frame


def metric_block(y: np.ndarray, pred: np.ndarray) -> dict[str, float | int | None]:
    return {
        "n": int(len(y)),
        "MAE": float(mean_absolute_error(y, pred)),
        "RMSE": float(mean_squared_error(y, pred) ** 0.5),
        "R2": float(r2_score(y, pred)) if len(y) >= 3 and np.std(y) > 0 else None,
    }


def full_metrics(predictions: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {"by_task": {}, "by_task_domain": {}}
    for task, group in predictions.groupby("task"):
        result["by_task"][task] = metric_block(group.target.to_numpy(), group.prediction.to_numpy())
    for (task, domain), group in predictions.groupby(["task", "domain"]):
        result["by_task_domain"][f"{task}/{domain}"] = metric_block(
            group.target.to_numpy(), group.prediction.to_numpy()
        )
    eligible_r2 = [
        value["R2"] for value in result["by_task_domain"].values()
        if value["R2"] is not None and value["n"] >= 15
    ]
    eligible_mae = [
        value["MAE"] for value in result["by_task_domain"].values() if value["n"] >= 15
    ]
    result["macro_domain_task_R2"] = float(np.mean(eligible_r2)) if eligible_r2 else None
    result["macro_domain_task_MAE"] = float(np.mean(eligible_mae)) if eligible_mae else None
    if "order_violation_mpa" in predictions:
        result["predicted_order_violation_rate"] = float(
            (predictions.order_violation_mpa > 0).mean()
        )
        result["predicted_order_violation_mean_mpa"] = float(
            predictions.order_violation_mpa.clip(lower=0).mean()
        )
    return result


class PhysicsPreprocessor(Preprocessor):
    def encode_rows(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        encoded = super().encode_rows(frame)
        n_tasks = len(self.vocab.tasks)
        all_mean = np.empty((len(frame), n_tasks), dtype=np.float32)
        all_std = np.empty((len(frame), n_tasks), dtype=np.float32)
        for row_index, domain in enumerate(frame.domain):
            for task_id, task in enumerate(self.vocab.tasks):
                mean, std = self.target_stats[(task, domain)]
                all_mean[row_index, task_id] = mean
                all_std[row_index, task_id] = std
        encoded["all_target_mean"] = all_mean
        encoded["all_target_std"] = all_std
        return encoded


class IndexedDataset(Dataset):
    def __init__(self, encoded: dict[str, np.ndarray], indices: np.ndarray):
        self.encoded = encoded
        self.indices = np.asarray(indices)

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, position: int) -> dict[str, torch.Tensor]:
        row = int(self.indices[position])
        result: dict[str, torch.Tensor] = {}
        for key, values in self.encoded.items():
            value = values[row]
            dtype = torch.long if np.issubdtype(values.dtype, np.integer) else torch.float32
            result[key] = torch.as_tensor(value, dtype=dtype)
        result["row_index"] = torch.tensor(row, dtype=torch.long)
        return result


class PhysicsGuidedAlloyTransformer(AlloyFoundation):
    def __init__(self, **kwargs):
        super().__init__(use_adapters=False, **kwargs)
        for parameter in self.adapters.parameters():
            parameter.requires_grad_(False)

    def shared_parts(self, batch: dict[str, torch.Tensor]) -> tuple[torch.Tensor, ...]:
        ids = batch["element_ids"]
        fractions = batch["fractions"]
        padding = ids.eq(0)
        fixed = self.element_projection(self.element_features[ids])
        x = fixed + self.learned_element(ids) + self.fraction_encoder(fractions)
        x = self.transformer(x, src_key_padding_mask=padding)
        weight = fractions.unsqueeze(-1)
        comp = (x * weight).sum(dim=1) / weight.sum(dim=1).clamp_min(1e-8)
        numeric = self.numeric_encoder(batch["numeric"])
        return (
            comp,
            numeric,
            self.process_embedding(batch["process"]),
            self.domain_embedding(batch["domain"]),
        )

    def forward_all(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        common = self.shared_parts(batch)
        outputs = []
        for task_id, head in enumerate(self.heads):
            task = torch.full_like(batch["task"], task_id)
            fused = self.fusion(torch.cat([*common, self.task_embedding(task)], dim=1))
            outputs.append(head(fused).squeeze(1))
        return torch.stack(outputs, dim=1)

    def forward(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        all_outputs = self.forward_all(batch)
        return all_outputs.gather(1, batch["task"].unsqueeze(1)).squeeze(1)


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


@torch.no_grad()
def predict_model(
    model: PhysicsGuidedAlloyTransformer,
    loader: DataLoader,
    frame: pd.DataFrame,
    device: torch.device,
    ys_id: int,
    uts_id: int,
) -> pd.DataFrame:
    model.eval()
    rows, selected, all_physical = [], [], []
    for batch in loader:
        batch = move_batch(batch, device)
        scaled_all = model.forward_all(batch)
        physical_all = scaled_all * batch["all_target_std"] + batch["all_target_mean"]
        prediction = physical_all.gather(1, batch["task"].unsqueeze(1)).squeeze(1)
        rows.append(batch["row_index"].cpu().numpy())
        selected.append(prediction.cpu().numpy())
        all_physical.append(physical_all.cpu().numpy())
    row_idx = np.concatenate(rows)
    both = np.concatenate(all_physical)
    output = frame.iloc[row_idx][
        ["record_id", "composition_key", "domain", "task", "target"]
    ].copy()
    output["prediction"] = np.concatenate(selected)
    output["predicted_YS"] = both[:, ys_id]
    output["predicted_UTS"] = both[:, uts_id]
    output["order_violation_mpa"] = output.predicted_YS - output.predicted_UTS
    output["row_index"] = row_idx
    return output.reset_index(drop=True)


def make_loaders(
    frame: pd.DataFrame,
    encoded: dict[str, np.ndarray],
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    seed: int,
) -> tuple[DataLoader, DataLoader, DataLoader]:
    train_ds = IndexedDataset(encoded, train_idx)
    pairs = [f"{frame.iloc[i].domain}/{frame.iloc[i].task}" for i in train_idx]
    counts = pd.Series(pairs).value_counts().to_dict()
    weights = np.asarray([1.0 / math.sqrt(counts[pair]) for pair in pairs], dtype=np.float64)
    sampler = WeightedRandomSampler(
        weights,
        num_samples=len(weights),
        replacement=True,
        generator=torch.Generator().manual_seed(seed),
    )
    train_loader = DataLoader(
        train_ds, batch_size=BATCH_SIZE, sampler=sampler, num_workers=0, pin_memory=True
    )
    val_loader = DataLoader(
        IndexedDataset(encoded, val_idx), batch_size=512, shuffle=False, num_workers=0,
        pin_memory=True,
    )
    test_loader = DataLoader(
        IndexedDataset(encoded, test_idx), batch_size=512, shuffle=False, num_workers=0,
        pin_memory=True,
    )
    return train_loader, val_loader, test_loader


def train_transformer(
    *,
    fold: int,
    variant: str,
    constraint_weight: float,
    frame: pd.DataFrame,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    pre: PhysicsPreprocessor,
    encoded: dict[str, np.ndarray],
    device: torch.device,
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    seed = SEED + fold
    seed_everything(seed)
    train_loader, val_loader, test_loader = make_loaders(
        frame, encoded, train_idx, val_idx, test_idx, seed
    )
    model = PhysicsGuidedAlloyTransformer(
        element_features=pre.element_features,
        n_numeric=len(pre.numeric_cols),
        n_domains=len(pre.vocab.domains),
        n_tasks=len(pre.vocab.tasks),
        n_processes=len(pre.vocab.processes),
    ).to(device)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=3e-4, weight_decay=2e-4
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=MAX_EPOCHS, eta_min=3e-5
    )
    criterion = nn.SmoothL1Loss(beta=0.5)
    ys_id = pre.vocab.task_to_id["YS"]
    uts_id = pre.vocab.task_to_id["UTS"]
    best_state, best_epoch, best_score = None, -1, float("inf")
    stale = 0
    history = []
    started = time.monotonic()
    for epoch in range(MAX_EPOCHS):
        model.train()
        losses, data_losses, physics_losses = [], [], []
        for batch in train_loader:
            batch = move_batch(batch, device)
            optimizer.zero_grad(set_to_none=True)
            scaled_all = model.forward_all(batch)
            selected = scaled_all.gather(1, batch["task"].unsqueeze(1)).squeeze(1)
            data_loss = criterion(selected, batch["target_scaled"])
            physical_all = scaled_all * batch["all_target_std"] + batch["all_target_mean"]
            violation = torch.relu(physical_all[:, ys_id] - physical_all[:, uts_id]) / 100.0
            physics_loss = torch.mean(violation.square())
            loss = data_loss + constraint_weight * physics_loss
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
            data_losses.append(float(data_loss.detach().cpu()))
            physics_losses.append(float(physics_loss.detach().cpu()))
        scheduler.step()
        if epoch % 2 and epoch != MAX_EPOCHS - 1:
            continue
        val_pred = predict_model(model, val_loader, frame, device, ys_id, uts_id)
        val_metrics = full_metrics(val_pred)
        score = float(val_metrics["macro_domain_task_MAE"])
        history.append({
            "epoch": epoch,
            "loss": float(np.mean(losses)),
            "data_loss": float(np.mean(data_losses)),
            "physics_loss": float(np.mean(physics_losses)),
            "validation_macro_MAE": score,
            "validation_order_violation_rate": val_metrics["predicted_order_violation_rate"],
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
        raise RuntimeError("No model checkpoint selected")
    model.load_state_dict(best_state)
    val_pred = predict_model(model, val_loader, frame, device, ys_id, uts_id)
    test_pred = predict_model(model, test_loader, frame, device, ys_id, uts_id)
    torch.save(
        {
            "model_state": model.state_dict(),
            "fold": fold,
            "variant": variant,
            "constraint_weight": constraint_weight,
            "domains": pre.vocab.domains,
            "tasks": pre.vocab.tasks,
            "processes": pre.vocab.processes,
            "numeric_cols": pre.numeric_cols,
            "target_stats": {f"{k[0]}/{k[1]}": v for k, v in pre.target_stats.items()},
        },
        OUT / f"fold_{fold}_{variant}.pt",
    )
    report = {
        "constraint_weight": constraint_weight,
        "parameter_count": int(sum(p.numel() for p in model.parameters() if p.requires_grad)),
        "best_epoch": best_epoch,
        "elapsed_seconds": time.monotonic() - started,
        "history": history,
        "validation": full_metrics(val_pred),
        "test": full_metrics(test_pred),
    }
    return report, val_pred, test_pred


def tree_features(frame: pd.DataFrame, pre: PhysicsPreprocessor) -> pd.DataFrame:
    comps = [json.loads(raw) for raw in frame.composition_json]
    elements = sorted({element for comp in comps for element in comp})
    parts = [pd.DataFrame([{f"x_{e}": comp.get(e, 0.0) for e in elements} for comp in comps])]
    numeric = pre.numeric_matrix(frame)
    parts.append(pd.DataFrame(numeric, columns=[f"n_{c}" for c in pre.numeric_cols]))
    parts.append(pd.get_dummies(frame.domain, prefix="domain", dtype=float))
    allowed = set(pre.vocab.processes)
    process = frame.process_label.where(frame.process_label.isin(allowed), "__OTHER__")
    parts.append(pd.get_dummies(process, prefix="process", dtype=float))
    return pd.concat(parts, axis=1)


def run_trees(
    *,
    fold: int,
    frame: pd.DataFrame,
    pre: PhysicsPreprocessor,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    x = tree_features(frame, pre)
    val_parts, test_parts = [], []
    for task in pre.vocab.tasks:
        tr = train_idx[frame.iloc[train_idx].task.to_numpy() == task]
        va = val_idx[frame.iloc[val_idx].task.to_numpy() == task]
        te = test_idx[frame.iloc[test_idx].task.to_numpy() == task]
        model = ExtraTreesRegressor(
            n_estimators=500,
            min_samples_leaf=2,
            max_features=0.8,
            n_jobs=-1,
            random_state=SEED + fold,
        )
        model.fit(x.iloc[tr], frame.iloc[tr].target)
        for indices, sink in ((va, val_parts), (te, test_parts)):
            part = frame.iloc[indices][
                ["record_id", "composition_key", "domain", "task", "target"]
            ].copy()
            part["prediction"] = model.predict(x.iloc[indices])
            part["row_index"] = indices
            sink.append(part)
    val_pred = pd.concat(val_parts).sort_values("row_index").reset_index(drop=True)
    test_pred = pd.concat(test_parts).sort_values("row_index").reset_index(drop=True)
    return {
        "validation": full_metrics(val_pred),
        "test": full_metrics(test_pred),
    }, val_pred, test_pred


def select_residual_weights(
    tree_val: pd.DataFrame,
    transformer_val: pd.DataFrame,
) -> dict[str, float]:
    merged = tree_val.merge(
        transformer_val[["row_index", "prediction"]], on="row_index", suffixes=("_tree", "_transformer")
    )
    weights: dict[str, float] = {}
    grids = np.linspace(0.0, 1.0, 21)
    for task, task_group in merged.groupby("task"):
        best = min(
            grids,
            key=lambda a: mean_absolute_error(
                task_group.target,
                task_group.prediction_tree + a * (
                    task_group.prediction_transformer - task_group.prediction_tree
                ),
            ),
        )
        weights[f"task:{task}"] = float(best)
    for (task, domain), group in merged.groupby(["task", "domain"]):
        if len(group) < 20:
            continue
        best = min(
            grids,
            key=lambda a: mean_absolute_error(
                group.target,
                group.prediction_tree + a * (
                    group.prediction_transformer - group.prediction_tree
                ),
            ),
        )
        weights[f"domain:{task}/{domain}"] = float(best)
    return weights


def apply_residual_correction(
    tree_pred: pd.DataFrame,
    transformer_pred: pd.DataFrame,
    weights: dict[str, float],
) -> pd.DataFrame:
    merged = tree_pred.merge(
        transformer_pred[["row_index", "prediction"]], on="row_index",
        suffixes=("_tree", "_transformer"),
    )
    alpha = []
    for row in merged.itertuples():
        alpha.append(weights.get(
            f"domain:{row.task}/{row.domain}", weights[f"task:{row.task}"]
        ))
    merged["residual_weight"] = alpha
    merged["prediction"] = merged.prediction_tree + merged.residual_weight * (
        merged.prediction_transformer - merged.prediction_tree
    )
    return merged[[
        "record_id", "composition_key", "domain", "task", "target", "prediction",
        "row_index", "prediction_tree", "prediction_transformer", "residual_weight",
    ]]


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    seed_everything(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    frame = add_explicit_physics_descriptors(pd.read_csv(DATA, low_memory=False))
    groups = frame.composition_key.to_numpy()
    splitter = GroupKFold(n_splits=N_FOLDS)
    fold_reports: list[dict[str, object]] = []
    all_predictions: dict[str, list[pd.DataFrame]] = {
        "extra_trees": [], "transformer_unconstrained": [],
        "transformer_physics": [], "hybrid_residual": [],
    }
    split_rows = []
    device = torch.device("cuda")

    for fold, (train_val_idx, test_idx) in enumerate(splitter.split(frame, groups=groups), start=1):
        inner = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=SEED + fold)
        train_rel, val_rel = next(inner.split(train_val_idx, groups=groups[train_val_idx]))
        train_idx = train_val_idx[train_rel]
        val_idx = train_val_idx[val_rel]
        pre = PhysicsPreprocessor(frame, train_idx)
        encoded = pre.encode_rows(frame)
        tree_report, tree_val, tree_test = run_trees(
            fold=fold, frame=frame, pre=pre, train_idx=train_idx,
            val_idx=val_idx, test_idx=test_idx,
        )
        unconstrained_report, _, unconstrained_test = train_transformer(
            fold=fold, variant="unconstrained", constraint_weight=0.0,
            frame=frame, train_idx=train_idx, val_idx=val_idx, test_idx=test_idx,
            pre=pre, encoded=encoded, device=device,
        )
        physics_report, physics_val, physics_test = train_transformer(
            fold=fold, variant="physics", constraint_weight=CONSTRAINT_WEIGHT,
            frame=frame, train_idx=train_idx, val_idx=val_idx, test_idx=test_idx,
            pre=pre, encoded=encoded, device=device,
        )
        weights = select_residual_weights(tree_val, physics_val)
        hybrid_test = apply_residual_correction(tree_test, physics_test, weights)
        for name, prediction in (
            ("extra_trees", tree_test),
            ("transformer_unconstrained", unconstrained_test),
            ("transformer_physics", physics_test),
            ("hybrid_residual", hybrid_test),
        ):
            prediction = prediction.copy()
            prediction["fold"] = fold
            all_predictions[name].append(prediction)
        for label, indices in (("train", train_idx), ("validation", val_idx), ("test", test_idx)):
            part = frame.iloc[indices][["record_id", "composition_key", "domain", "task"]].copy()
            part["fold"] = fold
            part["role"] = label
            split_rows.append(part)
        fold_report = {
            "fold": fold,
            "split": {
                "train_records": int(len(train_idx)), "validation_records": int(len(val_idx)),
                "test_records": int(len(test_idx)),
                "train_compositions": int(frame.iloc[train_idx].composition_key.nunique()),
                "validation_compositions": int(frame.iloc[val_idx].composition_key.nunique()),
                "test_compositions": int(frame.iloc[test_idx].composition_key.nunique()),
            },
            "extra_trees": tree_report,
            "transformer_unconstrained": unconstrained_report,
            "transformer_physics": physics_report,
            "hybrid_weights": weights,
            "hybrid_test": full_metrics(hybrid_test),
        }
        fold_reports.append(fold_report)
        (OUT / "progress.json").write_text(
            json.dumps({"completed_folds": fold, "fold_reports": fold_reports}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({
            "fold": fold,
            "tree": tree_report["test"]["by_task"],
            "unconstrained": unconstrained_report["test"]["by_task"],
            "physics": physics_report["test"]["by_task"],
            "hybrid": fold_report["hybrid_test"]["by_task"],
        }, ensure_ascii=False), flush=True)

    oof_metrics = {}
    for name, parts in all_predictions.items():
        combined = pd.concat(parts).sort_values("row_index").reset_index(drop=True)
        combined.to_csv(OUT / f"oof_{name}_predictions.csv", index=False)
        oof_metrics[name] = full_metrics(combined)
    pd.concat(split_rows).to_csv(OUT / "five_fold_split_assignments.csv", index=False)
    report = {
        "experiment": "alloy_foundation_physics_informed_cv_v2_20260916",
        "status": "offline_benchmark_only_not_for_service",
        "records": int(len(frame)),
        "independent_compositions": int(frame.composition_key.nunique()),
        "folds": N_FOLDS,
        "physics_layers": {
            "descriptors": [c for c in frame.columns if c.startswith("desc_phys_")],
            "constraint": "soft penalty on max(YS_pred - UTS_pred, 0)",
            "constraint_weight": CONSTRAINT_WEIGHT,
            "residual": "ExtraTrees + alpha*(physics Transformer - ExtraTrees), alpha selected on validation",
        },
        "oof_metrics": oof_metrics,
        "fold_reports": fold_reports,
    }
    (OUT / "benchmark_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "complete", "oof_metrics": oof_metrics}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
