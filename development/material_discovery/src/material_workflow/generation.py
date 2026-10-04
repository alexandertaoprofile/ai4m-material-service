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
_HEA_V2_DEFAULT_MODEL_DIR = (
    "/data/se42/hea_surrogate/models/generative/hea_mattergen_v2/outputs/run_002"
)
_HEA_V2_DEFAULT_SYSTEMS = "Co-Cr-Fe-Mn-Ni"


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


def _chemical_system_elements(constraints: GenerationConstraint) -> List[str]:
    """Return the element list required by MatterGen's set embedding."""
    chemical_system = _chemical_system(constraints)
    return chemical_system.split("-") if chemical_system else []


def _canonical_system(value: str) -> str:
    return "-".join(sorted({item.strip().capitalize() for item in value.split("-") if item.strip()}))


def _use_hea_v2(constraints: GenerationConstraint) -> bool:
    """Route only validated HEA systems to the locally fine-tuned checkpoint.

    The v2 checkpoint has a measured conditional-generation result for the
    Cantor system.  Other five-plus-element systems stay on the official
    conditional model until they have their own validation evidence.
    """
    enabled = os.getenv("MATTERGEN_HEA_V2_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    if (constraints.target_properties or {}).get("energy_above_hull") is None:
        return False
    current = _chemical_system(constraints)
    configured = os.getenv("MATTERGEN_HEA_V2_SYSTEMS", _HEA_V2_DEFAULT_SYSTEMS)
    supported = {_canonical_system(item) for item in configured.split(",") if item.strip()}
    return bool(current and _canonical_system(current) in supported)


def _model_provenance(model: str) -> Dict[str, object]:
    if model != "hea_v2_chemical_system_energy_above_hull":
        return {"route": "official_mattergen", "model": model}
    return {
        "route": "hea_v2_conditional",
        "model": model,
        "checkpoint": "epoch=18-loss_val=0.47.ckpt",
        "training": {
            "base_model": "official MatterGen chemical_system + energy_above_hull conditional model",
            "adaptation": "full fine-tuning for 30 epochs; best validation checkpoint at epoch 18",
            "dataset": "Cantor HEA DFT 2023-04-06, relaxed 5-7 element structures with at most 32 atoms",
            "splits": {"train": 15520, "validation": 774, "test": 630},
            "split_policy": "chemical-system-grouped deterministic split",
        },
        "validation_scope": (
            "Conditional-generation benchmarked for Co-Cr-Fe-Mn-Ni at E_hull=0.05 eV/atom; "
            "other systems remain on the official model."
        ),
    }


def _model_and_properties(constraints: GenerationConstraint) -> Tuple[str, Dict[str, object]]:
    properties = dict(constraints.target_properties or {})
    chemical_system = _chemical_system(constraints)
    chemical_system_elements = _chemical_system_elements(constraints)
    energy = properties.get("energy_above_hull")
    if chemical_system and energy is not None:
        return ("hea_v2_chemical_system_energy_above_hull" if _use_hea_v2(constraints) else "chemical_system_energy_above_hull"), {
            # MatterGen's chemical-system embedding expects an element set,
            # not the display string "Co-Cr-Fe-Mn-Ni".
            "chemical_system": chemical_system_elements,
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
        return "chemical_system", {"chemical_system": chemical_system_elements}
    return "mattergen_base", {}


def build_mattergen_command(constraints: GenerationConstraint, output_dir: Path, max_candidates: int) -> List[str]:
    """Create the official ``mattergen-generate`` invocation without a shell."""
    model, properties = _model_and_properties(constraints)
    executable = os.getenv("MATTERGEN_EXECUTABLE", "mattergen-generate")
    repo_root = Path(__file__).resolve().parents[2]
    fast_sampling_path = repo_root / "configs"
    use_hea_v2 = model == "hea_v2_chemical_system_energy_above_hull"
    command: List[str] = [
        executable,
        str(output_dir),
        f"--batch_size={max_candidates}",
        "--num_batches=1",
    ]
    if use_hea_v2:
        model_path = Path(os.getenv("MATTERGEN_HEA_V2_MODEL_DIR", _HEA_V2_DEFAULT_MODEL_DIR)).expanduser()
        checkpoint_epoch = os.getenv("MATTERGEN_HEA_V2_CHECKPOINT_EPOCH", "18").strip()
        if not (model_path / "config.yaml").is_file():
            raise ValueError(f"HEA v2 model configuration is unavailable: {model_path / 'config.yaml'}")
        if not any((model_path / "checkpoints").glob(f"epoch={checkpoint_epoch}-*.ckpt")):
            raise ValueError(f"HEA v2 best checkpoint is unavailable for epoch {checkpoint_epoch}: {model_path}")
        # Do not use the service's accelerated 100-step profile here.  v2 was
        # validated with MatterGen's default 1000-step sampler and guidance 1.
        command.extend([
            f"--model_path={model_path}",
            f"--checkpoint_epoch={checkpoint_epoch}",
        ])
    else:
        sampling_path = os.getenv("MATTERGEN_SAMPLING_CONFIG_PATH", str(fast_sampling_path)).strip()
        sampling_name = os.getenv("MATTERGEN_SAMPLING_CONFIG_NAME", "mattergen_fast_sampling").strip()
        sampling_steps = int(os.getenv("MATTERGEN_SAMPLING_STEPS", "100"))
        if sampling_steps < 1:
            raise ValueError("MATTERGEN_SAMPLING_STEPS must be positive")
        command.extend([
            f"--pretrained-name={model}",
            f"--sampling_config_path={sampling_path}",
            f"--sampling_config_name={sampling_name}",
            # MatterGen requires the atomic-number D3PM schedule to use the
            # same number of reverse steps as the sampler.
            "--config_overrides=" + json.dumps([
                "lightning_module.diffusion_module.corruption.discrete_corruptions.atomic_numbers.d3pm.schedule.num_steps=" + str(sampling_steps)
            ]),
        ])
    if properties:
        command.append(f"--properties_to_condition_on={json.dumps(properties, separators=(',', ':'))}")
        guidance = os.getenv("MATTERGEN_HEA_V2_GUIDANCE_FACTOR", "1.0") if use_hea_v2 else os.getenv("MATTERGEN_GUIDANCE_FACTOR", "2.0")
        command.append(f"--diffusion_guidance_factor={guidance}")
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
    model, _properties = _model_and_properties(constraints)
    try:
        command = build_mattergen_command(constraints, output_dir, max_candidates)
    except ValueError as exc:
        return GenerationManifest(
            taskid=constraints.taskid,
            status="unavailable",
            message=str(exc),
            metadata={"model": model, "model_provenance": _model_provenance(model)},
        )
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
                    metadata={"command": command, "log_path": str(log_path), "model": model, "model_provenance": _model_provenance(model)},
                )
    except FileNotFoundError as exc:
        return GenerationManifest(taskid=constraints.taskid, status="unavailable", message=str(exc), metadata={"command": command, "model": model, "model_provenance": _model_provenance(model)})
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
    return GenerationManifest(taskid=constraints.taskid, status=status, candidates=candidates, message=message, metadata={"command": command, "returncode": returncode, "log_path": str(log_path), "model": model, "model_provenance": _model_provenance(model)})


def run_mattergen_generation(constraints: GenerationConstraint, output_dir: Path, max_candidates: int = 8, runner: Optional[GenerationRunner] = None) -> GenerationManifest:
    """Generate real MatterGen structures and persist an auditable manifest."""
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least one")
    manifest = (runner or _default_mattergen_runner)(constraints, output_dir, max_candidates)
    write_generation_manifest(manifest, output_dir)
    return manifest
