#!/usr/bin/env python3
"""Assemble source and resource files and create a local configuration template."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil

from runtime_resources import ROOT, check_file, relative

SERVICES = {"1105": "material_selection", "1111": "material_composition_optimization",
            "1115": "material_discovery", "1116": "material_validation"}


def copy_checked(source, target):
    if target.is_symlink():
        raise ValueError(f"Linked target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if source.read_bytes() != target.read_bytes():
            raise ValueError(f"Existing file differs: {target}")
        return
    shutil.copyfile(source, target)


def settings(resource_root, env_root):
    runtime = resource_root / "run"
    common = {"TMPDIR": str(runtime / "tmp"), "XDG_CACHE_HOME": str(runtime / "cache"),
              "MPLCONFIGDIR": str(runtime / "matplotlib"), "OMP_NUM_THREADS": "1",
              "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    return {
        "1105": {**common, "PORT": "1105", "MATURE_MATERIAL_CATALOG_ROOT": str(resource_root / "data/1105/catalog")},
        "1111": {**common, "PORT": "1111", "HEA_SURROGATE_ROOT": str(resource_root / "packages/hea_surrogate"),
                 "ALLOY_MODEL_ROOT": str(resource_root / "packages/hea_surrogate/models"),
                 "HEA_SURROGATE_ENV_PREFIX": str(env_root / "numeric"),
                 "MATERIAL_RECIPE_DISPLAY_ROOT": str(ROOT / "packages/recipe_report"),
                 "MATERIAL_RECIPE_RUNTIME": str(resource_root / "packages/recipe/runtime"),
                 "MATERIAL_RECIPE_PYTHON": str(env_root / "recipe/bin/python"),
                 "MATERIAL_LEGACY_PYTHON": str(env_root / "numeric/bin/python"),
                 "MATERIAL_RESEARCH_PYTHON": str(env_root / "recipe/bin/python"),
                 "MICROMAMBA_EXECUTABLE": str(ROOT / "tools/worker_python.py"), "MAMBA_ROOT_PREFIX": str(env_root / "mamba"),
                 "UNIFIED_TENSILE_SHADOW_ENABLED": "0"},
        "1115": {**common, "PORT": "1115", "NEW_MATERIAL_GENERATOR": "omatg", "OMATG_DEFAULT_CANDIDATES": "10",
                 "OMATG_SOURCE_DIR": str(ROOT / "third_party/OMatG"), "OMATG_PYTHON": str(env_root / "omatg/bin/python"),
                 "OMATG_CHECKPOINT_ROOT": str(resource_root / "weights/omatg"), "OMATG_RUNTIME_ROOT": str(runtime / "1115"),
                 "OMATG_GPU_LOCK": str(runtime / "1115/gpu.lock"),
                 "MATTERSIM_PYTHON": str(env_root / "numeric/bin/python"), "MATTERSIM_ENV_PREFIX": str(env_root / "numeric"),
                 "MATTERGEN_ENV_PREFIX": str(env_root / "numeric"), "MATTERSIM_ENABLED": "1",
                 "MATTERSIM_CHECKPOINT": str(resource_root / "weights/omatg/mattersim-v1.0.0-1M.pth"),
                 "MATTERSIM_REFERENCE_CACHE_DIR": str(resource_root / "reference"),
                 "OMATG_PHASE_DIAGRAM_CACHE_DIR": str(runtime / "1115/phase_diagrams"),
                 "MATTERSIM_TMPDIR": str(runtime / "1115/tmp"), "ALIGNN_ENABLED": "1",
                 "ALIGNN_PYTHON": str(env_root / "alignn/bin/python"), "ALIGNN_SITE_PACKAGES": "",
                 "ALIGNN_CHECKPOINT_ROOT": str(resource_root / "weights/alignn"),
                 "OMATG_TIMEOUT_SEC": "300", "MATTERSIM_TIMEOUT_SEC": "900", "ALIGNN_TIMEOUT_SEC": "300",
                 "OMATG_QUEUE_TIMEOUT_SEC": "900", "OMATG_RELAX_FMAX": "0.05", "OMATG_RELAX_MAX_STEPS": "500"},
        "1116": {**common, "PORT": "1116", "W14_MLIP_ROOT": str(resource_root / "evidence/DeepMD-W-14-commercial-level"),
                 "W14_MD_ROOT": str(resource_root / "evidence/DeepMD_W-14_commercial-level"), "ENABLE_EXECUTION": "false"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--env-root", type=Path, required=True)
    parser.add_argument("--services", nargs="+", choices=SERVICES, default=list(SERVICES))
    parser.add_argument("--keep-existing-config", action="store_true", help="Preserve previously generated configuration templates")
    args = parser.parse_args()
    resources, env_root = args.resource_root.resolve(), args.env_root.absolute()
    if os.name == "posix":
        worker = ROOT / "tools/worker_python.py"
        worker.chmod(worker.stat().st_mode | 0o111)
    manifest = json.loads((ROOT / "resources/runtime-manifest.json").read_text())
    for row in manifest["files"]:
        if row["in_git"] and row["path"].startswith("packages/") and "1111" in args.services:
            source = ROOT / relative(row["path"])
            if check_file(source, row) != "ok":
                raise ValueError(f"Source snapshot differs: {source}")
            copy_checked(source, resources / row["path"])
    if "1105" in args.services:
        source = resources / "data/1105/fluid_property_evidence.sqlite"
        record = next(row for row in manifest["files"] if row["path"] == "data/1105/fluid_property_evidence.sqlite")
        if check_file(source, record) != "ok":
            raise ValueError("Download/install the 1105 resource group first")
        target = ROOT / "material_selection/data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite"
        copy_checked(source, target)
        catalog = resources / "data/1105/catalog"
        for row in manifest["files"]:
            if row["path"].startswith("data/1105/catalog/"):
                original = resources / relative(row["path"])
                if check_file(original, row) != "ok":
                    raise ValueError(f"Catalog resource differs: {original}")
                local = original.relative_to(catalog)
                # The deployed incremental bundles are omitted by the old
                # broad Git ignore rules. Restore them for existing test paths;
                # root catalog files remain the dated source snapshot in Git.
                if len(local.parts) > 1:
                    copy_checked(original, ROOT / "material_selection/data/processed" / local)
    for service in args.services:
        target = ROOT / SERVICES[service] / ".env.runtime.example"
        values = settings(resources, env_root)[service]
        for key in ("TMPDIR", "XDG_CACHE_HOME", "MPLCONFIGDIR", "MATTERSIM_TMPDIR"):
            if values.get(key):
                Path(values[key]).mkdir(parents=True, exist_ok=True)
        if target.exists():
            if args.keep_existing_config:
                print(f"Preserved existing configuration: {target}")
                continue
            raise ValueError(f"Generated template already exists; preserve your changes: {target}")
        with target.open("x", encoding="utf-8") as stream:
            stream.write("# Generated local paths. Add connection credentials from .env.example.\n")
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
        print(target)


if __name__ == "__main__":
    main()
