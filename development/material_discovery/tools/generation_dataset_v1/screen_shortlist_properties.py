#!/usr/bin/env python3
"""Predict a small, evidence-labelled property panel for four screened candidates.

Input structures are the MatterSim-relaxed ``.extxyz`` artifacts from completed
MatterSim--MP screening.  The script exports one relaxed CIF per candidate and
uses ALIGNN pretrained models for direct structure-to-property predictions.
It does not run DFT and does not turn model predictions into fabrication claims.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any


SHORTLIST = (
    {
        "track": "hea",
        "source_name": "aerospace_alloy-000111.cif",
        "role": "低 E_hull HEA 候选 A",
        "properties": ("bulk_modulus", "shear_modulus"),
    },
    {
        "track": "hea",
        "source_name": "aerospace_alloy-000415.cif",
        "role": "低 E_hull HEA 候选 B（组成差异化）",
        "properties": ("bulk_modulus", "shear_modulus"),
    },
    {
        "track": "chip",
        "source_name": "chip_packaging-000432.cif",
        "role": "AlN 封装陶瓷正对照",
        "properties": ("band_gap", "dielectric_constant_x"),
    },
    {
        "track": "chip",
        "source_name": "chip_packaging-000165.cif",
        "role": "Si-Al-O-N 探索候选",
        "properties": ("band_gap", "dielectric_constant_x"),
    },
)

PROPERTY_MODELS = {
    "bulk_modulus": ("体积模量", "GPa", "jv_bulk_modulus_kv_alignn"),
    "shear_modulus": ("剪切模量", "GPa", "jv_shear_modulus_gv_alignn"),
    "band_gap": ("带隙", "eV", "jv_mbj_bandgap_alignn"),
    "dielectric_constant_x": ("介电常数 εx", "无量纲", "jv_epsx_alignn"),
}
PREDICTED_VALUE = re.compile(r"Predicted value:.*?\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def completed_candidates(screen_root: Path) -> dict[str, dict[str, Any]]:
    """Keep the newest completed MatterSim result per source file name."""
    found: dict[str, dict[str, Any]] = {}
    for result_path in screen_root.rglob("mattersim_results.json"):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("status") != "ok":
            continue
        mtime = result_path.stat().st_mtime
        for row in payload.get("candidates", []):
            record = dict(row)
            record["result_file"] = str(result_path.resolve())
            record["result_mtime"] = mtime
            previous = found.get(record["source_name"])
            if previous is None or mtime >= previous["result_mtime"]:
                found[record["source_name"]] = record
    return found


def bundled_candidates(manifest_path: Path) -> dict[str, dict[str, Any]]:
    """Read a portable four-candidate bundle created on the screening server."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("candidates", []):
        record = dict(row)
        relative = Path(record.pop("relaxed_file"))
        record["relaxed_structure_path"] = str((manifest_path.parent / relative).resolve())
        result[record["source_name"]] = record
    return result


def export_relaxed_cif(extxyz: Path, target: Path) -> None:
    from ase.io import read
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.io.cif import CifWriter

    structure = AseAtomsAdaptor.get_structure(read(extxyz))
    CifWriter(structure).write_file(target)


def run_alignn(
    *, micromamba: Path | None, alignn_env: str, alignn_python: Path | None,
    model: str, cif_path: Path, timeout: int,
) -> float:
    command = (
        [str(alignn_python), "-m", "alignn.pretrained"]
        if alignn_python is not None else
        [str(micromamba), "run", "-n", alignn_env, "python", "-m", "alignn.pretrained"]
    ) + [
        "--model_name", model,
        "--file_format", "cif",
        "--file_path", str(cif_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if completed.returncode != 0:
        raise RuntimeError(text[-1000:] or f"ALIGNN exit code {completed.returncode}")
    match = PREDICTED_VALUE.search(text)
    if match is None:
        raise RuntimeError(f"ALIGNN predicted value not found: {text[-1000:]}")
    return float(match.group(1))


def screen(args: argparse.Namespace) -> list[dict[str, Any]]:
    available = bundled_candidates(args.bundle_manifest) if args.bundle_manifest else completed_candidates(args.screen_root)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    cif_dir = args.output_dir / "relaxed_cif"; cif_dir.mkdir()
    records: list[dict[str, Any]] = []
    for spec in SHORTLIST:
        candidate = available.get(spec["source_name"])
        if candidate is None:
            raise FileNotFoundError(f"Completed MatterSim result not found: {spec['source_name']}")
        relaxed_path = Path(candidate["relaxed_structure_path"])
        if not relaxed_path.is_file():
            raise FileNotFoundError(f"Relaxed structure missing: {relaxed_path}")
        cif_path = cif_dir / f"{Path(spec['source_name']).stem}.relaxed.cif"
        export_relaxed_cif(relaxed_path, cif_path)
        predictions: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for property_name in spec["properties"]:
            label, unit, model = PROPERTY_MODELS[property_name]
            try:
                value = run_alignn(
                    micromamba=args.micromamba, alignn_env=args.alignn_env,
                    alignn_python=args.alignn_python, model=model, cif_path=cif_path,
                    timeout=args.timeout_sec,
                )
                predictions[property_name] = {
                    "label": label, "value": value, "unit": unit, "model": model,
                    "evidence_level": "C：结构模型快速预测",
                    "structure_condition": "MatterSim 弛豫后结构",
                }
            except Exception as exc:
                errors[property_name] = str(exc)[-1000:]
        records.append({
            "track": spec["track"], "role": spec["role"], "source_name": spec["source_name"],
            "formula": candidate["formula_pretty"],
            "source_cif": candidate["source_cif"],
            "relaxed_cif": str(cif_path.resolve()),
            "energy_above_hull_ev": candidate["energy_above_hull_ev"],
            "formation_energy_per_atom_ev": candidate["formation_energy_per_atom_ev"],
            "thermodynamic_method": candidate["method"],
            "predictions": predictions, "prediction_errors": errors,
        })
    return records


def write_outputs(records: list[dict[str, Any]], output_dir: Path) -> None:
    payload = {
        "generated_at": now(),
        "selection_policy": "two lowest-E_hull HEA candidates plus one AlN reference and one Si-Al-O-N exploratory ceramic",
        "thermodynamic_evidence": "MatterSim MLFF relaxation plus MP2020 reference-hull screening; not DFT.",
        "property_evidence": "ALIGNN direct structure-model predictions on MatterSim-relaxed structures; for initial comparison only.",
        "candidates": records,
    }
    (output_dir / "shortlist_properties.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for item in records:
        row = {
            "track": item["track"], "role": item["role"], "source_name": item["source_name"],
            "formula": item["formula"], "energy_above_hull_ev": item["energy_above_hull_ev"],
            "formation_energy_per_atom_ev": item["formation_energy_per_atom_ev"], "relaxed_cif": item["relaxed_cif"],
        }
        for name, value in item["predictions"].items():
            row[name] = value["value"]
            row[f"{name}_unit"] = value["unit"]
        row["prediction_errors"] = json.dumps(item["prediction_errors"], ensure_ascii=False)
        rows.append(row)
    fields = sorted({field for row in rows for field in row})
    with (output_dir / "shortlist_properties.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    lines = ["# 前景候选快速性质筛选", ""]
    for item in records:
        lines.extend([
            f"## {item['role']}：{item['formula']}", "",
            f"- MatterSim–MP E_hull：{float(item['energy_above_hull_ev']):.4f} eV/atom",
            f"- 形成能：{float(item['formation_energy_per_atom_ev']):.4f} eV/atom",
        ])
        for prediction in item["predictions"].values():
            lines.append(f"- {prediction['label']}：{prediction['value']} {prediction['unit']}（{prediction['model']}）")
        for name, error in item["prediction_errors"].items():
            lines.append(f"- {name}：快速预测未完成，{error[-180:]}")
        lines.append("")
    lines.append("MatterSim–MP 与 ALIGNN 结果用于 DFT 优先级判断，不构成 DFT 或实验结论。")
    (output_dir / "shortlist_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-root", type=Path)
    parser.add_argument("--bundle-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--micromamba", type=Path)
    parser.add_argument("--alignn-env", default="alignn-gpu-test")
    parser.add_argument("--alignn-python", type=Path)
    parser.add_argument("--timeout-sec", type=int, default=600)
    args = parser.parse_args()
    if bool(args.screen_root) == bool(args.bundle_manifest):
        parser.error("provide exactly one of --screen-root or --bundle-manifest")
    if bool(args.micromamba) == bool(args.alignn_python):
        parser.error("provide exactly one of --micromamba or --alignn-python")
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to reuse output directory: {args.output_dir}")
    records = screen(args)
    write_outputs(records, args.output_dir)
    print(f"Completed fast property screen for {len(records)} shortlisted candidates.")


if __name__ == "__main__":
    main()
