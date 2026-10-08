#!/usr/bin/env python3
"""Run selected ALIGNN structure-property models on one relaxed extxyz file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


PROPERTY_MODELS = {
    "band_gap": ("带隙", "eV", ("jv_mbj_bandgap_alignn", "jv_optb88vdw_bandgap_alignn", "mp_gappbe_alignn")),
    "dielectric_constant_x": ("介电常数 εx", "无量纲", ("jv_epsx_alignn",)),
}
PREDICTED_VALUE = re.compile(r"Predicted value:.*?\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relaxed-structure", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--micromamba", type=Path, default=Path("/home/ubuntu/micromamba"))
    parser.add_argument("--alignn-env", default="alignn-gpu-test")
    parser.add_argument("--timeout-sec", default=600, type=int)
    return parser.parse_args()


def export_cif(extxyz: Path, target: Path) -> str:
    from ase.io import read
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.io.cif import CifWriter

    structure = AseAtomsAdaptor.get_structure(read(extxyz))
    CifWriter(structure).write_file(target)
    return structure.composition.reduced_formula


def predict(args: argparse.Namespace, model: str, cif: Path) -> float:
    command = [
        str(args.micromamba), "run", "-n", args.alignn_env,
        "python", "-m", "alignn.pretrained", "--model_name", model,
        "--file_format", "cif", "--file_path", str(cif),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=args.timeout_sec, check=False)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if completed.returncode:
        raise RuntimeError(output[-1500:])
    match = PREDICTED_VALUE.search(output)
    if match is None:
        raise RuntimeError(f"ALIGNN prediction was not found in output: {output[-1500:]}")
    return float(match.group(1))


def main() -> None:
    args = arguments()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to reuse output directory: {args.output_dir}")
    if not args.relaxed_structure.is_file():
        raise FileNotFoundError(args.relaxed_structure)
    args.output_dir.mkdir(parents=True)
    cif = args.output_dir / "relaxed_structure.cif"
    formula = export_cif(args.relaxed_structure, cif)
    predictions, errors = {}, {}
    for name, (label, unit, models) in PROPERTY_MODELS.items():
        for model in models:
            try:
                predictions[name] = {
                    "label": label, "unit": unit, "value": predict(args, model, cif),
                    "model": model, "model_version": "ALIGNN pretrained", "evidence_level": "C：结构模型快速预测",
                    "structure_condition": "MatterSim 松弛后结构",
                }
                break
            except Exception as exc:
                errors[f"{name}/{model}"] = str(exc)[-1500:]
    payload = {
        "formula": formula, "source_structure": str(args.relaxed_structure.resolve()),
        "relaxed_cif": str(cif.resolve()), "predictions": predictions, "errors": errors,
        "boundary": "Direct pretrained structure-model estimates for ranking only; not DFT, device performance, ionic-migration activation energy, or experimental evidence.",
    }
    (args.output_dir / "alignn_properties.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not predictions:
        raise SystemExit("No ALIGNN property completed")


if __name__ == "__main__":
    main()
