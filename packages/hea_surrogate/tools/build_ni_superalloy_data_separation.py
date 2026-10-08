#!/usr/bin/env python3
"""Create separate, immutable manifests for product evidence and model training.

No mature-material catalogue is modified.  The output describes exactly which
records can be used as model seed data, which require composition curation, and
which belong only to product evidence or specialised future model lanes.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


PRODUCT_BUNDLES = (
    "2026-08-06_document_mapped_v1",
    "2026-08-14_simulation_properties_v2",
    "2026-08-24_c276_n06230_physical_v2",
)
STRENGTH = {"yield_strength", "ultimate_tensile_strength", "tensile_strength"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, fields: list[str], records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        out = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        out.writeheader(); out.writerows(records)


def numeric(value: str) -> bool:
    try:
        float(value); return True
    except (ValueError, TypeError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed-root", type=Path, required=True)
    ap.add_argument("--training-gate", type=Path, required=True)
    ap.add_argument("--composition-candidates", type=Path, required=True)
    ap.add_argument("--raw-audit", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    # Mature product evidence stays in its existing immutable processed bundles.
    package_rows = []
    all_materials: dict[str, dict[str, str]] = {}
    all_points: list[dict[str, str]] = []
    for bundle in PRODUCT_BUNDLES:
        root = args.processed_root / bundle
        materials = read_csv(root / "materials.csv")
        points = read_csv(root / "property_points.csv")
        for row in materials: all_materials.setdefault(row["material_id"], row)
        all_points.extend(points)
        package_rows.append({
            "data_plane": "mature_product_catalogue",
            "bundle": bundle,
            "location": str(root),
            "materials": len(materials),
            "property_points": len(points),
            "allowed_use": "customer-facing evidence retrieval and known-grade comparison",
            "not_allowed_use": "automatic composition-model training without a separately admitted composition linkage",
        })
    write_csv(args.output_dir / "mature_product_catalogue_manifest.csv", list(package_rows[0]), package_rows)

    # Training seed: already cross-linked structured component + property data.
    seed_rows = read_csv(args.training_gate / "ni_strength_training_rows_v0.csv")
    write_csv(args.output_dir / "training_seed_admitted.csv", list(seed_rows[0]) if seed_rows else [], seed_rows)
    seed_material_ids = {row["material_id"] for row in seed_rows}

    # Existing product evidence suitable for the tensile lane after a distinct
    # composition record is approved; do not copy values into the seed.
    by_material = defaultdict(list)
    for point in all_points:
        if point["property"] in STRENGTH:
            by_material[point["material_id"]].append(point)
    components = read_csv(args.composition_candidates)
    candidate_ids = {row["candidate_material_id"] for row in components if row["candidate_material_id"]}
    coverage = []
    for material_id, points in sorted(by_material.items()):
        material = all_materials.get(material_id, {})
        family = material.get("family", "").casefold()
        display_name = material.get("display_name", "")
        # The product catalogue may contain Ti and other high-temperature
        # material families; do not quietly bring them into the Ni model lane.
        is_ni = material_id in seed_material_ids or (
            "nickel" in family and "titanium" not in family
        )
        if not is_ni:
            continue
        numeric_t = [p for p in points if numeric(p["value"]) and numeric(p["temperature_K"])]
        if material_id in seed_material_ids:
            composition_status, admission, action = "admitted_structured_composition", "admitted_seed", "Retain as pipeline seed only; acquire multiple independent compositions before model fitting"
        elif material_id in candidate_ids:
            composition_status, admission, action = "review_candidate_available", "review_only", "Review explicit composition table and state linkage"
        else:
            composition_status, admission, action = "missing_structured_composition", "blocked_missing_composition", "Acquire a traceable wt.% specification for this exact grade/state"
        coverage.append({
            "material_id": material_id,
            "display_name": material.get("display_name", ""),
            "family": material.get("family", ""),
            "strength_points_total": len(points),
            "strength_points_numeric_temperature": len(numeric_t),
            "properties": ";".join(sorted({p["property"] for p in points})),
            "training_lane": "short_time_tensile_v0",
            "composition_status": composition_status,
            "admission": admission,
            "next_action": action,
        })
    write_csv(args.output_dir / "short_time_strength_coverage_by_grade.csv", list(coverage[0]), coverage)

    raw = read_csv(args.raw_audit / "ni_superalloy_table_inventory.csv")
    lane_map = {
        "creep": "creep_rupture_v1", "rupture": "creep_rupture_v1",
        "tensile": "short_time_tensile_v0", "mechanical": "short_time_tensile_v0",
        "thermal": "thermal_physical_property", "elastic": "thermal_physical_property",
        "phase": "calphad_validation", "chemical_composition": "composition_curation",
    }
    raw_queue = []
    for row in raw:
        lane = lane_map.get(row["category"], "manual_scope_review")
        raw_queue.append({
            "source_set": row["source_set"], "source_file": row["local_path"], "category": row["category"],
            "proposed_model_lane": lane, "rows": row["row_count"], "current_disposition": row["disposition"],
            "training_admission": "excluded" if row["disposition"] == "exclude_welding_or_join" else "review_required",
            "reason": row["rationale"],
        })
    write_csv(args.output_dir / "raw_source_training_lane_queue.csv", list(raw_queue[0]), raw_queue)

    lanes = [
        {"model_lane":"short_time_tensile_v0", "inputs":"wt.% composition + heat treatment/product state + test temperature", "labels":"yield strength; UTS", "current_admitted_rows":len(seed_rows), "current_admitted_compositions":1, "status":"data acquisition required", "boundary":"No composition extrapolation claim until group-by-grade validation."},
        {"model_lane":"creep_rupture_v1", "inputs":"wt.% composition + heat treatment/product state + temperature + stress + time", "labels":"rupture time / creep rate / strain", "current_admitted_rows":0, "current_admitted_compositions":0, "status":"schema and curation required", "boundary":"Do not mix with short-time tensile labels."},
        {"model_lane":"calphad_validation", "inputs":"composition + validated thermodynamic database + temperature", "labels":"solidus/liquidus; phase fractions; phase stability", "current_admitted_rows":0, "current_admitted_compositions":0, "status":"database selection and calibration required", "boundary":"Calculator and database are separate; results need grade-level calibration."},
    ]
    write_csv(args.output_dir / "ni_superalloy_model_lanes.csv", list(lanes[0]), lanes)

    summary = {
        "purpose": "Separate mature product evidence from Ni-superalloy model-training assets without changing the product service.",
        "mature_product_catalogue": {"bundles": len(package_rows), "material_identities": len(all_materials), "property_points": len(all_points)},
        "training": {
            "admitted_seed_rows": len(seed_rows), "admitted_seed_compositions": 1,
            "strength_grades_with_missing_structured_composition": sum(1 for r in coverage if r["composition_status"] == "missing_structured_composition"),
            "strength_grades_with_review_composition_candidate": sum(1 for r in coverage if r["composition_status"] == "review_candidate_available"),
            "raw_source_tables_queued": len(raw_queue),
            "raw_category_counts": dict(sorted(Counter(r["category"] for r in raw).items())),
        },
        "non_negotiable": "No record crosses from mature product evidence into a model-training label set without explicit composition, condition, unit and source-link admission.",
    }
    (args.output_dir / "separation_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    text = f"""# 镍基高温合金数据分层

## 成熟商品证据层

保留 {len(all_materials)} 个已核验材料身份和 {len(all_points)} 条性质点，用于牌号检索、已知材料比较与客户证据展示。该层不自动成为训练集。

## 训练数据层

当前已准入的成分—温度—强度种子为 {len(seed_rows)} 条、1 个成分（IN718），只能用于管线联调，不能训练泛化模型。另有 {sum(1 for r in coverage if r['composition_status'] == 'review_candidate_available')} 个既有牌号具备待审核成分候选；其余 {sum(1 for r in coverage if r['composition_status'] == 'missing_structured_composition')} 个有强度数据的牌号仍缺可追溯结构化成分。

训练按三条独立模型线组织：短时拉伸、蠕变/持久、CALPHAD 热力学。详见 `ni_superalloy_model_lanes.csv`。

## 准入原则

成熟商品的已知牌号性能可以服务查询，但只有当 exact grade 的 wt.% 成分、热处理/产品状态、测试条件和来源行号全部可追溯时，才会复制到模型训练层。
"""
    (args.output_dir / "data_separation.md").write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
