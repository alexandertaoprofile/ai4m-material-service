#!/usr/bin/env python3
"""Build an auditable, composition-paired thermo-mechanical property table.

Only source fields whose units and measurement definitions are explicit in the
corresponding patent are emitted.  In particular, the unlabelled Young's
modulus column in US20130296157 is intentionally excluded.
"""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from bs4 import BeautifulSoup

CORE = ("SiO2", "Al2O3", "B2O3", "Na2O", "K2O", "Li2O", "MgO", "CaO", "BaO", "ZnO", "TiO2", "ZrO2", "P2O5")

def cells(row):
    return [c.get_text("", strip=True).replace("−", "-") for c in row.select(":scope > th, :scope > td")]

def missing(value):
    return value in {"", "—", "-", "N/A"}

def composition_rows(evidence):
    with evidence.open(encoding="utf-8", newline="") as handle:
        return {r["glass_id"]: r for r in csv.DictReader(handle)}

def emit(out, comp, source_id, locator, prop, value, unit, method, scope):
    if missing(value):
        return
    row = {key: comp[key] for key in ["glass_id", "glass_display_name", "glass_family", "composition_basis", *CORE, "other_oxides_text", "composition_total", "composition_source_batch_or_lot", "process_state"]}
    row.update({"source_id": source_id, "source_locator": locator, "property_name": prop, "property_value": value, "property_unit": unit, "test_method": method, "state_scope": scope, "evidence_level": "B_published_patent_example", "data_license": "public patent publication"})
    out.append(row)

def us2025(raw, evidence, matrix, out):
    comps = composition_rows(evidence)
    with matrix.open(encoding="utf-8", newline="") as handle:
        for r in csv.DictReader(handle):
            c = comps[r["glass_id"]]
            loc = r["source_locator"]
            emit(out, c, r["source_id"], loc, "density_g_per_cm3", r["density_g_per_cm3"], "g/cm3", "Archimedes, ASTM C693 (publication definition)", "source reports measured glass; measurement temperature not stated")
            emit(out, c, r["source_id"], loc, "young_modulus_GPa", r["young_modulus_GPa"], "GPa", "resonant ultrasonic spectroscopy, ASTM E1875-00e1 (publication definition)", "source reports measured glass; test temperature not stated")
            emit(out, c, r["source_id"], loc, "shear_modulus_GPa", r["shear_modulus_GPa"], "GPa", "resonant ultrasonic spectroscopy, ASTM E1875-00e1 (publication definition)", "source reports measured glass; test temperature not stated")
            emit(out, c, r["source_id"], loc, "poisson_ratio", r["poisson_ratio"], "1", "resonant ultrasonic spectroscopy, ASTM E1875-00e1 (publication definition)", "source reports measured glass; test temperature not stated")
            emit(out, c, r["source_id"], loc, "stress_optical_coefficient_nm_cm_per_MPa", r["stress_optical_coefficient_nm_cm_per_MPa"], "nm/cm/MPa", "glass-disc method, ASTM C770-16 (publication definition)", "source reports measured glass; test temperature not stated")
            emit(out, c, r["source_id"], loc, "strain_point_fibre_C", r["strain_point_fibre_C"], "C", "fibre elongation, ASTM C336 (publication definition)", "strain-point temperature")
            emit(out, c, r["source_id"], loc, "annealing_point_fibre_C", r["annealing_point_fibre_C"], "C", "fibre elongation, ASTM C338 (publication definition)", "annealing-point temperature")
            emit(out, c, r["source_id"], loc, "softening_point_fibre_C", r["softening_point_fibre_C"], "C", "fibre elongation, source table definition", "softening-point temperature")
            emit(out, c, r["source_id"], loc, "strain_point_BBV_C", r["strain_point_BBV_C"], "C", "BBV as labelled in publication", "strain-point temperature; BBV method")
            emit(out, c, r["source_id"], loc, "annealing_point_BBV_C", r["annealing_point_BBV_C"], "C", "BBV as labelled in publication", "annealing-point temperature; BBV method")
            emit(out, c, r["source_id"], loc, "viscosity_temperature_200_poise_C", r["viscosity_temperature_200_poise_C"], "C", "source Table 2–12 viscosity-fit output", "temperature at 200 poise")
            emit(out, c, r["source_id"], loc, "viscosity_temperature_35kpoise_C", r["viscosity_temperature_35kpoise_C"], "C", "source Table 2–12 viscosity-fit output", "temperature at 35 kpoise")
            emit(out, c, r["source_id"], loc, "liquidus_viscosity_kpoise", r["liquidus_viscosity_kpoise"], "kpoise", "source Table 2–12 liquidus-viscosity output", "source-reported liquidus condition")

def us2011(raw, evidence, out):
    comps = composition_rows(evidence)
    soup = BeautifulSoup(raw.read_text(encoding="utf-8"), "html.parser")
    map_props = {"Density": ("density_g_per_cm3", "g/cm3", "Archimedes, ASTM C693 (publication definition)", "source reports measured glass; measurement temperature not stated"), "Strain Point": ("strain_point_fibre_C", "C", "fibre elongation, ASTM C336 (publication definition)", "strain-point temperature"), "Young's Modulus": ("young_modulus_Mpsi", "Mpsi", "resonant ultrasonic spectroscopy, ASTM E1875-00e1 (publication definition)", "source reports measured glass; test temperature not stated")}
    for index in range(2, 7):
        rows = [cells(r) for r in soup.select("table")[index].select("tr")]
        for start, header in enumerate(rows):
            if not header or header[0] != "Composition (mol %)" or not all(x.isdigit() for x in header[1:]):
                continue
            samples = header[1:]; end = next((i for i in range(start + 1, len(rows)) if rows[i] and rows[i][0] == "Composition (mol %)"), len(rows))
            fields = {r[0]: r[1:] for r in rows[start:end] if len(r) == len(samples) + 1}
            for pos, sample in enumerate(samples):
                c = comps.get(f"US20110082022_sample_{sample}")
                if not c:
                    continue
                loc = f"US20110082022A1 PDF; Google Patents HTML Table 1 segment {index - 1}; example {sample}"
                for label, (prop, unit, method, scope) in map_props.items():
                    if label in fields:
                        emit(out, c, "SRC-PATENT-US20110082022", loc, prop, fields[label][pos], unit, method, scope)

def us2013(raw, evidence, out):
    comps = composition_rows(evidence)
    soup = BeautifulSoup(raw.read_text(encoding="utf-8"), "html.parser")
    props = {"Density": ("density_g_per_cm3", "g/cm3", "Archimedes, ASTM C693 (publication definition)", "source reports measured glass; measurement temperature not stated"), "Strain": ("strain_point_fibre_C", "C", "fibre elongation, ASTM C336 (publication definition)", "strain-point temperature"), "Anneal": ("annealing_point_fibre_C", "C", "fibre elongation, ASTM C338 (publication definition)", "annealing-point temperature")}
    for header_index in range(2, 42, 2):
        header = [cells(r) for r in soup.select("table")[header_index].select("tr")]
        examples = next(r[1:] for r in header if r and r[0] == "Example")
        fields = [cells(r) for r in soup.select("table")[header_index + 1].select("tr")]
        values = {r[1]: r[2:] for r in fields if len(r) == len(examples) + 2}
        table = 2 + (header_index - 2) // 2
        for pos, example in enumerate(examples):
            c = comps.get(f"US20130296157_sample_{example}")
            if not c:
                continue
            loc = f"US20130296157A1 PDF; Google Patents HTML Table {table}; example {example}"
            for label, (prop, unit, method, scope) in props.items():
                if label in values:
                    emit(out, c, "SRC-PATENT-US20130296157", loc, prop, values[label][pos], unit, method, scope)

def us2021(raw, evidence, out):
    comps = composition_rows(evidence)
    soup = BeautifulSoup(raw.read_text(encoding="utf-8"), "html.parser")
    rows = [cells(r) for r in soup.select("table")[1].select("tr")]
    props = {"Density (g/cm3)": ("density_g_per_cm3", "g/cm3", "Archimedes, ASTM C693 (publication definition)", "source reports measured glass; measurement temperature not stated"), "Strain point (° C.)": ("strain_point_fibre_C", "C", "beam bending or fibre elongation (publication definition)", "strain-point temperature"), "Anneal point (° C.)": ("annealing_point_fibre_C", "C", "fibre elongation, ASTM C336 (publication definition)", "annealing-point temperature")}
    for start, header in enumerate(rows):
        if not header or header[0] != "Component (mol %)":
            continue
        samples = header[1:]; end = next((i for i in range(start + 1, len(rows)) if rows[i] and rows[i][0] == "Component (mol %)"), len(rows))
        fields = {r[0]: r[1:] for r in rows[start:end] if len(r) == len(samples) + 1}
        for pos, sample in enumerate(samples):
            c = comps.get(f"US20210221730_example_{sample}")
            if not c: continue
            loc = f"US20210221730A1 PDF; Google Patents HTML Table 2; example {sample}"
            for label, (prop, unit, method, scope) in props.items():
                if label in fields: emit(out, c, "SRC-PATENT-US20210221730", loc, prop, fields[label][pos], unit, method, scope)

def us2021_table2prime(raw, out):
    """Recover Table 2''' mechanics only where its matching composition is explicit."""
    soup = BeautifulSoup(raw.read_text(encoding="utf-8"), "html.parser")
    compositions = {}
    for index in (15, 17, 19, 21):
        rows = [cells(r) for r in soup.select("table")[index].select("tr")]
        for at, header in enumerate(rows):
            if "(mol %)" not in header: continue
            first = header.index("(mol %)"); samples = header[first + 1:]
            if not samples or not any("′" in x for x in samples): continue
            end = next((i for i in range(at + 1, len(rows)) if "(mol %)" in rows[i]), len(rows))
            for pos, sample in enumerate(samples):
                oxides = {}
                for row in rows[at + 1:end]:
                    if len(row) >= first + pos + 2 and row[first] in set(CORE) | {"SrO", "SnO2", "Fe2O3"}:
                        oxides[row[first]] = row[first + pos + 1]
                if not oxides or any(missing(v) for v in oxides.values()): continue
                total = sum(float(v) for v in oxides.values())
                compositions[sample] = {"glass_id": f"US20210221730_table2prime_{sample}", "glass_display_name": f"US20210221730A1 实例 {sample}", "glass_family": "铝硼硅酸盐玻璃（专利实例族）", "composition_basis": "mol_percent", **{o: oxides.get(o, "0") for o in CORE}, "other_oxides_text": ";".join(f"{o}={v} mol%" for o,v in oxides.items() if o not in CORE), "composition_total": f"{total:.4f}", "composition_source_batch_or_lot": f"US20210221730A1 Tables 1a–1d; example {sample}", "process_state": "专利表中 batched 或 analyzed oxide mol% 状态，按原表标注"}
    for index in (23, 24):
        rows = [cells(r) for r in soup.select("table")[index].select("tr")]
        starts = [i for i,r in enumerate(rows) if len(r) > 1 and r[0] == "" and "′" in r[1]]
        for n,start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(rows); samples = rows[start][1:]; block = rows[start + 1:end]
            fields = {}
            for row in block:
                if len(row) == len(samples) + 1 and row[0] not in {"", "(Mpsi):", "(GPa):", "(GPa)):"}:
                    fields.setdefault(row[0], []).append(row[1:])
            for pos,sample in enumerate(samples):
                c = compositions.get(sample)
                if not c: continue
                loc = f"US20210221730A1 PDF; Google Patents HTML Table 2''' ; example {sample}"
                for label, prop, unit, method, scope, occurrence in (("Density (g/cm3):","density_g_per_cm3","g/cm3","Archimedes, ASTM C693 (publication definition)","source reports measured glass; measurement temperature not stated",0),("Strain Pt. (° C.):","strain_point_fibre_C","C","source Table 2''' strain point method not repeated; document defines strain-point methods","strain-point temperature",0),("Anneal Pt. (° C.):","annealing_point_fibre_C","C","source Table 2''' annealing point method not repeated; document defines fibre-elongation anneal method","annealing-point temperature",0),("Poisson's Ratio:","poisson_ratio","1","source Table 2''' ; test technique not repeated in table","source reports measured glass; test temperature not stated",0),("Young's Modulus","young_modulus_GPa","GPa","source Table 2''' explicitly reports GPa row","source reports measured glass; test temperature not stated",1)):
                    if label in fields and len(fields[label]) > occurrence: emit(out,c,"SRC-PATENT-US20210221730",loc,prop,fields[label][occurrence][pos],unit,method,scope)

def main():
    p = argparse.ArgumentParser(); p.add_argument("--raw-dir", type=Path, required=True); p.add_argument("--output", type=Path, required=True); a = p.parse_args()
    if a.output.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output}")
    root = a.raw_dir; out = []
    us2025(root / "patents/US20250026678A1.html", root / "evidence_records_us20250026678_cte.csv", Path("data/processed/chip_glass_substrate_us20250026678_same_identity_thermomechanical_v2.csv"), out)
    us2011(root / "patents/US20110082022A1.html", root / "evidence_records_us20110082022_cte.csv", out)
    us2013(root / "patents/US20130296157A1.html", root / "evidence_records_us20130296157_cte.csv", out)
    us2021(root / "patents/US20210221730A1.html", root / "evidence_records_us20210221730_cte.csv", out)
    us2021_table2prime(root / "patents/US20210221730A1.html", out)
    columns = ["glass_id", "glass_display_name", "glass_family", "composition_basis", *CORE, "other_oxides_text", "composition_total", "composition_source_batch_or_lot", "process_state", "source_id", "source_locator", "property_name", "property_value", "property_unit", "test_method", "state_scope", "evidence_level", "data_license"]
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=columns); w.writeheader(); w.writerows(out)
    counts = {}
    for r in out: counts[r["property_name"]] = counts.get(r["property_name"], 0) + 1
    print(f"wrote {len(out)} thermo-mechanical evidence records: {counts}")
if __name__ == "__main__": main()
