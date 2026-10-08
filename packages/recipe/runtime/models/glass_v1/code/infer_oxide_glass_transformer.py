#!/usr/bin/env python3
"""Raw-composition inference wrapper for the offline oxide-glass candidate."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import torch

from train_oxide_glass_transformer import OxideGlassTransformer, descriptor_vector, target_inverse


UNIT = {
    "TG": "degC", "CTE_BELOW_TG": "1e-7_K-1", "CTE_FIXED_T": "1e-7_K-1",
    "DENSITY_20C": "g_cm-3", "YOUNG_MODULUS": "GPa", "POISSON_RATIO": "1",
    "THERMAL_CONDUCTIVITY": "W_m-1_K-1",
}
FORBIDDEN = {"F", "Cl", "Br", "I", "S", "Se", "Te", "N", "C", "H"}


def is_oxide_formula(name: str) -> bool:
    compact = re.sub(r"[^A-Za-z0-9()]", "", name)
    elements = re.findall(r"[A-Z][a-z]?", compact)
    return bool(elements) and "O" in elements and not (set(elements) & FORBIDDEN)


def load_model(path: Path, device: torch.device):
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    model = OxideGlassTransformer(**checkpoint["constructor"])
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()
    return checkpoint, model


def validate_request(request: dict, pre: dict) -> tuple[list[tuple[str, float]], str, float | None, list[str]]:
    composition = request.get("composition_mol_percent")
    if not isinstance(composition, dict) or not composition:
        raise ValueError("composition_mol_percent must be a non-empty object")
    clean = {}
    for name, raw in composition.items():
        if not isinstance(name, str) or not is_oxide_formula(name):
            raise ValueError(f"not an admitted oxide token: {name!r}")
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid mol% for {name}: {raw!r}")
        if value > 0:
            clean[name] = clean.get(name, 0.0) + value
    if "SiO2" not in clean:
        raise ValueError("this candidate is restricted to SiO2-containing oxide glasses")
    total = sum(clean.values())
    if not 99.0 <= total <= 101.0:
        raise ValueError(f"composition sum must be within 99-101 mol%, got {total:.6g}")
    if not 1 <= len(clean) <= int(pre["max_tokens"]):
        raise ValueError(f"component count must be 1-{pre['max_tokens']}")
    unknown = sorted(set(clean) - set(pre["vocab"]))
    if unknown:
        raise ValueError(f"oxide tokens outside training vocabulary: {unknown}")
    components = sorted((name, value / total) for name, value in clean.items())
    task = str(request.get("task", ""))
    if task not in pre["tasks"]:
        raise ValueError(f"task must be one of {pre['tasks']}")
    supplied_temp = request.get("condition_temperature_C")
    if task == "CTE_FIXED_T":
        if supplied_temp is None:
            raise ValueError("condition_temperature_C is required for CTE_FIXED_T")
        condition = float(supplied_temp)
        if not 45.0 <= condition <= 360.0:
            raise ValueError("CTE_FIXED_T is restricted to the trained 55-350 C neighborhood")
    elif task == "DENSITY_20C":
        condition = 20.0
        if supplied_temp is not None and abs(float(supplied_temp) - 20.0) > 1e-6:
            raise ValueError("DENSITY_20C is fixed at 20 C")
    else:
        condition = None
        if supplied_temp is not None:
            raise ValueError(f"condition_temperature_C is not defined for task {task}")
    warnings = []
    if task == "THERMAL_CONDUCTIVITY":
        warnings.append("exploratory_only_low_cross_source_R2")
    return components, task, condition, warnings


@torch.no_grad()
def infer(checkpoint: dict, model: torch.nn.Module, request: dict, device: torch.device) -> dict:
    pre = checkpoint["preprocessor"]
    components, task, condition, warnings = validate_request(request, pre)
    max_tokens = int(pre["max_tokens"])
    species = torch.zeros((1, max_tokens), dtype=torch.long, device=device)
    fractions = torch.zeros((1, max_tokens), dtype=torch.float32, device=device)
    for index, (name, value) in enumerate(components):
        species[0, index] = int(pre["vocab"][name])
        fractions[0, index] = float(value)
    descriptor = np.asarray(descriptor_vector(components), dtype=np.float32)
    descriptor = (descriptor - np.asarray(pre["descriptor_mean"], dtype=np.float32)) / np.asarray(pre["descriptor_std"], dtype=np.float32)
    if condition is None:
        condition_z, condition_mask = 0.0, 0.0
    else:
        condition_z = (condition - float(pre["condition_mean"])) / float(pre["condition_std"])
        condition_mask = 1.0
    batch = {
        "species": species, "fractions": fractions,
        "descriptors": torch.from_numpy(descriptor).unsqueeze(0).to(device),
        "task": torch.tensor([pre["tasks"].index(task)], dtype=torch.long, device=device),
        "condition": torch.tensor([condition_z], dtype=torch.float32, device=device),
        "condition_mask": torch.tensor([condition_mask], dtype=torch.float32, device=device),
    }
    prediction_z = float(model(batch).cpu().item())
    stats = pre["target_stats"][task]
    transformed = prediction_z * float(stats["std"]) + float(stats["mean"])
    prediction = float(target_inverse(task, np.asarray([transformed]))[0])
    return {
        "status": "offline_research_candidate_not_deployed",
        "model": checkpoint["model_name"],
        "task": task,
        "prediction": prediction,
        "unit": UNIT[task],
        "condition_temperature_C": condition,
        "normalized_composition_mol_fraction": dict(components),
        "warnings": warnings + ["license_review_required_before_commercial_deployment", "no_formulation_or_process_optimization_claim"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint, model = load_model(args.checkpoint, device)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    response = infer(checkpoint, model, request, device)
    payload = json.dumps(response, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
