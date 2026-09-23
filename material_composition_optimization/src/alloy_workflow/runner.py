"""Infrastructure adapter for the isolated HEA surrogate process.

The HTTP process never imports the numerical model stack.  It exchanges JSON
request/response files with the dedicated micromamba environment instead.
"""
from __future__ import annotations

import json
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


UNIFIED_TENSILE_RELEASE_POLICY = {
    "version": "20260923_shadow_only",
    "mode": "shadow_only",
    "public_effect": False,
    "primary_switch_implemented": False,
    "domains": {
        "aerospace_aluminum_composition_v1": {
            "yield_strength_MPa": "specialist_primary",
            "uts_MPa": "specialist_primary",
            "unified_transformer": "shadow_only",
        },
        "reusable_rocket_stainless": {
            "yield_0p2_MPa": "specialist_primary",
            "uts_MPa": "specialist_primary",
            "unified_transformer": "shadow_only",
        },
        "ni_superalloy_hot_end": {
            "proof_strength_0p2_MPa": "specialist_primary",
            "ultimate_tensile_strength_MPa": "specialist_primary",
            "unified_transformer": "shadow_only",
        },
    },
}


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
        self._shadow_health_lock = threading.Lock()
        self._shadow_health_checked_at = 0.0
        self._shadow_health_cache: dict[str, Any] = {
            "ready": False,
            "inference_performed": False,
            "status": "not_checked",
        }

    def ready(self, domain: str = "hea_mpea") -> bool:
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
        runner = Path(os.getenv(
            "UNIFIED_TENSILE_RUNNER",
            str(self.surrogate_root / "tools/unified_alloy_tensile_shadow_runner.py"),
        ))
        runtime = Path(os.getenv(
            "UNIFIED_TENSILE_RUNTIME_ROOT",
            str(self.surrogate_root / "models/unified_alloy_tensile_v2_20260921/runtime"),
        ))
        return python.is_file() and runner.is_file() and (runtime / "assets/model_state.pt").is_file()

    def tensile_shadow_release_policy(self) -> dict[str, Any]:
        return deepcopy(UNIFIED_TENSILE_RELEASE_POLICY)

    def tensile_shadow_health(self, *, force: bool = False) -> dict[str, Any]:
        """Run and cache one real eligible inference, including artifact hash checks."""
        now = time.time()
        ttl = max(10, int(os.getenv("UNIFIED_TENSILE_HEALTH_TTL_SECONDS", "300")))
        if not force and now - self._shadow_health_checked_at < ttl:
            return deepcopy(self._shadow_health_cache)
        with self._shadow_health_lock:
            now = time.time()
            if not force and now - self._shadow_health_checked_at < ttl:
                return deepcopy(self._shadow_health_cache)
            record: dict[str, Any] = {
                "ready": False,
                "inference_performed": False,
                "status": "checking",
                "checked_at_epoch_seconds": now,
                "release_policy": self.tensile_shadow_release_policy(),
            }
            if not self.tensile_shadow_ready():
                record.update({"status": "assets_unavailable", "error": "python, runner, or model_state.pt is unavailable"})
            else:
                python = os.getenv("UNIFIED_TENSILE_PYTHON", "/home/ubuntu/miniconda3/bin/python")
                shadow_runner = os.getenv(
                    "UNIFIED_TENSILE_RUNNER",
                    str(self.surrogate_root / "tools/unified_alloy_tensile_shadow_runner.py"),
                )
                sample = {
                    "items": [{
                        "request_id": "health-aluminum-frozen-sample",
                        "service_domain": "aerospace_aluminum_composition_v1",
                        "actual_inputs": {
                            "composition_wt_percent": {
                                "Al": 92.35578, "Cr": 0.0, "Cu": 6.23236,
                                "Fe": 0.3, "Mg": 0.12039, "Mn": 0.32457,
                                "Si": 0.27509, "Ti": 0.06, "V": 0.1,
                                "Zn": 0.05722, "Zr": 0.1746,
                            },
                            "processing_category": "Solutionised + Artificially peak aged",
                            "test_temperature_C": 25.0,
                        },
                        "legacy_properties": {
                            "yield_strength_MPa": 286.2451141860001,
                            "uts_MPa": 411.7277816693333,
                        },
                    }],
                }
                started = time.perf_counter()
                try:
                    with tempfile.TemporaryDirectory(prefix="unified-tensile-health-") as directory:
                        request = Path(directory) / "request.json"
                        response = Path(directory) / "response.json"
                        request.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
                        command = [
                            python, shadow_runner,
                            "--request", str(request), "--response", str(response),
                            "--mode", "shadow", "--service-comparison", "--threads", "1",
                        ]
                        env = dict(os.environ)
                        for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                            env[variable] = "1"
                        completed = subprocess.run(
                            command,
                            cwd=self.surrogate_root,
                            capture_output=True,
                            text=True,
                            timeout=int(os.getenv("UNIFIED_TENSILE_HEALTH_TIMEOUT_SECONDS", "30")),
                            env=env,
                        )
                        payload = json.loads(response.read_text(encoding="utf-8")) if response.is_file() else {}
                        comparisons = payload.get("comparisons") or []
                        sample_status = comparisons[0].get("status") if len(comparisons) == 1 else None
                        ready = completed.returncode == 0 and payload.get("ok") is True and sample_status == "predicted"
                        record.update({
                            "ready": ready,
                            "inference_performed": True,
                            "status": "ready" if ready else "inference_failed",
                            "elapsed_seconds": round(time.perf_counter() - started, 4),
                            "sample_status": sample_status,
                            "model": payload.get("model"),
                            "returncode": completed.returncode,
                        })
                        if not ready:
                            record["error"] = payload.get("error") or completed.stderr[-1000:] or "health sample was not strictly predicted"
                except Exception as exc:
                    record.update({
                        "status": "inference_failed",
                        "inference_performed": True,
                        "elapsed_seconds": round(time.perf_counter() - started, 4),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
            self._shadow_health_checked_at = now
            self._shadow_health_cache = record
            return deepcopy(record)

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
        shadow_runner = os.getenv(
            "UNIFIED_TENSILE_RUNNER",
            str(self.surrogate_root / "tools/unified_alloy_tensile_shadow_runner.py"),
        )
        diagnostic_enabled = os.getenv(
            "UNIFIED_TENSILE_DIAGNOSTIC_OUTSIDE_SUPPORT_ENABLED", "1"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        command = [python, shadow_runner,
                   "--request", str(request), "--response", str(response), "--mode", "shadow",
                   "--service-comparison", "--threads", "1"]
        if diagnostic_enabled:
            command.append("--diagnostic-outside-support")
        record["diagnostic_outside_support_enabled"] = diagnostic_enabled
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
