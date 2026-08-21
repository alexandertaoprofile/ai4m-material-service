# P0–P3 材料性质库推进记录（2026-08-14）

## 已完成：P0 常用结构金属

- Ti-6Al-4V（TIMETAL 6-4）已从 `TIMETAL_6-4_Properties.pdf` 的 Table 7 导入 48 条基材板材拉伸证据：退火/固溶时效状态、氧含量、试验温度、屈服、抗拉和延伸率均保留在原始行与条件中。
- Al 6061-T6 保留既有 NIST 已发表导热曲线；其原始弹性系数表仍是公式系数而非直接性质点，待逐项复核公式量纲后再转换，避免把系数当成杨氏模量或 CTE。
- 316L 保留 NIST SRM 1155a 高温热物性身份，未与状态不明的“316 上传表”合并。
- 新增 `scripts/import_316l_pichler_thermal.py` 及严格包 `data/processed/high_temperature/2026-08-14_316l_srm1155a_thermal_v1/`：从同一 SRM 1155a 论文的 Table 6/7 导入密度 24 点（500–2800 K）和 DSC 比热 40 点（473–1253 K）。每点保留源表、页码、温度与不确定度；固/液/两相区仅按该论文 Table 2 的固相线/液相线标签。Table 6 的 `V(T)/V0` 是体积比，不转换为线膨胀系数。

## 已完成：P1 高温合金热—力输入

新增独立的严格导入包：`data/processed/high_temperature/2026-08-14_simulation_properties_v1/`。

- 10 个已确认身份的材料；748 条性质点。
- 新增可追溯字段覆盖：导热系数 207、比热容 179、热膨胀系数 128、杨氏模量 77、剪切模量 56、泊松比 41、热扩散率 12；另含 Ti-6Al-4V 基材拉伸 48 条。
- 导入范围仅限材料状态、表号、页码、单位均明确的基材表；焊接、蠕变、疲劳、腐蚀和压缩表头不清的表继续留在原始库。

### 2026-08-14 P1 续：INCONEL 783 热学表复核

已新增严格增量包：`data/processed/high_temperature/2026-08-14_simulation_properties_v2/`。

- INCONEL 783（UNS R30783）新增 19 条可追溯热学点：退火态导热系数 8 条（21–760 °C）及以 70 °F 为起点的平均线膨胀系数 11 条（93–649 °C）。每条保留来源 Table 5/page 2 或 Table 3/page 1、温度与脚注中的 780 °F 相变拐点说明。
- 原始表中 `W/(m·°C)` 与 `μm/(μm·°C)` 这两种单位已在导入器中受控映射为 `W/(m·K)` 与 `ppm/K`；规则仅匹配这两个完整字段名。
- HAYNES 282 Table 17 与 INCONEL HX Table 4 继续留在 `reports/high_temperature_material_2026-08-14_simulation_review_v2.json`：前者将温度、单位和数值压缩在列名/字符串中，后者的温度区间在 CSV 中发生错位。两者需要重提取或逐行结构化映射，不能以当前表头自动入库。

## P2 连续纤维复合材料：已建立的入库边界

已核验的原始来源包括 Toray Cetex TC1225/T700GC、Hexcel 8552 AS4、Hexcel 8552 IM7 及对应 NCAMP 规范/报告。

复材数值进入可比较目录前，每一条必须包含下列字段；缺任一关键字段时，只能留在待审核队列：

| 字段组 | 必填内容 |
|---|---|
| 构成体系 | 增强体、树脂体系、纤维面密度/树脂含量（如来源给出） |
| 构型与方向 | 单向带/织物、铺层、载荷方向（0°/90°/面内/厚向） |
| 制造状态 | 固化/压实工艺、固化制度、层厚或纤维体积分数 |
| 环境状态 | 干态/湿态、调湿制度、测试温度 |
| 试验 | ASTM/ISO 方法、统计量（平均值/最小值/B 基准等） |
| 溯源 | PDF 名称、页码、表号、原始行/表格定位 |

Toray TC1225 报告的第 27 页已可确认为“TC1225 PAEK + T700GC 12K T1E 单向带、145 gsm、34% 树脂含量、NPS 81225 Consolidate Cycle C”；其数值表还需将每个摘要列与 CTA/RTA/ETA/ETW、铺层和测试类型一一结构化后才允许写入在线候选库。该工作不能把 0° 层板强度伪装成各向同性材料强度。

逐表审计已见 [`toray_tc1225_composite_import_review_2026-08-14.md`](toray_tc1225_composite_import_review_2026-08-14.md)：Table 49/108 已能在 PDF 中定位到明确的测试类型、层合板与环境，但原始 CSV 混有其他页的统计行；下一步需先实现方向专属的复材条件记录，之后才放行这两组首批数据。

首批方向专属复材包已生成：`data/processed/high_temperature/2026-08-14_toray_tc1225_directional_v1/`。它仅含 5 条 PDF 逐页核验的平均值：Table 49（CTA、8 层纵向拉伸）的纵向强度、模量和泊松比；Table 108（CTA、16 层 `50/0/50` 非缺口压缩 0/90）的压缩强度和模量。字段均为方向专属名称，不映射到通用各向同性强度/模量字段，也不参加金属排序。

后续增量包 `data/processed/high_temperature/2026-08-14_toray_tc1225_directional_v2/` 将已核对的表扩展到 18 条方向专属平均值：纵向拉伸覆盖 CTA、RTA、ETA1 与 ETW；`50/0/50` 非缺口压缩 0/90 覆盖 CTA、ETA2 与 ETW。每条依然保留明确层数、方向、环境、测试温度、材料/工艺体系及 PDF 页码。RTA/ETA1 的 UNC0 表尚未出现在已结构化 CSV 中，仍待重抽取，未用相近工况代替。

第三批包 `data/processed/high_temperature/2026-08-14_toray_tc1225_directional_v3/` 将 DMA/DSC 热分析加入同一复材身份：DMA 常温与湿态的 onset/`tan δ` peak Tg，以及 DSC 的 Tg、熔融起始/峰值与结晶起始/峰值温度。它们明确标注为材料热分析样品组平均值，不被写作结构方向模量或无条件设计常数。

## P3 工程塑料/打印材料：当前状态

- 已可查询：拓竹 ASA、PETG、ABS、PLA、PAHT-CF、PC；都保留 FDM X-Y、100% 填充、ISO 方法及厂家来源。
- `polymerproperty.pdf` 与 `耗材详细性能.pdf` 为扫描型 PDF，当前运行环境未提供可信 OCR 文本；不能据截图或猜测补写 PEEK、PEKK、PEI、PPS、PA6/PA12 的数值。
- 下一次可自动放行的数据应至少同时具备：牌号/供应商、注塑或打印方向、调湿状态、测试标准和温度；优先性质为密度、拉伸/弯曲模量与强度、HDT/Tg、CTE、导热、吸水率。
