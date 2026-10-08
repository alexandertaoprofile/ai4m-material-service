"""Retrain the mature alloy Transformer on canonical corrected v8 semantics.

Unlike the hierarchical pilot, this experiment keeps the proven mixed numeric
encoder and changes only input semantics/coverage.  Selection is validation
only.  The frozen test is evaluated after a family and single seed are fixed.
No production repository is touched.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader

FOUNDATION_ROOT = Path("/data/se42/experiments/alloy_foundation_v0_20260916")
sys.path.insert(0, str(FOUNDATION_ROOT))
import train_process_adapter_v2 as base
from repair_process_data_v6 import sha, save

ROOT = Path(__file__).resolve().parent
SEEDS = [20260923, 20260924, 20260925]
MAX_EPOCHS = 120
PATIENCE = 20
DEVICE = torch.device("cuda")
EVIDENCE_PREFIX = "desc_v8_evidence_flag_"
NI_DUPLICATES = [
    "desc_v6_solution_stage_1_temp_C",
    "desc_v6_solution_stage_1_temp_C_missing",
    "desc_v6_solution_stage_1_time_h",
    "desc_v6_solution_stage_1_time_h_missing",
]
CONFIGS = {
    "correct_original_label": {
        "stable_process_label": False,
        "drop_ni_duplicate": False,
        "minimum_process_compositions": None,
    },
    "correct_stable_label": {
        "stable_process_label": True,
        "drop_ni_duplicate": False,
        "minimum_process_compositions": None,
    },
    "correct_stable_dedup": {
        "stable_process_label": True,
        "drop_ni_duplicate": True,
        "minimum_process_compositions": None,
    },
    "correct_stable_dedup_cov20": {
        "stable_process_label": True,
        "drop_ni_duplicate": True,
        "minimum_process_compositions": 20,
    },
}


def prepare_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    assert set(frame.task) == {"YS", "UTS"}
    assert frame.groupby("composition_key").role.nunique().max() == 1
    for raw in frame.composition_json:
        composition = json.loads(raw)
        assert len(composition) <= 20 and min(composition.values()) > 0
        assert abs(sum(composition.values()) - 1) < 1e-5
    return frame


def stable_process_label(frame: pd.DataFrame) -> pd.Series:
    flags = sorted(c for c in frame if c.startswith(EVIDENCE_PREFIX))
    labels = []
    for row in frame[flags].itertuples(index=False, name=None):
        present = [c[len(EVIDENCE_PREFIX):] for c, value in zip(flags, row) if float(value or 0) > 0]
        labels.append("+".join(present) if present else "unresolved")
    return pd.Series(labels, index=frame.index, dtype="object")


def process_base(column: str) -> bool:
    return column.startswith((
        "desc_v2_solution_", "desc_v2_aging_", "desc_v2_annealing_",
        "desc_v2_homogenization_", "desc_v2_tempering_", "desc_v2_cold_reduction_",
        "desc_v6_solution_", "desc_v6_precipitation_", "desc_v6_test_strain_rate_",
        "desc_v6_grain_", "desc_v6_phase_",
    )) and not column.endswith("_missing")


def view(frame: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    result = frame.copy()
    if config["stable_process_label"]:
        result["process_label"] = stable_process_label(result)
    drop = [c for c in result if c.startswith("desc_v2_flag_")]
    drop += [c for c in result if c.startswith("desc_v8_") and not c.startswith(EVIDENCE_PREFIX)]
    if config["drop_ni_duplicate"]:
        drop += NI_DUPLICATES
    coverage = {}
    minimum = config["minimum_process_compositions"]
    if minimum is not None:
        train = result[result.role.eq("train")]
        for column in [c for c in result if process_base(c)]:
            values = pd.to_numeric(train[column], errors="coerce")
            count = int(train.loc[values.notna(), "composition_key"].nunique())
            coverage[column] = count
            if count < minimum:
                drop.append(column)
                if column + "_missing" in result:
                    drop.append(column + "_missing")
    drop = sorted(set(c for c in drop if c in result))
    result = result.drop(columns=drop)
    return result, {
        "config": config,
        "dropped_columns": drop,
        "process_observed_composition_counts": coverage,
        "process_label_semantics": "positive evidence flag set" if config["stable_process_label"] else "frozen legacy label",
    }


def discontinuous(column: str) -> bool:
    return column.endswith("_span") or column in {
        "desc_component_count",
        "desc_phys_entropy_to_component_count",
        "desc_phys_dilute_fraction",
    }


class CanonicalContinuityModel(base.ProcessAdapterTransformer):
    def __init__(self, *, numeric_columns, **kwargs):
        super().__init__(**kwargs)
        mask = np.asarray([0.0 if discontinuous(column) else 1.0 for column in numeric_columns], dtype=np.float32)
        self.register_buffer("numeric_mask", torch.as_tensor(mask))

    def _composition(self, ids, fractions):
        x = self.element_projection(self.element_features[ids]) + self.learned_element(ids) + self.fraction_encoder(fractions)
        n = ids.shape[1]
        heads = self.transformer.layers[0].self_attn.num_heads
        prior = fractions.clamp_min(torch.finfo(fractions.dtype).tiny).log()
        prior = prior.masked_fill(ids.eq(0) | fractions.le(0), float("-inf"))
        attention_mask = prior[:, None, None, :].expand(-1, heads, n, -1).reshape(-1, n, n)
        for layer in self.transformer.layers:
            normalized = layer.norm1(x)
            attended = layer.self_attn(normalized, normalized, normalized, attn_mask=attention_mask, need_weights=False)[0]
            x = x + layer.dropout1(attended)
            normalized = layer.norm2(x)
            feed_forward = layer.linear2(layer.dropout(layer.activation(layer.linear1(normalized))))
            x = x + layer.dropout2(feed_forward)
        if self.transformer.norm is not None:
            x = self.transformer.norm(x)
        weight = fractions.unsqueeze(-1)
        return (x * weight).sum(1) / weight.sum(1).clamp_min(1e-8)

    def forward(self, batch):
        composition = self._composition(batch["element_ids"], batch["fractions"])
        z = self.fusion(torch.cat([
            composition,
            self.numeric_encoder(batch["numeric"] * self.numeric_mask),
            self.process_embedding(batch["process"]),
            self.domain_embedding(batch["domain"]),
        ], dim=1))
        adapter_delta = torch.zeros_like(z)
        for domain_id, adapter in enumerate(self.adapters):
            adapter_delta = adapter_delta + adapter(z) * batch["domain"].eq(domain_id).unsqueeze(1)
        z = z + adapter_delta
        domain = batch["domain"]
        raw_ys = self.ys_mean[domain] + self.ys_std[domain] * self.ys_head(z).squeeze(1)
        ys = 10 * nn.functional.softplus(raw_ys / 10)
        uts = ys + self.delta_scale[domain] * nn.functional.softplus(self.delta_head(z).squeeze(1))
        return torch.stack([ys, uts], dim=1)


def model_for(pre):
    mean, std, delta = base.old.domain_scales(pre)
    return CanonicalContinuityModel(
        numeric_columns=pre.numeric_cols,
        element_features=pre.element_features,
        n_numeric=len(pre.numeric_cols),
        n_domains=len(pre.vocab.domains),
        n_processes=len(pre.vocab.processes),
        ys_mean=mean,
        ys_std=std,
        delta_scale=delta,
    ).to(DEVICE)


def preprocessor_json(pre, config, view_audit, training_keys):
    return {
        "numeric_cols": pre.numeric_cols,
        "numeric_median": pre.numeric_median,
        "numeric_mean": pre.numeric_mean,
        "numeric_std": pre.numeric_std,
        "domains": pre.vocab.domains,
        "processes": pre.vocab.processes,
        "tasks": pre.vocab.tasks,
        "symbol_to_z": pre.symbol_to_z,
        "target_stats": {f"{key[0]}/{key[1]}": list(value) for key, value in pre.target_stats.items()},
        "training_composition_keys": sorted(training_keys),
        "masked_discontinuous_columns": [c for c in pre.numeric_cols if discontinuous(c)],
        "scaling": "legacy train-only median fill, with explicit missing masks",
        "config": config,
        "view_audit": view_audit,
    }


def architecture_checks(model, pre, frame):
    sample = frame[frame.role.eq("validation")].head(8)
    encoded = pre.encode_rows(sample)
    batch = {key: torch.as_tensor(encoded[key], device=DEVICE) for key in ["element_ids", "fractions", "numeric", "domain", "process"]}
    model.eval()
    with torch.no_grad():
        first = model(batch)
        order = torch.arange(batch["element_ids"].shape[1] - 1, -1, -1, device=DEVICE)
        permuted = dict(batch)
        permuted["element_ids"] = batch["element_ids"][:, order]
        permuted["fractions"] = batch["fractions"][:, order]
        second = model(permuted)
    torch.testing.assert_close(first, second, rtol=1e-5, atol=0.003)
    assert torch.isfinite(first).all() and torch.all(first[:, 1] >= first[:, 0])
    return {"permutation_invariance": True, "finite_outputs": True, "ordered_UTS_ge_YS": True}


def fit(frame, config_name, config, view_audit, seed, out):
    out.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    train_idx = np.flatnonzero(frame.role.eq("train"))
    validation_idx = np.flatnonzero(frame.role.eq("validation"))
    training_keys = set(frame.iloc[train_idx].composition_key)
    assert not training_keys & set(frame.iloc[validation_idx].composition_key)
    base.old.seed_everything(seed)
    pre = base.PhysicsPreprocessor(frame, train_idx)
    encoded = pre.encode_rows(frame)
    model = model_for(pre)
    train_loader, validation_loader, _ = base.old.make_loaders(frame, encoded, train_idx, validation_idx, validation_idx, seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=2e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, MAX_EPOCHS, eta_min=3e-5)
    best_score = math.inf
    best_state = None
    best_epoch = 0
    stale = 0
    history = []
    for epoch in range(MAX_EPOCHS):
        model.train()
        losses = []
        for batch in train_loader:
            batch = base.old.move_batch(batch, DEVICE)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(batch)
            output_index = torch.where(batch["task"].eq(pre.vocab.task_to_id["YS"]), 0, 1)
            selected = outputs.gather(1, output_index[:, None]).squeeze(1)
            error = (selected - batch["target"]) / batch["target_std"]
            loss = nn.functional.smooth_l1_loss(error, torch.zeros_like(error), beta=0.5)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach()))
        scheduler.step()
        if epoch % 2 == 0 or epoch == MAX_EPOCHS - 1:
            predictions = base.old.predict(model, validation_loader, frame, DEVICE)
            metrics = base.full_metrics(predictions)
            score = float(metrics["macro_domain_task_MAE"])
            history.append({"epoch": epoch + 1, "loss": float(np.mean(losses)), "validation_macro_MAE": score})
            if score < best_score - 0.05:
                best_score = score
                best_state = copy.deepcopy(model.state_dict())
                best_epoch = epoch + 1
                stale = 0
            else:
                stale += 2
            if epoch % 10 == 0:
                print(json.dumps({"run": out.name, **history[-1]}), flush=True)
            if stale >= PATIENCE:
                break
    model.load_state_dict(best_state)
    predictions = base.old.predict(model, validation_loader, frame, DEVICE)
    validation_metrics = base.full_metrics(predictions)
    torch.save(best_state, out / "model_state.pt")
    save(out / "preprocessor.json", preprocessor_json(pre, config, view_audit, training_keys))
    save(out / "validation_predictions.json", json.loads(predictions.to_json(orient="records")))
    report = {
        "config_name": config_name,
        "config": config,
        "seed": seed,
        "best_epoch": best_epoch,
        "validation_macro_MAE": validation_metrics["macro_domain_task_MAE"],
        "validation_metrics": validation_metrics,
        "history": history,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "architecture_checks": architecture_checks(model, pre, frame),
        "checkpoint_sha256": sha(out / "model_state.pt"),
        "preprocessor_sha256": sha(out / "preprocessor.json"),
        "elapsed_seconds": time.perf_counter() - started,
        "test_evaluated": False,
        "production_release_authorized": False,
    }
    save(out / "report.json", report)
    print(json.dumps({"completed": out.name, "validation_macro_MAE": report["validation_macro_MAE"]}), flush=True)
    return report


def restore(data, config, run_dir):
    frame, _ = view(data, config)
    pre = base.PhysicsPreprocessor(frame, np.flatnonzero(frame.role.eq("train")))
    saved = json.loads((run_dir / "preprocessor.json").read_text())
    assert pre.numeric_cols == saved["numeric_cols"]
    assert pre.numeric_mean == saved["numeric_mean"]
    assert pre.numeric_std == saved["numeric_std"]
    model = model_for(pre)
    model.load_state_dict(torch.load(run_dir / "model_state.pt", map_location=DEVICE, weights_only=True), strict=True)
    model.eval()
    return frame, pre, model


def evaluate(frame, pre, model, roles):
    encoded = pre.encode_rows(frame)
    result = {}
    for role in roles:
        idx = np.flatnonzero(frame.role.eq(role))
        loader = DataLoader(base.IndexedDataset(encoded, idx), batch_size=512, shuffle=False)
        prediction = base.old.predict(model, loader, frame, DEVICE)
        prediction["role"] = role
        result[role] = prediction
    return result


def ensemble(parts):
    output = parts[0].sort_values("row_index").reset_index(drop=True).copy()
    aligned = [part.sort_values("row_index").reset_index(drop=True) for part in parts]
    for part in aligned[1:]:
        assert output.record_id.tolist() == part.record_id.tolist()
    output["predicted_YS"] = np.mean([part.predicted_YS.to_numpy() for part in aligned], axis=0)
    output["predicted_UTS"] = np.mean([part.predicted_UTS.to_numpy() for part in aligned], axis=0)
    output["prediction_seed_std"] = np.std([part.prediction.to_numpy() for part in aligned], axis=0)
    output["prediction"] = np.where(output.task.eq("YS"), output.predicted_YS, output.predicted_UTS)
    output["order_violation_mpa"] = output.predicted_YS - output.predicted_UTS
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(2)
    data_path = args.data / "dataset_repaired.csv"
    data = prepare_frame(data_path)
    source_hashes = json.loads((args.data / "SOURCE_HASHES.json").read_text())
    assert all(sha(path) == digest for path, digest in source_hashes.items())
    protocol = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data": str(data_path),
        "data_sha256": sha(data_path),
        "configs": CONFIGS,
        "seeds": SEEDS,
        "selection_metric": "mean validation macro domain-task MAE",
        "test_not_used_for_selection": True,
        "max_epochs": MAX_EPOCHS,
        "patience": PATIENCE,
        "new_physics_loss": False,
        "new_blind_test": False,
        "production_release_authorized": False,
    }
    save(args.out / "FROZEN_PROTOCOL.json", protocol)
    code = args.out / "code_snapshot"
    code.mkdir()
    for name in ["train_v8_canonical.py", "repair_process_data_v8.py"]:
        shutil.copy2(ROOT / name, code / name)
    for name in ["train_process_adapter_v2.py", "run_hard_constraint_transformer_cv.py", "run_physics_informed_cv.py", "run_family_adapter_multitask.py"]:
        shutil.copy2(FOUNDATION_ROOT / name, code / name)

    reports = []
    for config_name, config in CONFIGS.items():
        frame, audit = view(data, config)
        for seed in SEEDS:
            report = fit(frame, config_name, config, audit, seed, args.out / f"{config_name}_seed_{seed}")
            reports.append(report)
            save(args.out / "VALIDATION_RESULTS.json", reports)

    families = []
    for config_name in CONFIGS:
        rows = [report for report in reports if report["config_name"] == config_name]
        scores = np.asarray([row["validation_macro_MAE"] for row in rows])
        families.append({
            "config_name": config_name,
            "validation_macro_MAE_mean": float(scores.mean()),
            "validation_macro_MAE_std": float(scores.std()),
            "by_seed": {str(row["seed"]): row["validation_macro_MAE"] for row in rows},
        })
    families.sort(key=lambda row: (row["validation_macro_MAE_mean"], row["validation_macro_MAE_std"], row["config_name"]))
    selected_family = families[0]["config_name"]
    selected_runs = [row for row in reports if row["config_name"] == selected_family]
    selected_single = min(selected_runs, key=lambda row: (row["validation_macro_MAE"], row["seed"]))
    selection = {
        "family_summary": families,
        "selected_family": selected_family,
        "selected_single_seed": selected_single["seed"],
        "selected_without_test_metrics": True,
    }
    save(args.out / "VALIDATION_SELECTION.json", selection)

    role_parts = {role: [] for role in ["calibration", "test", "source_challenge"]}
    single_predictions = None
    for report in selected_runs:
        seed = report["seed"]
        run_dir = args.out / f"{selected_family}_seed_{seed}"
        frame, pre, model = restore(data, CONFIGS[selected_family], run_dir)
        evaluated = evaluate(frame, pre, model, role_parts)
        save(run_dir / "selected_family_evaluation.json", {role: base.full_metrics(prediction) for role, prediction in evaluated.items()})
        for role, prediction in evaluated.items():
            role_parts[role].append(prediction)
        if seed == selected_single["seed"]:
            single_predictions = evaluated
        del model, pre
        torch.cuda.empty_cache()
    ensemble_predictions = {role: ensemble(parts) for role, parts in role_parts.items()}
    ensemble_metrics = {role: base.full_metrics(prediction) for role, prediction in ensemble_predictions.items()}
    single_metrics = {role: base.full_metrics(prediction) for role, prediction in single_predictions.items()}
    for role, prediction in ensemble_predictions.items():
        save(args.out / f"SELECTED_FAMILY_ENSEMBLE_{role.upper()}_PREDICTIONS.json", json.loads(prediction.to_json(orient="records")))
    selected_dir = args.out / "selected_single_checkpoint"
    selected_dir.mkdir()
    source_dir = args.out / f"{selected_family}_seed_{selected_single['seed']}"
    shutil.copy2(source_dir / "model_state.pt", selected_dir / "model_state.pt")
    shutil.copy2(source_dir / "preprocessor.json", selected_dir / "preprocessor.json")
    final = {
        **selection,
        "selected_single_validation_metrics": selected_single["validation_metrics"],
        "selected_single_metrics": single_metrics,
        "selected_family_ensemble_metrics": ensemble_metrics,
        "selected_single_checkpoint_sha256": sha(selected_dir / "model_state.pt"),
        "selected_single_preprocessor_sha256": sha(selected_dir / "preprocessor.json"),
        "test_has_been_seen_in_prior_experiments": True,
        "production_release_authorized": False,
    }
    save(args.out / "FINAL_RESULTS.json", final)
    assert all(sha(path) == digest for path, digest in source_hashes.items())
    save(args.out / "COMPLETED.json", {
        "validation_runs": len(reports),
        "selected_family_test_runs": len(selected_runs),
        "source_hashes_unchanged": True,
        "serving_repositories_changed": False,
        "production_release_authorized": False,
    })
    print(json.dumps({
        "selected_family": selected_family,
        "selected_single_seed": selected_single["seed"],
        "single_test_macro_MAE": single_metrics["test"]["macro_domain_task_MAE"],
        "ensemble_test_macro_MAE": ensemble_metrics["test"]["macro_domain_task_MAE"],
    }), flush=True)


if __name__ == "__main__":
    main()
