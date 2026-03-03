#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ls_service.py

Watch:
  .../material_discovery_demo/results/in-LS/*.json

For each input JSON:
  - extract baseline_material + advanced_material (run both)
  - Step1: MP export assets (mp-api-py311)
      micromamba run -n mp-api-py311 python tools/mp_export_assets.py
        --taskid <taskid>
        --jobid <formula>
        --formula <formula>
        --prefer-stable
    Expect:
      results/mp/<taskid>/<formula>/manifest.json

  - Step2: ADiT + pymatgen eval (adit-py310)
      micromamba run -n adit-py310 python tools/adit_pymatgen_eval.py
        --mp_manifest <mp_manifest>
    Expect:
      results/adit_pymatgen/<taskid>/<formula>/manifest.json

Dir convention (under results/):
  in-LS/           upstream drops *.json here
  processing-LS/   lock (move) to avoid double-consume
  archive-LS/      DONE/FAILED archived inputs + error sidecar
  logs/            ls_service.log
"""

import json
import os
import time
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ----------------------------
# Config
# ----------------------------

@dataclass
class Config:
    ai4m_root: Path
    results_base: Path
    inbox: Path
    processing: Path
    archive: Path
    logdir: Path

    # env names (exactly as you really use)
    env_mp: str = "mp-api-py311"
    env_adit: str = "adit-py310"

    # scripts (relative to ai4m_root)
    mp_export_script: str = "tools/mp_export_assets.py"
    adit_eval_script: str = "tools/adit_pymatgen_eval.py"

    # watcher behavior
    startup_scan: bool = False
    settle_delay_s: float = 0.2

    # timeouts (seconds)
    step1_wait_s: int = 1800
    step2_wait_s: int = 1800
    poll_s: float = 0.5


def load_config() -> Config:
    ai4m_root = Path(os.environ.get("AI4M_ROOT", "/home/ubuntu/se42/ai4m_tqm")).resolve()
    results_base = (ai4m_root / "src/MNS_CaseHub/cases/material_discovery_demo/results").resolve()

    inbox = results_base / "in-LS"
    processing = results_base / "processing-LS"
    archive = results_base / "archive-LS"
    logdir = results_base / "logs"

    return Config(
        ai4m_root=ai4m_root,
        results_base=results_base,
        inbox=inbox,
        processing=processing,
        archive=archive,
        logdir=logdir,
    )


CFG = load_config()


# ----------------------------
# Utilities
# ----------------------------

def ensure_dirs() -> None:
    for p in [CFG.inbox, CFG.processing, CFG.archive, CFG.logdir]:
        p.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    ensure_dirs()
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
    (CFG.logdir / "ls_service.log").open("a", encoding="utf-8").write(line)
    print(line, end="")


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json_retry(p: Path, retries: int = 8, sleep_s: float = 0.25) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for _ in range(retries):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise last_err  # type: ignore


def extract_materials_both(payload: dict) -> List[str]:
    st = (payload or {}).get("simulation_task", {})
    mats: List[str] = []
    for key in ["baseline_material", "advanced_material"]:
        v = st.get(key)
        if isinstance(v, str) and v.strip():
            mats.append(v.strip())

    # dedup preserve order
    seen = set()
    out: List[str] = []
    for m in mats:
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


def make_taskid(input_filename: str) -> str:
    safe = input_filename.replace(".json", "")
    return f"LS_{safe}"


def run_bash(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    """
    Run command via bash -lc to match your real usage style.
    Return CompletedProcess for stdout/stderr inspection.
    """
    return subprocess.run(
        ["bash", "-lc", cmd],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for_file(path: Path, timeout_s: int, poll_s: float) -> None:
    t0 = time.time()
    while True:
        if path.exists() and path.stat().st_size > 0:
            return
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"Timeout waiting for file: {path}")
        time.sleep(poll_s)


# ----------------------------
# Expected outputs
# ----------------------------

def mp_manifest_path(taskid: str, formula: str) -> Path:
    return CFG.results_base / "mp" / taskid / formula / "manifest.json"


def step2_manifest_path(taskid: str, formula: str) -> Path:
    return CFG.results_base / "adit_pymatgen" / taskid / formula / "manifest.json"


# ----------------------------
# Pipeline steps (REAL)
# ----------------------------

def run_step1_mp_export(taskid: str, formula: str) -> Path:
    """
    Step1 (REAL):
      micromamba run -n mp-api-py311 python tools/mp_export_assets.py
        --taskid <taskid>
        --jobid <formula>
        --formula <formula>
        --prefer-stable
    """
    script = CFG.mp_export_script
    out_manifest = mp_manifest_path(taskid, formula)
    out_manifest.parent.mkdir(parents=True, exist_ok=True)

    cmd = (
        f"set -e; "
        f"micromamba run -n {CFG.env_mp} "
        f"python {script} "
        f"--taskid {json.dumps(str(taskid))} "
        f"--jobid {json.dumps(str(formula))} "
        f"--formula {json.dumps(str(formula))} "
        f"--prefer-stable"
    )

    log(f"Step1 MP export: {formula} (taskid={taskid})")
    cp = run_bash(cmd, cwd=CFG.ai4m_root)

    if cp.stderr.strip():
        log(f"[Step1 STDERR]\n{cp.stderr.strip()}")

    if cp.returncode != 0:
        raise RuntimeError(f"Step1 failed rc={cp.returncode}: {cp.stderr.strip() or cp.stdout.strip()}")

    # Some versions print json; regardless, we rely on file existence.
    log(f"Step1: waiting for MP manifest -> {out_manifest}")
    wait_for_file(out_manifest, timeout_s=CFG.step1_wait_s, poll_s=CFG.poll_s)
    return out_manifest


def run_step2_adit_eval(taskid: str, formula: str, mp_manifest: Path) -> Path:
    """
    Step2 (REAL):
      micromamba run -n adit-py310 python tools/adit_pymatgen_eval.py --mp_manifest <MP_MANIFEST>
    """
    script = CFG.adit_eval_script
    out_manifest = step2_manifest_path(taskid, formula)
    out_manifest.parent.mkdir(parents=True, exist_ok=True)

    # Use relative mp_manifest if possible (cleaner logs)
    try:
        mp_arg = str(mp_manifest.resolve().relative_to(CFG.ai4m_root))
    except Exception:
        mp_arg = str(mp_manifest.resolve())

    cmd = (
        f"set -e; "
        f"micromamba run -n {CFG.env_adit} "
        f"python {script} "
        f"--mp_manifest {json.dumps(mp_arg)}"
    )

    log(f"Step2 ADiT eval: {formula} (taskid={taskid})")
    cp = run_bash(cmd, cwd=CFG.ai4m_root)

    if cp.stderr.strip():
        # keep last ~80 lines to avoid log explosion
        tail = "\n".join(cp.stderr.strip().splitlines()[-80:])
        log(f"[Step2 STDERR tail]\n{tail}")

    if cp.returncode != 0:
        # include stderr tail for debugging
        err_tail = "\n".join(cp.stderr.strip().splitlines()[-120:])
        raise RuntimeError(f"Step2 failed rc={cp.returncode}: {err_tail or cp.stdout.strip()}")

    log(f"Step2: waiting for manifest -> {out_manifest}")
    wait_for_file(out_manifest, timeout_s=CFG.step2_wait_s, poll_s=CFG.poll_s)
    return out_manifest


# ----------------------------
# Core processing
# ----------------------------

def process_input_json(p_processing: Path) -> None:
    taskid = make_taskid(p_processing.name)
    log(f"Task {taskid}: start (input={p_processing.name})")

    payload = read_json_retry(p_processing)
    materials = extract_materials_both(payload)
    if not materials:
        raise ValueError("No baseline_material / advanced_material found in input JSON")

    log(f"Task {taskid}: materials={materials}")

    for formula in materials:
        log(f"Task {taskid}/{formula}: Step1 -> Step2")
        mp_mani = run_step1_mp_export(taskid, formula)
        step2_mani = run_step2_adit_eval(taskid, formula, mp_mani)
        log(f"Task {taskid}/{formula}: OK")
        log(f"  mp_manifest    = {mp_mani}")
        log(f"  step2_manifest = {step2_mani}")

    log(f"Task {taskid}: all jobs complete")


def archive_input(p_processing: Path, ok: bool, err: Optional[str] = None) -> None:
    ensure_dirs()
    suffix = "DONE" if ok else "FAILED"
    ts = now_ts()
    base = p_processing.name.replace(".json", "")
    new_name = f"{base}.{suffix}.{ts}.json"
    dst = CFG.archive / new_name

    shutil.move(str(p_processing), str(dst))
    log(f"Archived input -> {dst}")

    if (not ok) and err:
        sidecar = CFG.archive / f"{base}.{suffix}.{ts}.error.txt"
        sidecar.write_text(err, encoding="utf-8")
        log(f"Wrote error -> {sidecar}")


def try_consume(p_inbox: Path) -> None:
    if not p_inbox.exists():
        return
    if p_inbox.suffix.lower() != ".json":
        return

    ensure_dirs()
    time.sleep(CFG.settle_delay_s)

    p_processing = CFG.processing / p_inbox.name

    try:
        shutil.move(str(p_inbox), str(p_processing))
    except Exception as e:
        log(f"[WARN] move to processing failed for {p_inbox.name}: {e}")
        return

    try:
        process_input_json(p_processing)
        archive_input(p_processing, ok=True)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        log(f"[ERROR] {p_processing.name} failed: {err}")
        try:
            archive_input(p_processing, ok=False, err=err)
        except Exception as ee:
            log(f"[WARN] archive failed input failed: {type(ee).__name__}: {ee}")


# ----------------------------
# Watchdog handler
# ----------------------------

class LSHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() != ".json":
            return
        log(f"Detected new input: {p.name}")
        try_consume(p)


def startup_scan() -> None:
    if not CFG.startup_scan:
        return
    ensure_dirs()
    files = sorted(CFG.inbox.glob("*.json"))
    if files:
        log(f"Startup scan: found {len(files)} pending inputs")
    for p in files:
        log(f"Startup scan consume: {p.name}")
        try_consume(p)


def main() -> None:
    ensure_dirs()
    log(f"AI4M_ROOT       = {CFG.ai4m_root}")
    log(f"Results base    = {CFG.results_base}")
    log(f"Watching INBOX  = {CFG.inbox}")
    log(f"Step1 env(mp)   = {CFG.env_mp}")
    log(f"Step2 env(adit) = {CFG.env_adit}")

    startup_scan()

    observer = Observer()
    observer.schedule(LSHandler(), str(CFG.inbox), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("Stopped by user")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
