# AI4M Materials Screening & Property Calculation Pipeline

## Overview

This service implements a structured, production-oriented materials
evaluation workflow:

MP (structure & basic screening) ↓ ADiT + Pymatgen (structure sanity &
stability gate) ↓ MACE (fast relax + short MD sanity check) ↓ Structured
assets (JSON / CIF / EXTXYZ / GLB)

This pipeline is designed for fast engineering-grade screening and
visualization, not high-precision thermodynamic production simulations.

------------------------------------------------------------------------

## Stage 1 --- Materials Project (MP)

Environment: mp-api-py311\
Script: tools/mp_export_assets.py

Outputs: - structure.cif - structure.glb (static visualization) -
summary.json - summary.md - manifest.json

Purpose: - Query stable structures - Export CIF + GLB - Generate
standardized manifest for frontend

------------------------------------------------------------------------

## Stage 2 --- ADiT + Pymatgen Stability Gate

Environment: adit-py310\
Script: tools/adit_pymatgen_eval.py

Outputs: - report.json - summary.md - manifest.json

Purpose: - Structure sanity checks - Basic geometric validation -
Stability gating before MACE

------------------------------------------------------------------------

## Stage 3 --- MACE Property Calculation

Environment: mace_ase\
Model: mace-mp-0b2-medium.model

### Fast Relax Mode

Arguments: --do-relax --relax-fmax 0.1 --relax-steps 200

Outputs: - relaxed.cif - relaxed.extxyz - summary.json - manifest.json

Purpose: - Quick structural relaxation - Force convergence check

------------------------------------------------------------------------

### Short MD Sanity Mode

Arguments: --do-md --md-steps 1000 --md-timestep-fs 0.25 --md-temp-K 300
--md-friction 0.20 --md-init-temp-K 300 --md-log-every 50
--md-tail-fraction 0.40

Outputs: - md_traj.extxyz - md_log.csv - md_final.cif - md_traj.glb
(animated) - summary.json - manifest.json

Purpose: - Short NVT Langevin MD - Detect structural instability -
Provide engineering sanity signal

------------------------------------------------------------------------

## Visualization

Static GLB: generated from structure.cif\
Animated GLB: generated from md_traj.extxyz

Tool: tools/extxyz_to_animated_glb.py

------------------------------------------------------------------------

## Runtime Model

-   Fast relax runs immediately
-   MD runs in background with GPU semaphore control
-   Frontend consumes manifest.json for asset rendering

------------------------------------------------------------------------

## Environments

MP → mp-api-py311\
ADiT → adit-py310\
MACE → mace_ase

------------------------------------------------------------------------

## Status

MP export: Stable\
ADiT evaluation: Stable\
MACE relax: Stable\
MACE MD: Stable\
Static + animated GLB: Working\
Frontend manifest integration: Working

Pipeline ready for engineering screening workflows.
