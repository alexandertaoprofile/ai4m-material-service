"""Optional MatterSim relaxation/stability adapter for generated CIFs."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Iterable

from .schemas import GeneratedCandidate, ValidationResult
from .validation import write_validation_result

logger = logging.getLogger("mattergen_workflow")


def reference_mode() -> str:
    """Return a validated thermodynamic reference mode for this service."""
    configured = os.environ.get("MATTERSIM_REFERENCE_MODE", "official").strip().lower()
    if configured in {"official", "mp_api"}:
        return configured
    logger.warning("[DISCOVERY] unknown MATTERSIM_REFERENCE_MODE=%r; using official", configured)
    return "official"


def mattersim_enabled() -> bool:
    # The deployed MatterGen environment is part of this service, so full-chain
    # evaluation is the normal behavior. Set 0 only for a generation-only or
    # diagnostic run.
    return os.environ.get("MATTERSIM_ENABLED", "1").lower() in {"1", "true", "yes"}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    # ReferenceMP2020Correction is decompressed to a temporary LMDB.  /data has
    # substantially more headroom than the OS temp partition on deployment VMs.
    temporary_root = environment.setdefault("MATTERSIM_TMPDIR", "/data/mattersim_tmp")
    Path(temporary_root).mkdir(parents=True, exist_ok=True)
    environment["TMPDIR"] = temporary_root
    return subprocess.run(
        command, text=True, capture_output=True, check=False, env=environment,
        timeout=int(environment.get("MATTERSIM_TIMEOUT_SEC", "900")),
    )


def run_mattersim_evaluation(
    candidates: Iterable[GeneratedCandidate],
    validation_dir: Path,
    *,
    enabled: bool | None = None,
    reference_system: str = "",
) -> dict[str, dict]:
    """Run one batched MatterSim job and return results keyed by resolved CIF path.

    Failures are persisted as a stage log and intentionally do not convert a
    structurally valid candidate into a failed one.
    """
    candidates = list(candidates)
    from .omatg_contract import omatg_enabled

    def failed_rows(code):
        # Preserve execution failures for customer explanation; legacy adapter
        # behavior and scientific values remain unchanged.
        if not omatg_enabled():
            return {}
        return {str(candidate.cif_path.resolve()): {
            "source_cif": str(candidate.cif_path), "status": code, "error_code": code,
            "relaxation_converged": None, "energy_above_hull_ev": None,
            "formation_energy_per_atom_ev": None,
        } for candidate in candidates if candidate.cif_path}

    if not candidates or not (mattersim_enabled() if enabled is None else enabled):
        logger.info("[DISCOVERY] MatterSim skipped: candidates=%s enabled=%s", len(candidates), mattersim_enabled() if enabled is None else enabled)
        return failed_rows("disabled") if candidates else {}
    cif_paths = [candidate.cif_path for candidate in candidates if candidate.cif_path]
    if len(cif_paths) != len(candidates):
        return failed_rows("missing_structure")

    task_dir = validation_dir.parent
    output_dir = task_dir / "mattersim"
    output_dir.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[2]
    from .omatg_contract import omatg_enabled
    helper = project_root / "tools" / ("run_omatg_mattersim.py" if omatg_enabled() else "run_mattersim_evaluation.py")
    env_prefix = os.environ.get("MATTERSIM_ENV_PREFIX", os.environ.get("MATTERGEN_ENV_PREFIX", "/data/mamba/envs/mattergen-py310")).strip()
    command = (["micromamba", "run", "-p", env_prefix] if env_prefix else []) + [
        "python", str(helper), "--output-dir", str(output_dir),
        # The MP2020-corrected MatterGen reference is the default because it
        # keeps the candidate and convex-hull reference in one compatible
        # energy convention.  The lightweight ``mp_api`` path remains an
        # explicit diagnostic fallback only.
        "--reference-mode", reference_mode(),
    ]
    if omatg_enabled():
        command = [os.getenv("MATTERSIM_PYTHON", str(Path(env_prefix) / "bin" / "python")), str(helper), "--output-dir", str(output_dir)]
    if reference_system.strip():
        command.extend(["--reference-system", reference_system.strip()])
    for cif_path in cif_paths:
        command.extend(["--input", str(cif_path)])
    try:
        completed = _run(command)
    except (subprocess.TimeoutExpired, OSError) as exc:
        timeout = os.environ.get("MATTERSIM_TIMEOUT_SEC", "900")
        message = f"MatterSim/MP evaluation did not complete ({type(exc).__name__}); timeout budget {timeout}s."
        (output_dir / "mattersim.log").write_text(message, encoding="utf-8")
        logger.error("[DISCOVERY] %s (full log: %s)", message, output_dir / "mattersim.log")
        return failed_rows("timeout" if isinstance(exc, subprocess.TimeoutExpired) else "unavailable")
    (output_dir / "mattersim.log").write_text(
        (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )
    result_path = output_dir / "mattersim_results.json"
    if completed.returncode != 0 or not result_path.exists():
        summary = (completed.stderr or completed.stdout or "no results file").strip().splitlines()[-1:]
        logger.error("[DISCOVERY] MatterSim/MP failed rc=%s: %s (full log: %s)", completed.returncode, " ".join(summary), output_dir / "mattersim.log")
        return failed_rows("process_failed" if completed.returncode else "missing_results")
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("candidates"), list) or any(not isinstance(row, dict) or not row.get("source_cif") or not {"energy_above_hull_ev", "formation_energy_per_atom_ev"}.issubset(row) for row in data["candidates"]):
            raise ValueError("Malformed MatterSim result")
    except (OSError, ValueError, TypeError):
        logger.error("[DISCOVERY] MatterSim result could not be read; see %s", output_dir / "mattersim.log")
        return failed_rows("invalid_results")
    rows = data.get("candidates", [])
    logger.info("[DISCOVERY] MatterSim/MP produced %s thermodynamic result(s)", len(rows))
    results = failed_rows("missing_results")
    results.update({str(Path(row["source_cif"]).resolve()): row for row in rows})
    return results


def enrich_validations_with_mattersim(
    validations: list[ValidationResult],
    candidates: list[GeneratedCandidate],
    validation_dir: Path,
    *,
    reference_system: str = "",
) -> list[ValidationResult]:
    results = run_mattersim_evaluation(candidates, validation_dir, reference_system=reference_system)
    if not results:
        return validations
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for validation in validations:
        candidate = candidates_by_id.get(validation.candidate_id)
        if candidate is None or not candidate.cif_path:
            continue
        result = results.get(str(candidate.cif_path.resolve()))
        if result is None:
            continue
        validation.validator = "pymatgen+mattersim"
        validation.energy_above_hull = result["energy_above_hull_ev"]
        validation.formation_energy_per_atom = result["formation_energy_per_atom_ev"]
        # The renderer must consume the structure belonging to this exact
        # source CIF, never the shared multi-frame relaxation trajectory.
        if result.get("relaxed_structure_path"):
            validation.artifacts["relaxed_structure_path"] = result["relaxed_structure_path"]
        validation.artifacts["relaxed_structures"] = str(validation_dir.parent / "mattersim" / "relaxed_structures.extxyz")
        validation.metadata["mattersim"] = result
        if "relaxed_geometry" in result:
            geometry = result["relaxed_geometry"]
            validation.space_group = geometry.get("space_group")
            validation.density = geometry.get("density")
            validation.metadata.update(geometry.get("metadata", {}))
            if geometry.get("is_valid") is not True:
                validation.is_valid = False
                validation.errors.extend(geometry.get("errors", []))
        write_validation_result(validation, validation_dir)
    return validations
