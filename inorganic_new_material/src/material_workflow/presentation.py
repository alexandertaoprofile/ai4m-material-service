"""Render and emit rich, evidence-backed frontend assets for new materials."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .emitters import build_scientific_conclusion, write_pipeline_manifest


def render_presentation_assets(result) -> dict[str, Any]:
    """Create PNG/GIF/optional GLB from actual pipeline outputs in the GPU env."""
    task_dir = Path(result.artifacts["result_dir"])
    manifest_path = write_pipeline_manifest(result, task_dir)
    output_dir = task_dir / "presentation"
    output_dir.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).resolve().parents[2] / "tools" / "render_new_material_assets.py"
    env_prefix = os.environ.get("MATTERGEN_ENV_PREFIX", "/data/mamba/envs/mattergen-py310").strip()
    command = (["micromamba", "run", "-p", env_prefix] if env_prefix else []) + [
        "python", str(helper), "--manifest", str(manifest_path), "--output-dir", str(output_dir),
    ]
    environment = os.environ.copy()
    temporary_root = environment.setdefault("MATTERSIM_TMPDIR", "/data/mattersim_tmp")
    Path(temporary_root).mkdir(parents=True, exist_ok=True)
    environment["TMPDIR"] = temporary_root
    completed = subprocess.run(command, text=True, capture_output=True, check=False, env=environment)
    (output_dir / "presentation.log").write_text(
        (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""), encoding="utf-8"
    )
    presentation_manifest = output_dir / "presentation_manifest.json"
    if completed.returncode != 0 or not presentation_manifest.exists():
        return {"status": "unavailable", "message": "Presentation assets were not rendered.", "log_path": str(output_dir / "presentation.log")}
    return json.loads(presentation_manifest.read_text(encoding="utf-8"))


def build_discovery_story(result) -> str:
    """Produce deterministic Markdown blocks; no result is invented by an LLM."""
    constraints = result.constraints
    conclusion = build_scientific_conclusion(result)
    elements = " · ".join(constraints.allowed_elements) or "未显式指定"
    targets = constraints.target_properties or {}
    target_rows = "\n".join(f"| {key} | {value} | MatterGen 生成引导 |" for key, value in targets.items()) or "| N/A | N/A | 未提供数值生成目标 |"
    return "\n".join([
        "### 设计任务解读",
        f"元素体系：`{elements}`。本轮生成 {len(result.generation.candidates)} 个候选，并仅对通过基础结构准入的结构进行后验热力学筛选。",
        "",
        "| 目标 | 数值 | 在流程中的作用 |",
        "|---|---:|---|",
        target_rows,
        "",
        "### 发现证据与阶段判断",
        conclusion["text"],
        "",
        "证据等级：`MatterSim--MP hybrid`。该阶段用于决定是否进入 DFT，不替代高温力学、电化学或实验验证。",
    ])


def build_requirement_brief(constraints) -> str:
    """A small, truthful streamable brief displayed before GPU work starts."""
    elements = " · ".join(constraints.allowed_elements) or "从化学式解析"
    properties = constraints.target_properties or {}
    property_rows = "\n".join(
        f"| {name} | {value} | MatterGen 条件引导 |" for name, value in properties.items()
    ) or "| N/A | 未提供 | 使用元素体系条件生成 |"
    validation = "、".join((constraints.validation_targets or {}).keys()) or "基础结构与热力学稳定性"
    return "\n".join([
        "### 新材料发现任务已建模",
        f"将围绕元素体系 `[{elements}]` 创建候选晶体；后验验证范围：{validation}。",
        "",
        "| 生成约束 | 数值 | 计算用途 |",
        "|---|---:|---|",
        property_rows,
        "",
        "接下来：MatterGen 生成结构 → MatterSim 松弛 → MP 局部相图。",
    ])


async def emit_presentation_assets(websocket, result, *, step_id: str = "NEW_MATERIAL_DISCOVERY") -> None:
    """Upload rendered assets via the service's established WebSocket contract."""
    presentation = (result.artifacts or {}).get("presentation") or {}
    assets = presentation.get("assets") if isinstance(presentation, dict) else []
    if not assets:
        return
    try:
        from src.storage_utils import get_image_url, oss_upload
    except Exception:
        return
    taskid = str(result.taskid).replace("/", "_")
    for asset in assets:
        path = Path(asset.get("path") or "")
        if not path.exists():
            continue
        try:
            uploaded = await oss_upload("alpha", f"materials/new_material/{taskid}/{path.name}", path.read_bytes())
            if uploaded.get("status") != 200:
                continue
            url = get_image_url("alpha", f"materials/new_material/{taskid}/{path.name}")
            # Match the existing material frontend asset protocol exactly.
            await websocket.send_json({
                "step_id": step_id,
                "name": asset.get("name") or path.stem,
                "docs": asset.get("docs") or "新材料发现可视化资产",
                "url": url,
                "type": asset.get("type") or "MaterialsPNG",
            })
            if path.suffix.lower() == ".png":
                await websocket.send_text(f"![{asset.get('name') or path.stem}]({url})\n")
        except Exception:
            continue
