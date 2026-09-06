"""Request parsing with conservative service-boundary checks."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from src.contracts.schemas import ValidationRequest

_SUPPORTED_PROPERTIES = {
    "lattice_parameter", "density", "elastic_constants", "polycrystalline_moduli",
    "sound_speed", "thermal_expansion", "heat_capacity_cv", "diffusion", "defects",
}


def task_id(payload: dict[str, Any]) -> str:
    supplied = str(payload.get("taskid") or f"refractory-{datetime.now(timezone.utc):%Y%m%d%H%M%S}").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", supplied):
        return supplied
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", supplied).strip("_.-")[:72]
    return f"{readable or 'refractory'}-{hashlib.sha256(supplied.encode()).hexdigest()[:16]}"


def _context(payload: dict[str, Any]) -> str:
    values = [payload.get(key) for key in ("idea", "content", "query", "project_idea", "upstream_result")]
    return "\n".join(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) for value in values if value)[:12000]


def normalize_request(payload: dict[str, Any]) -> ValidationRequest:
    scope = payload.get("refractory_validation") or {}
    if not isinstance(scope, dict):
        raise ValueError("refractory_validation must be an object")
    text = _context(payload)
    material = str(scope.get("material_system") or ("W" if re.search(r"(?:纯)?钨|tungsten|\\bW\\b", text, re.I) else "")).strip()
    if material != "W":
        raise ValueError("首版仅开放 W / W-14 标杆案例；Mo、Ta、Nb 等体系需提供各自的 DFT/MLIP 验证数据后启用")
    temperatures = scope.get("temperature_K", [300, 600, 900])
    if not isinstance(temperatures, list) or not temperatures or any(not isinstance(item, (int, float)) or item <= 0 for item in temperatures):
        raise ValueError("temperature_K must be a non-empty list of positive numbers")
    properties = scope.get("target_properties", ["lattice_parameter", "density", "elastic_constants", "polycrystalline_moduli", "sound_speed", "thermal_expansion", "heat_capacity_cv"])
    if not isinstance(properties, list) or any(item not in _SUPPORTED_PROPERTIES for item in properties):
        raise ValueError("target_properties contains an unsupported property")
    mode = str(scope.get("execution_mode", "reference_case"))
    if mode not in {"reference_case", "execute"}:
        raise ValueError("execution_mode must be reference_case or execute")
    return ValidationRequest(taskid=task_id(payload), raw_requirement=text, material_system="W", structure_source=str(scope.get("structure_source", "reference_case_w14_phase_i")), temperature_K=sorted({float(item) for item in temperatures}), target_properties=properties, execution_mode=mode, file_metadata=list(payload.get("file_metadata") or []))
