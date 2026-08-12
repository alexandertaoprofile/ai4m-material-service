# 高温材料数据解析交接（2026-08-06）

## 当前目标

将 1101 高温材料库中可追溯的原始 PDF/表格整理为成熟材料服务可查询的证据；只导入材料身份、测试状态、单位和来源表号明确的基材记录。焊缝、填充金属、对比表、综述/多材料论文不混入基础材料记录。

## 数据版本

- 当前应使用新版快照：`data/raw/incoming/material_platform/2026-08-06_high_temperature_header_v2/`
  - 331 张 CSV 表、5,193 行、下载完整（无 `.INCOMPLETE`）。
  - 58 个 `lineage_document_name` 可与 `/data/se42/docs/property datasets/<name>.pdf` 精确对应。
- 旧快照保留作审计回退：`data/raw/incoming/material_platform/2026-08-06_high_temperature_material_v1/`。
- 新版表名更可读，但并未全局统一字段；仍需依赖白名单表号和来源 PDF。

## 已完成

### 解析基础设施

- `scripts/import_high_temperature_evidence.py`
  - 支持受控 document registry、`--registry-only` 白名单导入。
  - 将 `ksi` 转为 `MPa`；若同一观察已有 `MPa` 列则不会重复导入 `ksi`。
  - 支持 `ultimate_tensilestrength` 等表头变体。
  - 保留 RT/室温等非数值温度标签，不伪造为 25°C。
  - `--registry-only` 逐行校验 document ID 与 source table ID；合并多 PDF 的 CSV 不会夹带未白名单的行。
- `data/processed/high_temperature/document_registry_2026-08-06.json`
  - 记录 PDF 身份、材料 ID、别名、允许导入的源表 ID 与导入范围。

### 已发布到严格导入包的基材证据

目录：`data/processed/high_temperature/2026-08-06_document_mapped_v1/`

| 材料 | 材料 ID | 证据数 | 已放行表 |
|---|---|---:|---|
| INCONEL 718 | `MAT-IN718` | 24 | Table 19/20，page 10 |
| HAYNES 188 | `MAT-1101-HT-H188` | 14 | Table 4，page 5 |
| HAYNES 214 | `MAT-1101-HT-H214` | 18 | Table 12，page 18 |
| HAYNES 244 | `MAT-1101-HT-H244` | 40 | 基材 plate/sheet tensile，page 4/5 |
| HAYNES 25 | `MAT-1101-HT-H25` | 64 | 冷作 sheet tensile，page 5/6 |
| HAYNES 263 | `MAT-1101-HT-H263` | 4 | solution-annealed sheet/plate tensile，page 9 |
| HAYNES 282 | `MAT-1101-HT-H282` | 36 | 基材 sheet/plate tensile，page 7 |
| HAYNES 556 | `MAT-1101-HT-H556` | 14 | hot-rolled、solution-annealed plate tensile，page 18 |
| HAYNES HR-120 | `MAT-1101-HT-HR120` | 28 | solution heat-treated sheet/plate tensile，page 4 |
| HASTELLOY C-2000 | `MAT-1101-HT-HC2000` | 42 | 基材 sheet/plate/bar tensile，page 15 |
| HASTELLOY C-22 | `MAT-1101-HT-HC22` | 48 | 基材 sheet/plate/bar tensile，page 15 |
| INCONEL C-276 | `MAT-1101-HT-INC276` | 36 | 基材 sheet/plate/bar tensile，page 14 |
| INCONEL 693 | `MAT-1101-HT-IN693` | 4 | Table 5，室温 plate/tubing tensile，page 2 |
| INCONEL 725 | `MAT-1101-HT-IN725` | 30 | Table 7，annealed-and-aged rod 高温 tensile，page 4 |
| INCONEL 601 | `MAT-1101-HT-IN601` | 10 | Table 8，solution-treated 基材 tensile，page 4 |
| INCONEL 625 | `MAT-1101-HT-IN625` | 26 | Table 10，as-drawn wire 室温 tensile，page 12 |
| INCONEL 22 | `MAT-1101-HT-IN22` | 2 | Table 5，ASTM limiting tensile，page 2 |
| INCONEL 690 | `MAT-1101-HT-IN690` | 14 | Table 5，annealed tensile，page 3 |
| INCONEL 783 | `MAT-1101-HT-IN783` | 10 | Table 6，高温 tensile，page 2 |
| INCONEL X-750 | `MAT-1101-HT-INX750` | 80 | Tables 8/9/11，状态明确的棒材 tensile |
| HAYNES 233 | `MAT-1101-HT-H233` | 16 | solution-annealed plate/sheet tensile |
| HAYNES 242 | `MAT-1101-HT-H242` | 54 | annealed-and-aged / mill-annealed tensile |
| HAYNES 75 | `MAT-1101-HT-H75` | 12 | bright-annealed tensile |
| HAYNES R-41 | `MAT-1101-HT-HR41` | 16 | solution-annealed / heat-treated tensile |
| INCONEL G-3 | `MAT-1101-HT-ING3` | 4 | annealed sheet/plate tensile |
| HAYNES Ti-3Al-2.5V | `MAT-1101-HT-TI325` | 9 | minimal mechanical properties |

当前合计 681 条 property points，覆盖 27 个材料身份（其中 INCONEL 718 复用已有目录材料，导入包新增 26 条材料记录）。每条均保留 PDF 文档 ID、表号、页码和原始 CSV 行。

2026-08-10 新放行两份单一材料厂商表：

- INCONEL 600（UNS N06600 / W. Nr. 2.4816）：Table 7，page 4，hot-rolled rod 的室温拉伸，14 点（7 个热处理状态 × tensile/yield）。Table 9 仍排除：压缩表头混合原始单位列；fatigue、creep 等表未导入。
- INCONEL 686（UNS N06686 / W. Nr. 2.4606）：Table 6，page 2，0.25 in plate、三炉批平均的高温拉伸，12 点（6 个测试温度 × tensile/yield）。热处理状态在来源中未报告，按缺失保留，未推断。
- INCONEL 693（UNS N06693）：Table 5，page 2，名义室温 tensile，4 点（hot-rolled-and-annealed plate、cold-drawn-and-annealed tubing 各 tensile/yield）。导入器现将 `product_form` 与其他状态字段同样写入每条 evidence 的 `condition`，不混合两种产品形态。
- INCONEL 725（UNS N07725）：Table 7，page 4，0.625–6.5 in diameter rod 的高温 tensile，30 点（15 个测试温度 × tensile/yield）。每条都保留 caption 中的 annealed + aged 以及脚注中的固溶/两段时效制度；其他表没有放行。
- INCONEL 601（UNS N06601 / W. Nr. 2.4851）：Table 8，page 4，solution-treated rod、flat、sheet、pipe、tubing 的典型 tensile，10 点（5 个产品形态 × tensile/yield）。原先待修的 Table 9 仍排除，因压缩表头混合单位。
- INCONEL 625（UNS N06625 / W. Nr. 2.4856）：Table 10，page 12，as-drawn wire 的室温 tensile，26 点（13 行 × tensile/yield）。wire diameter 与 cold reduction 进入条件文本；caption footnote 中的特殊 strand-annealed 说明也逐条保留。焊接 Table 15、Table 11 和其他非基材表均未放行。
- INCONEL 22（UNS N06022）：Table 5，page 2，ASTM B 574/B 575 bar、plate、sheet、strip 的 limiting tensile，2 点。该表只给出标准限定值，来源状态如实保留，未伪造成一般典型值。
- 批量放行的单一材料厂商表：INCONEL 690（Table 5，annealed form tensile，14 点）、INCONEL 783（Table 6，按测试温度 tensile，10 点）、INCONEL X-750（Tables 8/9/11，明确热处理的 hot-rolled round/bar tensile，80 点）、HAYNES 233（solution-annealed plate/sheet，16 点）、HAYNES 242（annealed-and-aged bar/ring/plate/sheet 及单列 mill-annealed form 表，54 点）、HAYNES 75（bright-annealed，12 点）、HAYNES R-41（solution-annealed 与 caption 明示热处理的 tensile，16 点）、INCONEL G-3（annealed sheet/plate 的 minimum tensile，4 点）、HAYNES Ti-3Al-2.5V（按 condition/specification 的 minimal mechanical properties，9 点）。

`scripts/import_high_temperature_evidence.py` 现会保留 `product_form`、`heat_treatment_*` 形式的来源列，例如 INCONEL 600 的°F/°C热处理标签；caption 与非空 footnote 也进入每条 evidence 的 condition 文本，避免跨热处理状态混用或丢失例外说明。

HAYNES 25 已以首页身份 `HAYNES 25 alloy; UNS R30605` 核验；仅放行 page 5 的 cold-worked sheet 和 page 6 的 cold-worked-and-aged sheet 拉伸表。热暴露表（page 7）及焊接拉伸表（page 15）仍未导入。导入器现会在同一张源表中继承分组首行的 `condition`、`form`、`material_condition`、`heat_treatment` 和 `cold_reduction` 到证据条件文本；原始 CSV 行本身未改写。

HAYNES 263 已以首页身份 `HAYNES 263 alloy; UNS N07263` 核验；仅放行 page 9 的 solution-annealed sheet/plate room-temperature tensile 表（4 个 MPa 强度点）。Thermal Stability 表包含 8,000 小时热暴露状态，按第二阶段条件模型规则保持排除；其余 creep、hardness、welding 表也未导入。

HAYNES 556 已以首页身份 `HAYNES 556 alloy; UNS R30556` 核验；仅放行 page 18 的 hot-rolled and solution-annealed plate tensile 表（14 个 MPa 强度点）。page 20 的热暴露后室温 tensile、page 19 creep 与 page 24 weld tensile 表均未导入。该次审查还修正了白名单实现：一个 556 CSV 同时含有 718 行，现已由逐行 document/table 校验排除；重建断言确认包内没有该 718 文档 ID。

HAYNES HR-120 已以首页身份 `HAYNES HR-120 alloy; UNS N08120` 核验；仅放行 page 4 的 solution heat-treated sheet 和 plate tensile 表（28 个 MPa 强度点）。Thermal Stability（page 6）、comparative strength（page 5）、creep 与 welding 表均未导入。

HAYNES HR-160 已以首页身份 `HAYNES HR-160 alloy; UNS N12160` 核验并登记，但未放行任何表。现有 tensile CSV 是 page 23 的 all-weld-metal 数据；其余为 creep、comparative、metallurgy 和 stress-corrosion 表，均不符合当前基材常规 tensile 导入范围。

HAYNES HR-224 已以首页身份核验并登记，但未放行任何表。现有表为热暴露后室温性质、蠕变及多材料 strain-age cracking 比较，不是常规基材 tensile 表。

本批放行三份单一材料基材拉伸手册：HASTELLOY C-2000（UNS N06200，page 15，42 点）、HASTELLOY C-22（UNS N06022，page 15，48 点）和 INCONEL C-276（UNS N10276 / W. Nr. 2.4819，page 14，36 点）。这些原始 CSV 合并了其他 PDF 的行，已依靠逐行 document/table 白名单隔离。HASTELLOY C-22 与已登记的另一厂牌 UNS N06022 记录保持分离，避免按相同 UNS 自动合并厂牌身份。

INCONEL 601（UNS N06601）身份已确认但仍未放行。其 Table 9 虽是 annealed hot-finished rod 基材拉伸表，当前 CSV 却将原表的 °F/°C 和 ksi/MPa 表头行压缩为同名字段，导致后半段 MPa 值会被误作 ksi；已撤回白名单，待重新抽取该表。

### 服务接入状态

- 服务仍只有两条工作流：通用成熟材料目录和导电润滑剂初筛（仅在润滑与导电意图同时明确时选择）。高温材料不是第三条工作流；`src/catalog/query.py` 会自动载入 `data/processed/high_temperature/*/` 下的严格导入包。
- 通用目录已新增 `抗拉强度`、`极限抗拉强度`、`UTS` 到 `ultimate_tensile_strength` 的属性映射，并有 HAYNES 556、649°C、600 MPa 阈值的端到端回归。不要将泛称 `拉伸强度` 自动改为 UTS，它仍保留为独立的 `tensile_strength`，避免错误合并。
- 通用目录现正式使用 `mature_material_catalogue_initial_screen`：manifest 会保存 `screening_request`、候选评估数、符合数和证据策略，与导电润滑剂的 `conductive_lubricant_initial_screen` 一样可审计；仍保持既有 `catalog_matched` 等业务结果及前端事件协议。对应回归已加入 `tests/test_mature_material_service.py`。
- 开放式请求（如“帮我挑选一款成熟金属材料”）会进入 `needs_screening_criteria`，不再误报“未识别材料名称”。页面会引导提供应用、服役温度、介质/环境、制造形式、材料体系/牌号或性能阈值；未满足可检索条件前不猜测或推荐材料。端到端回归覆盖该原句。
- 通用初筛现在按明确维度数量分级：0 项 `criteria_collection`（仅追问）；1 项 `evidence_landscape`（单条件证据地图）；2 项 `cross_filter`（交叉过滤）；3 项以上 `strict_evidence_screen`（严格筛选）。每个独立性能目标都计作一个维度，故“导热率≥100 W/(m·K)；屈服强度≥600 MPa”是两维交叉筛选，不是单一泛化条件。通用服务会从自然语言中识别这种“指标 + 比较符 + 数值 + 单位”的硬阈值；不完整的裸数字/定性描述仍只追问，绝不猜测。单位 `MPa` 已排除出材料缩写提取，避免被错误显示为待匹配材料名。`screening.next_action=await_user_criteria` 明确禁止上游在待条件状态下假设“高温高强”、生成候选族或转入文献检索；该状态应继续收集条件。回归覆盖四种模式和该自然语言双阈值句。
- 自然语言性能区间也已识别：`屈服强度600-800MPa及导热率300-350W/(m·K)` 会分别展开为屈服强度 `>=600` / `<=800 MPa` 和导热率 `>=300` / `<=350 W/(m·K)`；这仍是两个独立性能维度的 `cross_filter`，不是四个无关维度。该规则支持 `-`、`–`、`~`、`至`、`到` 等常见区间连接符，并有与实际上游“接下来需要进行执行的任务”摘要同形的端到端回归。
- 已将导电液体与通用目录共用的比较符/区间展开逻辑下沉到 `src/screening_language.py`；两边复用同一套 `>=`/`<=` 判定和区间转换，保留导电液体原有的“介于 1 Ω·m 和 10 Ω·m 之间”兼容。属性词典、单位白名单、默认配置和实际证据查询仍按各体系隔离，避免把导电油语义错误泛化到固体材料。
- 修复了上游执行摘要的 PEEK/碳纤维案例：`导热≥10 W/(m·K)` 此前因只写“导热”而未被识别，`层间/界面结合力≥20 MPa` 也缺少通用属性映射，且 PEEK 未作为材料锚点进入查询。现已支持这三类表达及 LaTex 单位包装（如 `\\text{W/(m·K)}`）；指定 PEEK 无目录记录或界面结合力未入库时会显示实际缺失，不会回退成 `needs_screening_criteria`。材料缩写仍只从短直接请求或最后的执行任务子句中恢复，避免长历史的 PLA/ASA 等旧别名污染本轮。
- 性质解析已从个别补丁升级为 `src/catalog/property_vocabulary.py` 词典。首批映射覆盖密度；拉伸/屈服/压缩/弯曲/剪切/界面结合/疲劳强度；弹性与剪切模量、硬度、延伸率；导热、比热、热扩散、热膨胀、HDT、Tg；电导/电阻、介电；吸水、表面粗糙度、成本。`PROPERTY_ALIASES`、通用自然语言阈值解析和前端显示标签共享该词典。识别到但尚无目录数据的属性按 `missing` 进入漏斗，绝不能被忽略或伪造成已有证据；跨域的压缩强度/CTE/Ra 回归已覆盖。
- 通用目录的约束筛选展示已迁移为与导电液体一致的初筛报告形态：不再先显示“材料名称核对”和全量性质清单，而是显示筛选条件、筛选漏斗、`pass/fail/missing` 状态汇总、候选核验与结论；始终发布与导电液体共用锥形视觉模板的 `evidence_funnel` 图（即使某项数据全缺失、漏斗归零），有可比较数值时再发布 `property_comparison` 分布图。分布图用绿色/橙色标识该性质通过/不通过，用红色虚线显示阈值边界。无硬阈值的普通材料查名仍保留旧式已入库性质查看，避免伪造筛选流程。
- “越高越好/越低越好”已实现为 `preference_goals`，而非伪造阈值。通用目录会将抗拉/屈服强度、导热率、硬度、延伸率、密度等方向偏好用于证据排序；导电液体会将电导率最大化、电阻率或动态黏度最小化用于排序。偏好与硬阈值并存时先过滤再排序；只有偏好时分别返回 `catalogue_evidence_landscape` / `fluid_evidence_landscape`，不宣称工程通过或推荐。回归覆盖通用和导电液体两条路径。
- 通用目录也识别简写的定性方向目标：`高散热`/`高导热` 转为导热率最大化，`高硬度`，以及“高散热、硬度”的并列省略表达转为硬度最大化。它们不会被伪造成数值阈值；页面显示“排序目标 + 证据覆盖漏斗 + 候选排序核验”，而不是退回空的初筛条件收集。`机器人` 作为应用上下文保留；`STL` 仅记录为几何参考、制造工艺待确认，绝不擅自假定 3D 打印工艺。新增端到端回归覆盖此输入。
- 硬阈值筛选若无候选完整通过，也不丢失过程：通用目录返回 `catalogue_no_eligible_candidates`，保留已扫描候选、每个指标的 `pass/fail/missing` 计数和原始约束；导电液体返回 `fluid_no_matching_evidence`，保留温度、电学、黏度和证据质量的完整筛选漏斗及归零位置。二者都不擅自放宽阈值、替换材料或推荐配方。回归覆盖两条无匹配路径。

## 已确认身份、尚未放行

registry 已登记但 `include_source_table_ids` 为空的主要材料包括：INCONEL 740H、MULTIMET，以及其他已核验 PDF。原因是表中仍混有焊缝、填充金属、对比材料、热暴露后室温数据或缺少结构化状态字段。

本轮盘点到、但必须先走专用解析或条件模型的原始表包括：HASTELLOY N（分组产品形态与热处理没有稳定下传）；INCONEL N06230（tensile 单元格为范围字符串，现有数值导入器不会猜测单值）；HASTELLOY S（首行 UTS/YS 语义疑似错位）；HASTELLOY X（低温温度列 °F/°C 不一致）；740H（CSV 压缩表头以及 weldment 与基材混杂）；以及所有 Toray/连续纤维复材、316L 论文、钛合金设计表、3D 打印论文等。这些都不应因扩大覆盖率而直接写入严格目录。

## 推荐后续顺序

1. 继续厂商单一材料手册：其余 HASTELLOY 系列、INCONEL X-750、625、686、693、725 等。
2. 对每份 PDF：先核验首页材料名称/UNS，再列出所有 CSV 的页码、caption、字段；只将明确的基材 tensile 表加入 registry 白名单。
3. 解析前检查：排除 caption 中的 `weld`、`filler`、`weld metal`、`comparative`、`dissimilar` 等表。
4. 对热暴露表建立第二阶段条件模型：至少区分 `test_temperature`、`exposure_temperature`、`exposure_time`、`product_form`、`heat_treatment`，完成前不将其与常规基材点混合。
5. 多材料/论文类（`main.pdf` 高熵合金、Toray 复材、316L 论文、蠕变机理论文）单独建解析器，不使用当前手册导入器。

### 复合材料/3D 打印现状

- 当前基础目录已包含拓竹 ASA、PETG、ABS、PLA Pure、PC 及 PAHT-CF（FDM、X-Y 方向、100% 填充）；PAHT-CF 是现有碳纤维增强 3D 打印条目。
- 当前 `incoming` 快照中没有新的 PEEK-CF、连续纤维复材或 Toray 等复合材料的可直接严格导入原始表；这些仍需按上述单独解析器路线处理，不能从文献建议或通用牌号推断入库。

## 重建命令

```bash
cd /data/se42/alpha_project/material_service_hub/material_database
rm -rf data/processed/high_temperature/2026-08-06_document_mapped_v1
rm -f reports/high_temperature_material_2026-08-06_mapping_queue_v2.json
PYTHONPATH=. python scripts/import_high_temperature_evidence.py \
  --input data/raw/incoming/material_platform/2026-08-06_high_temperature_header_v2 \
  --output data/processed/high_temperature/2026-08-06_document_mapped_v1 \
  --review-output reports/high_temperature_material_2026-08-06_mapping_queue_v2.json \
  --document-registry data/processed/high_temperature/document_registry_2026-08-06.json \
  --registry-only
PYTHONPATH=. pytest -q tests/test_mature_material_service.py
```

最后一次回归：`46 passed, 4 subtests passed`（批量放行 INCONEL 690/783/X-750、HAYNES 233/242/75/R-41、G-3、Ti-3Al-2.5V 后；27 个材料身份、681 条 property points）。
