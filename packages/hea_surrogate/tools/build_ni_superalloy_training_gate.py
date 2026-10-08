#!/usr/bin/env python3
"""Build a strict, reproducible readiness bundle for a Ni-superalloy model.

This intentionally consumes only already-reviewed processed property bundles for
numeric labels.  Newly acquired 1101 raw tables are retained as an audit/review
queue; their document titles are never promoted to alloy identities here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


TARGETS = {"yield_strength", "ultimate_tensile_strength"}
BUNDLES = (
    "2026-08-06_document_mapped_v1",
    "2026-08-14_simulation_properties_v2",
    "2026-08-24_c276_n06230_physical_v2",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fingerprint(row: dict[str, str]) -> str:
    keep = ("material_id", "property", "value", "unit", "temperature_K", "condition", "source_locator")
    text = "\x1f".join(row.get(key, "") for key in keep)
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold().replace("alloy", ""))


def is_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-root", type=Path, required=True)
    parser.add_argument("--raw-audit-dir", type=Path, required=True)
    parser.add_argument("--supplement-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    # 1. Read only the prior reviewed imports, preserving evidence locators.
    materials: dict[str, dict[str, str]] = {}
    property_rows: list[dict[str, str]] = []
    manifest = []
    for bundle in BUNDLES:
        root = args.processed_root / bundle
        for row in read_csv(root / "materials.csv"):
            materials.setdefault(row["material_id"], row)
        for row in read_csv(root / "property_points.csv"):
            row = dict(row)
            row["reviewed_bundle"] = bundle
            property_rows.append(row)
        manifest.append({"bundle": bundle, "path": str(root)})

    deduped: dict[str, dict[str, str]] = {}
    for row in property_rows:
        deduped.setdefault(fingerprint(row), row)
    property_rows = list(deduped.values())

    # 2. Composition input is admitted only where a structured component row is
    # linked to an actual Ni-superalloy material record.  It is deliberately not
    # inferred from grade names or brochure titles.
    remote_materials = {
        row["id"]: row for row in read_csv(args.supplement_root / "tables" / "material__material.csv")
    }
    components = read_csv(args.supplement_root / "tables" / "material__composition_component.csv")
    comp_rows: list[dict[str, object]] = []
    for row in components:
        material = remote_materials.get(row["material_id"])
        if not material:
            continue
        family = material.get("family", "")
        display_name = material.get("display_name", "")
        if "镍基高温合金" not in family and "inconel 718" not in display_name.casefold():
            continue
        if row.get("basis", "").casefold() not in {"mass %", "wt%", "wt.%"}:
            continue
        source_record = json.loads(material.get("source_record", "{}") or "{}")
        comp_rows.append({
            "composition_source_material_id": row["material_id"],
            "canonical_material_id": source_record.get("material_id", ""),
            "display_name": display_name,
            "standard": material.get("standard", ""),
            "element": row["component"],
            "minimum_wt_percent": row["minimum"],
            "maximum_wt_percent": row["maximum"],
            "nominal_wt_percent": row["nominal"],
            "basis": row["basis"],
            "source_artifact_id": row["source_artifact_id"],
            "source_row_number": row["source_row_number"],
            "admission": "structured_specification_composition",
        })

    # Map only exact grade-normalized identities.  This joins INCONEL 718 to
    # MAT-IN718 while keeping the original identifiers visible.
    comp_grades = {norm_name(row["display_name"]): row["display_name"] for row in comp_rows}
    training_rows: list[dict[str, object]] = []
    target_by_material = Counter()
    for row in property_rows:
        if row.get("property") not in TARGETS:
            continue
        if not is_numeric(row.get("value", "")) or not is_numeric(row.get("temperature_K", "")):
            continue
        material = materials.get(row["material_id"], {})
        display = material.get("display_name", "")
        # MAT-IN718 predates the reviewed local materials table but is directly
        # linked by material_id to the structured composition component record.
        direct_component_match = any(
            row["material_id"] in {
                component["composition_source_material_id"], component["canonical_material_id"]
            }
            for component in comp_rows
        )
        if direct_component_match:
            match = next(component["display_name"] for component in comp_rows
                         if row["material_id"] in {
                             component["composition_source_material_id"], component["canonical_material_id"]
                         })
            display = display or match
        else:
            match = comp_grades.get(norm_name(display))
        if not match:
            continue
        record = dict(row)
        record.update({
            "material_display_name": display,
            "composition_display_name": match,
            "admission": "training_label_with_structured_composition_and_temperature",
        })
        training_rows.append(record)
        target_by_material[row["material_id"]] += 1

    # 3. Report what raw tables may or may not enter later review. No rows from
    # this set become numerical labels in this release.
    audit_rows = read_csv(args.raw_audit_dir / "ni_superalloy_table_inventory.csv")
    audit_counts = Counter(row["disposition"] for row in audit_rows)
    audit_categories = Counter(row["category"] for row in audit_rows)
    review_rows = [row for row in audit_rows if row["disposition"] != "exclude_welding_or_join"]

    args.output_dir.mkdir(parents=True)
    property_fields = list(property_rows[0]) if property_rows else []
    write_csv(args.output_dir / "reviewed_property_points_deduplicated.csv", property_fields, property_rows)
    write_csv(args.output_dir / "structured_composition_admitted.csv", list(comp_rows[0]) if comp_rows else [], comp_rows)
    train_fields = list(training_rows[0]) if training_rows else property_fields + ["material_display_name", "composition_display_name", "admission"]
    write_csv(args.output_dir / "ni_strength_training_rows_v0.csv", train_fields, training_rows)
    review_fields = list(review_rows[0]) if review_rows else []
    write_csv(args.output_dir / "raw_tables_pending_identity_or_schema_review.csv", review_fields, review_rows)

    property_by_type = Counter(row["property"] for row in property_rows)
    material_target_counts = Counter()
    for row in property_rows:
        if row["property"] in TARGETS:
            material_target_counts[row["material_id"]] += 1
    report = {
        "dataset": "ni_superalloy_training_gate_v1",
        "policy": {
            "numeric_labels": "Only previously reviewed processed bundles are included.",
            "composition": "Only structured composition-component rows explicitly linked to a Ni-superalloy material are included.",
            "raw_1101": "Raw acquisition tables remain review-only; no document-title identity inference and no welding/join data admitted.",
        },
        "reviewed_property_points_deduplicated": len(property_rows),
        "reviewed_materials": len(materials),
        "reviewed_property_counts": dict(sorted(property_by_type.items())),
        "materials_with_yield_or_uts": len(material_target_counts),
        "structured_composition_component_rows": len(comp_rows),
        "structured_composition_grades": sorted(set(row["display_name"] for row in comp_rows)),
        "strength_rows_with_composition_and_numeric_temperature": len(training_rows),
        "distinct_trainable_compositions": len(target_by_material),
        "raw_tables": {
            "pending_or_parse_candidate": len(review_rows),
            "excluded_welding_or_join": audit_counts.get("exclude_welding_or_join", 0),
            "dispositions": dict(sorted(audit_counts.items())),
            "categories": dict(sorted(audit_categories.items())),
        },
        "training_gate": "blocked" if len(target_by_material) < 8 else "eligible_for_baseline",
        "gate_reason": "A composition-to-property model requires multiple distinct alloy compositions. Current strict admission links strength labels to only one structured Ni-superalloy composition, so train/test evaluation would be invalid.",
        "next_data_requirement": "Curate composition specifications (wt.%) for the reviewed high-temperature grades, retaining heat treatment/product state and test temperature for each strength observation. Then use group-by-alloy validation before selecting a model.",
        "inputs": manifest,
    }
    (args.output_dir / "training_readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = f"""# 镍基高温合金训练数据准入结论（v1）

## 结论

当前可以形成**可追溯的牌号级高温性能资料库**，但不能负责任地训练“任意成分 → 高温强度”的泛化模型。严格准入后，带有结构化 wt.% 成分、数值测试温度且对应屈服/抗拉强度标签的记录只来自 **{len(target_by_material)} 个独立成分**；该样本不能用于训练或评估成分外推模型。

## 已纳入的证据

- 已复核、去重的性质点：{len(property_rows)} 条；材料身份：{len(materials)} 个。
- 屈服/抗拉强度有记录的牌号：{len(material_target_counts)} 个。
- 已严格准入的结构化成分条目：{len(comp_rows)} 条；对应牌号：{', '.join(sorted(set(row['display_name'] for row in comp_rows))) or '无'}。
- 当前可用于“成分 + 温度 → 强度”训练的记录：{len(training_rows)} 条，独立成分：{len(target_by_material)} 个。

## 明确未混入训练标签的内容

- 原始 1101 镍基相关表：{len(audit_rows)} 张，其中焊接/填充/钎焊相关表 {audit_counts.get('exclude_welding_or_join', 0)} 张已排除；其余 {len(review_rows)} 张仅列入人工身份与单位复核队列。
- 不用文档标题推断材料身份；不把焊材、连接数据、相近牌号或无测试条件的数值混入基材模型。
- 现有数据没有可靠的 solidus/liquidus 标签，因此不训练“熔点”模型。

## 建模启动门槛

补齐每个已复核牌号的 wt.% 成分规范，并保留热处理/产品状态/测试温度后，再开始候选模型基准：

1. 以组成为分组单位做留牌号验证；
2. 首先比较正则化线性、ExtraTrees/梯度提升等基线；
3. v0 只输出指定温度和状态下的屈服强度、抗拉强度及适用域提示；
4. 蠕变/持久寿命必须另行引入温度、应力、时间和完整热处理信息；
5. 固相线/液相线、相稳定性由经验证的 CALPHAD 数据库独立计算并校准，不由本模型替代。
"""
    (args.output_dir / "training_readiness.md").write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
