#!/usr/bin/env python3
"""Aggregate MatterSim--MP screening shards without mixing chemical systems.

This is an offline reporting utility.  It reads only completed
``mattersim_results.json`` files, keeps the newest result for a source CIF if a
candidate was rerun, and writes separate HEA and chip-packaging statistics.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any


THRESHOLDS = (0.05, 0.10, 0.15)


def scenario_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return relative.parts[0] if relative.parts else "unclassified"


def load_records(screen_root: Path) -> tuple[list[dict[str, Any]], int]:
    """Return one newest record per input CIF and number of source result files."""
    latest: dict[str, dict[str, Any]] = {}
    results = sorted(screen_root.rglob("mattersim_results.json"))
    for result_path in results:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("status") != "ok":
            continue
        mtime = result_path.stat().st_mtime
        scenario = scenario_from_path(result_path, screen_root)
        for candidate in payload.get("candidates", []):
            source = str(candidate["source_cif"])
            record = dict(candidate)
            record.update({
                "scenario": scenario,
                "result_file": str(result_path.resolve()),
                "result_mtime": mtime,
            })
            previous = latest.get(source)
            if previous is None or mtime >= previous["result_mtime"]:
                latest[source] = record
    return list(latest.values()), len(results)


def describe(records: list[dict[str, Any]]) -> dict[str, Any]:
    hull = sorted(float(row["energy_above_hull_ev"]) for row in records)
    formation = [float(row["formation_energy_per_atom_ev"]) for row in records]
    return {
        "unique_candidates": len(records),
        "mean_energy_above_hull_ev": statistics.fmean(hull) if hull else None,
        "median_energy_above_hull_ev": statistics.median(hull) if hull else None,
        "minimum_energy_above_hull_ev": min(hull) if hull else None,
        "mean_formation_energy_per_atom_ev": statistics.fmean(formation) if formation else None,
        "counts_by_energy_above_hull_threshold": {
            f"<={threshold:.2f}_eV_per_atom": sum(value <= threshold for value in hull)
            for threshold in THRESHOLDS
        },
        "top_candidates": [
            {
                "source_name": row["source_name"],
                "formula": row["formula_pretty"],
                "energy_above_hull_ev": row["energy_above_hull_ev"],
                "formation_energy_per_atom_ev": row["formation_energy_per_atom_ev"],
                "relaxed_structure_path": row["relaxed_structure_path"],
            }
            for row in sorted(records, key=lambda row: float(row["energy_above_hull_ev"]))[:10]
        ],
    }


def write_outputs(records: list[dict[str, Any]], result_files: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["scenario"]].append(row)
    summary = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "result_files_discovered": result_files,
        "deduplication_policy": "newest completed MatterSim result per source_cif",
        "evidence_note": "MatterSim MLFF relaxation plus MP2020 reference-hull screening; not DFT.",
        "scenarios": {name: describe(rows) for name, rows in sorted(grouped.items())},
    }
    (output_dir / "screening_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fields = [
        "scenario", "source_name", "formula_pretty", "energy_above_hull_ev",
        "formation_energy_per_atom_ev", "is_stable_at_threshold",
        "reference_chemical_system", "relaxed_structure_path", "source_cif", "result_file",
    ]
    ranked = sorted(records, key=lambda row: (row["scenario"], float(row["energy_above_hull_ev"])))
    with (output_dir / "candidate_ranking.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in ranked)
    lines = ["# MatterSim--MP 初筛汇总", ""]
    for name, stats in sorted(summary["scenarios"].items()):
        lines.extend([
            f"## {name}", "",
            f"- 去重后候选数：{stats['unique_candidates']}",
            f"- 平均 E_hull：{stats['mean_energy_above_hull_ev']:.4f} eV/atom" if stats["mean_energy_above_hull_ev"] is not None else "- 平均 E_hull：无",
            f"- 中位 E_hull：{stats['median_energy_above_hull_ev']:.4f} eV/atom" if stats["median_energy_above_hull_ev"] is not None else "- 中位 E_hull：无",
            f"- 最低 E_hull：{stats['minimum_energy_above_hull_ev']:.4f} eV/atom" if stats["minimum_energy_above_hull_ev"] is not None else "- 最低 E_hull：无",
            f"- 阈值计数：{stats['counts_by_energy_above_hull_threshold']}",
            "",
        ])
    lines.append("MatterSim–MP 结果用于候选排序和 DFT 优先级判断，不是 DFT 最终结论。")
    (output_dir / "screening_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    records, result_files = load_records(args.screen_root)
    if not records:
        raise RuntimeError(f"No completed mattersim_results.json found under {args.screen_root}")
    write_outputs(records, result_files, args.output_dir)
    print(f"Aggregated {len(records)} unique candidates from {result_files} result files.")


if __name__ == "__main__":
    main()
