#!/usr/bin/env python3
"""Normalize only explicit composition tables into a review-only Ni queue.

The active high-temperature registry deliberately excluded these composition
tables.  This tool makes the values inspectable and linkable without silently
changing that decision or admitting them to model training.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


LINEAGE = {"lineage_source_table_id", "lineage_document_id", "lineage_document_name", "lineage_page_number", "lineage_source_item_index", "lineage_caption", "lineage_footnote", "lineage_table_context", "lineage_source_content_list", "lineage_source_csv_path", "lineage_equivalent_source_table_ids"}
ELEMENTS = {"al": "Al", "aluminum": "Al", "b": "B", "boron": "B", "c": "C", "carbon": "C", "co": "Co", "cobalt": "Co", "cr": "Cr", "chromium": "Cr", "cu": "Cu", "copper": "Cu", "fe": "Fe", "iron": "Fe", "mn": "Mn", "manganese": "Mn", "mo": "Mo", "molybdenum": "Mo", "nb": "Nb", "ni": "Ni", "nickel": "Ni", "niobium": "Nb", "p": "P", "phosphorus": "P", "s": "S", "sulfur": "S", "si": "Si", "silicon": "Si", "ti": "Ti", "titanium": "Ti", "w": "W", "tungsten": "W", "zr": "Zr", "zirconium": "Zr"}


def rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def range_values(value: str):
    value = value.strip().replace("≤", "max ").replace("<", "max ")
    if not value or value in {"-", "---", "Bal.", "Bal", "remainder"}:
        return "", "", "", value
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", value)
    if match:
        return match.group(1), match.group(2), "", ""
    match = re.fullmatch(r"max\s*(\d+(?:\.\d+)?)\.?", value, re.I)
    if match:
        return "", match.group(1), "", ""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", value)
    if match:
        return "", "", match.group(1), ""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*nb", value, re.I)
    if match:
        return "", "", match.group(1), "Nb"
    return "", "", "", value


def write(path: Path, fields, data):
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, fieldnames=fields)
        out.writeheader(); out.writerows(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", type=Path, required=True)
    ap.add_argument("--materials", type=Path, required=True)
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    if args.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    materials = rows(args.materials)
    by_uns = {r["UNS/standard"].split(";")[0].replace("UNS ", "").upper(): r for r in materials if r["UNS/standard"]}
    by_grade = {r["grade"].upper(): r for r in materials if r["grade"]}
    registry = json.loads(args.registry.read_text())
    admitted = {t for d in registry["documents"] for t in d.get("include_source_table_ids", [])}
    out=[]; table_audit=[]
    paths = sorted(args.raw_root.glob("*chemical_composition*.csv"))
    for path in paths:
        source_rows=rows(path)
        if not source_rows: continue
        tid=source_rows[0].get("lineage_source_table_id", "")
        doc=source_rows[0].get("lineage_document_id", "")
        parsed=0
        if "740h" in path.name.casefold():
            material_id = ""
            display = "INCONEL alloy 740H"
            bands = {}
            for idx, r in enumerate(source_rows, start=2):
                label = r.get("element", "").casefold().rstrip(".")
                if label not in {"min", "nom", "max"}: continue
                for column, value in r.items():
                    element = ELEMENTS.get(column.casefold())
                    if not element: continue
                    lo, hi, nom, note = range_values(value)
                    if lo or hi or nom:
                        bands.setdefault(element, {"min":"", "max":"", "nom":"", "note":"", "row":idx})[label] = lo or hi or nom
                        if note: bands[element]["note"] = note
            for element, band in bands.items():
                parsed += 1
                out.append({"candidate_material_id":material_id,"candidate_display_name":display,"identity_basis":"document_identity",
                    "element":element,"minimum_wt_percent":band["min"],"maximum_wt_percent":band["max"],"nominal_wt_percent":band["nom"],"value_note":band["note"],"basis":"wt.%","source_file":path.name,"source_table_id":tid,"source_document_id":doc,"source_page":source_rows[0].get("lineage_page_number", ""),"source_caption":source_rows[0].get("lineage_caption", ""),"source_row_number":band["row"],"admission":"review_only_registry_not_expanded" if tid not in admitted else "unexpected_registry_admitted","blocker":"Current high-temperature registry excludes this composition table; review identity, coverage, and intended use before any training admission."})
            table_audit.append({"source_file":path.name,"source_table_id":tid,"source_document_id":doc,"rows":len(source_rows),"normalized_component_rows":parsed,"admission":"review_only_registry_not_expanded" if tid not in admitted else "unexpected_registry_admitted"})
            continue
        # Vertically extracted IN693 table: first column is actually element name.
        vertical = set(source_rows[0]) >= {"nickel", "remainder_a"} and "693" in path.name
        for idx,r in enumerate(source_rows, start=2):
            identity = r.get("alloy", "")
            material = None
            uns = r.get("uns_number", "").upper()
            if uns: material=by_uns.get(uns)
            if not material and identity:
                key = identity.replace("UNS ", "").replace("INCONEL alloy ", "").upper()
                material=by_uns.get(key) or by_grade.get(key)
            if vertical:
                identity="INCONEL alloy 693"; material=by_grade.get("693")
                values={r.get("nickel", "").casefold(): r.get("remainder_a", "")}
            else:
                values={k:v for k,v in r.items() if k not in LINEAGE and k not in {"alloy","uns_number","werkstoff_number","pren","cr_mo_w","element"}}
            for column,value in values.items():
                element=ELEMENTS.get(column.casefold())
                lo,hi,nom,note=range_values(value)
                if not element and column.casefold()=="w_or_nb" and note=="Nb": element="Nb"
                if not element or (not lo and not hi and not nom): continue
                parsed += 1
                out.append({
                    "candidate_material_id": material["material_id"] if material else "",
                    "candidate_display_name": material["display_name"] if material else identity,
                    "identity_basis": "explicit_uns_or_row_grade" if material else "document_identity_or_unmapped_row",
                    "element": element, "minimum_wt_percent":lo, "maximum_wt_percent":hi, "nominal_wt_percent":nom,
                    "value_note":note, "basis":"wt.%", "source_file":path.name,
                    "source_table_id":tid, "source_document_id":doc,
                    "source_page":r.get("lineage_page_number", ""), "source_caption":r.get("lineage_caption", ""),
                    "source_row_number":idx,
                    "admission":"review_only_registry_not_expanded" if tid not in admitted else "unexpected_registry_admitted",
                    "blocker":"Current high-temperature registry excludes this composition table; review identity, coverage, and intended use before any training admission.",
                })
        table_audit.append({"source_file":path.name,"source_table_id":tid,"source_document_id":doc,"rows":len(source_rows),"normalized_component_rows":parsed,"admission":"review_only_registry_not_expanded" if tid not in admitted else "unexpected_registry_admitted"})
    args.output_dir.mkdir(parents=True)
    fields=list(out[0]) if out else []
    write(args.output_dir/"existing_ni_composition_review_candidates.csv",fields,out)
    write(args.output_dir/"existing_ni_composition_table_audit.csv",list(table_audit[0]),table_audit)
    summary={"source_tables":len(table_audit),"review_candidate_component_rows":len(out),"mapped_to_existing_reviewed_material_ids":len({x['candidate_material_id'] for x in out if x['candidate_material_id']}),"admitted_to_training":0,"policy":"All normalized values remain review-only because their source tables are excluded by the current high-temperature registry."}
    (args.output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
