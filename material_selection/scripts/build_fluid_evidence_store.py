"""Build a query-ready, traceable evidence store for fluid-property data.

This is a *normalisation* step, not a screening step: it retains experimental
records, source references, composition completeness, and review flags without
embedding any conductive-lubricant thresholds.  The resulting SQLite database
is intended to be queried by a later service layer that translates a user's
question into explicit filters and report specifications.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import xml.sax
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable


RAW_FILES = {
    "conductivity": "material.fluid_conductivity.csv",
    "viscosity": "material.fluid_viscosity.csv",
    "stability": "material.fluid_stability.csv",
    "source": "material.fluid_property_source.csv",
}


def number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE source_registry (
            source_id TEXT NOT NULL, dataset_name TEXT, source_type TEXT,
            publisher_or_repository TEXT, landing_page_url TEXT,
            direct_download_url TEXT, related_publication TEXT, doi TEXT,
            license TEXT, access_date TEXT, notes TEXT,
            source_artifact_id TEXT NOT NULL, source_row_number TEXT NOT NULL,
            PRIMARY KEY (source_id, source_artifact_id, source_row_number)
        );
        CREATE TABLE composition_evidence (
            record_id TEXT PRIMARY KEY, property_name TEXT,
            component_1 TEXT, component_2 TEXT, component_3 TEXT,
            composition_basis TEXT, component_1_fraction REAL,
            component_2_fraction REAL, component_3_fraction REAL,
            composition_complete TEXT, temperature_k REAL, pressure_pa REAL,
            manual_review_required TEXT, manual_review_reason TEXT,
            source_id TEXT, source_reference TEXT
        );
        CREATE TABLE property_evidence (
            evidence_id INTEGER PRIMARY KEY, record_id TEXT NOT NULL,
            property_family TEXT NOT NULL, property_name TEXT NOT NULL,
            value_normalized REAL, normalized_unit TEXT,
            value_original TEXT, original_unit TEXT,
            temperature_k REAL, pressure_pa REAL,
            component_1 TEXT, component_2 TEXT, component_3 TEXT,
            pure_component_or_mixture TEXT, composition_basis TEXT,
            component_1_fraction REAL, component_2_fraction REAL,
            component_3_fraction REAL, viscosity_type TEXT,
            experimental_or_predicted TEXT, extraction_method TEXT,
            manual_review_required TEXT, manual_review_reason TEXT,
            source_id TEXT, source_reference TEXT, source_artifact_id TEXT,
            source_row_number TEXT
        );
        CREATE TABLE stability_evidence (
            evidence_id INTEGER PRIMARY KEY, record_id TEXT NOT NULL,
            component_or_mixture TEXT, pure_component_or_mixture TEXT,
            stability_type TEXT, test_temperature_k REAL, test_time_h REAL,
            atmosphere TEXT, heating_rate_k_min REAL,
            decomposition_temperature_k REAL, melting_temperature_k REAL,
            glass_transition_temperature_k REAL, cloud_point_k REAL,
            phase_count TEXT, miscible TEXT, phase_separation TEXT,
            precipitation TEXT, mass_loss_percent REAL,
            experimental_or_predicted TEXT, extraction_method TEXT,
            manual_review_required TEXT, manual_review_reason TEXT,
            source_id TEXT, source_reference TEXT, source_artifact_id TEXT,
            source_row_number TEXT
        );
        CREATE TABLE transport_pair_evidence (
            pair_id INTEGER PRIMARY KEY, source_id TEXT,
            component_1 TEXT, component_2 TEXT, component_3 TEXT,
            pure_component_or_mixture TEXT, composition_basis TEXT,
            component_1_fraction REAL, component_2_fraction REAL,
            component_3_fraction REAL, temperature_k REAL, pressure_pa REAL,
            conductivity_record_ids TEXT, conductivity_s_m_min REAL,
            conductivity_s_m_max REAL, resistivity_ohm_m_min REAL,
            resistivity_ohm_m_max REAL, viscosity_record_ids TEXT,
            dynamic_viscosity_mpa_s_min REAL, dynamic_viscosity_mpa_s_max REAL,
            conductivity_review_required TEXT, viscosity_review_required TEXT
        );
        CREATE INDEX idx_property_query ON property_evidence
          (property_name, temperature_k, source_id, experimental_or_predicted);
        CREATE INDEX idx_property_record ON property_evidence(record_id);
        CREATE INDEX idx_composition_source ON composition_evidence(source_id, composition_complete);
        CREATE INDEX idx_stability_source ON stability_evidence(source_id, stability_type);
        CREATE INDEX idx_pair_temperature ON transport_pair_evidence(temperature_k, source_id);
        """
    )


class MixtureHandler(xml.sax.ContentHandler):
    """Stream the large inline-string Mixtures sheet without loading XLSX in RAM."""

    def __init__(self, writer: sqlite3.Cursor) -> None:
        super().__init__()
        self.writer = writer
        self.row: dict[str, str] = {}
        self.headers: dict[str, str] | None = None
        self.cell_ref = ""
        self.cell_type = ""
        self.text: list[str] = []
        self.rows = 0

    def startElement(self, name: str, attrs: xml.sax.xmlreader.AttributesImpl) -> None:
        if name == "c":
            self.cell_ref = attrs.get("r", "")
            self.cell_type = attrs.get("t", "")
            self.text = []
        elif name == "t" and self.cell_type == "inlineStr":
            self.text = []

    def characters(self, content: str) -> None:
        if self.cell_ref:
            self.text.append(content)

    def endElement(self, name: str) -> None:
        if name == "c":
            column = "".join(char for char in self.cell_ref if char.isalpha())
            self.row[column] = "".join(self.text)
            self.cell_ref = ""
        elif name == "row":
            if self.headers is None:
                self.headers = {value: column for column, value in self.row.items()}
            else:
                h = self.headers
                get = lambda field: self.row.get(h[field], "")
                self.writer.execute(
                    "INSERT INTO composition_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ",
                    (get("record_id"), get("property_name"), get("component_1"), get("component_2"), get("component_3"),
                     get("composition_basis"), number(get("component_1_fraction")), number(get("component_2_fraction")),
                     number(get("component_3_fraction")), get("composition_complete"), number(get("temperature_K")),
                     number(get("pressure_Pa")), get("manual_review_required"), get("manual_review_reason"),
                     get("source_id"), get("source_reference"),))
                self.rows += 1
            self.row = {}


def load_sources(connection: sqlite3.Connection, path: Path) -> int:
    fields = ("source_id", "dataset_name", "source_type", "publisher_or_repository", "landing_page_url",
              "direct_download_url", "related_publication", "doi", "license", "access_date", "notes",
              "source_artifact_id", "source_row_number")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [tuple(row.get(field, "") for field in fields) for row in csv.DictReader(handle)]
    connection.executemany("INSERT INTO source_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_properties(connection: sqlite3.Connection, path: Path, family: str) -> int:
    rows: list[tuple[object, ...]] = []
    inserted = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            common = (row["record_id"], family, "", None, "", "", "", number(row.get("temperature_k")),
                      number(row.get("pressure_pa")), row.get("component_1", ""), row.get("component_2", ""),
                      row.get("component_3", ""), row.get("pure_component_or_mixture", ""), row.get("composition_basis", ""),
                      number(row.get("component_1_fraction")), number(row.get("component_2_fraction")),
                      number(row.get("component_3_fraction")), "", row.get("experimental_or_predicted", ""),
                      row.get("extraction_method", ""), row.get("manual_review_required", ""), row.get("manual_review_reason", ""),
                      row.get("source_id", ""), row.get("source_reference", ""), row.get("source_artifact_id", ""), row.get("source_row_number", ""))
            if family == "conductivity":
                rows.append(common[:2] + ("conductivity", number(row.get("conductivity_s_m")), "S/m", row.get("conductivity_original", ""), row.get("conductivity_original_unit", "")) + common[7:])
                if number(row.get("resistivity_ohm_m")) is not None:
                    rows.append(common[:2] + ("resistivity", number(row.get("resistivity_ohm_m")), "ohm*m", "", "") + common[7:])
            else:
                viscosity_type = row.get("viscosity_type", "")
                if number(row.get("dynamic_viscosity_mpa_s")) is not None:
                    rows.append(common[:2] + ("dynamic_viscosity", number(row.get("dynamic_viscosity_mpa_s")), "mPa*s", row.get("viscosity_original", ""), row.get("viscosity_original_unit", "")) + common[7:17] + (viscosity_type,) + common[18:])
                if number(row.get("kinematic_viscosity_mm2_s")) is not None:
                    rows.append(common[:2] + ("kinematic_viscosity", number(row.get("kinematic_viscosity_mm2_s")), "mm2/s", row.get("viscosity_original", ""), row.get("viscosity_original_unit", "")) + common[7:17] + (viscosity_type,) + common[18:])
            if len(rows) >= 10000:
                connection.executemany("""INSERT INTO property_evidence
                    (record_id,property_family,property_name,value_normalized,normalized_unit,value_original,original_unit,temperature_k,pressure_pa,component_1,component_2,component_3,pure_component_or_mixture,composition_basis,component_1_fraction,component_2_fraction,component_3_fraction,viscosity_type,experimental_or_predicted,extraction_method,manual_review_required,manual_review_reason,source_id,source_reference,source_artifact_id,source_row_number)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                inserted += len(rows)
                rows = []
    if rows:
        connection.executemany("""INSERT INTO property_evidence
            (record_id,property_family,property_name,value_normalized,normalized_unit,value_original,original_unit,temperature_k,pressure_pa,component_1,component_2,component_3,pure_component_or_mixture,composition_basis,component_1_fraction,component_2_fraction,component_3_fraction,viscosity_type,experimental_or_predicted,extraction_method,manual_review_required,manual_review_reason,source_id,source_reference,source_artifact_id,source_row_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        inserted += len(rows)
    return inserted


def load_stability(connection: sqlite3.Connection, path: Path) -> int:
    fields = ("record_id", "component_or_mixture", "pure_component_or_mixture", "stability_type", "test_temperature_k", "test_time_h", "atmosphere", "heating_rate_k_min", "decomposition_temperature_k", "melting_temperature_k", "glass_transition_temperature_k", "cloud_point_k", "phase_count", "miscible", "phase_separation", "precipitation", "mass_loss_percent", "experimental_or_predicted", "extraction_method", "manual_review_required", "manual_review_reason", "source_id", "source_reference", "source_artifact_id", "source_row_number")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [tuple(number(row.get(f)) if f in {"test_temperature_k", "test_time_h", "heating_rate_k_min", "decomposition_temperature_k", "melting_temperature_k", "glass_transition_temperature_k", "cloud_point_k", "mass_loss_percent"} else row.get(f, "") for f in fields) for row in csv.DictReader(handle)]
    connection.executemany("""INSERT INTO stability_evidence
        (record_id,component_or_mixture,pure_component_or_mixture,stability_type,test_temperature_k,test_time_h,atmosphere,heating_rate_k_min,decomposition_temperature_k,melting_temperature_k,glass_transition_temperature_k,cloud_point_k,phase_count,miscible,phase_separation,precipitation,mass_loss_percent,experimental_or_predicted,extraction_method,manual_review_required,manual_review_reason,source_id,source_reference,source_artifact_id,source_row_number)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def load_mixtures(connection: sqlite3.Connection, workbook: Path) -> int:
    handler = MixtureHandler(connection.cursor())
    with zipfile.ZipFile(workbook) as archive:
        with archive.open("xl/worksheets/sheet5.xml") as sheet:
            xml.sax.parse(sheet, handler)
    return handler.rows


def load_transport_pairs(connection: sqlite3.Connection) -> int:
    groups: dict[tuple[object, ...], dict[str, list[sqlite3.Row]]] = defaultdict(lambda: defaultdict(list))
    for row in connection.execute("""SELECT source_id, component_1, component_2, component_3, pure_component_or_mixture,
                    composition_basis, component_1_fraction, component_2_fraction, component_3_fraction,
                    temperature_k, pressure_pa, property_name, record_id, value_normalized,
                    manual_review_required FROM property_evidence
             WHERE property_name IN ('conductivity', 'dynamic_viscosity')
               AND experimental_or_predicted='experimental' AND value_normalized > 0"""):
        groups[tuple(row[:11])][row[11]].append(row)
    inserted = 0
    for key, values in groups.items():
        conductivity, viscosity = values["conductivity"], values["dynamic_viscosity"]
        if not conductivity or not viscosity:
            continue
        c_values, v_values = [r[13] for r in conductivity], [r[13] for r in viscosity]
        resistivities = [1 / value for value in c_values]
        connection.execute("""INSERT INTO transport_pair_evidence
          (source_id,component_1,component_2,component_3,pure_component_or_mixture,composition_basis,component_1_fraction,component_2_fraction,component_3_fraction,temperature_k,pressure_pa,conductivity_record_ids,conductivity_s_m_min,conductivity_s_m_max,resistivity_ohm_m_min,resistivity_ohm_m_max,viscosity_record_ids,dynamic_viscosity_mpa_s_min,dynamic_viscosity_mpa_s_max,conductivity_review_required,viscosity_review_required)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          key + ("|".join(sorted({r[12] for r in conductivity})), min(c_values), max(c_values), min(resistivities), max(resistivities), "|".join(sorted({r[12] for r in viscosity})), min(v_values), max(v_values), "yes" if any(r[14] == "yes" for r in conductivity) else "no", "yes" if any(r[14] == "yes" for r in viscosity) else "no"))
        inserted += 1
    return inserted


def write_contract(output: Path, counts: dict[str, int]) -> None:
    output.write_text(f"""# 导电润滑介质规范证据库：查询与可视化契约

此 SQLite 文件是**证据库**，不是“合格材料清单”。建立时间的记录数如下：

| 表 | 记录数 |
|---|---:|
| `source_registry` | {counts['sources']:,} |
| `composition_evidence` | {counts['compositions']:,} |
| `property_evidence` | {counts['properties']:,} |
| `stability_evidence` | {counts['stability']:,} |
| `transport_pair_evidence` | {counts['pairs']:,} |

## 服务侧的职责边界

1. 将用户问题解析为显式条件：性质、数值范围、温度/压力、对象类型、是否仅实验数据、是否接受待人工复核记录。
2. 用这些条件查询 `property_evidence` 或 `transport_pair_evidence`；只在用户提出项目标准时才应用阈值。
3. 以 `composition_evidence.composition_complete`、`manual_review_required` 和来源字段决定证据等级，不能静默剔除或补全数据。
4. `stability_evidence` 的热分解、相行为等记录仅按其实际测试含义展示；不能被自动表述为长期服役或机理验证。

## 报告式可视化的推荐数据源

- **筛选漏斗**：每一步查询条件的命中数；步骤必须随用户问题动态生成。
- **性能散点图**：`transport_pair_evidence`，横轴动态黏度、纵轴电导率；颜色可映射温度或热稳定性证据等级，形状映射数据质量。
- **温度曲线**：`property_evidence` 按相同配方/条件分组，显示原始点与来源，不伪造插值。
- **候选证据卡**：配方、测试条件、数值范围、质量标记、原始记录 ID、来源链接。
- **数据缺口图**：以属性/来源/温区/组分完整性统计，说明为何某些问题不能下结论。

所有图都应由一次具体查询的结果生成 PNG/SVG，并连同图的条件、版本与记录 ID 一起保存，才能达到报告式结果的可复现性。
""", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build query-ready fluid-property evidence SQLite store")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True, help="summary_tables.xlsx containing Mixtures sheet")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = [args.input / name for name in RAW_FILES.values()]
    if any(not path.is_file() for path in required) or not args.workbook.is_file():
        raise SystemExit("missing required source CSV or workbook")
    if args.output.exists():
        args.output.unlink()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output)
    try:
        create_schema(connection)
        counts = {"sources": load_sources(connection, args.input / RAW_FILES["source"])}
        counts["properties"] = load_properties(connection, args.input / RAW_FILES["conductivity"], "conductivity")
        counts["properties"] += load_properties(connection, args.input / RAW_FILES["viscosity"], "viscosity")
        counts["stability"] = load_stability(connection, args.input / RAW_FILES["stability"])
        counts["compositions"] = load_mixtures(connection, args.workbook)
        counts["pairs"] = load_transport_pairs(connection)
        connection.commit()
    finally:
        connection.close()
    write_contract(args.output.with_name("evidence_store_contract.md"), counts)
    print(f"wrote {args.output}")
    print(counts)


if __name__ == "__main__":
    main()
