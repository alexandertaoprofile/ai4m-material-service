"""Render and emit rich, evidence-backed frontend assets for new materials."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.storage_utils import get_storage_client, oss_upload

from .emitters import build_scientific_conclusion, structural_admission_failure_reason, write_pipeline_manifest


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
    3: ("通常 20 秒–3 分钟", "正在完成结构松弛与同元素体系稳定性比较；外部数据查询波动会延长等待。"),
    4: ("通常少于 1 分钟", "正在整理数值、渲染结构图和热力学评分卡，并上传前端资产。"),
}


def build_retrieval_progress_payload(
    request_id: str,
    seq: int,
    *,
    stage: str,
    percent: int,
    status: str,
    text: str,
) -> dict[str, Any]:
    """Build the frontend progress-bar event without manufacturing progress."""
    return {
        "type": "retrieval_progress",
        "request_id": request_id,
        "seq": seq,
        "content": {
            "stage": stage,
            "percent": max(0, min(100, int(percent))),
            "status": status,
            "text": text,
        },
    }


@dataclass
class DiscoveryProgressEvents:
    """Own a monotonic event sequence shared by live and terminal updates."""

    request_id: str
    seq: int = 0

    async def emit(
        self,
        websocket: Any,
        *,
        stage: str,
        percent: int,
        status: str,
        text: str,
    ) -> None:
        self.seq += 1
        await websocket.send_json(build_retrieval_progress_payload(
            self.request_id,
            self.seq,
            stage=stage,
            percent=percent,
            status=status,
            text=text,
        ))


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
        return ""

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


def build_discovery_conclusion(result) -> str:
    """Close the final report with a concise, evidence-scoped decision."""
    conclusion = build_scientific_conclusion(result)
    generated = len(result.generation.candidates)
    admitted = sum(item.is_valid is True for item in result.validations)
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    if top is None or top.validation is None:
        finding = "本轮尚未形成可进入稳定性判断的候选结构。"
        next_step = "建议检查元素体系、生成条件或模型资源后重新执行。"
    else:
        validation = top.validation
        formula = top.candidate.formula_pretty or validation.formula_pretty or top.candidate.candidate_id
        hull = validation.energy_above_hull
        if validation.is_valid is not True:
            finding = f"候选 `{formula}` 未通过基础结构检查。{structural_admission_failure_reason(validation)}"
            next_step = "建议先调整生成条件或进行几何松弛；待原子间距满足结构准入条件后，再开展性质和热力学稳定性评估。"
        elif hull is None:
            finding = f"候选 `{formula}` 已通过基础结构检查，但尚未得到 E_hull，暂不能判断其热力学稳定性。"
            next_step = "建议先完成稳定性评估，再决定后续验证优先级。"
        else:
            threshold = float((result.constraints.target_properties or {}).get("energy_above_hull", 0.05))
            if conclusion.get("decision") == "comparison_candidate":
                finding = (
                    f"本轮候选均未达到 {threshold:.2f} eV/atom 的稳定性初筛阈值；"
                    f"`{formula}` 的 E_hull 为 {hull:.4f} eV/atom，是当前批次中最接近该目标的比较候选。"
                )
                next_step = "建议保留该结构及下方性质初筛结果作为本轮比较基准，并调整元素体系或生成条件后继续探索。"
            else:
                status = "达到" if hull <= threshold else "未达到"
                finding = f"排名第一的候选为 `{formula}`，E_hull 为 {hull:.4f} eV/atom，{status} {threshold:.2f} eV/atom 的本轮初筛阈值。"
                next_step = {
                    "shortlist_for_dft": "建议优先开展高精度计算与目标性能验证，并结合实际工况确认制备与服役可行性。",
                    "structure_only": "建议先完成稳定性评估，再决定后续验证优先级。",
                }.get(conclusion.get("decision"), "建议结合专项计算与实验继续验证。")
    return "\n\n".join([
        "## 4. 结论",
        f"针对本轮无机新材料结构探索，在已设定元素体系与生成条件下，{finding}",
        f"本轮生成 {generated} 个候选，其中 {admitted} 个通过基础结构检查。{next_step}",
    ])


def build_property_screening_card(result) -> str:
    """Render only properties actually computed for the shortlisted candidate."""
    top = result.ranked_candidates[0] if result.ranked_candidates else None
    predictions = (top.validation.property_predictions if top and top.validation else {}) or {}
    validation = top.validation if top else None
    if validation is None or validation.is_valid is not True:
        return ""
    formula = top.candidate.formula_pretty or top.validation.formula_pretty or top.candidate.candidate_id
    lines = [
        "## 5. 候选性质初筛",
        f"以下为无机材料候选（成分式：{formula}）在本轮结构上的初筛结果。模型名称与版本已保留在任务记录中。",
        "",
        "| 性质 | 数值 | 本轮条件、方法与解读 | 证据等级 |",
        "|---|---:|---|---|",
    ]
    if validation.density is not None:
        lines.append(f"| 密度 | {validation.density:.4f} g/cm³ | 当前候选结构；pymatgen 结构计算 | C：结构计算结果 |")
    if validation.formation_energy_per_atom is not None:
        lines.append(
            f"| 形成能 | {validation.formation_energy_per_atom:.4f} eV/atom | "
            "MatterSim 松弛；采用 MP2020 校正参考能量体系，仅作热力学初筛 | C：模型初筛 |"
        )
    if validation.energy_above_hull is not None:
        lines.append(
            f"| E_hull | {validation.energy_above_hull:.4f} eV/atom | "
            "MatterSim 松弛后相对于 MP2020 校正参考凸包；用于候选排序，需由统一 DFT 复核 | C：模型初筛 |"
        )
    band_gap = predictions.get("band_gap", {}).get("value")
    near_metal = isinstance(band_gap, (int, float)) and float(band_gap) <= 0.05
    bulk = predictions.get("bulk_modulus", {}).get("value")
    shear = predictions.get("shear_modulus", {}).get("value")
    elastic_caution = False
    if isinstance(bulk, (int, float)) and isinstance(shear, (int, float)) and bulk > 0 and shear > 0:
        poisson_ratio = (3 * bulk - 2 * shear) / (2 * (3 * bulk + shear))
        elastic_caution = poisson_ratio > 0.45
    for property_name, item in predictions.items():
        value = item.get("value")
        if not isinstance(value, (int, float)):
            continue
        number = f"{float(value):.4f}"
        interpretation = ""
        if property_name == "band_gap" and near_metal:
            interpretation = "；接近零按金属/半金属候选解读，负值是回归越界，不作为负带隙"
        elif property_name in {"electron_effective_mass", "hole_effective_mass"} and near_metal:
            interpretation = "；带隙接近零，带边有效质量不用于该候选的载流子判断"
        elif property_name == "dielectric_constant_x" and near_metal:
            interpretation = "；近零带隙候选的介电单值不宜直接用于工程评价，仅保留模型记录"
        elif property_name in {"bulk_modulus", "shear_modulus"} and elastic_caution:
            interpretation = "；与另一弹性模量组合偏离常见金属范围，需以弹性张量或 DFT 复核"
        lines.append(
            f"| {item.get('label', '性质')} | {number} {item.get('unit', '')} | "
            f"当前候选结构；{item.get('display_method', 'ALIGNN 性质快速预测')}{interpretation} | "
            f"{item.get('evidence_level', 'C：结构模型快速预测')} |"
        )
    if isinstance(bulk, (int, float)) and isinstance(shear, (int, float)) and bulk > 0 and shear > 0:
        chen_hardness = max(0.0, 2.0 * (((shear / bulk) ** 2 * shear) ** 0.585) - 3.0)
        low, high = chen_hardness * 0.65, chen_hardness * 1.35
        hardness_note = (
            "由低剪切模量输入经非负截断得到，不能解读为实际硬度为零；应先复核弹性模量"
            if chen_hardness == 0 else
            "由本轮体积/剪切模量按 Chen 经验式推导；±35% 初步区间"
        )
        lines.append(
            f"| 硬度（估算） | {low:.2f}–{high:.2f} GPa | "
            f"{hardness_note} | D：工程估算 |"
        )
    return "\n".join(lines)


def planned_discovery_method_block(constraints) -> str:
    """Describe this task's configured calculation chain before execution.

    This is a method definition, not a statement that any model has completed.
    """
    from .alignn import _PREDICTED_PROPERTIES, requested_properties
    from .generation import _model_and_properties
    from .mattersim import mattersim_enabled

    _, conditioned_properties = _model_and_properties(constraints)
    condition_labels = {
        "chemical_system": "允许元素体系",
        "energy_above_hull": "稳定性目标 E_hull",
        "band_gap": "带隙目标",
        "bulk_modulus": "体积模量目标",
        "mag_density": "磁性密度目标",
        "space_group": "空间群偏好",
    }
    condition_text = (
        "；".join(f"{condition_labels.get(name, name)}：{value}" for name, value in conditioned_properties.items())
        or "无附加性质条件"
    )
    sampling_steps = int(os.environ.get("MATTERGEN_SAMPLING_STEPS", "100"))
    guidance = os.environ.get("MATTERGEN_GUIDANCE_FACTOR", "2.0")
    requested = requested_properties(
        constraints.target_properties or {},
        constraints.validation_targets or {},
    )
    alignn_properties = [
        _PREDICTED_PROPERTIES[name]["label"]
        for name in requested
        if name in _PREDICTED_PROPERTIES
    ]
    blocks = [
        "## 4. 计划计算链与模型定义",
        "以下为本任务已配置、将按顺序调用的计算链；本节定义输入、输出与适用场景，不包含计算结果。",
        "",
        "### 4.1 MatterGen 条件扩散生成",
        "MatterGen 以晶格、原子种类和分数坐标为联合生成变量，通过反向扩散从噪声结构逐步形成候选晶体。条件信息在采样时作为引导项进入生成过程。",
        "候选结构由晶格、原子种类和原子位置共同表示：",
        r"$$S=(\mathbf{L},\mathbf{Z},\mathbf{F}),\qquad \mathbf{r}_i=\mathbf{L}\mathbf{f}_i$$",
        "其中 L 为晶格矩阵，Z 为原子种类，F 为分数坐标，r_i 为第 i 个原子的笛卡尔坐标。",
        "条件扩散的反向生成与引导写为：",
        r"$$p_\theta(S_{t-1}\mid S_t,\mathbf{c}),\qquad \hat\epsilon=(1+w)\epsilon_\theta(S_t,\mathbf{c})-w\epsilon_\theta(S_t,\varnothing)$$",
        "",
        "| 项目 | 本任务配置 |",
        "|---|---|",
        "| 预训练模型 | 化学体系—稳定性条件生成模型 |",
        f"| 条件描述符 | {condition_text} |",
        f"| 采样设置 | {sampling_steps} 个反向扩散步；条件引导系数 {guidance} |",
        "| 输入 | 允许元素集合、目标性质条件与候选数量 |",
        "| 输出 | 候选 CIF：晶格参数、元素种类、原子分数坐标 |",
        "",
        "### 4.2 pymatgen 结构合理性检查",
        "对每个候选 CIF 解析周期结构，计算原子间距离矩阵，并用共价半径下限筛查过近原子对；同时记录有序性、空间群、密度和位点数。",
        "周期边界下，原子对采用最小像距离；结构准入采用共价半径下限：",
        r"$$d_{ij}=\min_{\mathbf{n}\in\mathbb{Z}^3}\|\mathbf{L}(\mathbf{f}_i-\mathbf{f}_j+\mathbf{n})\|_2,\qquad d_{ij}\geq0.75(r_i^{cov}+r_j^{cov})$$",
        "空间群由保持分数坐标等价的旋转—平移操作记录：",
        r"$$\mathbf{f}_j\equiv\mathbf{R}\mathbf{f}_i+\mathbf{t}\pmod{1}$$",
        "",
        "| 输入 | 输出 | 判定规则 |",
        "|---|---|---|",
        "| 生成的晶体结构文件 | 有效性、化学式、空间群、密度、最小原子间距 | 无部分占位；原子对距离满足共价半径下限 |",
    ]
    if alignn_properties:
        blocks.extend([
            "",
            "### 4.3 ALIGNN 结构—性质快速预测",
            "ALIGNN 将晶体表示为原子邻接图及边角线图（atomistic line graph），用预训练图神经网络从候选结构直接预测所选性质。",
            "原子图以原子为节点、原子间距离为边特征；边角线图进一步编码相邻化学键与键角：",
            r"$$G=(V,E),\qquad L(G):\ (i,j)\xrightarrow{\angle jik}(j,k)$$",
            "图网络逐层聚合局域原子环境，性质头输出对应的结构—性质预测：",
            r"$$\mathbf{h}_i^{(l+1)}=\phi_l\!\left(\mathbf{h}_i^{(l)},\sum_{j\in\mathcal{N}(i)}\psi_l(\mathbf{h}_i^{(l)},\mathbf{h}_j^{(l)},\mathbf{e}_{ij}^{(l)})\right),\qquad \hat y_q=f_q(G,L(G))$$",
            "",
            "| 输入 | 本任务属性头 | 输出 |",
            "|---|---|---|",
            f"| 通过结构检查的晶体结构 | {'、'.join(alignn_properties)} | 相应性质的结构模型预测值 |",
        ])
    if mattersim_enabled():
        blocks.extend([
            "",
            "### 4.4 MatterSim 势函数松弛与相稳定性比较",
            "MatterSim 机器学习原子间势用于在候选结构上进行几何松弛并计算形成能；随后以 MP2020 校正参考能量体系构建凸包并计算高于凸包能 E_hull。",
            "势能由局域原子环境贡献求和，原子力为势能对位置的负梯度；松弛寻找最低能量结构：",
            r"$$E_\theta(S)=\sum_i\varepsilon_\theta(\mathcal{N}_i),\qquad \mathbf{F}_i=-\nabla_{\mathbf{r}_i}E_\theta,\qquad S^*=\arg\min_S E_\theta(S)$$",
            "随后计算形成能，并与同元素体系竞争相构成的凸包基准比较：",
            r"$$E_f=\frac{E_\theta(S^*)-\sum_\alpha n_\alpha\mu_\alpha}{N},\qquad E_{hull}=E_f-E_{hull}^{ref}(\mathbf{x})$$",
            "",
            "| 输入 | 输出 | 定量用途 |",
            "|---|---|---|",
            "| 通过结构检查的候选晶体 | 松弛后结构、形成能、高于凸包能 | 与 MP2020 校正参考凸包一致的热力学初筛与排序 |",
        ])
    return "\n".join(blocks)


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
        f"| {friendly_names.get(name, name)} | {display_value(name, value)} | 任务输入条件 |" for name, value in properties.items()
    ) or "| 目标性质 | 未提供数值 | 任务输入条件 |"
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
        "## 1. 问题描述",
        f"在元素体系 [{elements}] 中探索候选晶体结构，并以 {validation} 作为本轮研发关注点。",
        "",
        "## 2. 参数定义",
        "| 符号/参数 | 定义 | 本轮输入 |",
        "|---|---|---|",
        f"| $\\mathcal{{E}}$ | 允许元素集合 | {elements} |",
        "| $S$ | 待求候选晶体结构 | 晶格、元素种类与分数坐标由后续计算确定 |",
        "| $\\mathbf{t}$ | 目标性质或稳定性偏好 | 见下表 |",
        "",
        "## 3. 设计约束",
        "| 设计偏好 | 目标值 | 属性 |",
        "|---|---:|---|",
        property_rows,
        "",
        *( [f"需求解析：{' '.join(constraints.notes)}", ""] if constraints.notes else [] ),
        planned_discovery_method_block(constraints),
        "",
        "计算完成后，结果区仅保留实际产出并用于结论的模型结果。",
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
    progress_events: DiscoveryProgressEvents | None = None,
) -> None:
    """Emit concise, evidence-based JSON progress events for the frontend bar."""
    progress_events = progress_events or DiscoveryProgressEvents(request_id)
    last_phase: int | None = None
    last_sampling_current: int | None = None
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
        sampling_changed = sampling is not None and sampling["current"] != last_sampling_current
        sampling_text = (
            f" 当前已完成真实扩散步数 {sampling['current']}/{sampling['total']}（{sampling['percent']}%）；"
            if sampling else ""
        )
        full_description = (
            f"{description}{sampling_text} 已等待 {total_elapsed // 60} 分 {total_elapsed % 60} 秒；"
            f"本阶段已进行 {phase_elapsed // 60} 分 {phase_elapsed % 60} 秒；"
            f"常见耗时：{expected}。{expectation_note}"
        )
        if phase_changed or sampling_changed or now - last_heartbeat >= 15:
            description_for_state = full_description if phase_changed else (
                f"第 {phase}/4 阶段；"
                + (f"当前扩散步数 {sampling['current']}/{sampling['total']}（{sampling['percent']}%）" if sampling else "正在执行。")
            )
            _write_progress_state(
                task_dir, phase=phase, title=title, description=description_for_state,
                total_elapsed=total_elapsed, phase_elapsed=phase_elapsed,
            )
            if phase_changed or sampling_changed:
                if phase == 1:
                    stage, percent, text = "preparing", 5, "正在准备计算"
                elif phase == 2:
                    # 20–75% is reserved for the only fine-grained live metric:
                    # MatterGen's own sampled diffusion-step counter.
                    progress = sampling["percent"] if sampling else 0
                    stage, percent = "generation", 20 + round(progress * 0.55)
                    text = (
                        f"正在生成候选结构：{sampling['current']}/{sampling['total']} 步"
                        if sampling else "正在加载生成模型"
                    )
                elif phase == 3:
                    stage, percent, text = "structure_validation", 80, "正在检查候选结构"
                else:
                    stage, percent, text = "result_packaging", 95, "正在整理结果"
                await progress_events.emit(
                    websocket,
                    stage=stage,
                    percent=percent,
                    status="in_progress",
                    text=text,
                )
            if phase_changed:
                last_phase = phase
            if sampling is not None:
                last_sampling_current = sampling["current"]
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
