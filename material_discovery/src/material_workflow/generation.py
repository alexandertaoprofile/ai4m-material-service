"""Real MatterGen subprocess integration for new-material discovery."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .schemas import GeneratedCandidate, GenerationConstraint, GenerationManifest


GenerationRunner = Callable[[GenerationConstraint, Path, int], GenerationManifest]
_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)")
_PROPERTY_MODELS = {
    "band_gap": ("dft_band_gap", "dft_band_gap"),
    "dft_band_gap": ("dft_band_gap", "dft_band_gap"),
    "bulk_modulus": ("ml_bulk_modulus", "ml_bulk_modulus"),
    "ml_bulk_modulus": ("ml_bulk_modulus", "ml_bulk_modulus"),
    "mag_density": ("dft_mag_density", "dft_mag_density"),
    "dft_mag_density": ("dft_mag_density", "dft_mag_density"),
}


def write_generation_manifest(manifest: GenerationManifest, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "generation_manifest.json"
    path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _chemical_system(constraints: GenerationConstraint) -> Optional[str]:
    elements = list(constraints.allowed_elements)
    if not elements and constraints.target_formula:
        elements = _ELEMENT_RE.findall(constraints.target_formula)
    unique = list(dict.fromkeys(element.capitalize() for element in elements))
    return "-".join(unique) if unique else None


def _model_and_properties(constraints: GenerationConstraint) -> Tuple[str, Dict[str, object]]:
    properties = dict(constraints.target_properties or {})
    chemical_system = _chemical_system(constraints)
    energy = properties.get("energy_above_hull")
    if chemical_system and energy is not None:
        return "chemical_system_energy_above_hull", {
            "chemical_system": chemical_system,
            "energy_above_hull": float(energy),
        }
    magnetic_density = properties.get("mag_density", properties.get("dft_mag_density"))
    hhi_score = properties.get("hhi_score")
    if magnetic_density is not None and hhi_score is not None:
        return "dft_mag_density_hhi_score", {
            "dft_mag_density": float(magnetic_density),
            "hhi_score": float(hhi_score),
        }
    if properties.get("space_group") is not None:
        return "space_group", {"space_group": int(properties["space_group"])}
    for key, value in properties.items():
        if key in _PROPERTY_MODELS and value is not None:
            model, property_name = _PROPERTY_MODELS[key]
            return model, {property_name: float(value)}
    if chemical_system:
        return "chemical_system", {"chemical_system": chemical_system}
    return "mattergen_base", {}


def build_mattergen_command(constraints: GenerationConstraint, output_dir: Path, max_candidates: int) -> List[str]:
    """Create the official ``mattergen-generate`` invocation without a shell."""
    model, properties = _model_and_properties(constraints)
    executable = os.getenv("MATTERGEN_EXECUTABLE", "mattergen-generate")
    repo_root = Path(__file__).resolve().parents[2]
    fast_sampling_path = repo_root / "configs"
    sampling_path = os.getenv("MATTERGEN_SAMPLING_CONFIG_PATH", str(fast_sampling_path)).strip()
    sampling_name = os.getenv("MATTERGEN_SAMPLING_CONFIG_NAME", "mattergen_fast_sampling").strip()
    sampling_steps = int(os.getenv("MATTERGEN_SAMPLING_STEPS", "100"))
    if sampling_steps < 1:
        raise ValueError("MATTERGEN_SAMPLING_STEPS must be positive")
    command: List[str] = [
        executable,
        str(output_dir),
        f"--pretrained-name={model}",
        f"--batch_size={max_candidates}",
        "--num_batches=1",
        f"--sampling_config_path={sampling_path}",
        f"--sampling_config_name={sampling_name}",
        # MatterGen requires the atomic-number D3PM schedule to use the same
        # number of reverse steps as the sampler.  The override keeps the
        # accelerated N=100 profile internally consistent.
        "--config_overrides=" + json.dumps([
            "lightning_module.diffusion_module.corruption.discrete_corruptions.atomic_numbers.d3pm.schedule.num_steps=" + str(sampling_steps)
        ]),
    ]
    if properties:
        command.append(f"--properties_to_condition_on={json.dumps(properties, separators=(',', ':'))}")
        command.append(f"--diffusion_guidance_factor={os.getenv('MATTERGEN_GUIDANCE_FACTOR', '2.0')}")
    record_trajectories = os.getenv("MATTERGEN_RECORD_TRAJECTORIES", "false").strip().lower() in {"1", "true", "yes", "on"}
    command.append(f"--record_trajectories={record_trajectories}")
    environment_prefix = os.getenv("MATTERGEN_ENV_PREFIX", "/data/mamba/envs/mattergen-py310").strip()
    environment_name = os.getenv("MATTERGEN_ENV", "mattergen-py310").strip()
    if environment_prefix:
        return [os.getenv("MICROMAMBA_EXECUTABLE", "micromamba"), "run", "-p", environment_prefix, *command]
    if environment_name:
        return [os.getenv("MICROMAMBA_EXECUTABLE", "micromamba"), "run", "-n", environment_name, *command]
    return command


def _extract_cifs(output_dir: Path, max_candidates: int) -> List[Path]:
    archive = output_dir / "generated_crystals_cif.zip"
    cifs_dir = output_dir / "cifs"
    if archive.exists():
        cifs_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                if item.is_dir() or not item.filename.lower().endswith(".cif"):
                    continue
                destination = cifs_dir / Path(item.filename).name
                with zipped.open(item) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
    return sorted((output_dir / "cifs").glob("*.cif"))[:max_candidates] if cifs_dir.exists() else sorted(output_dir.glob("*.cif"))[:max_candidates]


def _formula_from_cif(cif_path: Path) -> Optional[str]:
    try:
        from pymatgen.core import Structure

        return Structure.from_file(cif_path).composition.reduced_formula
    except Exception:
        return None


def _default_mattergen_runner(constraints: GenerationConstraint, output_dir: Path, max_candidates: int) -> GenerationManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_mattergen_command(constraints, output_dir, max_candidates)
    environment = os.environ.copy()
    # Respect a deployment-provided HF_HOME, but do not force a new empty cache
    # here: existing MatterGen checkpoints may already be cached by the service
    # account in Hugging Face's normal location.
    environment.setdefault("MAMBA_ROOT_PREFIX", "/data/mamba")
    log_path = output_dir / "mattergen.log"
    timeout_seconds = int(os.getenv("MATTERGEN_TIMEOUT_SEC", "1800"))
    try:
        # Stream the child output directly to the durable log.  MatterGen's
        # tqdm lines can then be parsed by the WebSocket progress reporter
        # while diffusion is still running, rather than only after completion.
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=output_dir,
                text=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return GenerationManifest(
                    taskid=constraints.taskid,
                    status="timeout",
                    message=f"MatterGen exceeded {timeout_seconds}s",
                    metadata={"command": command, "log_path": str(log_path)},
                )
    except FileNotFoundError as exc:
        return GenerationManifest(taskid=constraints.taskid, status="unavailable", message=str(exc), metadata={"command": command})
    output_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    cifs = _extract_cifs(output_dir, max_candidates) if returncode == 0 else []
    candidates = [GeneratedCandidate(candidate_id=f"mg-{index:03d}", formula_pretty=_formula_from_cif(cif), cif_path=cif, structure_path=cif, metadata={"mattergen_cif": str(cif)}) for index, cif in enumerate(cifs, start=1)]
    if candidates:
        status = "ok"
        message = "已生成候选晶体结构。"
    elif "Network is unreachable" in output_text and "huggingface" in output_text.lower():
        status = "unavailable"
        message = (
            "MatterGen 所需的本地模型权重不可用，且当前运行环境无法连接模型仓库；"
            "本轮尚未开始有效的候选结构生成。"
        )
    else:
        status = "failed"
        if "AssertionError" in output_text and "discrete_corruptions" in output_text:
            message = "MatterGen 采样配置的步数不一致，生成在采样开始前中止。"
        else:
            message = "MatterGen 生成进程未产出可读取的候选晶体结构文件。"
    return GenerationManifest(taskid=constraints.taskid, status=status, candidates=candidates, message=message, metadata={"command": command, "returncode": returncode, "log_path": str(log_path), "model": _model_and_properties(constraints)[0]})


def run_mattergen_generation(constraints: GenerationConstraint, output_dir: Path, max_candidates: int = 8, runner: Optional[GenerationRunner] = None) -> GenerationManifest:
    """Generate real MatterGen structures and persist an auditable manifest."""
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least one")
    manifest = (runner or _default_mattergen_runner)(constraints, output_dir, max_candidates)
    write_generation_manifest(manifest, output_dir)
    return manifest
