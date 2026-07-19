#!/usr/bin/env python3
"""Run an end-to-end MatterGen smoke test for a refractory HEA design case."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.material_workflow.pipeline import run_new_material_pipeline
from src.material_workflow.schemas import GenerationConstraint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--taskid", default="demo-nb-mo-ta-w-alloy")
    parser.add_argument("--candidates", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.candidates <= 8:
        parser.error("--candidates must be between 1 and 8 for this smoke test")

    # This script itself is launched inside mattergen-py310. Avoid nesting
    # ``micromamba run`` (the online ai4m service still uses that isolation).
    os.environ.setdefault("MATTERGEN_ENV_PREFIX", "")
    os.environ.setdefault("MATTERGEN_ENV", "")

    constraints = GenerationConstraint(
        taskid=args.taskid,
        raw_requirement=(
            "Generate Nb-Mo-Ta-W refractory high-entropy alloy crystal candidates for "
            "high-temperature structural screening."
        ),
        allowed_elements=["Nb", "Mo", "Ta", "W"],
        target_properties={"energy_above_hull": 0.05},
        validation_targets={
            "high_temperature_strength": None,
            "creep_resistance": None,
            "oxidation_resistance": None,
        },
        notes=[
            "MatterGen target is a low energy-above-hull generation guide, not a verified result.",
            "Mechanical and oxidation targets require MatterSim/DFT/experiment after generation.",
        ],
    )
    result = run_new_material_pipeline(
        constraints=constraints,
        results_root=REPO_ROOT / "src/MNS_CaseHub/cases/material_discovery_demo/results/new_material",
        max_candidates=args.candidates,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
