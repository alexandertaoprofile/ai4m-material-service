"""Lightweight ALIGNN property screening for generated crystal candidates.

This stage deliberately complements MatterSim stability screening.  It makes
direct structure-to-property predictions only; it does not infer transport or
other properties for which no corresponding ALIGNN model was run.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from .schemas import GeneratedCandidate, ValidationResult
from .validation import write_validation_result


_PREDICTED_PROPERTIES = {
    "band_gap": {
        "label": "带隙",
        "unit": "eV",
        "display_method": "ALIGNN 带隙快速预测",
        "models": ("jv_mbj_bandgap_alignn", "jv_optb88vdw_bandgap_alignn", "mp_gappbe_alignn"),
    },
    "bulk_modulus": {
        "label": "体积模量",
        "unit": "GPa",
        "display_method": "ALIGNN 弹性性质快速预测",
        "models": ("jv_bulk_modulus_kv_alignn",),
    },
    "shear_modulus": {
        "label": "剪切模量",
        "unit": "GPa",
        "display_method": "ALIGNN 弹性性质快速预测",
        "models": ("jv_shear_modulus_gv_alignn",),
    },
    "electron_effective_mass": {
        "label": "电子有效质量",
        "unit": "m0",
        "display_method": "ALIGNN 电子结构快速预测",
        "models": ("jv_avg_elec_mass_alignn",),
    },
    "hole_effective_mass": {
        "label": "空穴有效质量",
        "unit": "m0",
        "display_method": "ALIGNN 电子结构快速预测",
        "models": ("jv_avg_hole_mass_alignn",),
    },
    "dielectric_constant_x": {
        "label": "介电常数 εx",
        "unit": "无量纲",
        "display_method": "ALIGNN 介电性质快速预测",
        "models": ("jv_epsx_alignn",),
    },
}

_ALIASES = {
    "bandgap": "band_gap",
    "bulk_modulus_gpa": "bulk_modulus",
    "shear_modulus_gpa": "shear_modulus",
    "avg_elec_mass": "electron_effective_mass",
    "avg_hole_mass": "hole_effective_mass",
    "dielectric_constant": "dielectric_constant_x",
    "dielectric": "dielectric_constant_x",
}
_DEFAULT_PROPERTIES = (
    "band_gap", "bulk_modulus", "shear_modulus", "dielectric_constant_x",
    "electron_effective_mass", "hole_effective_mass",
)
_PREDICTED_VALUE = re.compile(r"Predicted value:.*?\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]")


def requested_properties(target_properties: dict, validation_targets: dict) -> tuple[str, ...]:
    """Return the compact default panel plus explicitly requested properties."""
    requested = list(_DEFAULT_PROPERTIES)
    for source in (target_properties or {}, validation_targets or {}):
        for raw_name in source:
            name = _ALIASES.get(str(raw_name), str(raw_name))
            if name in _PREDICTED_PROPERTIES and name not in requested:
                requested.append(name)
    return tuple(requested)


def _run_alignn(model_name: str, cif_path: Path, timeout_sec: int) -> float:
    environment_name = os.environ.get("ALIGNN_ENV", "alignn-gpu-test")
    command = [
        "micromamba", "run", "-n", environment_name,
        "python", "-m", "alignn.pretrained",
        "--model_name", model_name,
        "--file_format", "cif",
        "--file_path", str(cif_path),
    ]
    completed = subprocess.run(
        command, text=True, capture_output=True, check=False, timeout=timeout_sec,
    )
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    if completed.returncode != 0:
        raise RuntimeError(output[-1000:] or f"ALIGNN exited with {completed.returncode}")
    match = _PREDICTED_VALUE.search(output)
    if match is None:
        raise RuntimeError(f"ALIGNN output did not contain a prediction: {output[-500:]}")
    return float(match.group(1))


def predict_candidate_properties(
    candidate: GeneratedCandidate,
    validation: ValidationResult,
    property_names: Iterable[str],
    *,
    predictor: Callable[[str, Path, int], float] = _run_alignn,
) -> ValidationResult:
    """Attach independently traceable ALIGNN predictions to one valid candidate."""
    if validation.is_valid is not True or candidate.cif_path is None or not candidate.cif_path.exists():
        return validation

    # A cached model normally returns quickly.  The longer default also lets a
    # newly deployed worker fetch its first official checkpoint without
    # misclassifying that cold start as an unsupported property.
    timeout_sec = int(os.environ.get("ALIGNN_TIMEOUT_SEC", "600"))
    predictions = dict(validation.property_predictions or {})
    errors = list(validation.metadata.get("alignn_errors", []))
    for property_name in property_names:
        spec = _PREDICTED_PROPERTIES.get(property_name)
        if spec is None or property_name in predictions:
            continue
        for model_name in spec["models"]:
            try:
                value = predictor(model_name, candidate.cif_path, timeout_sec)
            except Exception as exc:  # A property screen must not invalidate a structure.
                last_error = f"{property_name}/{model_name}: {str(exc)[-500:]}"
                continue
            predictions[property_name] = {
                "value": value,
                "unit": spec["unit"],
                "label": spec["label"],
                "model": model_name,
                "model_version": "ALIGNN 2025.4.1",
                "display_method": spec["display_method"],
                "structure_path": str(candidate.cif_path),
                # This is a direct prediction from the generated structure,
                # not an engineering conversion from another property.
                "evidence_level": "C：结构模型快速预测",
            }
            break
        else:
            errors.append(last_error)

    validation.property_predictions = predictions
    if errors:
        validation.metadata["alignn_errors"] = errors
    validation.metadata["alignn_property_screening"] = {
        "status": "ok" if predictions else "unavailable",
        "requested_properties": list(property_names),
        "completed_properties": sorted(predictions),
        "environment": os.environ.get("ALIGNN_ENV", "alignn-gpu-test"),
    }
    return validation


def enrich_validations_with_alignn(
    validations: list[ValidationResult],
    candidates: Iterable[GeneratedCandidate],
    validation_dir: Path,
    *,
    target_properties: dict | None = None,
    validation_targets: dict | None = None,
) -> list[ValidationResult]:
    """Run the lightweight screen only after structural admission succeeds."""
    if os.environ.get("ALIGNN_ENABLED", "1").lower() not in {"1", "true", "yes"}:
        return validations
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    property_names = requested_properties(target_properties or {}, validation_targets or {})
    for validation in validations:
        candidate = candidates_by_id.get(validation.candidate_id)
        if candidate is None:
            continue
        predict_candidate_properties(candidate, validation, property_names)
        write_validation_result(validation, validation_dir)
    return validations
