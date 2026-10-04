"""Service-process adapter; GPU libraries remain isolated in the OMatG worker."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .schemas import GeneratedCandidate, GenerationManifest, ValidationResult

MODEL_DIRECTORIES = {"csp": "Alex-MP-20-CSP/Trig-SDE-Gamma", "dng": "MP-20-DNG/Linear-SDE-Gamma"}


def run_omatg_generation(constraints, output_dir: Path, max_candidates=10) -> GenerationManifest:
    mode = constraints.generation_mode
    model = MODEL_DIRECTORIES[mode]
    checkpoint_dir = Path(os.getenv("OMATG_CHECKPOINT_ROOT", "/data/se42/omatg_local/checkpoints")) / model
    python = Path(os.getenv("OMATG_PYTHON", "/data/mamba/envs/omatg-py312/bin/python"))
    source = Path(os.getenv("OMATG_SOURCE_DIR", "/data/third_party/OMatG"))
    output_dir.mkdir(parents=True, exist_ok=False)
    config, checkpoint = checkpoint_dir / "train.yaml", checkpoint_dir / "checkpoint.ckpt"
    if not all(path.exists() for path in (python, source, config, checkpoint)):
        return GenerationManifest(taskid=constraints.taskid, status="unavailable", backend="omatg", message="OMatG 运行环境或本地检查点尚未就绪；已保留用户约束。")
    request = {"constraints": constraints.to_dict(), "output_dir": str(output_dir.resolve()), "max_candidates": max_candidates,
        "source_dir": str(source), "config_path": str(config), "checkpoint_path": str(checkpoint), "model_name": model}
    request_path = output_dir / "worker_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    environment = os.environ.copy()
    cache = Path(os.getenv("OMATG_RUNTIME_ROOT", "/data/se42/omatg_local/runtime"))
    for key, subdir in (("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache"), ("MPLCONFIGDIR", "matplotlib")):
        path = cache / subdir
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    helper = Path(__file__).resolve().parents[2] / "tools" / "run_omatg_generation.py"
    started = time.monotonic()
    with (output_dir / "omatg.log").open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run([str(python), str(helper), "--request", str(request_path)],
                cwd=output_dir, env=environment, stdout=log, stderr=log, timeout=int(os.getenv("OMATG_TIMEOUT_SEC", "300")), check=False)
        except subprocess.TimeoutExpired:
            return GenerationManifest(taskid=constraints.taskid, status="timeout", backend="omatg", message="结构生成超过计算时限，未把部分文件作为已完成结果。")
        except OSError:
            return GenerationManifest(taskid=constraints.taskid, status="unavailable", backend="omatg", message="OMatG 解释器无法启动；已保留原始条件，未把部分文件作为生成结果。")
    manifest = output_dir / "generation_manifest.json"
    if completed.returncode or not manifest.is_file():
        return GenerationManifest(taskid=constraints.taskid, status="failed", backend="omatg", message="OMatG 未产出完整的候选结构，可按已保留条件复核并重试。", metadata={"log_path": str(output_dir / "omatg.log")})
    data = json.loads(manifest.read_text(encoding="utf-8"))
    candidates = [GeneratedCandidate(**{**row, "cif_path": Path(row["cif_path"]) if row.get("cif_path") else None}) for row in data.pop("candidates")]
    data["metadata"]["process_wall_seconds"] = time.monotonic()-started
    return GenerationManifest(**data, candidates=candidates)


def generated_admission(candidate) -> ValidationResult:
    geometry = candidate.metadata.get("geometry") or {}
    values = {name: geometry.get(name) for name in ("formula_pretty", "space_group", "density")}
    values["metadata"] = {**geometry.get("metadata", {}), "composition_ok": candidate.metadata.get("composition_ok", False)}
    errors = list(geometry.get("errors", []))
    if not candidate.metadata.get("composition_ok"):
        errors.extend(candidate.metadata.get("composition_errors") or ["组成未满足允许/必含/禁止元素或固定化学计量要求。"])
    return ValidationResult(candidate_id=candidate.candidate_id, status="ok", is_valid=geometry.get("is_valid") is True and not errors,
        errors=errors, artifacts={"cif_path": str(candidate.cif_path) if candidate.cif_path else ""}, **values)
