"""Service-level orchestration; transport stays in main.py."""
from __future__ import annotations

from pathlib import Path

from src.application.request_normalization import normalize_request
from src.application.validation_workflow import execute

SERVICE_ID = "material_validation"
ROLE_NAME = "RefractoryMultiscaleValidationRole"
FRONTEND_STEP_ID = "REFRACTORY_MULTISCALE_VALIDATION"


def execute_refractory_validation(payload: dict, service_root: Path):
    # 阶段 1：标准化材料体系、结构来源、温度和目标性质。
    request = normalize_request(payload)
    # 阶段 2/3/4：读取 DFT、MLIP/MD、实验/文献证据，并形成同等级可比结果。
    return execute(payload, request, service_root)
