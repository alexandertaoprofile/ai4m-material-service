"""Isolated OMatG worker: build input, apply DNG mask, infer and export CIFs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sys
import time
from itertools import product
from pathlib import Path


def species_mask_indices(numbers, width):
    """Validate on CPU before any CUDA indexing; OMatG uses Z-1 logits."""
    if not numbers or any(number < 1 or number > width for number in numbers):
        raise ValueError(f"Element mask exceeds OMatG species vocabulary 1–{width}")
    return [number - 1 for number in numbers]


def install_species_mask(symbols, anchor_plan=None):
    import torch
    from pymatgen.core import Element
    from omg.si.discrete_flow_matching_mask import DiscreteFlowMatchingMask
    numbers = [Element(symbol).Z for symbol in symbols]
    if not numbers:
        raise ValueError("Empty OMatG element mask")
    original = DiscreteFlowMatchingMask.integrate
    from omg.sampler.independent_sampler import IndependentSampler
    original_sample = IndependentSampler.sample_p_0
    cache = {}

    def anchors(batch_indices, reference):
        if not anchor_plan:
            return None
        if not cache:
            groups = batch_indices.detach().cpu().tolist()
            offsets = {}
            rows, labels = [], []
            for row, group in enumerate(groups):
                if group < 0 or group >= len(anchor_plan):
                    raise ValueError("Presence anchor template does not match sampling batch")
                site = offsets.get(group, 0)
                offsets[group] = site + 1
                if site in anchor_plan[group]:
                    rows.append(row)
                    labels.append(anchor_plan[group][site])
            if set(offsets) != set(range(len(anchor_plan))):
                raise ValueError("Presence anchor batch is incomplete")
            cache.update(rows=torch.tensor(rows, dtype=torch.long, device=reference.device),
                labels=torch.tensor(labels, dtype=reference.dtype, device=reference.device), size=reference.numel())
        if cache["size"] != reference.numel():
            raise ValueError("Presence anchor batch shape changed")
        # IndependentSampler constructs x_0 on CPU; Lightning moves the batch
        # to CUDA before coupled integration. Follow the actual species tensor.
        cache["rows"] = cache["rows"].to(reference.device)
        cache["labels"] = cache["labels"].to(device=reference.device, dtype=reference.dtype)
        return cache["rows"], cache["labels"]

    def condition(values, batch_indices):
        selected = anchors(batch_indices, values)
        if selected is not None:
            values[selected[0]] = selected[1]
        return values

    def conditioned_sample(self, x_1):
        sampled = original_sample(self, x_1)
        condition(sampled.species, sampled.batch)
        return sampled

    def restricted(self, model_function, x_t, time, time_step, batch_indices):
        condition(x_t, batch_indices)
        def masked(t, current):
            prediction = model_function(t, current)
            logits = prediction[0]
            allowed = torch.zeros(logits.shape[-1], dtype=torch.bool, device=logits.device)
            allowed[species_mask_indices(numbers, logits.shape[-1])] = True
            return (logits.masked_fill(~allowed, -torch.inf), *prediction[1:])
        integrated = original(self, masked, x_t, time, time_step, batch_indices)
        return condition(integrated, batch_indices)

    DiscreteFlowMatchingMask.integrate = restricted
    if anchor_plan:
        IndependentSampler.sample_p_0 = conditioned_sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    sys.path.insert(0, request["source_dir"])
    import lmdb
    import numpy as np
    import torch
    import yaml
    import omg
    from ase.io import read
    from pymatgen.core import Element
    from pymatgen.io.ase import AseAtomsAdaptor
    from crystal_checks import check_structure
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.material_workflow.omatg_contract import ELEMENTS, formula_counts
    from src.material_workflow.omatg_sampling import composition_errors, composition_summary, presence_anchor_plan

    started = time.monotonic()
    output = Path(request["output_dir"])
    constraints = request["constraints"]
    mode = constraints["generation_mode"]
    counts = formula_counts(constraints["target_formula"]) if mode == "csp" else {"H": constraints["num_atoms"]}
    if mode == "csp":
        counts = {symbol: number * constraints["formula_units"] for symbol, number in counts.items()}
    numbers = [Element(symbol).Z for symbol, count in counts.items() for _ in range(count)]
    grid = np.array(list(product((0.125, 0.375, 0.625, 0.875), repeat=3))[:len(numbers)])
    cell = np.eye(3) * 8
    record = {"cell": torch.as_tensor(cell, dtype=torch.float64), "pos": torch.as_tensor(grid @ cell, dtype=torch.float64), "atomic_numbers": torch.as_tensor(numbers, dtype=torch.int64)}
    lmdb_path = output / "inputs.lmdb"
    with lmdb.open(str(lmdb_path), subdir=False, map_size=64*1024*1024) as database:
        with database.begin(write=True) as transaction:
            for index in range(request["max_candidates"]):
                transaction.put(f"input-{index:04d}".encode(), pickle.dumps(record))
    config_path = Path(request["config_path"])
    config = yaml.safe_load(config_path.read_text())
    # Use a separate predict-only config; never load model training datasets.
    for name in ("train_dataset", "val_dataset", "pred_dataset"):
        config["data"][name]["init_args"].update(file_path=str(lmdb_path), lazy_storage=False, niggli_reduce=False)
    config["data"].update(batch_size=request["max_candidates"], num_workers=0, persistent_workers=False, pin_memory=False)
    config["trainer"] = {"accelerator": "gpu", "devices": 1, "precision": "32-true", "logger": False,
        "enable_checkpointing": False, "default_root_dir": str(output), "enable_progress_bar": True}
    xyz_path = output / "candidates.xyz"
    config["model"]["generation_xyz_filename"] = str(xyz_path)
    resolved_config = output / "predict.yaml"
    resolved_config.write_text(yaml.safe_dump(config), encoding="utf-8")
    anchor_plan = []
    if mode == "dng":
        symbols = constraints["allowed_elements"] or ELEMENTS
        symbols = [symbol for symbol in symbols if symbol not in constraints["excluded_elements"]]
        if constraints.get("dng_species_strategy") == "presence_anchors":
            required = [Element(symbol).Z for symbol in constraints["required_elements"]]
            minimum = constraints.get("min_distinct_elements", 1) if constraints["allowed_elements"] else max(1, len(required))
            anchor_plan = presence_anchor_plan([Element(symbol).Z for symbol in symbols], required,
                minimum, constraints["num_atoms"], request["max_candidates"], constraints["seed"])
            if not any(anchor_plan):
                anchor_plan = []
        install_species_mask(symbols, anchor_plan)
        print("OMATG_SPECIES_MASK=" + ",".join(symbols), flush=True)
        if anchor_plan:
            print("OMATG_PRESENCE_ANCHORS=" + json.dumps(anchor_plan), flush=True)
    sys.argv = ["omg", "predict", "--config", str(resolved_config), "--ckpt_path", request["checkpoint_path"], "--seed_everything", str(constraints["seed"])]
    from omg.main import main as omg_main
    omg_main()
    candidates = []
    for index, atoms in enumerate(read(str(xyz_path), index=":"), start=1):
        candidate_id = f"{mode}-{index:03d}"
        try:
            structure = AseAtomsAdaptor.get_structure(atoms)
            counts_actual = {symbol: int(number) for symbol, number in structure.composition.get_el_amt_dict().items()}
            elements = set(counts_actual)
            failures = composition_errors(counts_actual, constraints)
            if mode == "csp":
                if counts_actual != counts:
                    failures.append("组成未满足固定化学计量要求")
            composition_ok = not failures
            cif = output / f"{candidate_id}.cif"
            structure.to(filename=str(cif), fmt="cif")
            check = check_structure(structure)
            candidates.append({"candidate_id": candidate_id, "formula_pretty": structure.composition.reduced_formula,
                "cif_path": str(cif), "source": "omatg_" + mode,
                "metadata": {"mode": mode, "composition_ok": bool(composition_ok), "element_counts": counts_actual,
                    "composition_errors": failures, "presence_anchors": anchor_plan[index-1] if anchor_plan else {},
                    "geometry": check, "seed": constraints["seed"], "input_index": index}})
        except Exception as exc:
            candidates.append({"candidate_id": candidate_id, "source": "omatg_" + mode,
                "metadata": {"composition_ok": False, "geometry": {"is_valid": False, "errors": [str(exc)]}}})
    if len(candidates) != request["max_candidates"]:
        raise RuntimeError("OMatG output count differs from the requested sample count")
    checkpoint = Path(request["checkpoint_path"])
    manifest = {"taskid": constraints["taskid"], "status": "ok", "backend": "omatg", "candidates": candidates,
        "message": "OMatG 候选结构生成完成。", "metadata": {"requested_count": request["max_candidates"], "mode": mode,
            "wall_seconds": time.monotonic()-started, "model_provenance": {"model": request["model_name"],
                "architecture": "CSPNetFull + stochastic interpolants", "dataset": config["model"]["dataset_name"],
                "package_version": omg.__version__, "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(), "integration_time_steps": config["model"]["si"]["init_args"]["integration_time_steps"],
                "operation": "pretrained_inference", "element_mask": constraints["allowed_elements"], "seed": constraints["seed"],
                "dng_species_strategy": constraints.get("dng_species_strategy", "masked") if mode == "dng" else "fixed_formula",
                "presence_anchor_templates": anchor_plan, "service_conditioning": bool(anchor_plan)},
            "composition_summary": composition_summary(candidates)}}
    (output / "generation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OMATG_GENERATION_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
