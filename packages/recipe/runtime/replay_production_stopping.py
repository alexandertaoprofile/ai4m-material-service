"""Read-only replay of the deployed proposal loops on saved 1111 requests.

This measures *actual* production stop conditions, not a synthetic fixed-size
random sample. No service/result/artifact file is changed. The replayed request
is the saved `effective_model_input`; only `random_seed` may be overridden.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

# The deployed JSON runner forces one BLAS/OpenMP thread before model imports.
# Replaying without this setting grossly overstates nickel latency.
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"

ROOT = Path("/data/se42/hea_surrogate")
TASK_ROOT = Path(
    "/data/se42/alpha_project/material_service_hub/"
    "material_composition_optimization/results/alloy_composition_optimization"
)
sys.path.insert(0, str(ROOT))

from src.api import aerospace_aluminum_runtime as al
from src.api import chip_glass_thermomechanical_runtime as glass
from src.api import copper_hotend_local_runtime as cu
from src.api import ni_superalloy_hotend_runtime as ni
from src.api import reusable_rocket_stainless_runtime as steel
from src.api import runtime as hea
from src.api import short_cf_thermomechanical_runtime as cf

DOMAINS = {
    "hea_mpea": (hea, "propose", "_evaluate_many", "batch_second"),
    "ni_superalloy_hot_end": (ni, "propose", "evaluate", "scalar"),
    "copper_hot_end_local_composition_v1": (cu, "propose", "_predict", "scalar"),
    "reusable_rocket_stainless": (steel, "propose", "_predict_many", "batch_first"),
    "aerospace_aluminum_composition_v1": (al, "propose", "_predict", "scalar"),
    "chip_glass_thermomechanical_family_v1": (glass, "propose_space", "_predict_properties_many", "batch_first"),
    "short_cf_thermomechanical_rve_v1": (cf, "propose_space", "evaluate", "scalar"),
}


def recent_manifests() -> dict[str, tuple[Path, dict]]:
    found = {}
    for path in sorted(TASK_ROOT.rglob("manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        domain = data.get("model_domain")
        if domain in DOMAINS and domain not in found and data.get("initial_candidates"):
            found[domain] = (path, data)
    return found


def measure(domain: str, path: Path, task: dict, seed: int) -> dict:
    module, proposal_name, inference_name, call_kind = DOMAINS[domain]
    scope = dict(task.get("requirement_interpretation", {}).get("effective_model_input") or {})
    if not scope:
        raise ValueError(f"{domain}: saved task lacks effective_model_input")
    scope["random_seed"] = seed
    original = getattr(module, inference_name)
    counts = {"inference_batches": 0, "inference_rows": 0, "ad_batches": 0, "ad_rows": 0}

    def instrument(*args, **kwargs):
        counts["inference_batches"] += 1
        counts["inference_rows"] += (
            len(args[0]) if call_kind == "batch_first" else
            len(args[1]) if call_kind == "batch_second" else 1
        )
        return original(*args, **kwargs)

    original_ad = getattr(steel, "_applicability_many") if domain == "reusable_rocket_stainless" else None

    def instrument_ad(*args, **kwargs):
        counts["ad_batches"] += 1
        counts["ad_rows"] += len(args[0])
        return original_ad(*args, **kwargs)

    t0 = time.perf_counter()
    with patch.object(module, inference_name, instrument):
        if original_ad is not None:
            with patch.object(steel, "_applicability_many", instrument_ad):
                result = getattr(module, proposal_name)(scope)
        else:
            result = getattr(module, proposal_name)(scope)
    seconds = time.perf_counter() - t0
    sampling = result.get("sampling", {})
    return {
        "domain": domain, "source_manifest": str(path), "taskid": task.get("taskid"),
        "seed": seed, "requested": scope.get("num_candidates", scope.get("candidate_count", 1000)),
        "attempts": sampling.get("attempts", sampling.get("generated")),
        "generated": sampling.get("generated"), "feasible": sampling.get("feasible"),
        "returned_rows": len(result.get("all_candidates", result.get("initial_candidates", []))),
        "stop_limit": {
            "hea_mpea": "candidate_count bounded to [50,5000]; at most 40 Dirichlet batches",
            "ni_superalloy_hot_end": "accepted K or attempts=30K; K bounded to [20,300]",
            "copper_hot_end_local_composition_v1": "accepted K or attempts=15K; K bounded to [3,40]",
            "reusable_rocket_stainless": "first K non-outside after at most 30K raw proposals; K bounded to [20,80]",
            "aerospace_aluminum_composition_v1": "accepted K or attempts=20K; K bounded to [3,30]",
            "chip_glass_thermomechanical_family_v1": "accepted K or attempts=80K; K in [1,500]",
            "short_cf_thermomechanical_rve_v1": "exactly K proposal attempts; K bounded to [1,200]",
        }[domain],
        "elapsed_seconds": seconds, **counts,
        "sampling": {k: sampling.get(k) for k in ("generated", "feasible", "attempts", "property_threshold_passed")},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--domains", nargs="*", choices=DOMAINS, default=list(DOMAINS))
    ap.add_argument("--seeds", nargs="*", type=int, default=[17, 29, 43])
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite existing result")
    tasks = recent_manifests()
    records = []
    for domain in args.domains:
        if domain not in tasks:
            records.append({"domain": domain, "error": "saved task not found"})
            continue
        path, task = tasks[domain]
        for seed in args.seeds:
            try:
                item = measure(domain, path, task, seed)
            except Exception as exc:
                item = {"domain": domain, "source_manifest": str(path), "seed": seed,
                        "error": f"{type(exc).__name__}: {exc}"}
            records.append(item)
            print(domain, seed, item.get("error", f"{item.get('attempts')} attempts / {item.get('inference_rows')} inferences"), flush=True)
    args.output.write_text(json.dumps({"scope": "read-only exact deployed proposer replay",
                                       "production_release_authorized": False, "records": records},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", args.output)


if __name__ == "__main__":
    main()
