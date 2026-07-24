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

    def ready(self) -> bool:
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
        if not self.ready():
            raise RuntimeError("HEA surrogate runner is not ready; create hea-surrogate-py310 and retrain models with tools/setup_hea_surrogate_env.sh")
        task_dir = self.results_root / taskid
        task_dir.mkdir(parents=True, exist_ok=True)
        request = (task_dir / "runner_request.json").resolve()
        response = (task_dir / "runner_response.json").resolve()
        request.write_text(json.dumps({"operation": operation, "constraints": constraints, "candidates": candidates or []}, ensure_ascii=False), encoding="utf-8")
        command = [self.executable, "run", "-p", str(self.environment_prefix), "python", str(self.surrogate_root / "tools/service_runner.py"), "--request", str(request), "--response", str(response)]
        completed = subprocess.run(command, cwd=self.surrogate_root, capture_output=True, text=True, timeout=int(os.getenv("HEA_RUNNER_TIMEOUT_SECONDS", "90")))
        if not response.is_file():
            raise RuntimeError(f"HEA runner did not return JSON: {completed.stderr[-1000:]}")
        data = json.loads(response.read_text(encoding="utf-8"))
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "HEA runner failed"))
        return data["result"]
