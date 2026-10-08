"""Family-adapter, multi-task alloy foundation benchmark.

Uses the unified admitted tensile-strength table produced by v0.  The model
shares a composition Transformer and descriptor/context fusion trunk across
all alloy families, then optionally applies family-specific residual adapters
and always uses separate YS/UTS heads.  Splits are global by exact canonical
atomic composition so no chemistry crosses train/validation/test.
"""
from __future__ import annotations

import copy
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler


ROOT = Path("/data/se42/experiments/alloy_foundation_v0_20260916")
DATA = ROOT / "unified_alloy_tensile_strength_v0.csv"
OUT = ROOT / "family_adapter_multitask_v1"
MAGPIE = Path(
    "/data/mamba/envs/crabnet-py310/lib/python3.10/site-packages/"
    "crabnet/data/element_properties/magpie.csv"
)
MAT2VEC = Path(
    "/data/mamba/envs/crabnet-py310/lib/python3.10/site-packages/"
    "crabnet/data/element_properties/mat2vec.csv"
)

SEED = 2026
MAX_ELEMENTS = 20
DESCRIPTOR_PROPERTIES = (
    "Number",
    "MendeleevNumber",
    "AtomicWeight",
    "MeltingT",
    "CovalentRadius",
    "Electronegativity",
    "NValence",
    "GSvolume_pa",
    "GSmagmom",
)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


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
    eligible = [
        value["R2"]
        for value in result["by_task_domain"].values()
        if value["R2"] is not None and value["n"] >= 15
    ]
    result["macro_domain_task_R2"] = float(np.mean(eligible)) if eligible else None
    result["macro_domain_task_MAE"] = float(
        np.mean([value["MAE"] for value in result["by_task_domain"].values() if value["n"] >= 15])
    )
    return result


def global_group_split(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = np.arange(len(frame))
    groups = frame.composition_key.to_numpy()
    outer = GroupShuffleSplit(n_splits=1, test_size=0.17, random_state=SEED)
    train_val, test = next(outer.split(rows, groups=groups))
    inner = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=SEED + 1)
    train_rel, val_rel = next(inner.split(train_val, groups=groups[train_val]))
    return train_val[train_rel], train_val[val_rel], test


@dataclass
class Vocabulary:
    domains: list[str]
    tasks: list[str]
    processes: list[str]

    @property
    def domain_to_id(self) -> dict[str, int]:
        return {value: index for index, value in enumerate(self.domains)}

    @property
    def task_to_id(self) -> dict[str, int]:
        return {value: index for index, value in enumerate(self.tasks)}

    @property
    def process_to_id(self) -> dict[str, int]:
        return {value: index + 1 for index, value in enumerate(self.processes)}


class Preprocessor:
    def __init__(self, frame: pd.DataFrame, train_idx: np.ndarray):
        train = frame.iloc[train_idx]
        self.descriptor_cols = [c for c in frame.columns if c.startswith("desc_")]
        self.numeric_cols = ["test_temperature_C", "temperature_missing", *self.descriptor_cols]
        self.numeric_median: dict[str, float] = {}
        self.numeric_mean: dict[str, float] = {}
        self.numeric_std: dict[str, float] = {}
        for column in self.numeric_cols:
            values = pd.to_numeric(train[column], errors="coerce")
            median = float(values.median()) if values.notna().any() else 0.0
            filled = values.fillna(median)
            mean = float(filled.mean())
            std = float(filled.std()) if len(filled) > 1 else 1.0
            if not math.isfinite(std) or std < 1e-12:
                std = 1.0
            self.numeric_median[column] = median
            self.numeric_mean[column] = mean
            self.numeric_std[column] = std

        self.vocab = Vocabulary(
            domains=sorted(train.domain.unique()),
            tasks=sorted(train.task.unique()),
            processes=list(train.process_label.value_counts().head(128).index),
        )
        self.target_stats: dict[tuple[str, str], tuple[float, float]] = {}
        for (task, domain), group in train.groupby(["task", "domain"]):
            mean = float(group.target.mean())
            std = float(group.target.std())
            if not math.isfinite(std) or std < 20.0:
                std = 20.0
            self.target_stats[(task, domain)] = (mean, std)

        magpie = pd.read_csv(MAGPIE).set_index("element")
        self.symbol_to_z = magpie["Number"].round().astype(int).to_dict()
        self.max_z = int(max(self.symbol_to_z.values()))

        m2v = pd.read_csv(MAT2VEC).set_index("element").apply(pd.to_numeric, errors="coerce").fillna(0.0)
        self.element_features = np.zeros((self.max_z + 1, m2v.shape[1]), dtype=np.float32)
        for symbol, z in self.symbol_to_z.items():
            if symbol in m2v.index:
                self.element_features[z] = m2v.loc[symbol].to_numpy(dtype=np.float32)

    def numeric_matrix(self, frame: pd.DataFrame) -> np.ndarray:
        columns = []
        for column in self.numeric_cols:
            values = pd.to_numeric(frame[column], errors="coerce").fillna(self.numeric_median[column])
            columns.append(((values - self.numeric_mean[column]) / self.numeric_std[column]).to_numpy(dtype=np.float32))
        return np.stack(columns, axis=1)

    def encode_rows(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        element_ids = np.zeros((len(frame), MAX_ELEMENTS), dtype=np.int64)
        fractions = np.zeros((len(frame), MAX_ELEMENTS), dtype=np.float32)
        for row_index, raw in enumerate(frame.composition_json):
            comp = json.loads(raw)
            items = sorted(comp.items(), key=lambda item: item[1], reverse=True)[:MAX_ELEMENTS]
            for col_index, (symbol, fraction) in enumerate(items):
                element_ids[row_index, col_index] = self.symbol_to_z[symbol]
                fractions[row_index, col_index] = float(fraction)
        domain_map = self.vocab.domain_to_id
        task_map = self.vocab.task_to_id
        process_map = self.vocab.process_to_id
        domain = np.asarray([domain_map[value] for value in frame.domain], dtype=np.int64)
        task = np.asarray([task_map[value] for value in frame.task], dtype=np.int64)
        process = np.asarray([process_map.get(value, 0) for value in frame.process_label], dtype=np.int64)
        target = frame.target.to_numpy(dtype=np.float32)
        target_scaled = np.empty(len(frame), dtype=np.float32)
        target_mean = np.empty(len(frame), dtype=np.float32)
        target_std = np.empty(len(frame), dtype=np.float32)
        for i, (task_name, domain_name, value) in enumerate(zip(frame.task, frame.domain, target)):
            mean, std = self.target_stats[(task_name, domain_name)]
            target_mean[i] = mean
            target_std[i] = std
            target_scaled[i] = (value - mean) / std
        return {
            "element_ids": element_ids,
            "fractions": fractions,
            "numeric": self.numeric_matrix(frame),
            "domain": domain,
            "task": task,
            "process": process,
            "target": target,
            "target_scaled": target_scaled,
            "target_mean": target_mean,
            "target_std": target_std,
        }


class AlloyDataset(Dataset):
    def __init__(self, encoded: dict[str, np.ndarray], indices: np.ndarray):
        self.encoded = encoded
        self.indices = np.asarray(indices)

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, position: int) -> dict[str, torch.Tensor]:
        idx = self.indices[position]
        result: dict[str, torch.Tensor] = {}
        for key, values in self.encoded.items():
            value = values[idx]
            if np.issubdtype(values.dtype, np.integer):
                result[key] = torch.as_tensor(value, dtype=torch.long)
            else:
                result[key] = torch.as_tensor(value, dtype=torch.float32)
        result["row_index"] = torch.tensor(idx, dtype=torch.long)
        return result


class FractionEncoder(nn.Module):
    def __init__(self, d_model: int):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(7, d_model), nn.SiLU(), nn.Linear(d_model, d_model))

    def forward(self, fractions: torch.Tensor) -> torch.Tensor:
        f = fractions.clamp_min(1e-10)
        features = torch.stack(
            [
                f,
                torch.sqrt(f),
                torch.log10(f).clamp_min(-10) / 10.0,
                torch.sin(math.pi * f),
                torch.cos(math.pi * f),
                torch.sin(10 * math.pi * f),
                torch.cos(10 * math.pi * f),
            ],
            dim=-1,
        )
        return self.net(features)


class ResidualAdapter(nn.Module):
    def __init__(self, width: int, bottleneck: int = 32):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(width),
            nn.Linear(width, bottleneck),
            nn.SiLU(),
            nn.Linear(bottleneck, width),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class AlloyFoundation(nn.Module):
    def __init__(
        self,
        *,
        element_features: np.ndarray,
        n_numeric: int,
        n_domains: int,
        n_tasks: int,
        n_processes: int,
        use_adapters: bool,
        d_model: int = 128,
    ):
        super().__init__()
        element_tensor = torch.as_tensor(element_features, dtype=torch.float32)
        self.register_buffer("element_features", element_tensor)
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
            nn.Linear(n_numeric, 96), nn.LayerNorm(96), nn.SiLU(), nn.Dropout(0.1), nn.Linear(96, 64)
        )
        self.process_embedding = nn.Embedding(n_processes + 1, 24, padding_idx=0)
        self.domain_embedding = nn.Embedding(n_domains, 16)
        self.task_embedding = nn.Embedding(n_tasks, 16)
        fusion_width = d_model + 64 + 24 + 16 + 16
        self.fusion = nn.Sequential(
            nn.Linear(fusion_width, 160), nn.LayerNorm(160), nn.SiLU(), nn.Dropout(0.12),
            nn.Linear(160, 128), nn.LayerNorm(128), nn.SiLU(),
        )
        self.use_adapters = use_adapters
        self.adapters = nn.ModuleList([ResidualAdapter(128, 32) for _ in range(n_domains)])
        self.heads = nn.ModuleList(
            [nn.Sequential(nn.Linear(128, 64), nn.SiLU(), nn.Dropout(0.08), nn.Linear(64, 1)) for _ in range(n_tasks)]
        )

    def forward(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        ids = batch["element_ids"]
        fractions = batch["fractions"]
        padding = ids.eq(0)
        fixed = self.element_projection(self.element_features[ids])
        x = fixed + self.learned_element(ids) + self.fraction_encoder(fractions)
        x = self.transformer(x, src_key_padding_mask=padding)
        weight = fractions.unsqueeze(-1)
        comp = (x * weight).sum(dim=1) / weight.sum(dim=1).clamp_min(1e-8)
        numeric = self.numeric_encoder(batch["numeric"])
        fused = self.fusion(
            torch.cat(
                [
                    comp,
                    numeric,
                    self.process_embedding(batch["process"]),
                    self.domain_embedding(batch["domain"]),
                    self.task_embedding(batch["task"]),
                ],
                dim=1,
            )
        )
        if self.use_adapters:
            adapted = torch.empty_like(fused)
            for domain_id, adapter in enumerate(self.adapters):
                mask = batch["domain"].eq(domain_id)
                if mask.any():
                    adapted[mask] = adapter(fused[mask])
            fused = adapted
        output = torch.empty(len(fused), device=fused.device)
        for task_id, head in enumerate(self.heads):
            mask = batch["task"].eq(task_id)
            if mask.any():
                output[mask] = head(fused[mask]).squeeze(1)
        return output


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


@torch.no_grad()
def predict_model(
    model: nn.Module,
    loader: DataLoader,
    frame: pd.DataFrame,
    device: torch.device,
) -> pd.DataFrame:
    model.eval()
    rows: list[np.ndarray] = []
    preds: list[np.ndarray] = []
    for batch in loader:
        batch = move_batch(batch, device)
        scaled = model(batch)
        prediction = scaled * batch["target_std"] + batch["target_mean"]
        rows.append(batch["row_index"].cpu().numpy())
        preds.append(prediction.cpu().numpy())
    row_idx = np.concatenate(rows)
    output = frame.iloc[row_idx][["record_id", "composition_key", "domain", "task", "target"]].copy()
    output["prediction"] = np.concatenate(preds)
    output["row_index"] = row_idx
    return output.reset_index(drop=True)


def train_model(
    *,
    name: str,
    use_adapters: bool,
    frame: pd.DataFrame,
    encoded: dict[str, np.ndarray],
    pre: Preprocessor,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    device: torch.device,
) -> dict[str, object]:
    seed_everything(SEED)
    train_ds = AlloyDataset(encoded, train_idx)
    val_ds = AlloyDataset(encoded, val_idx)
    test_ds = AlloyDataset(encoded, test_idx)
    pairs = [f"{frame.iloc[i].domain}/{frame.iloc[i].task}" for i in train_idx]
    counts = pd.Series(pairs).value_counts().to_dict()
    weights = np.asarray([1.0 / math.sqrt(counts[pair]) for pair in pairs], dtype=np.float64)
    sampler = WeightedRandomSampler(weights, num_samples=len(weights), replacement=True, generator=torch.Generator().manual_seed(SEED))
    train_loader = DataLoader(train_ds, batch_size=256, sampler=sampler, num_workers=0, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=512, shuffle=False, num_workers=0, pin_memory=True)
    test_loader = DataLoader(test_ds, batch_size=512, shuffle=False, num_workers=0, pin_memory=True)
    model = AlloyFoundation(
        element_features=pre.element_features,
        n_numeric=len(pre.numeric_cols),
        n_domains=len(pre.vocab.domains),
        n_tasks=len(pre.vocab.tasks),
        n_processes=len(pre.vocab.processes),
        use_adapters=use_adapters,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=2e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=160, eta_min=3e-5)
    criterion = nn.SmoothL1Loss(beta=0.5)
    best_state = None
    best_score = float("inf")
    best_epoch = -1
    patience = 24
    stale = 0
    history: list[dict[str, float | int]] = []
    start = time.monotonic()
    for epoch in range(160):
        model.train()
        losses = []
        for batch in train_loader:
            batch = move_batch(batch, device)
            optimizer.zero_grad(set_to_none=True)
            prediction = model(batch)
            loss = criterion(prediction, batch["target_scaled"])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step()
        if epoch % 2 != 0 and epoch != 159:
            continue
        val_predictions = predict_model(model, val_loader, frame, device)
        val_metrics = full_metrics(val_predictions)
        score = float(val_metrics["macro_domain_task_MAE"])
        history.append({"epoch": epoch, "train_loss": float(np.mean(losses)), "validation_macro_MAE": score})
        if score < best_score - 0.1:
            best_score = score
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 2
        if stale >= patience:
            break
    if best_state is None:
        raise RuntimeError("No model checkpoint was selected")
    model.load_state_dict(best_state)
    val_predictions = predict_model(model, val_loader, frame, device)
    test_predictions = predict_model(model, test_loader, frame, device)
    torch.save(
        {
            "model_state": model.state_dict(),
            "use_adapters": use_adapters,
            "domains": pre.vocab.domains,
            "tasks": pre.vocab.tasks,
            "processes": pre.vocab.processes,
            "numeric_cols": pre.numeric_cols,
            "target_stats": {f"{k[0]}/{k[1]}": v for k, v in pre.target_stats.items()},
        },
        OUT / f"{name}.pt",
    )
    val_predictions.to_csv(OUT / f"{name}_validation_predictions.csv", index=False)
    test_predictions.to_csv(OUT / f"{name}_test_predictions.csv", index=False)
    return {
        "use_adapters": use_adapters,
        "parameter_count": int(sum(p.numel() for p in model.parameters() if p.requires_grad)),
        "best_epoch": best_epoch,
        "elapsed_seconds": time.monotonic() - start,
        "history": history,
        "validation": full_metrics(val_predictions),
        "test": full_metrics(test_predictions),
    }


def tree_features(frame: pd.DataFrame, pre: Preprocessor, train_idx: np.ndarray) -> pd.DataFrame:
    comps = [json.loads(raw) for raw in frame.composition_json]
    elements = sorted({element for comp in comps for element in comp})
    x = pd.DataFrame([{f"x_{e}": comp.get(e, 0.0) for e in elements} for comp in comps])
    numeric = pre.numeric_matrix(frame)
    for i, column in enumerate(pre.numeric_cols):
        x[f"n_{column}"] = numeric[:, i]
    for domain in pre.vocab.domains:
        x[f"domain_{domain}"] = (frame.domain == domain).astype(float)
    for process in pre.vocab.processes:
        key = abs(hash(process))
        x[f"process_{key}"] = (frame.process_label == process).astype(float)
    return x


def run_tree_baseline(
    frame: pd.DataFrame,
    pre: Preprocessor,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
) -> dict[str, object]:
    x = tree_features(frame, pre, train_idx)
    report: dict[str, object] = {"validation": {}, "test": {}}
    val_predictions = []
    test_predictions = []
    for task in pre.vocab.tasks:
        task_train = train_idx[frame.iloc[train_idx].task.to_numpy() == task]
        task_val = val_idx[frame.iloc[val_idx].task.to_numpy() == task]
        task_test = test_idx[frame.iloc[test_idx].task.to_numpy() == task]
        model = ExtraTreesRegressor(
            n_estimators=400,
            min_samples_leaf=2,
            max_features=0.8,
            n_jobs=-1,
            random_state=SEED,
        )
        model.fit(x.iloc[task_train], frame.iloc[task_train].target)
        for indices, sink in ((task_val, val_predictions), (task_test, test_predictions)):
            part = frame.iloc[indices][["record_id", "composition_key", "domain", "task", "target"]].copy()
            part["prediction"] = model.predict(x.iloc[indices])
            part["row_index"] = indices
            sink.append(part)
    val_frame = pd.concat(val_predictions).sort_values("row_index").reset_index(drop=True)
    test_frame = pd.concat(test_predictions).sort_values("row_index").reset_index(drop=True)
    val_frame.to_csv(OUT / "extra_trees_validation_predictions.csv", index=False)
    test_frame.to_csv(OUT / "extra_trees_test_predictions.csv", index=False)
    report["validation"] = full_metrics(val_frame)
    report["test"] = full_metrics(test_frame)
    return report


def main() -> None:
    seed_everything(SEED)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(DATA, low_memory=False)
    train_idx, val_idx, test_idx = global_group_split(frame)
    pre = Preprocessor(frame, train_idx)
    encoded = pre.encode_rows(frame)
    split_frame = frame[["record_id", "composition_key", "domain", "task"]].copy()
    split_frame["split"] = ""
    split_frame.loc[train_idx, "split"] = "train"
    split_frame.loc[val_idx, "split"] = "validation"
    split_frame.loc[test_idx, "split"] = "test"
    split_frame.to_csv(OUT / "global_composition_split.csv", index=False)

    device = torch.device("cuda")
    report: dict[str, object] = {
        "experiment": "alloy_foundation_family_adapter_multitask_v1_20260916",
        "status": "offline_benchmark_only_not_for_service",
        "records": int(len(frame)),
        "independent_compositions": int(frame.composition_key.nunique()),
        "split": {
            "train_records": int(len(train_idx)),
            "validation_records": int(len(val_idx)),
            "test_records": int(len(test_idx)),
            "train_compositions": int(frame.iloc[train_idx].composition_key.nunique()),
            "validation_compositions": int(frame.iloc[val_idx].composition_key.nunique()),
            "test_compositions": int(frame.iloc[test_idx].composition_key.nunique()),
        },
        "tree_baseline": run_tree_baseline(frame, pre, train_idx, val_idx, test_idx),
        "shared_no_adapter": train_model(
            name="shared_no_adapter",
            use_adapters=False,
            frame=frame,
            encoded=encoded,
            pre=pre,
            train_idx=train_idx,
            val_idx=val_idx,
            test_idx=test_idx,
            device=device,
        ),
        "shared_family_adapter": train_model(
            name="shared_family_adapter",
            use_adapters=True,
            frame=frame,
            encoded=encoded,
            pre=pre,
            train_idx=train_idx,
            val_idx=val_idx,
            test_idx=test_idx,
            device=device,
        ),
    }
    (OUT / "benchmark_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
