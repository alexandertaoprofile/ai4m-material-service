"""Read-only readiness check; never installs/downloads or starts generation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env.omatg.example")
    args = parser.parse_args()
    environment = os.environ.copy()
    for line in args.env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            environment.setdefault(key.strip(), value.strip())
    records, errors, warnings = [], [], []

    def file(key, path):
        path = Path(path)
        okay = path.exists()
        records.append({"check": key, "status": "ok" if okay else "failed", "path": str(path)})
        if not okay:
            errors.append(key + " missing")

    root = Path(environment["OMATG_CHECKPOINT_ROOT"])
    for mode in ("MP-20-DNG/Linear-SDE-Gamma", "Alex-MP-20-CSP/Trig-SDE-Gamma"):
        for name in ("checkpoint.ckpt", "train.yaml"):
            file(mode + "/" + name, root / mode / name)
    file("OMatG source", environment["OMATG_SOURCE_DIR"])
    file("MatterSim checkpoint", environment["MATTERSIM_CHECKPOINT"])
    # Existing reference uses lmdb.open(subdir=False), one file not data.mdb.
    file("MP2020 LMDB", Path(environment["MATTERSIM_REFERENCE_CACHE_DIR"]) / "reference_MP2020correction")
    for key in ("OMATG_RUNTIME_ROOT", "OMATG_PHASE_DIAGRAM_CACHE_DIR", "MATTERSIM_TMPDIR", "ALIGNN_CHECKPOINT_ROOT"):
        if not Path(environment[key]).resolve().is_relative_to(Path("/data")):
            errors.append(key + " must remain under /data")
    cache = Path(environment["OMATG_RUNTIME_ROOT"])
    environment.update(TMPDIR=str(cache / "tmp"), XDG_CACHE_HOME=str(cache / "cache"), MPLCONFIGDIR=str(cache / "matplotlib"), HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    workers = {
        "OMATG_PYTHON": "import torch,lmdb,lightning,pymatgen; assert torch.cuda.is_available(); print(torch.__version__)",
        "MATTERSIM_PYTHON": "import torch,pymatgen,ase,lmdb; from mattersim.forcefield.potential import Potential; from mattersim.applications.batch_relax import BatchRelaxer; print(torch.__version__)",
        "ALIGNN_PYTHON": "import sys,os; sys.path.append(os.environ['ALIGNN_SITE_PACKAGES']); import torch; from alignn.models.alignn import ALIGNN; from alignn.graphs import Graph; from jarvis.core.atoms import Atoms; print(torch.__version__)",
    }
    for key, script in workers.items():
        file(key, environment[key])
        try:
            completed = subprocess.run([environment[key], "-c", script], env=environment, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=40)
            records.append({"check": key + " imports", "status": "ok" if completed.returncode == 0 else "failed", "torch": completed.stdout.strip()[-100:]})
            if completed.returncode:
                errors.append(key + " import/runtime failure; inspect the worker environment")
        except (OSError, subprocess.TimeoutExpired):
            errors.append(key + " unavailable")
    heads = ("jv_mbj_bandgap_alignn", "jv_bulk_modulus_kv_alignn", "jv_shear_modulus_gv_alignn", "jv_avg_elec_mass_alignn", "jv_avg_hole_mass_alignn")
    def available(name):
        return any((path / (name + ".zip")).is_file() for path in (Path(environment["ALIGNN_CHECKPOINT_ROOT"]), Path(environment["ALIGNN_SITE_PACKAGES"]) / "alignn"))
    for head in heads:
        records.append({"check": head, "status": "ok" if available(head) else "failed"})
        if not available(head):
            errors.append(head + " checkpoint missing")
    if not available("jv_epsx_alignn"):
        warnings.append("dielectric_constant_x unavailable: disable this popup option; missing predictions never pass a hard constraint")
    print(json.dumps({"ready": not errors, "checks": records, "errors": errors, "warnings": warnings, "mutation": "none; no installation, download or generation"}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
