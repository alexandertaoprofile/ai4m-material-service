from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .models import StageResult


@dataclass
class RunnerConfig:
    repo_root: str
    case_root: str = "src/MNS_CaseHub/cases/material_discovery_demo"
    mp_env: str = "mp-api-py311"
    adit_env: str = "adit-py310"
    mace_env: str = "mace_ase"
    mace_script: str = "/home/ubuntu/runtimes-packages/mace-ase/scripts/run_mace_stage.py"
    mace_model_path: str = "/home/ubuntu/runtimes-packages/mace-ase/models/mace-mp-0b2-medium.model"


def _run(cmd: List[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout or ""


def _task_s(task_id: str) -> str:
    return str(task_id).replace("/", "_")


def _find_latest(path_glob: str) -> Optional[str]:
    import glob

    cands = sorted(glob.glob(path_glob))
    return cands[-1] if cands else None


def run_mp(config: RunnerConfig, task_id: str, formula: str) -> StageResult:
    script = os.path.join(config.repo_root, "tools", "mp_export_assets.py")
    cmd = [
        "micromamba", "run", "-n", config.mp_env,
        "python", script,
        "--taskid", str(task_id),
        "--jobid", str(formula),
        "--formula", str(formula),
        "--prefer-stable",
    ]
    rc, out = _run(cmd, cwd=config.repo_root)
    manifest_glob = os.path.join(
        config.repo_root,
        config.case_root,
        "results",
        "mp",
        f"*{_task_s(task_id)}*",
        str(formula),
        "manifest.json",
    )
    manifest = _find_latest(manifest_glob)
    return StageResult(
        stage="mp",
        formula=formula,
        ok=(rc == 0),
        return_code=rc,
        command=cmd,
        stdout_tail=out[-8000:],
        manifest_path=manifest,
        error=None if rc == 0 else f"mp failed rc={rc}",
    )


def run_adit(config: RunnerConfig, task_id: str, formula: str) -> StageResult:
    mp_manifest_glob = os.path.join(
        config.repo_root,
        config.case_root,
        "results",
        "mp",
        f"*{_task_s(task_id)}*",
        str(formula),
        "manifest.json",
    )
    mp_manifest = _find_latest(mp_manifest_glob)
    if not mp_manifest:
        return StageResult(stage="adit", formula=formula, ok=False, error="mp manifest not found")

    script = os.path.join(config.repo_root, "tools", "adit_pymatgen_eval.py")
    cmd = [
        "micromamba", "run", "-n", config.adit_env,
        "python", script,
        "--mp_manifest", mp_manifest,
    ]
    rc, out = _run(cmd, cwd=config.repo_root)
    report_glob = os.path.join(
        config.repo_root,
        config.case_root,
        "results",
        "adit_pymatgen",
        f"*{_task_s(task_id)}*",
        str(formula),
        "report.json",
    )
    report = _find_latest(report_glob)
    return StageResult(
        stage="adit",
        formula=formula,
        ok=(rc == 0),
        return_code=rc,
        command=cmd,
        stdout_tail=out[-8000:],
        report_path=report,
        error=None if rc == 0 else f"adit failed rc={rc}",
    )


def run_mace(config: RunnerConfig, task_id: str, formula: str, fast: bool = True) -> StageResult:
    import json

    report_glob = os.path.join(
        config.repo_root,
        config.case_root,
        "results",
        "adit_pymatgen",
        f"*{_task_s(task_id)}*",
        str(formula),
        "report.json",
    )
    adit_report = _find_latest(report_glob)
    inp_cif = None
    if adit_report and os.path.exists(adit_report):
        try:
            with open(adit_report, "r", encoding="utf-8") as f:
                rep = json.load(f)
            files = rep.get("files") if isinstance(rep.get("files"), dict) else {}
            c = files.get("structure_cif", "")
            if c:
                inp_cif = c if os.path.isabs(c) else os.path.abspath(os.path.join(os.path.dirname(adit_report), c))
        except Exception:
            inp_cif = None

    if not inp_cif or not os.path.exists(inp_cif):
        return StageResult(stage="mace_fast" if fast else "mace_md", formula=formula, ok=False, error="input cif not found")

    pipeline = "mace" if fast else "mace_md"
    outdir = os.path.join(
        config.repo_root,
        config.case_root,
        "results",
        pipeline,
        f"dr_{_task_s(task_id)}",
        str(formula),
    )
    os.makedirs(outdir, exist_ok=True)

    cmd = [
        "micromamba", "run", "-n", config.mace_env,
        "python", config.mace_script,
        "--in", inp_cif,
        "--out", outdir,
        "--model-path", config.mace_model_path,
        "--device", "cuda",
        "--dtype", "float32",
    ]
    if fast:
        cmd += ["--do-relax", "--relax-fmax", "0.1", "--relax-steps", "200"]
    else:
        cmd += [
            "--do-md",
            "--md-steps", "1000",
            "--md-timestep-fs", "0.25",
            "--md-temp-K", "300",
            "--md-friction", "0.20",
            "--md-init-temp-K", "300",
            "--md-log-every", "50",
            "--md-tail-fraction", "0.40",
        ]

    rc, out = _run(cmd, cwd=config.repo_root)
    summary = os.path.join(outdir, "summary.json")
    return StageResult(
        stage="mace_fast" if fast else "mace_md",
        formula=formula,
        ok=(rc == 0),
        return_code=rc,
        command=cmd,
        stdout_tail=out[-8000:],
        summary_path=summary if os.path.exists(summary) else None,
        error=None if rc == 0 else f"{pipeline} failed rc={rc}",
    )
