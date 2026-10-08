"""Build traceable, non-leaky data tables from the provided HEA sources."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.composition.parser import CompositionParseError, parse_composition

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW = ROOT.parent / "backend" / "property datasets"
SCHEMA_COLUMNS = [
    "record_id", "canonical_formula", "composition_at_pct", "element_fractions",
    "processing_method", "heat_treatment", "grain_size_um", "test_temperature_C",
    "phase_raw", "phase_standardized", "yield_strength_MPa", "hardness_HV",
    "density_g_cm3", "source_doi", "source_dataset", "data_type",
    "data_quality_flag", "raw_record_json",
]


def _read_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8", "utf-8-sig", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"cannot decode {path}")


def _num(value: Any) -> float | None:
    if pd.isna(value): return None
    # Preserve only the central value from values such as "0.71 ± 0.35".
    import re
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
    return float(m.group()) if m else None


def _phase(value: Any) -> str | None:
    if pd.isna(value): return None
    text = str(value).upper().replace(" ", "")
    if not text: return None
    if "AMORPH" in text or text == "AM": return "AM"
    has_ss = any(x in text for x in ("FCC", "BCC", "HCP", "SS", "SOLIDSOLUTION"))
    has_im = any(x in text for x in ("B2", "LAVES", "SIGMA", "IMC", "INTERMETALLIC", "MU", "CHI"))
    if has_ss and has_im: return "SS+IM"
    if has_ss: return "SS"
    if has_im: return "IM"
    return None


def _first(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row and pd.notna(row[name]): return row[name]
    return None


def _raw_columns(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose original fields without allowing them to overwrite canonical ones."""
    import re
    return {"raw_" + re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_"): value for key, value in raw.items()}


def _canonical_row(row: pd.Series, dataset: str) -> dict[str, Any]:
    raw = {str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}
    formula = _first(row, "FORMULA", "Alloy")
    parsed = parse_composition(formula)
    source_doi = _first(row, "REFERENCE: doi", "REFERENCE: doi ", "Original DOI")
    process = _first(row, "PROPERTY: Processing method", "PROPERTY: synthesis method")
    phase = _first(row, "PROPERTY: Microstructure", "PROPERTY: Type of phases", "Phases")
    density = _first(row, "PROPERTY: Exp. Density (g/cm$^3$)", "PROPERTY: ROM Density (g/cm$^3$)")
    result = {
        "canonical_formula": parsed.canonical_formula,
        "composition_at_pct": json.dumps(parsed.atomic_percent, sort_keys=True),
        "element_fractions": json.dumps({k: v / 100 for k, v in parsed.atomic_percent.items()}, sort_keys=True),
        "processing_method": None if process is None else str(process),
        "heat_treatment": None,
        "grain_size_um": _num(_first(row, "PROPERTY: grain size ($\\mu$m)")),
        "test_temperature_C": _num(_first(row, "PROPERTY: Test temperature ($^\\circ$C)")),
        "phase_raw": None if phase is None else str(phase), "phase_standardized": _phase(phase),
        "yield_strength_MPa": _num(_first(row, "PROPERTY: YS (MPa)", "YS (MPa)", "Strength")),
        "hardness_HV": _num(_first(row, "PROPERTY: HV", "Hardness (HV)", "Hardness")),
        "density_g_cm3": _num(density), "source_doi": None if source_doi is None else str(source_doi).lower(),
        "source_dataset": dataset, "data_type": "experimental", "data_quality_flag": "parsed",
        "raw_record_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }
    result.update(_raw_columns(raw))
    result["record_id"] = hashlib.sha256((dataset + result["raw_record_json"]).encode()).hexdigest()[:16]
    return result


def _feature_rows(path: Path, dataset: str, canonical_target: str, source_target: str) -> tuple[list[dict], list[dict]]:
    valid, errors = [], []
    for index, row in _read_csv(path).iterrows():
        try:
            item = _canonical_row(row, dataset)
            item[canonical_target] = _num(row[source_target])
            # These tables are derived feature exports without process/DOI. Keep
            # them traceable but lower quality rather than fabricating metadata.
            item["data_quality_flag"] = "parsed_limited_provenance"
            valid.append(item)
        except (CompositionParseError, ValueError) as exc:
            errors.append({"source_dataset": dataset, "source_row": int(index), "formula": str(row.get("Alloy")), "error": str(exc)})
    return valid, errors


def _disma_rows(raw_root: Path) -> tuple[list[dict], list[dict]]:
    root = raw_root / "DISMA_Research Dataset (High-entropy alloys)"
    specs = [("Training_data_Strength.csv", "yield_strength_MPa"), ("Training_data_independent_Predictor_hardness-190521.csv", "hardness_HV")]
    valid, errors = [], []
    elements = ["Co", "Cr", "Fe", "Ni", "Mn", "Al", "Cu", "Mo"]
    for name, target in specs:
        for index, row in _read_csv(root / name).iterrows():
            values = {e: float(row[e]) for e in elements if float(row[e]) > 0}
            try:
                parsed = parse_composition("".join(f"{e}{v:g}" for e, v in values.items()))
                raw = {str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}
                record = {k: None for k in SCHEMA_COLUMNS}
                record.update({"canonical_formula": parsed.canonical_formula, "composition_at_pct": json.dumps(parsed.atomic_percent, sort_keys=True), "element_fractions": json.dumps({k:v/100 for k,v in parsed.atomic_percent.items()},sort_keys=True), "source_dataset": f"DISMA/{name}", "data_type":"experimental", "data_quality_flag":"parsed_limited_provenance", "raw_record_json":json.dumps(raw,sort_keys=True), target:_num(row["Strength" if target == "yield_strength_MPa" else "Hardness"])})
                record["record_id"] = hashlib.sha256((record["source_dataset"] + record["raw_record_json"]).encode()).hexdigest()[:16]
                record.update(_raw_columns(raw))
                valid.append(record)
            except Exception as exc:
                errors.append({"source_dataset": f"DISMA/{name}", "source_row":int(index), "formula":str(values), "error":str(exc)})
    return valid, errors


def _deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Target values are part of the key: distinct measurements are retained.
    keys = ["canonical_formula", "processing_method", "test_temperature_C", "yield_strength_MPa", "hardness_HV", "source_doi"]
    dupes = df[df.duplicated(keys, keep="first")].copy()
    return df.drop_duplicates(keys, keep="first").copy(), dupes


def _nbcrvwzr(path: Path) -> pd.DataFrame:
    df = _read_csv(path).copy()
    fractions = df[["Nb", "Cr", "V", "W", "Zr"]].astype(float)
    if not np.allclose(fractions.sum(axis=1), 1.0):
        raise ValueError("NbCrVWZr fractions do not sum to one")
    df.insert(0, "data_type", "calculated")
    df.insert(1, "source_dataset", "NbCrVWZr_data.csv")
    df.insert(2, "data_quality_flag", "model_derived_calculated")
    return df


def run(raw_root: Path, output_root: Path | None = None) -> dict[str, int]:
    output_root = output_root or ROOT
    processed, reports = output_root / "data" / "processed", output_root / "reports"
    processed.mkdir(parents=True, exist_ok=True); reports.mkdir(parents=True, exist_ok=True)
    source_files = [
        "MPEA_dataset.csv", "2018_data.csv", "2019_data.csv",
        "Alloy_Features_all_data_yield_strength.csv", "Alloy_features_all_data_hardness.csv",
        "2021-08-03-HEA_dataset.xlsx", "NbCrVWZr_data.csv", "references.csv",
        "reference_data.json",
        "DISMA_Research Dataset (High-entropy alloys)/Training_data_Strength.csv",
        "DISMA_Research Dataset (High-entropy alloys)/Training_data_independent_Predictor_hardness-190521.csv",
        "DISMA_Research Dataset (High-entropy alloys)/Training_data_independent_Predictor_phase_n=76.csv",
        "DISMA_Research Dataset (High-entropy alloys)/Training_data_independent_Predictor_phase_n=94.csv",
    ]
    audit = []
    for name in source_files:
        p = raw_root / name
        if name == "MPEA_dataset.csv": role = "primary_experimental_integrated_gorsse_2018_2019"
        elif name in {"2018_data.csv", "2019_data.csv"}: role = "audit_only_integrated_lineage"
        elif name == "NbCrVWZr_data.csv": role = "separate_calculated_only"
        elif name.endswith(".xlsx"): role = "discovered_deferred_requires_explicit_mapping"
        elif name in {"references.csv", "reference_data.json"}: role = "provenance_reference_only"
        else: role = "separate_experimental_candidate"
        audit.append({"file":name, "exists":p.exists(), "bytes":p.stat().st_size if p.exists() else None, "role": role})
    errors: list[dict] = []
    records = []
    # MPEA is authoritative for the 2018/2019/Gorsse lineage.
    for index, row in _read_csv(raw_root / "MPEA_dataset.csv").iterrows():
        try: records.append(_canonical_row(row, "MPEA_dataset.csv"))
        except Exception as exc: errors.append({"source_dataset":"MPEA_dataset.csv", "source_row":int(index), "formula":str(row.get("FORMULA")), "error":str(exc)})
    for filename, dataset, target, source_target in [("Alloy_Features_all_data_yield_strength.csv", "yield_feature_export", "yield_strength_MPa", "YS (MPa)"), ("Alloy_features_all_data_hardness.csv", "hardness_feature_export", "hardness_HV", "Hardness (HV)")]:
        rows, failures = _feature_rows(raw_root / filename, dataset, target, source_target); records += rows; errors += failures
    rows, failures = _disma_rows(raw_root); records += rows; errors += failures
    all_records = pd.DataFrame(records)
    # Required schema first; source-preserving raw_* fields follow unchanged.
    all_records = all_records[SCHEMA_COLUMNS + [c for c in all_records.columns if c not in SCHEMA_COLUMNS]]
    clean, duplicates = _deduplicate(all_records)
    # Cross-audit only: report matching normalized composition in excluded files.
    audit_matches = []
    mpea_formulas = set(clean.loc[clean.source_dataset.eq("MPEA_dataset.csv"), "canonical_formula"])
    for filename in ("2018_data.csv", "2019_data.csv"):
        for index, row in _read_csv(raw_root / filename).iterrows():
            try:
                canonical = parse_composition(row["FORMULA"]).canonical_formula
                if canonical in mpea_formulas: audit_matches.append({"audit_source":filename,"source_row":int(index),"canonical_formula":canonical,"status":"composition_present_in_MPEA"})
            except Exception as exc: errors.append({"source_dataset":filename,"source_row":int(index),"formula":str(row.get("FORMULA")),"error":str(exc)})
    clean.to_csv(processed / "experimental_canonical.csv", index=False)
    clean[clean.yield_strength_MPa.notna()].to_csv(processed / "experimental_yield_strength.csv", index=False)
    clean[clean.hardness_HV.notna()].to_csv(processed / "experimental_hardness.csv", index=False)
    clean[clean.phase_standardized.notna()].to_csv(processed / "experimental_phase.csv", index=False)
    _nbcrvwzr(raw_root / "NbCrVWZr_data.csv").to_csv(processed / "nbcrvwzr_calculated.csv", index=False)
    pd.DataFrame(audit).to_csv(reports / "data_audit.csv", index=False)
    pd.DataFrame(errors).to_csv(reports / "composition_parse_errors.csv", index=False)
    duplicates.to_csv(reports / "duplicate_records_removed.csv", index=False)
    pd.DataFrame(audit_matches).to_csv(reports / "integrated_lineage_overlap_audit.csv", index=False)
    summary = {"experimental_records":len(clean), "duplicates_removed":len(duplicates), "parse_errors":len(errors), "nbcrvwzr_records":10626, "integrated_lineage_overlap_rows":len(audit_matches)}
    (reports / "cleaning_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.output_root), indent=2))
