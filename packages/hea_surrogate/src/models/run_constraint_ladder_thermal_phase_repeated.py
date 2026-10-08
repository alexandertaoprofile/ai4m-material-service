"""Repeated DOI-group check for the exploratory thermal M0--M3 ladder.

This script deliberately keeps the Machaka compilation labelled as a discovery
pool.  Its only purpose is to test whether a positive single-split M3 result is
stable across independently sampled DOI-held-out splits before spending effort
on a primary-PDF calibration set.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

from src.models.run_constraint_ladder_performance import physical_descriptors
from src.models.run_constraint_ladder_thermal_phase import (
    INPUT, calphad_coarse, composition_json, ensemble_predict, metric,
    soft_constrained_m3,
)
from src.models.train_baselines import _features


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "constraint_ladder" / "thermal_phase_m0_m1_m2_m3_repeated_doi.json"
SPLIT_SEEDS = (101, 211, 307, 401, 503, 601, 709, 811, 907, 1009)


def split_70_15_15(groups: pd.Series, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    index = np.arange(len(groups))
    train, held = next(GroupShuffleSplit(n_splits=1, train_size=.70, random_state=seed).split(index, groups=groups))
    valid_inner, test_inner = next(GroupShuffleSplit(n_splits=1, train_size=.50, random_state=seed + 1).split(held, groups=groups.iloc[held]))
    return train, held[valid_inner], held[test_inner]


def summarize(rows: list[dict], name: str) -> dict:
    values = np.asarray([row[name]["macro_F1"] for row in rows], dtype=float)
    return {
        "mean_macro_F1": float(values.mean()),
        "std_macro_F1": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
        "median_macro_F1": float(np.median(values)),
        "wins_vs_M0": int(sum(row[name]["macro_F1"] > row["M0_data"]["macro_F1"] for row in rows)),
        "splits": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-seed", type=int, choices=SPLIT_SEEDS, help="run and persist one DOI split only")
    parser.add_argument("--combine-existing", action="store_true", help="combine completed single-seed reports")
    args = parser.parse_args()
    if args.combine_existing:
        files = sorted(REPORT.parent.glob("thermal_phase_m0_m1_m2_m3_doi_seed_*.json"))
        rows = [json.loads(path.read_text(encoding="utf-8"))["splits"][0] for path in files]
        report = {
            "method": "independent 70/15/15 DOI-group splits; M3 weight selected inside each validation partition",
            "data_status": "secondary-compilation discovery pool only; stability diagnostic, not a release calibration",
            "completed_split_seeds": [row["split_seed"] for row in rows],
            "summary": {name: summarize(rows, name) for name in ("M0_data", "M1_descriptor", "M2_direct_calphad", "M3_soft_thermo_constraint")},
            "splits": rows,
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return
    frame = pd.read_csv(INPUT)
    frame["test_temperature_C"] = frame["anneal_temperature_C"].astype(float)
    frame["processing_method"] = frame["Sythesis_Route"].fillna("UNKNOWN")
    frame["composition_at_pct"] = frame["composition_at_pct"].map(composition_json)
    base, _, _ = _features(frame)
    comp = [c for c in base if c.startswith("x_")]
    m0 = pd.concat([base[comp + ["test_temperature_C", "temperature_missing"]], pd.DataFrame({"log_anneal_time_min": np.log1p(pd.to_numeric(frame["Annealing_Time_(min)"], errors="coerce"))})], axis=1)
    m1 = pd.concat([m0, physical_descriptors(frame)], axis=1)
    y = frame.observed_phase_coarse.astype(str)
    rows: list[dict] = []
    seeds = (args.split_seed,) if args.split_seed is not None else SPLIT_SEEDS
    for seed in seeds:
        split = split_70_15_15(frame.References.astype(str), seed)
        test = split[2]
        rows.append({
            "split_seed": seed,
            "test_records": int(len(test)),
            "test_dois": int(frame.References.iloc[test].nunique()),
            "M0_data": ensemble_predict(m0, y, split),
            "M1_descriptor": ensemble_predict(m1, y, split),
            "M2_direct_calphad": metric(y.iloc[test], np.asarray([calphad_coarse(x) for x in frame.stable_phases.iloc[test]])),
            "M3_soft_thermo_constraint": soft_constrained_m3(m1, y, frame.stable_phases, split),
        })
    report = {
        "method": "ten independent 70/15/15 DOI-group splits; M3 CALPHAD prior weight selected inside each validation partition",
        "data_status": "secondary-compilation discovery pool only; report is a stability diagnostic, not a release calibration",
        "records": int(len(frame)), "dois": int(frame.References.nunique()),
        "summary": {name: summarize(rows, name) for name in ("M0_data", "M1_descriptor", "M2_direct_calphad", "M3_soft_thermo_constraint")},
        "splits": rows,
    }
    output = REPORT if args.split_seed is None else REPORT.with_name(f"thermal_phase_m0_m1_m2_m3_doi_seed_{args.split_seed}.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
