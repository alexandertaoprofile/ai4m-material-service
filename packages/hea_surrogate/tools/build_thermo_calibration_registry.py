#!/usr/bin/env python3
"""Create one auditable, tiered registry for HEA thermodynamic calibration.

The registry is deliberately conservative: a coarse literature-compilation
label is never upgraded to phase-fraction or Gibbs-energy evidence.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACHKA = ROOT / "data" / "processed" / "calphad_phase_calibration_candidates" / "machaka_2021_tdb_domain_700_900c.csv"
MANUAL = ROOT / "reports" / "calphad" / "hea_700_900c_phase_candidates_20260826.csv"
PRIMARY = ROOT / "data" / "processed" / "thermo_calibration" / "primary_source_calibration_candidates_v1.csv"
OUTPUT = ROOT / "data" / "processed" / "thermo_calibration" / "hea_thermo_calibration_registry_v1.csv"
MANIFEST = ROOT / "data" / "processed" / "thermo_calibration" / "hea_thermo_calibration_registry_v1_manifest.json"


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def machaka_conflicts(rows: list[dict[str, str]]) -> set[str]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["composition_at_pct"], row["annealing_temperature_c"], row["Annealing_Time_(min)"])].append(row)
    return {row["source_record_id"] for group in grouped.values() if len({item["observed_phase_coarse"] for item in group}) > 1 for row in group}


def main() -> None:
    machaka = read(MACHKA); manual = read(MANUAL); primary = read(PRIMARY); conflicts = machaka_conflicts(machaka)
    output: list[dict[str, str]] = []
    for row in manual:
        suitability = row["equilibrium_suitability"]
        output.append({
            "registry_id": f"manual:{row['record_id']}", "source_kind": "primary_or_directly_audited_literature",
            "composition_at_pct": row["composition_at_pct"], "temperature_C": row["anneal_temperature_C"], "hold_time_h": row["anneal_time_h"],
            "cooling": row["cooling"], "observed_phase": row["observed_phases"], "phase_fractions": row["phase_fractions"],
            "source_locator": row["source_url"], "provenance_status": "directly_audited",
            "calibration_tier": "B_phase_identity_anchor" if suitability in {"high", "medium"} else "C_kinetic_or_incomplete_context",
            "blocker": "phase fractions unavailable" if not row["phase_fractions"].strip() else "",
        })
    for row in primary:
        output.append({
            "registry_id": row["registry_id"], "source_kind": row["source_kind"],
            "composition_at_pct": row["composition_at_pct"], "temperature_C": row["temperature_C"], "hold_time_h": row["hold_time_h"],
            "cooling": row["cooling"], "observed_phase": row["observed_phase"], "phase_fractions": row["phase_fractions"],
            "source_locator": f"doi:{row['source_doi']}; {row['source_locator']}", "provenance_status": "verified_primary_pdf",
            "calibration_tier": row["calibration_tier"], "blocker": row["blocker"],
        })
    for row in machaka:
        conflict = row["source_record_id"] in conflicts
        # A source-semantic audit found entries pointing to e.g. an atomistic
        # Cantor study and high-pressure-torsion work.  Until every compiled
        # row is recovered from the cited primary table/figure, even a row with
        # apparent process fields remains a discovery lead, not calibration.
        tier = "Q_conflicting_coarse_label" if conflict else "D_compilation_discovery_only"
        blocker = "same composition-temperature-duration group has contradictory coarse labels" if conflict else "secondary coarse-label compilation; primary table/figure, measurement method and state mapping not yet verified"
        output.append({
            "registry_id": f"machaka:{row['source_record_id'].replace(' ', '_')}", "source_kind": "secondary_experimental_compilation",
            "composition_at_pct": row["composition_at_pct"], "temperature_C": row["annealing_temperature_c"], "hold_time_h": str(float(row["Annealing_Time_(min)"]) / 60),
            "cooling": row["Quenching"], "observed_phase": row["observed_phase_coarse"], "phase_fractions": "",
            "source_locator": f"doi:{row['References']}", "provenance_status": "compilation_row",
            "calibration_tier": tier, "blocker": blocker,
        })
    fields = list(output[0])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(output)
    manifest = {"records": len(output), "manual_records": len(manual), "verified_primary_pdf_records": len(primary), "machaka_records": len(machaka), "machaka_conflicting_records": len(conflicts), "tier_counts": dict(Counter(row["calibration_tier"] for row in output)), "decision": "Only B_phase_identity_anchor records may be used to anchor phase-name review. All secondary-compilation rows are discovery/review leads until primary-source state mapping is recovered. None are phase-fraction/Gibbs-loss ready."}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
