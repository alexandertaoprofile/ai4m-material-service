"""Read-only audit for an incoming conductive-lubricant data snapshot.

The audit deliberately does not repair, drop, or rename source records.  It
creates a reproducible inventory that informs the later normalisation rules.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TABLES = {
    "fluid_property_source": "material.fluid_property_source.csv",
    "fluid_conductivity": "material.fluid_conductivity.csv",
    "fluid_viscosity": "material.fluid_viscosity.csv",
    "fluid_stability": "material.fluid_stability.csv",
    "fluid_missing_field": "material.fluid_missing_field.csv",
    "fluid_duplicate_record": "material.fluid_duplicate_record.csv",
}

COMMON_CATEGORIES = (
    "source_id",
    "experimental_or_predicted",
    "extraction_method",
    "manual_review_required",
    "pure_component_or_mixture",
)
NUMERIC_FIELDS = (
    "temperature_k",
    "test_temperature_k",
    "conductivity_s_m",
    "resistivity_ohm_m",
    "dynamic_viscosity_mpa_s",
    "kinematic_viscosity_mm2_s",
    "decomposition_temperature_k",
    "melting_temperature_k",
    "glass_transition_temperature_k",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(counter.most_common())


def _profile_csv(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = Counter({field: 0 for field in fields})
        categories = {field: Counter() for field in COMMON_CATEGORIES if field in fields}
        numeric: dict[str, dict[str, float | int | None]] = {
            field: {"count": 0, "min": None, "max": None}
            for field in NUMERIC_FIELDS if field in fields
        }
        composition_basis = Counter()
        mixture_completeness = Counter()
        rows = 0

        for row in reader:
            rows += 1
            for field in fields:
                if not (row.get(field) or "").strip():
                    missing[field] += 1
            for field, counter in categories.items():
                counter[(row.get(field) or "<missing>").strip() or "<missing>"] += 1
            for field, summary in numeric.items():
                value = _as_number(row.get(field))
                if value is None:
                    continue
                summary["count"] = int(summary["count"] or 0) + 1
                summary["min"] = value if summary["min"] is None else min(float(summary["min"]), value)
                summary["max"] = value if summary["max"] is None else max(float(summary["max"]), value)

            if "composition_basis" in fields:
                composition_basis[(row.get("composition_basis") or "<missing>").strip() or "<missing>"] += 1
            if "pure_component_or_mixture" in fields and row.get("pure_component_or_mixture") == "mixture":
                components = [row.get(f"component_{index}") or "" for index in (1, 2, 3)]
                fractions = [row.get(f"component_{index}_fraction") or "" for index in (1, 2, 3)]
                named = sum(bool(value.strip()) for value in components)
                fractioned = sum(bool(value.strip()) for value in fractions)
                mixture_completeness[f"components={named}; fractions={fractioned}"] += 1

    return {
        "file": path.name,
        "sha256": _sha256(path),
        "rows": rows,
        "columns": fields,
        "missing": {field: count for field, count in missing.items() if count},
        "categories": {field: _counter_dict(counter) for field, counter in categories.items()},
        "numeric_ranges": numeric,
        "composition_basis": _counter_dict(composition_basis),
        "mixture_completeness": _counter_dict(mixture_completeness),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 导电润滑介质原始快照审查",
        "",
        f"- 快照目录：`{report['snapshot_dir']}`",
        f"- 审查时间：{report['audited_at']}",
        "- 审查模式：只读；未修改、删除或归一化任何原始记录。",
        "",
        "## 文件清单",
        "",
        "| 逻辑表 | 文件 | 数据行 | SHA-256 |",
        "|---|---|---:|---|",
    ]
    for name, table in report["tables"].items():
        lines.append(f"| {name} | `{table['file']}` | {table['rows']:,} | `{table['sha256']}` |")

    for name, table in report["tables"].items():
        lines += ["", f"## {name}", "", f"字段数：{len(table['columns'])}；数据行：{table['rows']:,}。"]
        if table["categories"]:
            lines += ["", "### 分类字段分布", ""]
            for field, values in table["categories"].items():
                rendered = "；".join(f"{key}：{value:,}" for key, value in values.items())
                lines.append(f"- `{field}`：{rendered}")
        if table["numeric_ranges"]:
            lines += ["", "### 数值覆盖", "", "| 字段 | 非空数 | 最小值 | 最大值 |", "|---|---:|---:|---:|"]
            for field, values in table["numeric_ranges"].items():
                lines.append(f"| `{field}` | {values['count']:,} | {values['min'] if values['min'] is not None else '-'} | {values['max'] if values['max'] is not None else '-'} |")
        if table["composition_basis"]:
            lines += ["", "### 组成基准", ""]
            lines += [f"- `{key}`：{value:,}" for key, value in table["composition_basis"].items()]
        if table["mixture_completeness"]:
            lines += ["", "### 混合物组成完整度", ""]
            lines += [f"- {key}：{value:,}" for key, value in table["mixture_completeness"].items()]

    lines += [
        "",
        "## 下一阶段决策",
        "",
        "1. 仅将单位明确、实验类型明确、温度与组成可比较的记录纳入候选筛选视图。",
        "2. `manual_review_required=yes` 与重复/冲突记录保留为质量标记，不能静默删除。",
        "3. 电导率与黏度必须按来源、组分、组成基准、组成和温度严格匹配；本审查不进行跨表合并。",
        "4. 热分解温度不能替代 135 °C 长期老化证据。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only audit of a fluid-property snapshot")
    parser.add_argument("--input", type=Path, required=True, help="Directory containing downloaded CSV files")
    parser.add_argument("--output", type=Path, required=True, help="Directory for generated audit reports")
    args = parser.parse_args()

    missing = [filename for filename in TABLES.values() if not (args.input / filename).is_file()]
    if missing:
        raise SystemExit(f"missing expected snapshot files: {', '.join(missing)}")

    tables = {name: _profile_csv(args.input / filename) for name, filename in TABLES.items()}
    report = {
        "snapshot_dir": str(args.input.resolve()),
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "snapshot_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "snapshot_audit.md").write_text(_markdown(report), encoding="utf-8")
    print(f"wrote {args.output / 'snapshot_audit.json'}")
    print(f"wrote {args.output / 'snapshot_audit.md'}")


if __name__ == "__main__":
    main()
