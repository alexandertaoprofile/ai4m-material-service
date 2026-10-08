"""Strict, dependency-free input contract for *internal-only* glass shadow replay.

This module deliberately does not decide whether a glass prediction is accurate,
commercially licensable, or equivalent to a legacy service property.
"""

from __future__ import annotations

import math
from typing import Any


SHADOW_TASKS = frozenset({"DENSITY_20C", "YOUNG_MODULUS"})
RESEARCH_ONLY_TASKS = frozenset({
    "TG", "CTE_BELOW_TG", "CTE_FIXED_T", "POISSON_RATIO",
    "THERMAL_CONDUCTIVITY",
})
UNITS = {
    "TG": "degC",
    "CTE_BELOW_TG": "1e-7_K-1",
    "CTE_FIXED_T": "1e-7_K-1",
    "DENSITY_20C": "g_cm-3",
    "YOUNG_MODULUS": "GPa",
    "POISSON_RATIO": "1",
    "THERMAL_CONDUCTIVITY": "W_m-1_K-1",
}


class ContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("invalid_number", f"{label} must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError("invalid_number", f"{label} must be finite")
    return result


def normalize_request(request: Any, preprocessor: dict[str, Any]) -> dict[str, Any]:
    """Return a training-compatible request; reject missing/zero ambiguity.

    The tight 0.05 mol.% tolerance permits published oxide rounding in the
    1111 family anchors but not the research CLI's broad 99-101 mol.% window.
    The received total remains in the audit record; all other fields are explicit.
    """
    if not isinstance(request, dict):
        raise ContractError("invalid_object", "request must be a JSON object")
    allowed = {"request_id", "composition_mol_percent", "task", "condition_temperature_C"}
    extra = set(request) - allowed
    if extra:
        raise ContractError("unsupported_fields", f"unsupported request fields: {sorted(extra)}")
    request_id = request.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 256):
        raise ContractError("invalid_request_id", "request_id must be a string of at most 256 characters")

    composition = request.get("composition_mol_percent")
    if not isinstance(composition, dict) or not composition:
        raise ContractError("invalid_composition", "composition_mol_percent must be a nonempty object")
    vocabulary = preprocessor.get("vocab")
    if not isinstance(vocabulary, dict) or not vocabulary:
        raise ContractError("invalid_artifact", "checkpoint vocabulary is missing")
    positive: dict[str, float] = {}
    for oxide, raw in composition.items():
        if not isinstance(oxide, str) or oxide not in vocabulary:
            raise ContractError("unknown_oxide", f"oxide outside frozen training vocabulary: {oxide!r}")
        value = _number(raw, f"composition_mol_percent.{oxide}")
        if value < 0 or value > 100:
            raise ContractError("invalid_fraction", f"{oxide} must be within 0-100 mol.%")
        if value > 0:
            positive[oxide] = value
    if positive.get("SiO2", 0.0) <= 0:
        raise ContractError("unsupported_family", "positive SiO2 is required")
    max_tokens = preprocessor.get("max_tokens")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= len(positive) <= max_tokens:
        raise ContractError("component_count", "positive oxide count exceeds frozen model capacity")
    total = sum(positive.values())
    # Some traceable family anchors total 99.97-100.02 after two-decimal
    # rounding. Their local perturbations conserve that original total.
    # Permit at most 0.05 mol.% of rounding, much tighter than the 99-101
    # research wrapper. Add a tiny float guard at the equality boundary.
    if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=0.050000001):
        raise ContractError("composition_closure", f"composition sums to {total:.6f}, not 100 mol.%")
    normalized = {oxide: value * 100.0 / total for oxide, value in sorted(positive.items())}

    task = request.get("task")
    if not isinstance(task, str) or task not in preprocessor.get("tasks", ()) or task not in UNITS:
        raise ContractError("unknown_task", "task is not in the frozen model task list")
    supplied = request.get("condition_temperature_C")
    if task == "CTE_FIXED_T":
        if supplied is None:
            raise ContractError("missing_condition", "CTE_FIXED_T requires condition_temperature_C")
        condition = _number(supplied, "condition_temperature_C")
        if not 55.0 <= condition <= 350.0:
            raise ContractError("temperature_outside_training_range", "CTE_FIXED_T requires 55-350 C")
    elif task == "DENSITY_20C":
        if supplied is not None and abs(_number(supplied, "condition_temperature_C") - 20.0) > 1e-6:
            raise ContractError("condition_mismatch", "DENSITY_20C requires 20 C")
        condition = 20.0
    else:
        if supplied is not None:
            raise ContractError("unsupported_condition", f"{task} does not accept condition_temperature_C")
        condition = None
    return {
        "request_id": request_id,
        "composition_mol_percent": normalized,
        "composition_total_received_mol_percent": total,
        "task": task,
        "condition_temperature_C": condition,
        "shadow_task_admitted": task in SHADOW_TASKS,
        "comparison_allowed": False,
        "display_allowed": False,
        "production_release_authorized": False,
    }
