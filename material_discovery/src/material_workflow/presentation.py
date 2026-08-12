"""Render and emit rich, evidence-backed frontend assets for new materials."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.storage_utils import get_storage_client, oss_upload

from .emitters import build_scientific_conclusion, write_pipeline_manifest


logger = logging.getLogger(__name__)


PICTURE_PUBLIC_BASE_URL = os.getenv(
    "PICTURE_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/materials/modelfiles/image",
).rstrip("/")
GLB_PUBLIC_BASE_URL = os.getenv(
    "GLB_PUBLIC_BASE_URL",
    # The public reverse proxy exposes the same object key as images, with
    # ``glb`` below ``materials/modelfiles``.  The historical
    # ``/alpha/glb/materials/modelfiles`` order returns HTTP 404.
    "https://www.science42.tech/alpha/materials/modelfiles/glb",
).rstrip("/")


# These are deliberately ranges from observed single-candidate runs, not a
# promise. GPU queueing and Materials Project API latency are external factors.
PHASE_EXPECTATIONS = {
    1: ("通常少于 30 秒", "正在准备元素约束、模型配置和任务目录。"),
    2: ("模型加载通常 1–5 分钟；加载后 100 步采样通常更短", "每个任务目前都会独立加载 MatterGen 条件模型；加载完成后才开始扩散采样。"),
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
    """Explain only the result indicators a decision-maker needs to read."""
    return "\n".join([
        "#### 如何阅读本轮结果",
        "| 指标 | 如何理解 |",
        "|---|---|",
        "| E_hull（高于凸包能） | 候选相对同元素体系稳定组合的能量距离；越低，越值得进入后续验证。 |",
        "| 形成能 | 材料由组成元素形成晶体时的能量变化；须与 E_hull 一起判断，不能单独说明能否合成。 |",
        "| 证据范围 | 本轮是机器学习快速评估与公开数据库比对，不等同于 DFT、相平衡计算或实验结果。 |",
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
            "#### 制备可追溯信息",
            "本轮尚未形成可用的 MP 同元素竞争相数据，因此暂不能提供原型匹配或稳定相清单。",
            "当前仅可交付候选 CIF 与基础结构检查；这不是合成路线建议。",
        ])

    fingerprint = traceability.get("candidate_crystallography") or {}
    prototype = traceability.get("prototype_match")
    rows = [
        "#### 制备可追溯信息",
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
    elements = " · ".join(constraints.allowed_elements) or "未显式指定"
    target_text = _target_description(constraints.target_properties or {})
    return "\n".join([
        "#### 本轮任务与生成条件",
        "| 项目 | 本轮设置 |",
        "|---|---|",
        f"| 元素体系 | `{elements}` |",
        f"| 生成引导 | {target_text}（仅用于引导生成，不是验证结果） |",
        f"| 候选数量 | 生成 {len(result.generation.candidates)} 个；通过基础结构检查后才进入稳定性评估。 |",
        "",
        "#### 本轮如何评估",
        "| 阶段 | 使用的方法 | 用途 |",
        "|---|---|---|",
        "| 生成候选 | MatterGen 条件生成模型 | 在指定元素体系与生成偏好下探索晶体结构。 |",
        "| 结构与能量评估 | pymatgen 基础检查 + MatterSim 松弛 | 排除基础结构问题，并估计较低能量构型。 |",
        "| 稳定性比较 | Materials Project 同元素竞争相查询 | 将候选与已知稳定相比较，得到 E_hull 初筛依据。 |",
        "",
        build_preparation_traceability_report(result),
        "",
        build_term_guide(),
    ])


def build_discovery_conclusion(result) -> str:
    """Close the final report with a concise, evidence-scoped decision."""
    conclusion = build_scientific_conclusion(result)
    generated = len(result.generation.candidates)
    admitted = sum(item.is_valid is True for item in result.validations)
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    if top is None or top.validation is None:
        finding = "本轮尚未形成可进入稳定性判断的候选结构。"
    else:
        validation = top.validation
        formula = top.candidate.formula_pretty or validation.formula_pretty or top.candidate.candidate_id
        hull = validation.energy_above_hull
        if hull is None:
            finding = f"候选 `{formula}` 已通过基础结构检查，但尚未得到 E_hull，暂不能判断其热力学稳定性。"
        else:
            threshold = float((result.constraints.target_properties or {}).get("energy_above_hull", 0.05))
            status = "达到" if hull <= threshold else "未达到"
            finding = f"排名第一的候选为 `{formula}`，E_hull 为 {hull:.4f} eV/atom，{status} {threshold:.2f} eV/atom 的本轮初筛阈值。"
    next_step = {
        "shortlist_for_dft": "建议优先开展 DFT 与目标性能验证，并结合实际工况确认制备与服役可行性。",
        "deprioritize": "建议降低该候选优先级，补充元素体系或生成条件后重新探索；不宜直接进入工程验证。",
        "structure_only": "建议先完成稳定性评估，再决定是否进入 DFT 与目标性能验证。",
        "no_candidate": "建议检查元素体系、生成条件或模型资源后重新执行。",
    }.get(conclusion.get("decision"), "建议结合专项计算与实验继续验证。")
    return "\n\n".join([
        "### 本轮结论",
        f"本轮采用条件生成、结构松弛和同元素体系稳定相比较的流程，在 {generated} 个候选中有 {admitted} 个通过基础结构检查。",
        finding,
        next_step,
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
        "### 新材料发现任务",
        "#### 设计条件",
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


def _mattergen_sampling_progress(task_dir: Path) -> dict[str, int] | None:
    """Read an actual tqdm step count when MatterGen has flushed it to disk."""
    log_path = task_dir / "generation" / "mattergen.log"
    if not log_path.exists():
        return None
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-20_000:]
    except OSError:
        return None
    matches = re.findall(r"(\d+)%\|.*?(\d+)/(\d+)", tail)
    if not matches:
        return None
    percent, current, total = matches[-1]
    total_i = int(total)
    current_i = int(current)
    if total_i <= 0 or current_i < 0 or current_i > total_i:
        return None
    return {"percent": int(percent), "current": current_i, "total": total_i}


def _mattergen_runtime_stage(task_dir: Path) -> tuple[str, str]:
    """Describe the actual pre-sampling/model-loading state from the live log."""
    log_path = task_dir / "generation" / "mattergen.log"
    if not log_path.exists():
        return (
            "正在启动生成进程",
            "生成任务已提交，正在启动独立的 MatterGen 计算进程。",
        )
    try:
        log = log_path.read_text(encoding="utf-8", errors="replace")[-20_000:]
    except OSError:
        return "正在启动生成进程", "正在读取生成进程状态。"
    if "Generating samples:" in log or re.search(r"\d+/\d+ \[", log):
        return "正在生成候选结构", "模型已加载，正在执行扩散采样并构造候选晶体。"
    if "Loading model from checkpoint" in log:
        return (
            "正在加载生成模型",
            "正在从本地缓存加载 MatterGen 条件模型并初始化 GPU；此阶段尚未开始扩散采样。",
        )
    return "正在初始化生成模型", "正在读取模型配置并准备加载条件模型。"


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


async def stream_discovery_progress(
    websocket,
    task_dir: Path,
    request_id: str,
    *,
    step_id: str,
) -> None:
    """Stream phase updates as plain body text; step JSON is sent by the caller."""
    last_phase: int | None = None
    last_heartbeat = 0.0
    started_at = time.monotonic()
    phase_started_at = started_at
    while True:
        phase, title, description = _phase_from_files(task_dir)
        if phase == 2:
            title, description = _mattergen_runtime_stage(task_dir)
        now = time.monotonic()
        phase_changed = phase != last_phase
        if phase_changed:
            phase_started_at = now
        total_elapsed = int(now - started_at)
        phase_elapsed = int(now - phase_started_at)
        expected, expectation_note = PHASE_EXPECTATIONS[phase]
        sampling = _mattergen_sampling_progress(task_dir) if phase == 2 else None
        sampling_text = (
            f" 当前已完成真实扩散步数 {sampling['current']}/{sampling['total']}（{sampling['percent']}%）；"
            if sampling else ""
        )
        full_description = (
            f"{description}{sampling_text} 已等待 {total_elapsed // 60} 分 {total_elapsed % 60} 秒；"
            f"本阶段已进行 {phase_elapsed // 60} 分 {phase_elapsed % 60} 秒；"
            f"常见耗时：{expected}。{expectation_note}"
        )
        if phase_changed or now - last_heartbeat >= 15:
            description_for_text = full_description if phase_changed else (
                f"进度：第 {phase}/4 阶段；已等待 {total_elapsed // 60} 分 {total_elapsed % 60} 秒；"
                f"本阶段已进行 {phase_elapsed // 60} 分 {phase_elapsed % 60} 秒"
                + (f"；当前扩散步数 {sampling['current']}/{sampling['total']}（{sampling['percent']}%）" if sampling else "")
                + "。"
            )
            _write_progress_state(
                task_dir, phase=phase, title=title, description=description_for_text,
                total_elapsed=total_elapsed, phase_elapsed=phase_elapsed,
            )
            # Keep the former Markdown presentation: a heading for a newly
            # entered phase and a compact quote for every heartbeat.  It is
            # ordinary body text, deliberately without CONTENT markers and
            # without another progress JSON.
            if phase_changed:
                markdown = f"\n\n#### {title}\n\n{description_for_text}\n\n"
            else:
                markdown = f"> {description_for_text}\n"
            await websocket.send_text(markdown)
            if phase_changed:
                last_phase = phase
            last_heartbeat = now
        await asyncio.sleep(5)


async def emit_presentation_assets(websocket, result, *, step_id: str = "FILAMENT_SELECTION_OPTIMIZATION") -> str:
    """Publish GLB JSON events and return Markdown for image and GIF assets."""
    presentation = (result.artifacts or {}).get("presentation") or {}
    assets = presentation.get("assets") if isinstance(presentation, dict) else []
    if not assets:
        logger.warning("[new-material-assets] no presentation assets taskid=%s", result.taskid)
        return ""
    taskid = str(result.taskid).replace("/", "_")
    pipeline = "inorganic_new_material"
    jobid = taskid or "job"

    def _trace(path: Path, event: str, **details: Any) -> None:
        """Persist per-asset delivery diagnostics next to the generated assets.

        This trace is deliberately independent from the process logger: the
        service may be started from different working directories, whereas the
        task directory is retained with the calculation result for inspection.
        """
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "taskid": taskid,
            "event": event,
            "path": str(path),
            **details,
        }
        try:
            trace_file = path.parent / "asset_delivery.log"
            with trace_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("[new-material-assets] trace write failed path=%s error=%s", path, exc)
        logger.info("[new-material-assets] trace=%s", record)

    seen_asset_paths: set[Path] = set()
    markdown_images: list[str] = []
    for asset in assets:
        path = Path(asset.get("path") or "")
        if not path.exists():
            _trace(path, "source_missing", asset_type=asset.get("type"), asset_name=asset.get("name"))
            continue
        resolved_path = path.resolve()
        if resolved_path in seen_asset_paths:
            logger.warning("[new-material-assets] skipped duplicate asset path=%s", resolved_path)
            _trace(path, "duplicate_skipped")
            continue
        seen_asset_paths.add(resolved_path)
        asset_type = str(asset.get("type") or "MaterialsPNG")
        if asset_type == "MaterialsGLB" or path.suffix.lower() == ".glb":
            publish_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
            object_key = f"materials/modelfiles/glb/{publish_name}"
            public_url = f"{GLB_PUBLIC_BASE_URL}/{publish_name}"
            asset_type = "MaterialsGLB"
        else:
            object_key = f"materials/modelfiles/image/{taskid}/{pipeline}/{jobid}/{path.name}"
            public_url = f"{PICTURE_PUBLIC_BASE_URL}/{taskid}/{pipeline}/{jobid}/{path.name}"
            asset_type = "MaterialsPNG"
        try:
            _trace(
                path,
                "upload_started",
                asset_type=asset_type,
                bytes=path.stat().st_size,
                object_key=object_key,
                public_url=public_url,
            )
            response = await oss_upload("alpha", object_key, path.read_bytes())
            _trace(path, "upload_finished", response=response, object_key=object_key)
            if not isinstance(response, dict) or response.get("status") != 200:
                logger.warning("[new-material-assets] upload failed: key=%s response=%s", object_key, response)
                _trace(path, "upload_rejected", response=response, object_key=object_key)
                continue
            # Confirm that the S3-compatible endpoint can see the object before
            # handing its URL to the browser.  This catches a failed/minio-race
            # rather than displaying a permanent broken asset card.
            try:
                exists = await get_storage_client().aobject_exists("alpha", object_key)
            except Exception as exc:
                logger.warning("[new-material-assets] object verification failed: key=%s error=%s", object_key, exc)
                _trace(path, "storage_verification_error", object_key=object_key, error=repr(exc))
                exists = False
            _trace(path, "storage_verified", object_key=object_key, exists=exists)
            if not exists:
                logger.warning("[new-material-assets] object absent after upload: key=%s", object_key)
                _trace(path, "storage_absent", object_key=object_key)
                continue

            name = str(asset.get("name") or path.stem)
            if asset_type == "MaterialsGLB":
                payload = {
                    "step_id": step_id,
                    "stepId": "FILAMENT_SELECTION_OPTIMIZATION",
                    "title": "无机新材料发现与初步验证",
                    "name": name,
                    "docs": str(asset.get("docs") or "新材料发现可视化资产"),
                    "url": public_url,
                    "type": "MaterialsGLB",
                    "description": str(asset.get("docs") or "新材料发现可视化资产"),
                }
                _trace(path, "websocket_send_started", payload=payload)
                await websocket.send_json(payload)
                _trace(path, "websocket_send_finished", asset_type=asset_type, public_url=public_url)
                logger.info("[new-material-assets] emitted GLB key=%s url=%s", object_key, public_url)
            else:
                markdown_images.append(f"![{name}]({public_url})")
                _trace(path, "markdown_image_ready", asset_type=asset_type, public_url=public_url)
                logger.info("[new-material-assets] prepared Markdown image key=%s url=%s", object_key, public_url)
        except Exception as exc:
            logger.exception("[new-material-assets] failed to emit asset path=%s error=%s", path, exc)
            _trace(path, "delivery_error", error=repr(exc), object_key=object_key, public_url=public_url)
            continue
    return "\n\n".join(markdown_images)
