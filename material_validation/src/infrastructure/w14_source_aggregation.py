"""Read-only aggregation of the complementary W-14 training and MD sources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _last_lcurve_record(path: Path) -> dict[str, float] | None:
    if not path.is_file():
        return None
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if not rows:
        return None
    fields = ("step", "rmse_val", "rmse_trn", "rmse_e_val", "rmse_e_trn", "rmse_f_val", "rmse_f_trn", "rmse_v_val", "rmse_v_trn", "learning_rate")
    return {field: float(value) for field, value in zip(fields, rows[-1])}


def _dataset_summary(set_root: Path) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError:
        return {"status": "numpy_unavailable"}
    arrays = {}
    for path in sorted(set_root.glob("*.npy")):
        arrays[path.name] = list(np.load(path, mmap_mode="r").shape)
    frames = arrays.get("energy.npy", [0])[0]
    return {"status": "available", "frames": frames, "arrays": arrays}


def _mean_npt(path: Path) -> dict[str, float] | None:
    if not path.is_file():
        return None
    import numpy as np
    values = np.loadtxt(path, comments="#")
    values = values.reshape(1, -1) if values.ndim == 1 else values
    mean = values.mean(axis=0)
    # The recorded lx is a 6×6×6 supercell edge, not the bcc lattice constant.
    return {"temperature_K": float(mean[1]), "supercell_edge_angstrom": float(mean[3]), "lattice_parameter_angstrom": float(mean[3] / 6.0), "density_g_cm3": float(mean[7])}


def _fit_elastic(c11c12_path: Path, c44_path: Path) -> dict[str, float] | None:
    if not (c11c12_path.is_file() and c44_path.is_file()): return None
    import numpy as np
    axial, shear = np.loadtxt(c11c12_path), np.loadtxt(c44_path)
    mask = np.abs(axial[:, 0]) <= .003
    slope_11 = abs(float(np.linalg.lstsq(np.vstack([axial[mask, 0], np.ones(mask.sum())]).T, axial[mask, 1], rcond=None)[0][0]))
    slope_12 = abs(float(np.linalg.lstsq(np.vstack([axial[mask, 0], np.ones(mask.sum())]).T, axial[mask, 2], rcond=None)[0][0]))
    mask = np.abs(shear[:, 0]) <= .01
    slope_44 = abs(float(np.linalg.lstsq(np.vstack([shear[mask, 0], np.ones(mask.sum())]).T, shear[mask, 1], rcond=None)[0][0]))
    bulk = (slope_11 + 2 * slope_12) / 3
    gv = (slope_11 - slope_12 + 3 * slope_44) / 5
    gr = 5 * (slope_11 - slope_12) * slope_44 / (4 * slope_44 + 3 * (slope_11 - slope_12))
    shear_modulus = .5 * (gv + gr)
    young = 9 * bulk * shear_modulus / (3 * bulk + shear_modulus)
    poisson = (3 * bulk - 2 * shear_modulus) / (2 * (3 * bulk + shear_modulus))
    return {"C11_GPa": slope_11, "C12_GPa": slope_12, "C44_GPa": slope_44, "K_GPa": bulk, "G_GPa": shear_modulus, "E_GPa": young, "nu": poisson}


def _heat_capacity(path: Path, temperature: float) -> dict[str, float] | None:
    if not path.is_file(): return None
    import numpy as np
    data = np.loadtxt(path, comments="#"); energy = data[:, 3]
    variance = float(np.mean((energy - energy.mean()) ** 2))
    kb_ev, ev_to_j, avogadro, atoms = 8.617333262e-5, 1.602176634e-19, 6.02214076e23, 432
    cv_atom_ev = variance / (kb_ev * temperature**2) / atoms
    return {"temperature_K": temperature, "variance_eV2": variance, "Cv_per_atom_kB": cv_atom_ev / kb_ev, "Cv_J_mol_K": cv_atom_ev * ev_to_j * avogadro}


def aggregate_w14_sources(mlip_root: Path | None, md_root: Path | None) -> dict[str, Any]:
    """Return only facts directly read from provided source directories."""
    output: dict[str, Any] = {"case_id": "w14_phase_i", "sources": {}, "training": {}, "md": {}}
    if mlip_root and mlip_root.is_dir():
        input_path, model_path, curve_path = mlip_root / "input.json", mlip_root / "frozen_300000.pb", mlip_root / "lcurve.out"
        config = json.loads(input_path.read_text(encoding="utf-8")) if input_path.is_file() else None
        output["sources"]["mlip_training"] = {"path": str(mlip_root), "available": True, "artifacts": [{"path": item.name, "sha256": _sha256(item)} for item in (input_path, model_path, curve_path) if item.is_file()]}
        output["training"] = {"input_config": config, "dataset": _dataset_summary(mlip_root / "train" / "set.000"), "final_lcurve": _last_lcurve_record(curve_path), "force_target_note": "W-14 当前训练集的 force.npy 为静态小应变构型的零力标签；接近零的 force RMSE 不能外推为有限温度或缺陷构型的力精度。"}
    if md_root and md_root.is_dir():
        npt = {str(temp): _mean_npt(md_root / f"W6x6x6_NPT_{temp}K_ave.dat") for temp in (300, 600, 900)}
        elastic = {str(temp): _fit_elastic(md_root / f"W_{temp}K_C11C12.dat", md_root / f"W_{temp}K_C44.dat") for temp in (300, 600, 900)}
        for temperature, item in elastic.items():
            if item is not None:
                item["temperature_K"] = float(temperature)
        heat_capacity = {str(temp): _heat_capacity(md_root / f"W_{temp}K_Cv_timeseries.dat", float(temp)) for temp in (300, 600, 900)}
        sound_speed = {}
        for key, cij in elastic.items():
            state = npt.get(key)
            if cij and state:
                rho = state["density_g_cm3"] * 1000
                sound_speed[key] = {"temperature_K": state["temperature_K"], "vs_m_s": (cij["G_GPa"] * 1e9 / rho) ** .5, "vp_m_s": ((cij["K_GPa"] + 4 * cij["G_GPa"] / 3) * 1e9 / rho) ** .5}
        available = [path for path in md_root.glob("*") if path.is_file()]
        model_references = sorted({line.strip() for path in md_root.glob("*.lmp") for line in path.read_text(encoding="utf-8").splitlines() if "pair_style" in line and "deepmd" in line})
        output["sources"]["md_validation"] = {"path": str(md_root), "available": True, "artifact_count": len(available), "artifacts": [{"path": item.name, "sha256": _sha256(item)} for item in available if item.name.startswith(("in.", "W6x6x6_NPT_", "W_"))]}
        output["md"] = {"npt_equilibrium": npt, "elastic": elastic, "sound_speed": sound_speed, "heat_capacity": heat_capacity, "model_references": model_references, "model_link_verified": False, "scope": "6×6×6 bcc W；300、600、900 K。弹性、声速、Cv 与热膨胀的原始后处理文件已随源目录保留，待服务图表执行器读取。"}
    return output
