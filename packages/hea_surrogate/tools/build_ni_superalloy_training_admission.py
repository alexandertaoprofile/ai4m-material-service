#!/usr/bin/env python3
"""Create an immutable, training-only admission package for Ni hot-end alloys.

The package intentionally keeps supplier composition specifications as intervals
and qualifiers.  It does not turn a grade specification into a fictional heat
analysis, and it does not modify the mature-material catalogue.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


TARGETS = {"yield_strength", "ultimate_tensile_strength"}
CORE = {
    "MAT-IN718", "MAT-1101-HT-IN725", "MAT-1101-HT-INX750",
    "MAT-1101-HT-HR41", "MAT-1101-HT-H282", "MAT-1101-HT-H263",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-components", type=Path, required=True)
    ap.add_argument("--structured-components", type=Path, required=True)
    ap.add_argument("--reviewed-properties", type=Path, required=True)
    ap.add_argument("--additional-properties", type=Path,
                    help="Locally parsed, exact-document-mapped base-tensile observations to add to the reviewed layer.")
    ap.add_argument("--raw-audit", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    pdf = read_csv(args.pdf_components)
    structured = read_csv(args.structured_components)
    props = read_csv(args.reviewed_properties)
    if args.additional_properties:
        props.extend(read_csv(args.additional_properties))
    raw = read_csv(args.raw_audit)

    # Specification compositions from local manufacturer PDFs.  All preserve
    # the page, raw string and qualifier; only well-defined numeric entries
    # enter the numerical feature layer.  `balance` remains categorical.
    composition = []
    for row in pdf:
        rec = dict(row)
        rec.update({
            "composition_origin": "manufacturer_pdf_specification",
            "composition_representation": "grade_specification_envelope",
            "training_source_admission": "admitted_source_evidence",
            "numeric_feature_admission": (
                "admitted" if row["value_kind"] != "unparsed_keep_raw" else "raw_value_retained_pending_parser_resolution"
            ),
            "training_scope": "engine_hot_end_core" if row["material_id"] in CORE else "nickel_alloy_supplementary",
        })
        composition.append(rec)
    for row in structured:
        # This retained source is already explicitly linked to MAT-IN718.
        material_id = row["canonical_material_id"]
        composition.append({
            "material_id": material_id, "display_name": row["display_name"], "element": row["element"],
            "minimum_wt_percent": row["minimum_wt_percent"], "maximum_wt_percent": row["maximum_wt_percent"],
            "nominal_wt_percent": row["nominal_wt_percent"], "raw_value_text": "",
            "value_kind": "structured_specification", "basis": row["basis"],
            "source_pdf": "", "source_document_id": row["source_artifact_id"],
            "source_page": row["source_row_number"], "source_section": "structured material composition record",
            "admission": row["admission"], "blocker": "",
            "composition_origin": "previously_admitted_structured_component", 
            "composition_representation": "grade_specification_envelope",
            "training_source_admission": "admitted_source_evidence", "numeric_feature_admission": "admitted",
            "training_scope": "engine_hot_end_core",
        })
    fields = list(composition[0])

    # One exact grade may use only its own specification envelope.  Labels are
    # admitted only with numerical value and numerical test temperature.
    composition_ids = {row["material_id"] for row in composition}
    labels = []
    seen_labels = set()
    for row in props:
        if row.get("property") not in TARGETS:
            continue
        if not numeric(row.get("value", "")) or not numeric(row.get("temperature_K", "")):
            continue
        if row["material_id"] not in composition_ids:
            continue
        dedup_key=(row["material_id"],row["property"],row["value"],row["temperature_K"],row.get("source_locator",""))
        if dedup_key in seen_labels:
            continue
        seen_labels.add(dedup_key)
        rec = dict(row)
        rec.update({
            "composition_representation": "grade_specification_envelope",
            "composition_link": "exact_material_id",
            "training_scope": "engine_hot_end_core" if row["material_id"] in CORE else "nickel_alloy_supplementary",
            "training_admission": "admitted_short_time_strength_label",
            "exclusions": "not creep; not rupture; not weld/filler; not inferred composition",
        })
        labels.append(rec)

    # Grade card creates the direct input to group-by-grade validation.  It
    # describes coverage, never claims a sample is a measured melt composition.
    by_grade = defaultdict(list)
    for row in composition:
        by_grade[row["material_id"]].append(row)
    label_by_grade = defaultdict(list)
    for row in labels:
        label_by_grade[row["material_id"]].append(row)
    cards = []
    for material_id, comps in sorted(by_grade.items()):
        label_rows = label_by_grade[material_id]
        cards.append({
            "material_id": material_id,
            "display_name": comps[0]["display_name"],
            "training_scope": comps[0]["training_scope"],
            "composition_representation": "grade_specification_envelope",
            "component_rows": len(comps),
            "elements": ";".join(sorted({r["element"] for r in comps})),
            "components_pending_parser_resolution": sum(r["numeric_feature_admission"] != "admitted" for r in comps),
            "strength_rows_numeric_temperature": len(label_rows),
            "yield_rows": sum(r["property"] == "yield_strength" for r in label_rows),
            "uts_rows": sum(r["property"] == "ultimate_tensile_strength" for r in label_rows),
            "temperature_min_K": min((float(r["temperature_K"]) for r in label_rows), default=""),
            "temperature_max_K": max((float(r["temperature_K"]) for r in label_rows), default=""),
            "grade_admission": "admitted_for_training_package",
            "caveat": "Specification envelope, not a heat-analysis composition; require group-by-grade split and applicability-domain reporting.",
        })

    creep_queue=[]
    for row in raw:
        if row["category"] not in {"creep", "rupture"}:
            continue
        creep_queue.append({
            "local_path":row["local_path"], "category":row["category"], "row_count":row["row_count"],
            "identity_fields":row["identity_fields"], "current_disposition":row["disposition"],
            "admission":"review_required_not_a_short_time_label",
            "required_before_training":"parse stress, temperature, exposure time, endpoint and heat-treatment/product state; exact-grade composition linkage",
        })
    calphad=[]
    for card in cards:
        calphad.append({
            "material_id":card["material_id"], "display_name":card["display_name"], "training_scope":card["training_scope"],
            "composition_input":"admitted grade specification envelope", "calphad_status":"pending validated thermodynamic database selection and grade-level calibration",
            "not_a_label":"No solidus/liquidus, phase fraction or phase-stability result is claimed in this package.",
        })

    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir / "composition_specification_envelopes.csv", fields, composition)
    numeric_comps=[r for r in composition if r["numeric_feature_admission"] == "admitted"]
    write_csv(args.output_dir / "composition_numeric_feature_layer.csv", fields, numeric_comps)
    label_fields=list(dict.fromkeys(k for row in labels for k in row)) if labels else list(props[0])+["composition_representation","composition_link","training_scope","training_admission","exclusions"]
    write_csv(args.output_dir / "short_time_strength_observations.csv", label_fields, labels)
    write_csv(args.output_dir / "grade_cards.csv", list(cards[0]), cards)
    write_csv(args.output_dir / "creep_rupture_admission_queue.csv", list(creep_queue[0]), creep_queue)
    write_csv(args.output_dir / "calphad_readiness_manifest.csv", list(calphad[0]), calphad)

    summary={
        "package":"ni_superalloy_training_admission_v3",
        "training_model_started":False,
        "source_policy":"local manufacturer PDF specifications plus previously reviewed property records only",
        "explicitly_excluded_source":"Cambridge repository and any other external training source",
        "mature_product_service_changed":False,
        "grade_specification_envelopes":len(cards),
        "composition_component_rows":len(composition),
        "numeric_feature_component_rows":len(numeric_comps),
        "components_retained_raw_pending_parser_resolution":len(composition)-len(numeric_comps),
        "admitted_short_time_strength_observations":len(labels),
        "admitted_grades_with_short_time_strength":sum(bool(label_by_grade[x]) for x in by_grade),
        "core_engine_hot_end_grades":sum(c["training_scope"]=="engine_hot_end_core" for c in cards),
        "core_short_time_strength_observations":sum(r["training_scope"]=="engine_hot_end_core" for r in labels),
        "creep_rupture_tables_queued":len(creep_queue),
        "creep_rupture_rows_queued":sum(int(r["row_count"]) for r in creep_queue),
        "calphad_grade_manifests":len(calphad),
        "required_before_model_fit":[
            "freeze a train/validation/test split grouped by material_id (no same-grade leakage)",
            "choose and record a specification-envelope featurization method; do not silently midpoint-impute or zero-impute", 
            "retain heat-treatment/product state; suppress predictions outside the grade/specification applicability domain",
            "keep creep/rupture and CALPHAD as separate model/calculation lanes",
        ],
        "not_claimed":["measured heat chemistry", "DFT result", "CALPHAD result", "melting point", "creep/rupture prediction", "experimental manufacturability"],
    }
    (args.output_dir / "training_admission_manifest.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.output_dir / "README.md").write_text("""# 镍基高温合金训练准入包 v1

这是一个**仅供模型研发**的冻结输入包。它不替换成熟牌号资料库，也没有启动模型拟合。

## 已准入

- `composition_specification_envelopes.csv`：精确牌号的供应商 wt.% 规范、原文、页码和限定词；表示的是牌号规范区间，不是某一炉次实测化学成分。
- `short_time_strength_observations.csv`：只含数值温度下的屈服强度/抗拉强度，且仅与同 `material_id` 的成分规范相连。
- `grade_cards.csv`：按牌号冻结的成分与标签覆盖，供后续按牌号分组拆分。

## 未准入为标签

蠕变/持久、焊材/连接数据、无数值温度的强度、CALPHAD 结果和任何外部剑桥数据都没有进入短时强度标签集。

## 拟合前唯一需要做的工作

锁定按 `material_id` 分组的拆分方案，并选择可审计的规范区间特征化方式。特征化不得悄悄把区间中点当作实测成分，也不得把未列元素补零。之后才允许启动基线训练。
""",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
