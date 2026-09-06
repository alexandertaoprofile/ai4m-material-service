"""Infrastructure adapter for the isolated HEA surrogate process.

The HTTP process never imports the numerical model stack.  It exchanges JSON
request/response files with the dedicated micromamba environment instead.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


class HEASurrogateRunner:
    def __init__(self, *, results_root: Path, surrogate_root: Path, environment_prefix: Path, executable: str) -> None:
        self.results_root = results_root
        self.surrogate_root = surrogate_root
        self.environment_prefix = environment_prefix
        self.executable = executable

    def ready(self, domain: str = "hea_mpea") -> bool:
        if domain == "chip_glass_thermomechanical_family_v1":
            required = (
                self.surrogate_root / "models/chip_glass_cte_family_v1_20260902/model.joblib",
                self.surrogate_root / "models/chip_glass_family_thermomechanical_candidate_v2_20260904/model.joblib",
                self.surrogate_root / "data/processed/chip_glass_substrate_us20250026678_gate_v1/glass_thermal_admitted.csv",
            )
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        if domain == "reusable_rocket_stainless":
            required = (
                self.surrogate_root / "data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees/austenitic_design_v2_extratrees.joblib",
                self.surrogate_root / "data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees/applicability_domain.json",
                self.surrogate_root / "data/processed/reusable_rocket_stainless_reference_baseline/20260902_v0/admitted_tensile_observations.csv",
            )
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        if domain == "ni_superalloy_hot_end":
            variant = os.getenv("NI_SUPERALLOY_STRENGTH_VARIANT", "m0").strip().casefold()
            if variant not in {"m0", "m1_candidate"}:
                return False
            strength_model = (
                self.surrogate_root / "models/ni_superalloy_cast_ds_sc_strength_v0/m1_20260902_candidate/m1_models.joblib"
                if variant == "m1_candidate"
                else self.surrogate_root / "models/ni_superalloy_cast_ds_sc_strength_v0/baseline_20260831_cambridge_serving_v2/baseline_models.joblib"
            )
            required = (
                strength_model,
                self.surrogate_root / "models/ni_superalloy_cast_ds_sc_elongation_v0/baseline_20260901_serving_v1/elongation_model.joblib",
                self.surrogate_root / "models/ni_superalloy_cast_ds_sc_creep_rupture_v0/baseline_20260901_serving_v1/rupture_life_model.joblib",
            )
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        return (self.environment_prefix / "bin/python").is_file() and all(
            (self.surrogate_root / "models" / f"{name}_ensemble.joblib").is_file()
            for name in ("yield_strength", "hardness", "phase")
        )

    def model_evidence(self) -> dict[str, Any]:
        reports: dict[str, Any] = {}
        for key in ("yield_strength", "hardness", "phase"):
            path = self.surrogate_root / "reports" / "models" / f"{key}_training_report.json"
            try:
                reports[key] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reports[key] = {"status": "report_unavailable"}
        return {
            "model_version": "hea_mpea_baseline_v0.1",
            "data_type": "仅使用实验高熵合金/多主元合金（HEA/MPEA）数据；NbCrVWZr 计算数据未参与本轮训练",
            "validation": "按规范化成分分组划分训练/验证/测试集（同一成分不会同时出现在训练和测试中），并用 5 个随机种子形成集成预测",
            "reports": reports,
        }

    def run(self, taskid: str, operation: str, constraints: dict[str, Any], candidates: list[Any] | None = None) -> dict[str, Any]:
        domain = constraints.get("model_domain", "hea_mpea")
        if not self.ready(domain):
            raise RuntimeError(f"surrogate runner is not ready for {domain}")
        task_dir = self.results_root / taskid
        task_dir.mkdir(parents=True, exist_ok=True)
        request = (task_dir / "runner_request.json").resolve()
        response = (task_dir / "runner_response.json").resolve()
        request.write_text(json.dumps({"operation": operation, "constraints": constraints, "candidates": candidates or []}, ensure_ascii=False), encoding="utf-8")
        command = [self.executable, "run", "-p", str(self.environment_prefix), "python", str(self.surrogate_root / "tools/service_runner.py"), "--request", str(request), "--response", str(response)]
        # Deployments may run the web service under a different account from
        # the one that created ~/.cache/mamba.  Keep micromamba's transient
        # lock/cache task-local so inference does not depend on that home cache.
        env = dict(os.environ)
        env["XDG_CACHE_HOME"] = str(task_dir / ".cache")
        # This runner evaluates many small HGBR batches.  On the shared host,
        # inherited BLAS/OpenMP defaults can fan each prediction into many
        # threads and exceed the 90-second HTTP-side timeout.  One numerical
        # thread is faster and keeps concurrent service requests predictable.
        for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[variable] = "1"
        Path(env["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command, cwd=self.surrogate_root, capture_output=True, text=True, timeout=int(os.getenv("HEA_RUNNER_TIMEOUT_SECONDS", "90")), env=env)
        if not response.is_file():
            raise RuntimeError(f"HEA runner did not return JSON: {completed.stderr[-1000:]}")
        data = json.loads(response.read_text(encoding="utf-8"))
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "HEA runner failed"))
        return data["result"]
