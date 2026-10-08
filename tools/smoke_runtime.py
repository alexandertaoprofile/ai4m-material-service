#!/usr/bin/env python3
"""Real numerical smoke checks against the restored local runtime resources.

Run services in separate processes to isolate their identically named src
packages. Crystal checks acquire the specified interprocess GPU lock.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(python, script, cwd, environment, *, timeout=180):
    completed = subprocess.run([str(python), "-c", script], cwd=cwd, env=environment,
                               capture_output=True, text=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-4000:] + completed.stdout[-2000:])
    return completed.stdout.strip()[-4000:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--web-python", type=Path, required=True)
    parser.add_argument("--numeric-python", type=Path, required=True)
    parser.add_argument("--recipe-python", type=Path, required=True)
    parser.add_argument("--crystals", action="store_true")
    parser.add_argument("--omatg-python", type=Path)
    parser.add_argument("--alignn-python", type=Path)
    parser.add_argument("--alignn-site", default="")
    parser.add_argument("--gpu-lock", type=Path)
    parser.add_argument("--candidates", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new report filename")
    resources = args.resources.resolve()
    environment = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    temp = resources / "run/smoke"
    temp.mkdir(parents=True, exist_ok=True)
    environment.update(TMPDIR=str(temp), MPLCONFIGDIR=str(temp / "matplotlib"), XDG_CACHE_HOME=str(temp / "cache"))
    for value in ("MPLCONFIGDIR", "XDG_CACHE_HOME"):
        Path(environment[value]).mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, function):
        started = time.monotonic()
        try:
            detail = function()
            checks.append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3), "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "seconds": round(time.monotonic() - started, 3), "error": str(exc)})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    check("1105 SQLite integrity and record count", lambda: run(args.web_python,
        "import sqlite3,json; db=sqlite3.connect(" + repr(str(resources / "data/1105/fluid_property_evidence.sqlite")) + "); assert db.execute('pragma integrity_check').fetchone()[0]=='ok'; print(json.dumps({'tables':db.execute(\"select count(*) from sqlite_master where type='table'\").fetchone()[0]}))", ROOT, environment))
    # Models and their training-class definitions are loaded from the restored tree.
    check("1111 frozen specialist checksums and Transformer inference classes", lambda: run(args.recipe_python,
        "import sys;sys.path.insert(0," + repr(str(resources / "packages/recipe/runtime")) + ");import rsi_api,candidate_model_stack as c; print('verified specialist weights:',len(rsi_api.verify_specialists())); c.load_alloy();c.load_glass();print('alloy and glass checkpoints loaded')", ROOT, environment))
    scopes = {
        "hea_mpea": {"allowed_elements": ["Fe", "Co", "Cr", "Ni"], "num_candidates": 10, "test_temperature_C": 25},
        "ni_superalloy_hot_end": {"num_candidates": 10, "manufacturing_route": "cast", "test_temperature_C": 900,
                                  "heat_treatment": "solution_and_age", "applied_stress_MPa": 200,
                                  "element_bounds_wt_percent": {element: [0, 100] for element in
                                      "Ni Cr Co Re Ru Al Ta W Ti Mo Fe V Ir Mn Y C B Zr Si Nb Hf".split()}},
        "copper_hot_end_local_composition_v1": {"num_candidates": 10},
        "aerospace_aluminum_composition_v1": {"num_candidates": 10},
        "reusable_rocket_stainless": {"num_candidates": 10, "test_temperature_K": 293},
        "chip_glass_thermomechanical_family_v1": {"num_candidates": 10},
        "short_cf_thermomechanical_rve_v1": {"num_candidates": 10},
        "perovskite_transport_stability_v2": {"num_candidates": 10},
    }
    with tempfile.TemporaryDirectory(prefix="specialist-", dir=temp) as tmp:
        for domain, scope in scopes.items():
            request = Path(tmp) / (domain + ".json")
            response = Path(tmp) / (domain + "-response.json")
            operation = "propose_space" if domain == "chip_glass_thermomechanical_family_v1" else "propose"
            request.write_text(json.dumps({"operation": operation, "constraints": {"model_domain": domain, **scope}}))
            def specialist(request=request, response=response):
                completed = subprocess.run([str(args.numeric_python), str(resources / "packages/hea_surrogate/tools/service_runner.py"),
                                            "--request", str(request), "--response", str(response)], env=environment,
                                           capture_output=True, text=True, timeout=180)
                if not response.exists():
                    raise RuntimeError(f"worker exit={completed.returncode}; " + completed.stderr[-2500:] + completed.stdout[-1000:])
                payload = json.loads(response.read_text())
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("error", "specialist calculation failed"))
                if completed.returncode:
                    raise RuntimeError(f"worker exit={completed.returncode} with unexpected successful response")
                result = payload["result"]
                return {"model_domain": result.get("model_domain"), "operation": result.get("operation"),
                        "returned_fields": sorted(result)}
            check("1111 " + domain, specialist)
    validation_env = dict(environment, W14_MLIP_ROOT=str(resources / "evidence/DeepMD-W-14-commercial-level"),
                          W14_MD_ROOT=str(resources / "evidence/DeepMD_W-14_commercial-level"))
    check("1116 W-14 reference evidence workflow", lambda: run(args.web_python,
        "from src.team_config import execute_refractory_validation; from pathlib import Path; import json; r=execute_refractory_validation({'taskid':'repro-w14','idea':'验证纯钨','refractory_validation':{'material_system':'W'}},service_root=Path('.'));assert r.status=='completed';print(str(r)[:2000])",
        ROOT / "material_validation", validation_env))
    if args.crystals:
        if not all((args.omatg_python, args.alignn_python, args.gpu_lock)):
            parser.error("Crystal checks need OMatG/ALIGNN Python paths and an explicit shared GPU lock")
        crystal_env = dict(environment, NEW_MATERIAL_GENERATOR="omatg", OMATG_PYTHON=str(args.omatg_python),
            OMATG_SOURCE_DIR=str(ROOT / "third_party/OMatG"), OMATG_CHECKPOINT_ROOT=str(resources / "weights/omatg"),
            OMATG_RUNTIME_ROOT=str(temp / "crystal"), OMATG_GPU_LOCK=str(args.gpu_lock), OMATG_QUEUE_TIMEOUT_SEC="60",
            MATTERSIM_ENABLED="1", MATTERSIM_PYTHON=str(args.numeric_python), MATTERSIM_ENV_PREFIX=str(args.numeric_python.parent.parent),
            MATTERGEN_ENV_PREFIX=str(args.numeric_python.parent.parent), MATTERSIM_CHECKPOINT=str(resources / "weights/omatg/mattersim-v1.0.0-1M.pth"),
            MATTERSIM_REFERENCE_CACHE_DIR=str(resources / "reference"), OMATG_PHASE_DIAGRAM_CACHE_DIR=str(temp / "phase_diagrams"),
            MATTERSIM_TMPDIR=str(temp / "mattersim"), ALIGNN_ENABLED="1", ALIGNN_PYTHON=str(args.alignn_python),
            ALIGNN_SITE_PACKAGES=args.alignn_site, ALIGNN_CHECKPOINT_ROOT=str(resources / "weights/alignn"))
        for mode in ("csp", "dng"):
            values = {"taskid": "repro-" + mode + "-" + str(time.time_ns()), "generation_mode": mode,
                      "raw_requirement": "生成钛氧晶体并评价稳定性和带隙", "requested_candidate_count": args.candidates,
                      "allowed_elements": ["Ti", "O"], "min_distinct_elements": 2,
                      "target_properties": {"energy_above_hull": 0.05}, "refine_with_csp": False}
            if mode == "csp":
                values.update(target_formula="TiO2", formula_units=2)
            else:
                values.update(required_elements=["Ti", "O"], num_atoms=6)
            script = "from src.material_workflow.schemas import GenerationConstraint; from src.material_workflow.omatg_pipeline import run_omatg_pipeline;import json; r=run_omatg_pipeline(GenerationConstraint(**" + repr(values) + ")," + repr(str(temp / "crystals")) + ",max_candidates=" + str(args.candidates) + "); d=r.to_dict(); rows=[x.get('validation') or {} for x in d['ranked_candidates']]; print(json.dumps({'generation_status':r.generation.status,'generated':len(r.generation.candidates),'geometrically_valid':sum(x.get('is_valid') is True for x in rows),'relaxation_converged':sum(x.get('metadata',{}).get('mattersim',{}).get('relaxation_converged') is True for x in rows),'hull_estimates':sum(x.get('energy_above_hull') is not None for x in rows),'property_predictions':sum(bool(x.get('property_predictions')) for x in rows),'manifest':d.get('artifacts')},ensure_ascii=False)); assert r.generation.status=='ok' and len(r.generation.candidates)>0"
            script += "; assert any(x.get('metadata',{}).get('mattersim',{}).get('relaxation_converged') is True for x in rows), 'No relaxed candidate converged'; assert any(x.get('energy_above_hull') is not None for x in rows), 'No stability estimate completed'; assert any(x.get('property_predictions') for x in rows), 'No property prediction completed'"
            check("1115 " + mode + " generation / relaxation / properties", lambda script=script: run(args.web_python, script, ROOT / "material_discovery", crystal_env, timeout=1200))
    report = {"ok": all(row["ok"] for row in checks), "source_root": str(ROOT), "resource_root": str(resources), "checks": checks,
              "interpreters": {name: str(getattr(args, name + "_python")) for name in ("web", "numeric", "recipe", "omatg", "alignn")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
