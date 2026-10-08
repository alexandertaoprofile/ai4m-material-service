#!/usr/bin/env python3
"""Parse the Cambridge Superalloy Database v3.1 into traceable research data.

This intentionally creates a *separate* cast/DS/single-crystal Ni-superalloy
training lane.  Phase rows and multi-phase constituent rows are retained in an
audit table but never combined with whole-alloy tensile observations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ELEMENTS = ["Ni", "Cr", "Co", "Re", "Ru", "Al", "Ta", "W", "Ti", "Mo", "Fe", "V", "Ir", "Mn", "Y", "C", "B", "Zr", "Si", "Nb", "Hf"]
ROUTES = {"sc": "single_crystal", "ds": "directionally_solidified", "cc": "cast", "am/lc": "additive_laser_cladding", "am/sebm": "additive_electron_beam"}


def num(x: str) -> float | None:
    try:
        y = float(str(x).strip())
        return y if math.isfinite(y) else None
    except ValueError:
        return None


def text(x: str) -> str:
    return str(x).strip()


def write(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def load(path: Path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith("alloy_spreadsheet_v3.1.csv"))
        with z.open(name) as b:
            rows = list(csv.reader((line.decode("utf-8-sig") for line in b)))
    return rows[4:], digest, name


def composition(row):
    # v3.1 verified fixed columns: 77..97, all wt.%.
    return {e: num(row[77+i]) for i, e in enumerate(ELEMENTS)}


def heat_treatment(row):
    parts = []
    # Solution stages at 56..64; precipitation stages at 65..76.
    for family, start, stages in (("solution", 56, 3), ("precipitation", 65, 4)):
        for stage in range(stages):
            t, h, rate = (num(row[start+stage*3+i]) for i in range(3))
            if t not in (None, 0): parts.append(f"{family}_stage_{stage+1}_temp_C={t}")
            if h not in (None, 0): parts.append(f"{family}_stage_{stage+1}_time_h={h}")
            if rate not in (None, 0): parts.append(f"{family}_stage_{stage+1}_heating_C_per_h={rate}")
    return "; ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    a = ap.parse_args()
    if a.output_dir.exists(): raise SystemExit(f"refusing to overwrite existing output: {a.output_dir}")
    raw, sha, member = load(a.archive)
    all_rows, whole, phase_rows, tensile, tensile_aux, queues = [], [], [], [], [], defaultdict(list)
    for line, row in enumerate(raw, start=5):
        if len(row) < 607: continue
        tag, alloy = text(row[0]), text(row[1])
        phase, route = text(row[51]), text(row[53]).casefold()
        base = {
            "source_archive": str(a.archive), "source_member": member, "source_row": line,
            "tag": tag, "alloy_name": alloy, "reference_1_name": text(row[2]), "reference_1_doi": text(row[3]),
            "reference_1_link": text(row[5]), "reference_2_name": text(row[6]), "reference_2_doi": text(row[7]),
            "reference_2_link": text(row[9]), "year": text(row[10]), "commercial": text(row[49]),
            "multiphase": text(row[11]), "phase": phase, "casting_code": route, "casting_gradient_K_per_mm": num(row[54]), "manufacturing_route": ROUTES.get(route, "other_or_unspecified"),
            "parent_alloy": text(row[50]), "notes": text(row[56]), "heat_treatment": heat_treatment(row),
            **{f"{e}_wt_percent": composition(row)[e] for e in ELEMENTS},
        }
        all_rows.append(base)
        # `phase` names or parent-alloy links indicate constituent/phase rows.
        # Keep them observable, but never use them as whole-material labels.
        if phase or text(row[50]):
            base["admission"] = "phase_or_constituent_review_only"
            phase_rows.append(base); continue
        base["admission"] = "whole_alloy_candidate"
        whole.append(base)
        for slot in range(7):
            off = 170 + slot * 12
            tc = num(row[off]); tk = tc + 273.15 if tc is not None else None
            for label, idx, definition in (("yield_strength", 1, "explicit_yield_stress"), ("ultimate_tensile_strength", 2, "ultimate_tensile_stress"), ("proof_strength_0p2", 3, "0.2_percent_proof_stress")):
                v = num(row[off+idx])
                if tk is not None and v is not None:
                    tensile.append({**base, "property": label, "strength_definition": definition, "value_MPa": v,
                                    "test_temperature_C": tc, "temperature_K": tk, "tensile_slot": slot+1,
                                    "label_admission": "admitted_whole_alloy_tensile_observation"})
            # These are preserved as independent targets.  They are not used
            # as features for strength prediction because doing so would leak
            # values unavailable when proposing a new composition.
            for label, idx, unit in (("elongation",4,"percent"),("reduction_of_area",5,"percent"),("youngs_modulus",6,"GPa"),("shear_modulus",7,"GPa"),("poissons_ratio",8,"dimensionless"),("C11",9,"GPa"),("C12",10,"GPa"),("C44",11,"GPa")):
                v=num(row[off+idx])
                if tk is not None and v is not None:
                    tensile_aux.append({**base,"property":label,"value":v,"unit":unit,"test_temperature_C":tc,"temperature_K":tk,"tensile_slot":slot+1,"admission":"separate_mechanical_target_not_strength_feature"})
        # Preserve other physical lanes rather than pretending they are tensile labels.
        for kind, start, width, fields in (("creep_rupture", 254, 10, ("temperature_C", "stress_MPa", "time_h")),
                                           ("fatigue", 394, 4, ("temperature_C", "stress_MPa", "cycles"))):
            for slot in range(14 if kind == "creep_rupture" else 4):
                vals = [num(row[start+slot*width+i]) for i in range(len(fields))]
                if any(v is not None for v in vals):
                    queues[kind].append({**base, "slot":slot+1, **dict(zip(fields, vals)), "admission":"separate_task_lane_pending_endpoint_audit"})
        for name, ix in (("liquidus_C",418),("solidus_C",419),("solvus_phase_1_C",420),("solvus_phase_2_C",421)):
            v=num(row[ix])
            if v is not None: queues["phase_transition"].append({**base,"property":name,"value_C":v,"admission":"separate_thermodynamic_target_lane"})
    # The first research model is deliberately restricted to hot-end relevant routes.
    model_routes = set(ROUTES)
    admitted = [r for r in tensile if r["casting_code"] in model_routes]
    a.output_dir.mkdir(parents=True)
    write(a.output_dir/"alloy_level_records.csv", whole)
    write(a.output_dir/"phase_constituent_review_queue.csv", phase_rows)
    write(a.output_dir/"short_time_tensile_observations.csv", tensile)
    write(a.output_dir/"tensile_auxiliary_property_queue.csv", tensile_aux)
    write(a.output_dir/"cast_ds_sc_short_time_strength_admitted.csv", admitted)
    for name, rows in queues.items():
        if rows: write(a.output_dir/f"{name}_separate_task_queue.csv", rows)
    by_tag = defaultdict(list)
    for r in admitted: by_tag[r["tag"]].append(r)
    cards=[]
    for tag, rs in sorted(by_tag.items()):
        cards.append({"tag":tag,"alloy_name":rs[0]["alloy_name"],"casting_code":rs[0]["casting_code"],"manufacturing_route":rs[0]["manufacturing_route"],
                      "observation_count":len(rs),"targets":";".join(sorted(set(r["property"] for r in rs))),"temperature_min_K":min(r["temperature_K"] for r in rs),"temperature_max_K":max(r["temperature_K"] for r in rs),
                      "source_row":";".join(sorted(set(str(r["source_row"]) for r in rs))),"admission":"candidate_for_group_disjoint_research_training"})
    write(a.output_dir/"admitted_alloy_cards.csv",cards)
    summary={"package":"cambridge_ni_superalloy_v3_1_research_admission_v1","archive_sha256":sha,"source":"University of Cambridge Apollo repository, DOI 10.17863/CAM.92063; source CSV v3.1; CC-BY per source README","mature_service_changed":False,
             "raw_rows":len(all_rows),"whole_alloy_records":len(whole),"phase_or_constituent_records_excluded":len(phase_rows),"all_tensile_observations":len(tensile),"hot_end_route_tensile_observations":len(admitted),"hot_end_route_tags":len(cards),
             "route_counts":dict(Counter(r["casting_code"] for r in admitted)),"target_counts":dict(Counter(r["property"] for r in admitted)),"tensile_auxiliary_property_rows":len(tensile_aux),"tensile_auxiliary_target_counts":dict(Counter(r["property"] for r in tensile_aux)),"separate_task_rows":{k:len(v) for k,v in queues.items()},
             "training_rule":"only whole-alloy records, route-specific model, group-disjoint split by Tag; no phase constituent rows; no mixing with the existing wrought/commercial model"}
    (a.output_dir/"manifest.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf8")
    (a.output_dir/"README.md").write_text("# 剑桥高温镍基合金研究准入包\n\n该包保留来源行、文献定位、制造路线、wt.% 成分、热处理与性能。相/多相组分记录没有作为整体合金性能样本。铸造、定向凝固、单晶与增材路线只构成独立研究模型线，不能与现有变形商品镍基牌号模型混训。蠕变/持久、疲劳与相变温度已单独排队，不进入短时拉伸模型。\n",encoding="utf8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__": main()
