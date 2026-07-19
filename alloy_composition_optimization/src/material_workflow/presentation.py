"""Render and emit rich, evidence-backed frontend assets for new materials."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .emitters import build_scientific_conclusion, write_pipeline_manifest


# These are deliberately ranges from observed single-candidate runs, not a
# promise. GPU queueing and Materials Project API latency are external factors.
PHASE_EXPECTATIONS = {
    1: ("通常少于 30 秒", "正在准备元素约束、模型配置和任务目录。"),
    2: ("单候选通常 2–6 分钟", "MatterGen 正在进行扩散采样；GPU 冷启动或繁忙时会更久。"),
    3: ("通常 20 秒–3 分钟", "正在完成 MatterSim 松弛并查询 MP 同元素竞争相；MP API 波动会延长等待。"),
    4: ("通常少于 1 分钟", "正在整理数值、渲染结构图和热力学评分卡，并上传前端资产。"),
}


def _target_description(properties: dict[str, object]) -> str:
    if not properties:
        return "未设置数值生成目标"
    labels = {
        "energy_above_hull": "稳定性偏好 E_hull（越低越接近热力学稳定）",
        "band_gap": "带隙", "bulk_modulus": "体积模量", "mag_density": "磁性密度", "space_group": "空间群",
    }
    values = []
    for name, value in properties.items():
        suffix = " eV/atom" if name == "energy_above_hull" else ""
        values.append(f"{labels.get(name, name)}={value}{suffix}")
    return "；".join(values)


def build_term_guide() -> str:
    """Short explanations for scientific terms shown in the frontend."""
    return "\n".join([
        "### 术语小提示",
        "- **E_hull（高于凸包能）**：候选与同元素体系中更稳定组合相比的能量距离；越低越值得继续验证。",
        "- **形成能**：材料由组成元素形成晶体时的能量变化；需要与 E_hull 一起看，不能单独判断能否合成。",
        "- **结构松弛**：让原子位置和晶胞在计算中自动调整到更低能量的构型。",
        "- **局部相图比对**：把候选与公开数据库中同元素体系的已知稳定相作比较。",
        "- **MatterGen / MatterSim / MP**：分别是生成候选结构的 AI、快速评估结构能量的机器学习模型、Materials Project 公开材料数据库。",
        "- **DFT（第一性原理计算）**：比本轮快速筛选更严格的计算验证；目标性能通常还需要 DFT 或实验确认。",
    ])


def build_preparation_traceability_report(result) -> str:
    """Summarize computational provenance useful to a preparation team.

    This intentionally distinguishes structural/thermodynamic references from
    a real experimental synthesis route, which this workflow does not predict.
    """
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    validation = top.validation if top else None
    mattersim = (validation.metadata or {}).get("mattersim") if validation else None
    traceability = (mattersim or {}).get("preparation_traceability") if isinstance(mattersim, dict) else None
    if not isinstance(traceability, dict):
        return "\n".join([
            "### 制备可追溯信息",
            "本轮尚未形成可用的 MP 同元素竞争相数据，因此暂不能提供原型匹配或稳定相清单。",
            "当前仅可交付候选 CIF 与基础结构检查；这不是合成路线建议。",
        ])

    fingerprint = traceability.get("candidate_crystallography") or {}
    prototype = traceability.get("prototype_match")
    rows = [
        "### 制备可追溯信息",
        "候选晶体学指纹："
        f"化学式 `{fingerprint.get('formula_pretty') or 'N/A'}`；"
        f"空间群 `{fingerprint.get('space_group_symbol') or '未确定'}`"
        f"（#{fingerprint.get('space_group_number') or 'N/A'}），"
        f"晶系 `{fingerprint.get('crystal_system') or '未确定'}`，"
        f"原胞位点数 `{fingerprint.get('sites') or 'N/A'}`。",
    ]
    if prototype:
        rows.append(
            "已找到同化学计量的公开结构原型匹配："
            f"`{prototype.get('material_id') or '未提供 ID'}`；{prototype.get('match_method') or ''}。"
        )
    else:
        rows.append("未在本次查询到的同元素 MP 竞争相中找到同化学计量的直接结构原型匹配；该候选应视为待验证的新结构，而非已知原型的简单复现。")

    stable_phases = traceability.get("same_system_stable_phases") or []
    if stable_phases:
        rows.extend(["", "同元素体系的稳定竞争相（用于相稳定性与配方讨论）："])
        for phase in stable_phases[:6]:
            material_id = f"（{phase.get('material_id')}）" if phase.get("material_id") else ""
            rows.append(f"- `{phase.get('formula_pretty') or 'N/A'}`{material_id}")
    rows.extend([
        "",
        "边界说明：上述原型和竞争相来自计算数据库比对，用于指导后续相图、热处理和前驱体筛选；它们不是 MatterGen 的扩散轨迹，也不构成已验证的制备路线。",
    ])
    return "\n".join(rows)


def write_preparation_traceability_report(result) -> Path:
    """Persist the preparation-oriented report beside the durable manifest."""
    task_dir = Path(result.artifacts["result_dir"])
    path = task_dir / "preparation_traceability.md"
    path.write_text(build_preparation_traceability_report(result) + "\n", encoding="utf-8")
    return path


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
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False, env=environment,
            timeout=int(environment.get("PRESENTATION_TIMEOUT_SEC", "180")),
        )
    except subprocess.TimeoutExpired:
        (output_dir / "presentation.log").write_text("Presentation rendering exceeded its timeout.", encoding="utf-8")
        return {"status": "unavailable", "message": "Presentation rendering timed out.", "log_path": str(output_dir / "presentation.log")}
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
    target_text = _target_description(constraints.target_properties or {})
    evidence_note = (
        "证据等级：机器学习快速评估 + 公开数据库比对。它用于决定是否值得进入 DFT，不替代高温力学、电化学或实验验证。"
        if conclusion["evidence_level"] == "mattersim_mp_hybrid"
        else "证据等级：基础结构检查。尚未完成原子位置优化和同元素稳定相比较，因此暂不能判断可合成性。"
    )
    return "\n".join([
        "### 设计任务解读",
        f"元素体系：`{elements}`。本轮生成 {len(result.generation.candidates)} 个候选；只有通过基础结构检查的候选才会进入后续稳定性评估。",
        f"生成引导条件：`{target_text}`（它是生成时的偏好，不是最终验证结果）。",
        "",
        "### 发现证据与阶段判断",
        conclusion["text"],
        "",
        evidence_note,
        "",
        build_preparation_traceability_report(result),
        "",
        build_term_guide(),
    ])


def build_requirement_brief(constraints) -> str:
    """A small, truthful streamable brief displayed before GPU work starts."""
    elements = " · ".join(constraints.allowed_elements) or "从化学式解析"
    properties = constraints.target_properties or {}
    friendly_names = {
        "energy_above_hull": "稳定性偏好 E_hull（越低越好）",
        "band_gap": "带隙目标",
        "bulk_modulus": "体积模量目标",
        "mag_density": "磁性密度目标",
        "space_group": "空间群偏好",
    }
    def display_value(name: str, value: object) -> str:
        return f"{value} eV/atom" if name == "energy_above_hull" else str(value)

    property_rows = "\n".join(
        f"| {friendly_names.get(name, name)} | {display_value(name, value)} | 让生成模型优先探索相应结构；最终仍需用结果复核 |" for name, value in properties.items()
    ) or "| 元素体系 | 未提供数值 | 在指定元素组合内探索晶体结构 |"
    validation_names = {
        "high_temperature_strength": "高温强度",
        "creep_resistance": "抗蠕变能力",
        "oxidation_resistance": "抗氧化能力",
        "ionic_conductivity": "离子电导率",
        "thermal_fatigue": "热疲劳抗力",
        "additive_manufacturability": "增材制造适配性",
    }
    validation = "、".join(validation_names.get(item, item) for item in (constraints.validation_targets or {}).keys()) or "基础结构与热力学稳定性"
    return "\n".join([
        "### 新材料发现任务已建模",
        f"将围绕元素体系 `[{elements}]` 创建候选晶体；后续重点验证：{validation}。",
        "",
        "| 设计偏好 | 目标值 | 系统如何使用 |",
        "|---|---:|---|",
        property_rows,
        "",
        *( [f"解析说明：{' '.join(constraints.notes)}", ""] if constraints.notes else [] ),
        "接下来将依次完成：生成候选结构、让原子位置自动调整到较低能量、与同元素体系的已知稳定相比较，再给出是否值得进入 DFT 的阶段建议。",
    ])


def _phase_from_files(task_dir: Path) -> tuple[int, str, str]:
    """Derive a truthful phase from durable artifacts rather than fake timers."""
    if (task_dir / "presentation" / "presentation_manifest.json").exists():
        return 4, "结果正在整理", "稳定性评估已形成，正在生成结构模型、评分卡和可视化结论。"
    if (task_dir / "mattersim" / "mattersim_results.json").exists():
        return 4, "结果正在整理", "稳定性评估已形成，正在生成结构模型、评分卡和可视化结论。"
    if (task_dir / "generation" / "generated_crystals_cif.zip").exists():
        return 3, "正在评估可行性", "候选结构已经生成，正在让原子位置自动调整到较低能量，并与同元素体系的已知稳定相比较。"
    if (task_dir / "generation").exists():
        return 2, "正在生成候选结构", "正在探索晶体结构空间；生成模型会逐步去噪并形成候选晶体。"
    return 1, "正在准备计算", "设计条件已完成建模，正在为指定元素体系准备生成任务。"


def _write_progress_state(task_dir: Path, *, phase: int, title: str, description: str, total_elapsed: int, phase_elapsed: int) -> None:
    """Persist progress so HTTP clients can poll while a WebSocket job runs."""
    task_dir.mkdir(parents=True, exist_ok=True)
    expected, _ = PHASE_EXPECTATIONS[phase]
    payload = {
        "status": "in_progress",
        "phase": phase,
        "total_phases": 4,
        "title": title,
        "description": description,
        "elapsed_seconds": total_elapsed,
        "phase_elapsed_seconds": phase_elapsed,
        "typical_duration": expected,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    target = task_dir / "progress.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def write_terminal_progress(task_dir: Path, *, status: str, description: str) -> None:
    """Mark the durable polling state after success or an unrecoverable error."""
    target = task_dir / "progress.json"
    try:
        payload = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except json.JSONDecodeError:
        payload = {}
    payload.update({
        "status": status,
        "phase": 4 if status == "completed" else payload.get("phase", 1),
        "total_phases": 4,
        "title": "结果已生成" if status == "completed" else "任务未完成",
        "description": description,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    task_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def stream_discovery_progress(websocket, task_dir: Path, request_id: str, *, step_id: str) -> None:
    """Emit artifact-backed phase progress and a periodic human-readable heartbeat."""
    last_phase = -1
    last_heartbeat = 0.0
    started_at = time.monotonic()
    phase_started_at = started_at
    while True:
        phase, title, description = _phase_from_files(task_dir)
        now = time.monotonic()
        if phase != last_phase:
            phase_started_at = now
        total_elapsed = int(now - started_at)
        phase_elapsed = int(now - phase_started_at)
        expected, expectation_note = PHASE_EXPECTATIONS[phase]
        human_description = (
            f"{description} 已等待 {total_elapsed // 60} 分 {total_elapsed % 60} 秒；"
            f"本阶段已进行 {phase_elapsed // 60} 分 {phase_elapsed % 60} 秒；"
            f"常见耗时：{expected}。{expectation_note}"
        )
        if phase != last_phase or now - last_heartbeat >= 20:
            from .payloads import build_payload

            _write_progress_state(
                task_dir, phase=phase, title=title, description=human_description,
                total_elapsed=total_elapsed, phase_elapsed=phase_elapsed,
            )

            await websocket.send_json(build_payload(
                {
                    "id": step_id,
                    "icon": "",
                    "title": title,
                    "status": "in_progress",
                    "description": human_description,
                    "progress": {
                        "mode": "phases",
                        "current": phase,
                        "total": 4,
                        "label": f"第 {phase}/4 阶段",
                        "elapsed_seconds": total_elapsed,
                        "phase_elapsed_seconds": phase_elapsed,
                        "typical_duration": expected,
                    },
                },
                type_="progress",
                request_id=request_id,
            ))
            last_phase = phase
            last_heartbeat = now
        await asyncio.sleep(5)


def _service_asset_url(websocket, taskid: str, filename: str) -> str:
    """Build a same-origin HTTP(S) URL visible to the connected browser."""
    url = websocket.url
    scheme = "https" if url.scheme == "wss" else "http"
    root_path = (websocket.scope.get("root_path") or "").rstrip("/")
    return f"{scheme}://{url.netloc}{root_path}/new-material/tasks/{taskid}/assets/{filename}"


async def emit_presentation_assets(websocket, result, *, step_id: str = "NEW_MATERIAL_DISCOVERY") -> None:
    """Emit assets as dedicated cards, never as Markdown content or table cells."""
    presentation = (result.artifacts or {}).get("presentation") or {}
    assets = presentation.get("assets") if isinstance(presentation, dict) else []
    if not assets:
        return
    taskid = str(result.taskid).replace("/", "_")
    for asset in assets:
        path = Path(asset.get("path") or "")
        if not path.exists():
            continue
        # The service endpoint is same-origin and preserves the file MIME type.
        # It is reliable even when MinIO is private, HTTP-only, or temporarily down.
        url = _service_asset_url(websocket, taskid, path.name)
        try:
            await websocket.send_json({
                "step_id": step_id,
                "name": asset.get("name") or path.stem,
                "docs": asset.get("docs") or "新材料发现可视化资产",
                "url": url,
                "type": asset.get("type") or "MaterialsPNG",
            })
        except Exception:
            return
