"""Infrastructure adapter for the isolated HEA surrogate process.

The HTTP process never imports the numerical model stack.  It exchanges JSON
request/response files with the dedicated micromamba environment instead.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from src.alloy_workflow.tensile_shadow import (
    SHADOW_DOMAINS,
    build_comparison_items,
    future_primary_presentation,
)
from src.alloy_workflow.glass_shadow_compare import build_glass_shadow_batch, merge_private_diagnostics
from src.alloy_workflow.private_shadow_dispatcher import PrivateShadowDispatcher
from src.alloy_workflow.shadow_channel_configs import model_shadow_channels, optimization_shadow_channels


_SHADOW_LOG = logging.getLogger("alloy.private_shadow")


class HEASurrogateRunner:
    def __init__(self, *, results_root: Path, surrogate_root: Path, environment_prefix: Path, executable: str) -> None:
        self.results_root = results_root
        self.surrogate_root = surrogate_root
        self.environment_prefix = environment_prefix
        self.executable = executable
        workers = max(1, min(2, int(os.getenv("UNIFIED_TENSILE_SHADOW_WORKERS", "1"))))
        queue_size = max(workers, int(os.getenv("UNIFIED_TENSILE_SHADOW_QUEUE", "8")))
        self._shadow_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unified-tensile-shadow")
        self._shadow_slots = threading.BoundedSemaphore(queue_size)
        self._tensile_health_lock = threading.Lock()
        self._tensile_health_cache: tuple[float, tuple[str, str], bool] | None = None
        alloy_runtime = os.getenv("ALLOY_V10_SHADOW_RUNTIME_ROOT")
        glass_runtime = os.getenv("GLASS_TRANSFORMER_SHADOW_RUNTIME_ROOT")
        configured = model_shadow_channels(
            alloy_python=Path(os.getenv("ALLOY_V10_SHADOW_PYTHON", "/home/ubuntu/miniconda3/bin/python")),
            alloy_runtime=Path(alloy_runtime or "/nonexistent/alloy_v10_shadow"),
            alloy_items=build_comparison_items,
            glass_python=Path(os.getenv("GLASS_TRANSFORMER_SHADOW_PYTHON", "/home/ubuntu/miniconda3/bin/python")),
            glass_runtime=Path(glass_runtime or "/nonexistent/glass_transformer_shadow"),
            glass_checkpoint=Path('/data/se42/experiments/material_release_validation_20260928/models/glass_v1/model_state.pt'),
            glass_code=Path('/data/se42/experiments/material_release_validation_20260928/models/glass_v1/code'),
            glass_builder=build_glass_shadow_batch,
            glass_merger=merge_private_diagnostics,
        )
        channels = [channel for channel in configured if
                    (channel.name.startswith("alloy_v10") and alloy_runtime) or
                    (channel.name == "glass_v1" and glass_runtime)]
        optimizer_runtime = os.getenv("NSGA_SHADOW_RUNTIME_ROOT")
        if optimizer_runtime:
            channels.extend(optimization_shadow_channels(
                Path(os.getenv("NSGA_SHADOW_PYTHON", "/home/ubuntu/miniconda3/bin/python")),
                Path(optimizer_runtime)))
        self._new_shadows = PrivateShadowDispatcher(results_root, channels)

    def ready(self, domain: str = "hea_mpea") -> bool:
        if domain not in {"hea_mpea", "ni_superalloy_hot_end", "reusable_rocket_stainless",
                          "aerospace_aluminum_composition_v1", "copper_hot_end_local_composition_v1",
                          "chip_glass_thermomechanical_family_v1", "short_cf_thermomechanical_rve_v1",
                          "perovskite_transport_stability_v2"}:
            return False
        if domain == "copper_hot_end_local_composition_v1":
            required = (self.surrogate_root / "models/copper_hot_end_local_composition_v1_20260911/model.joblib",)
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        if domain == "aerospace_aluminum_composition_v1":
            required = (self.surrogate_root / "models/aerospace_aluminum_composition_v1_20260914/model.joblib",)
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        if domain == "perovskite_transport_stability_v2":
            required = (self.surrogate_root / "pure_zero_shot_v2/model_artifact.json", self.surrogate_root / "pure_zero_shot_v2/predict_joint.py")
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
        if domain == "short_cf_thermomechanical_rve_v1":
            required = (self.surrogate_root / "models/short_cf_thermomechanical_rve_v1_20260907/model.joblib",)
            return (self.environment_prefix / "bin/python").is_file() and all(path.is_file() for path in required)
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

    def tensile_shadow_ready(self) -> bool:
        python = Path(os.getenv("UNIFIED_TENSILE_PYTHON", "/home/ubuntu/miniconda3/bin/python"))
        runner = self.surrogate_root / "tools/unified_alloy_tensile_shadow_runner.py"
        runtime = Path(os.getenv("UNIFIED_TENSILE_RUNTIME_ROOT", '/data/se42/experiments/material_release_validation_20260928')).resolve()
        identity = (str(python.resolve()), str(runtime))
        now = time.monotonic()
        with self._tensile_health_lock:
            cache = self._tensile_health_cache
            if cache and cache[1] == identity and now - cache[0] < 30:
                return cache[2]
            ready = False
            if python.is_file() and runner.is_file() and (runtime / "runner.py").is_file():
                try:
                    with tempfile.TemporaryDirectory(prefix="material-tensile-health-") as temporary:
                        response = Path(temporary) / "response.json"
                        completed = subprocess.run(
                            [str(python), str(runner), "--health-check", "--response", str(response), "--threads", "1"],
                            cwd=self.surrogate_root, capture_output=True, text=True, timeout=25,
                            env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
                        )
                        payload = json.loads(response.read_text(encoding="utf-8")) if response.is_file() else {}
                        ready = completed.returncode == 0 and payload.get("ok") is True and payload.get("health_check") == "real_raw_input_inference_passed"
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    ready = False
            self._tensile_health_cache = (time.monotonic(), identity, ready)
            return ready

    def submit_new_model_shadows(self, taskid: str, specialist_result: dict[str, Any], optimization_scope=None) -> None:
        """Submit configured opt-in channels after the formal model returns."""
        for name in self._new_shadows.channels:
            try:
                private = specialist_result
                if name.startswith("nsga_") and optimization_scope is not None:
                    private = deepcopy(specialist_result)
                    private["_optimization_request"] = {
                        "model_domain": specialist_result.get("model_domain"),
                        "scope": deepcopy(optimization_scope), "specialist_result": deepcopy(specialist_result),
                    }
                self._new_shadows.submit(name, taskid, private)
            except Exception as exc:
                # A diagnostic channel cannot change the specialist response.
                _SHADOW_LOG.warning("Private shadow submission failed (%s): %s", name, type(exc).__name__)

    def submit_tensile_shadow(self, taskid: str, specialist_result: dict[str, Any]) -> Future[dict[str, Any]] | None:
        """Queue a private comparison without delaying or mutating the formal result."""
        enabled = os.getenv("UNIFIED_TENSILE_SHADOW_ENABLED", "1").strip().casefold() not in {"0", "false", "no", "off"}
        if not enabled or specialist_result.get("model_domain") not in SHADOW_DOMAINS:
            return None
        if not self._shadow_slots.acquire(blocking=False):
            self._write_shadow_notice(taskid, "queue_full", specialist_result.get("model_domain"))
            return None
        # The application enriches the formal result immediately after this
        # submission.  Freeze a private copy so the background thread never
        # races with presentation mutations.
        future = self._shadow_pool.submit(self.run_tensile_shadow_sync, taskid, deepcopy(specialist_result))
        future.add_done_callback(lambda _future: self._shadow_slots.release())
        return future

    def run_tensile_shadow_sync(self, taskid: str, specialist_result: dict[str, Any]) -> dict[str, Any]:
        domain = str(specialist_result.get("model_domain") or "")
        items = build_comparison_items(specialist_result)
        batch_id = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        # The model subprocess runs with ``cwd=hea_surrogate``.  Resolve the
        # task path before crossing that process boundary; production normally
        # configures ALLOY_RESULTS_ROOT as a path relative to the 1111 service.
        batch_dir = (self.results_root / taskid / "shadow" / batch_id).resolve()
        batch_dir.mkdir(parents=True, exist_ok=False)
        request = batch_dir / "request.json"
        response = batch_dir / "response.json"
        manifest = batch_dir / "manifest.json"
        request.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        started = time.perf_counter()
        record: dict[str, Any] = {
            "status": "skipped_no_candidates" if not items else "running",
            "mode": "shadow",
            "public_effect": False,
            "formal_result_mutated": False,
            "domain": domain,
            "batch_id": batch_id,
            "item_count": len(items),
            "future_primary_presentation": future_primary_presentation(domain),
        }
        if not items:
            manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
        python = os.getenv("UNIFIED_TENSILE_PYTHON", "/home/ubuntu/miniconda3/bin/python")
        command = [python, str(self.surrogate_root / "tools/unified_alloy_tensile_shadow_runner.py"),
                   "--request", str(request), "--response", str(response), "--mode", "shadow",
                   "--service-comparison", "--threads", "1"]
        env = dict(os.environ)
        for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[variable] = "1"
        try:
            completed = subprocess.run(
                command, cwd=self.surrogate_root, capture_output=True, text=True,
                timeout=int(os.getenv("UNIFIED_TENSILE_SHADOW_TIMEOUT_SECONDS", "60")), env=env,
            )
            payload = json.loads(response.read_text(encoding="utf-8")) if response.is_file() else {"ok": False, "error": "response_missing"}
            comparisons = payload.get("comparisons") or []
            statuses: dict[str, int] = {}
            for row in comparisons:
                status = str(row.get("status") or "unknown")
                statuses[status] = statuses.get(status, 0) + 1
            record.update({
                "status": "completed" if completed.returncode == 0 and payload.get("ok") else "failed",
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "returncode": completed.returncode,
                "comparison_status_counts": statuses,
                "response_ok": bool(payload.get("ok")),
                "stderr_tail": completed.stderr[-2000:],
            })
        except Exception as exc:  # shadow failures must remain private and non-blocking
            record.update({
                "status": "failed",
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
        manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def _write_shadow_notice(self, taskid: str, status: str, domain: Any) -> None:
        directory = (self.results_root / taskid / "shadow").resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"notice-{time.time_ns()}.json"
        path.write_text(json.dumps({
            "status": status, "mode": "shadow", "public_effect": False,
            "formal_result_mutated": False, "domain": domain,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
