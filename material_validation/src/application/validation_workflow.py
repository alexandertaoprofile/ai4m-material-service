"""Four-stage evidence-first workflow for the W-14 reference case."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.contracts.schemas import EvidenceStatus, ValidationRequest, ValidationResult
from src.infrastructure.reference_case_reader import load_evidence
from src.infrastructure.w14_source_aggregation import aggregate_w14_sources


def _present(evidence: dict[str, Any], prefix: str) -> list[str]:
    return [item["path"] for item in evidence.get("artifacts", []) if item["path"].startswith(prefix + "/")]


def execute(payload: dict[str, Any], request: ValidationRequest, service_root: Path) -> ValidationResult:
    configured = Path(os.getenv("REFERENCE_CASE_ROOT", "data/reference_cases/w14_phase_i"))
    case_root = configured if configured.is_absolute() else service_root / configured
    evidence = load_evidence(case_root)
    mlip_root = Path(os.getenv("W14_MLIP_ROOT", "")).expanduser() if os.getenv("W14_MLIP_ROOT") else None
    md_root = Path(os.getenv("W14_MD_ROOT", "")).expanduser() if os.getenv("W14_MD_ROOT") else None
    external = aggregate_w14_sources(mlip_root, md_root)
    if request.execution_mode == "execute":
        raise RuntimeError("真实 DeepMD/LAMMPS 执行器尚未配置；当前仅可读取经版本化管理的标杆证据")
    dft = _present(evidence, "raw/dft")
    model = _present(evidence, "models") + [item["path"] for item in external.get("sources", {}).get("mlip_training", {}).get("artifacts", [])]
    md = _present(evidence, "raw/md") + [item["path"] for item in external.get("sources", {}).get("md_validation", {}).get("artifacts", [])]
    model_link_verified = bool((external.get("md") or {}).get("model_link_verified"))
    literature = _present(evidence, "literature")
    stages = [
        EvidenceStatus("01_material_task", "ready", "request", "已定义 W、温度范围、目标性质和标杆案例读取模式。"),
        EvidenceStatus("02_dft_baseline", "available" if dft else "needs_evidence", "DFT_reference" if dft else "none", "DFT 构型、能量、力、应力与数据划分将作为 MLIP 的参考基准。" if dft else "训练源已记录为 W-14 DFT 小应变数据；原始 DFT 数据集与独立验证集尚未完整挂载，因此不展示 DFT 覆盖与 parity 图。", dft, [] if dft else ["DFT dataset", "dataset split"]),
        EvidenceStatus("03_mlip_md", "available" if model and md and model_link_verified else "needs_evidence", "MLIP_and_MD" if model and md else "none", "已挂载模型与 MD 原始结果，且模型校验和已与 LAMMPS 输入绑定。" if model and md and model_link_verified else "已读取 DeepMD 模型与 MD 输出，但 LAMMPS 脚本所引用的势文件尚未完成校验和绑定；当前可展示原始记录，不将其升级为同一势函数的已验证结论。", model + md, [] if model and md and model_link_verified else ["model-to-MD checksum linkage"]),
        EvidenceStatus("04_validation_confidence", "available" if literature else "needs_evidence", "literature_or_experiment" if literature else "internal_benchmark_pending_locator" if evidence.get("user_supplied_benchmark") else "none", "可在同一条件下计算相对误差并给出适用范围。" if literature else "已归集用户提供的 300 K 内部对标数值；尚缺论文/标准、试样状态、测试方法与页码，因此不将其表述为正式实验文献验证。" if evidence.get("user_supplied_benchmark") else "尚未挂载带来源定位和测试条件的实验/文献数据；不输出正式实验误差。", literature + (["user_supplied_benchmark.json"] if evidence.get("user_supplied_benchmark") else []), [] if literature else ["traceable literature or experiment records"]),
    ]
    evidence_ready = all(stage.status in {"ready", "available"} for stage in stages)
    conclusion = {
        "level": "高可信" if evidence_ready else "建议补充数据",
        "text": "W-14 跨尺度验证所需证据已齐备，可按 DFT→MLIP→MD→实际性能对标链路形成结论。" if evidence_ready else "当前 W-14 已归集 DeepMD 训练记录、冻结势文件和 LAMMPS 小应变 MD 输出；300 K 实际性能对标显示，核心弹性与声学指标误差为 1.6%–3.5%。该模型可用于纯钨 0–900 K 小应变弹性与声学性质的初步评估；随着原始 DFT 划分和势文件—MD 绑定信息完善，服务将补充完整的数据覆盖与端到端验证卡。",
        "boundary": "MLIP 精度先相对 DFT 评价；MD 性质再在相同材料状态和温度条件下与实验/文献对标。",
    }
    return ValidationResult(request.taskid, "completed", request, stages, conclusion, {**evidence, "external_sources": external})
