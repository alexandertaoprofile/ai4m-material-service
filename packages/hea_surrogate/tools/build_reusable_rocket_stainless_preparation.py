"""Build the auditable data-preparation bundle for reusable-rocket stainless screening.

This is deliberately a preparation gate, not a model trainer.  It inventories
the current 30X folder and the retained public-source manifest, then separates
directly usable records from proxy/reference and review-only evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_30X = ROOT / "data/raw/30x/材料性能数据集.csv"
RAW_BUNDLE = ROOT / "data/raw/cryogenic_stainless_20260901"

LANES = {
    "short_time_tensile": "成分—工艺—温度—拉伸：YS/UTS/延伸率",
    "fracture_fatigue_weld": "焊接、断裂韧性、冲击、疲劳与裂纹扩展",
    "thermal_environment": "热物性、热循环、LOX/GOX 相容性与清洁度",
}


def classification(row: dict[str, str]) -> tuple[str, str]:
    material = (row.get("Material") or "").strip()
    level = (row.get("Evidence_Level") or "").strip()
    applicability = (row.get("Applicability") or "").casefold()
    value = (row.get("Value") or "").strip().casefold()
    if material == "30X" or level == "D0" or "not disclosed" in value:
        return "blocked_30x_undisclosed", "30X 无公开成分、状态或数值标签；不准入。"
    if material in {"301", "304", "304L", "301LN", "304LN", "316", "316L", "316LN", "21-6-9"}:
        if "proxy" in applicability:
            return "reference_only", "标准牌号代理数据；可作基线，不可标为 30X。"
        return "review_for_stainless_lane", "不锈钢相关记录，但须核验成分和状态后准入。"
    return "out_of_scope_engine_hot_end", "发动机热端/铜合金/镍基资料，不进入壳体钢训练池。"


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/processed/reusable_rocket_stainless_preparation/20260902_v1",
    )
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    with RAW_30X.open(encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))

    audited: list[dict[str, object]] = []
    for row in source_rows:
        status, reason = classification(row)
        audited.append({
            "data_id": row.get("Data_ID", ""),
            "material": row.get("Material", ""),
            "property": row.get("Property", ""),
            "value": row.get("Value", ""),
            "unit": row.get("Unit", ""),
            "temperature_K": row.get("Temperature_K", ""),
            "condition_or_state": row.get("Condition_or_State", ""),
            "evidence_level": row.get("Evidence_Level", ""),
            "source_id": row.get("Source_ID", ""),
            "source_url": row.get("Source_URL", ""),
            "admission_status": status,
            "reason": reason,
        })
    fields = list(audited[0]) if audited else []
    write_csv(output / "existing_30x_record_audit.csv", fields, audited)

    manifest = json.loads((RAW_BUNDLE / "acquisition_manifest.json").read_text(encoding="utf-8"))
    queue: list[dict[str, object]] = []
    for source in manifest["sources"]:
        files = [source["file"]] if "file" in source else source["files"]
        for rel in files:
            queue.append({
                "source_id": source["id"],
                "source_file": rel,
                "material_scope": source["material_scope"],
                "source_url": source["url"],
                "target_lane": "short_time_tensile; fracture_fatigue_weld; thermal_environment",
                "required_extraction": "material identity; wt.% composition; product form; processing; test temperature; property; unit; page/table/figure locator",
                "admission_status": source["admission"],
            })
    write_csv(output / "public_source_extraction_queue.csv", list(queue[0]), queue)

    schema_rows = [
        {"lane": "all", "field": "alloy_record_id", "required": "yes", "purpose": "stable traceable sample identifier"},
        {"lane": "all", "field": "Fe/Cr/Ni/Mn/C/N/Si/Mo/Cu/Nb/Ti wt.%", "required": "yes", "purpose": "composition features; retain measured/nominal basis"},
        {"lane": "all", "field": "composition_basis", "required": "yes", "purpose": "measured heat / specification range / nominal; never silently merge"},
        {"lane": "all", "field": "product_form; thickness_mm; rolling_direction", "required": "yes", "purpose": "thin sheet/plate and anisotropy controls"},
        {"lane": "all", "field": "cold_reduction_pct; solution_anneal; aging; cryoforming", "required": "yes", "purpose": "processing features"},
        {"lane": "all", "field": "weld_status; weld_process; filler; HAZ/base location", "required": "yes", "purpose": "prevent base-metal and weld labels from mixing"},
        {"lane": "all", "field": "test_temperature_K; strain_rate; test_method; source_locator", "required": "yes", "purpose": "condition and evidence traceability"},
        {"lane": "short_time_tensile", "field": "0.2% proof/yield; UTS; total elongation", "required": "yes", "purpose": "independent multi-target tensile labels"},
        {"lane": "fracture_fatigue_weld", "field": "KIC/JIC; Charpy; da/dN; fatigue life", "required": "task-specific", "purpose": "separate safety/validation models"},
        {"lane": "thermal_environment", "field": "k(T); Cp(T); CTE(T); E(T); oxygen compatibility result", "required": "task-specific", "purpose": "simulation inputs and screening gates"},
    ]
    write_csv(output / "training_schema.csv", list(schema_rows[0]), schema_rows)

    counts = Counter(row["admission_status"] for row in audited)
    report = f"""# 可回收火箭不锈钢配方设计：数据准备状态

用户可见服务名：**可回收火箭不锈钢配方设计**。

本包定义的第一版材料范围是 Fe–Cr–Ni–Mn 奥氏体不锈钢薄板/壳体，面向低温贮箱、承力机身和再入热循环。30X 不作为训练材料身份或标签。

## 当前 30X 目录审计

- 记录数：{len(audited)}
- 30X 未披露、阻止准入：{counts['blocked_30x_undisclosed']}
- 301/304L 等参考或待复核不锈钢记录：{counts['reference_only'] + counts['review_for_stainless_lane']}
- 发动机热端等范围外记录：{counts['out_of_scope_engine_hot_end']}

当前没有一条同时具备“可追溯 wt.% 成分 + 工艺状态 + 温度 + 拉伸标签”的壳体钢样本。因此尚未启动回归训练；下一步是逐表抽取 `public_source_extraction_queue.csv` 中的原始文献。

## 互不混训的三条数据线

| 数据线 | 目的 | 不能替代 |
|---|---|---|
| 短时拉伸 | 成分—工艺—温度下的 YS/UTS/延伸率筛选 | 断裂、疲劳、氧相容性 |
| 焊接/断裂/疲劳 | 焊缝和缺陷容限的验证优先级 | 母材拉伸模型 |
| 热物性/环境 | 热—结构仿真输入和 LOX/GOX 门槛 | 机械性能标签 |

## 首轮温度锚点

293 K（制造/地面）、111 K（LCH4）、90 K（LOX）、77 K（LN2 可比试验）以及需要覆盖 LH2 时的 20 K。每条记录必须保留实际测试温度，不能用 77 K 替代 90 K 或 111 K。
"""
    (output / "training_readiness.md").write_text(report, encoding="utf-8")
    (output / "training_readiness.json").write_text(json.dumps({
        "service_name": "可回收火箭不锈钢配方设计",
        "internal_scope": "低温奥氏体不锈钢承压壳体",
        "model_status": "blocked_pending_source_table_extraction",
        "existing_records": len(audited),
        "admission_counts": dict(counts),
        "source_files_queued": len(queue),
        "lanes": LANES,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
