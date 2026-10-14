# 机械臂与增材制造材料库扩展留痕（2026-08-25）

## 本批目的

将成熟材料服务的覆盖重点从高熵合金与高温镍基合金，扩展至机械臂结构、关节传动/热管理，以及 FDM/连续纤维复材相关的成熟商品材料。在线目录只接收可回查到原始文件、页码/行号、材料状态和测试条件的数值；缺少其中任一关联的数据保留在审核清单，不进入筛选或前端候选表。

## 本批已入库的可追溯证据

| 组别 | 新增身份 | 原始证据 | 状态隔离 | 前端呈现 |
|---|---|---|---|---|
| 2：工程打印聚合物 | Stratasys ULTEM 9085 Natural | `MDS_Stratasys_ULTEM_9085_0925A.pdf`，第 6–8 页，SHA-256 写入 manifest | F900、T16、0.254 mm、XZ/ZX 方向；ZX 数值单列 | 复用既有“材料性质汇总”卡与来源/条件列 |
| 2/3：短纤与功能聚合物 | Markforged Onyx、Onyx FR、Onyx ESD、Nylon White | `Markforged_Composites_Data_Sheet.pdf`，第 1 页，SHA-256 写入 manifest | FFF 全填充、仅基材、无连续纤维 | 同上 |

每个包均保留独立 `import_manifest.json`，其中包含来源 URL/文件、哈希、页码、导入计数以及明确的排除理由。

## 2026-08-25：1–4 组 D 级工程估算种子

用户已明确允许在材料身份、制造状态和适用范围定义清楚时，将缺失性质以 D 级工程估算纳入材料卡。当前运行包为 `data/processed/material_core/2026-08-25_robot_arm_groups_1_4_d_estimates_v2/`：26 个身份、149 条独立估算记录，覆盖 7075/2024/6082、17-4PH、42CrMo/4140、齿轮钢、CuCrZr/C11000、A356/AlSi10Mg/AZ91、PA11/PA12/PA12-CF/PP/TPU/PEKK/PPS、PPS-CF/PC-CF/CF-PEKK/CF-PPS，以及 T700/环氧与 IM7/8552。PA12 与 PA12-CF 已拆分身份，不能再互相命中。

估算记录单独存于 `engineering_estimates.csv`，包含低/高值、单位、20–100 °C 初始温区、材料/制造状态、估算依据、版本和替换条件。查询层不读取该文件进行 `evaluate()` 或排序；展示层将其标为 `D：模型/工程估算，不能用于通过判断；不参与筛选/排序`。这允许先完成材料索引与前期敏感性分析，同时避免将估算升级为目录事实。

## 2026-08-25：优先材料来源包（v9）

运行包 `data/processed/material_core/2026-08-25_robot_arm_priority_sources_v9/` 将 15 个明确构型的商品材料身份与 88 条来源性质写入服务。原始 PDF 位于 `data/raw/incoming/robot_arm_priority/2026-08-25/`；每份 PDF 的 SHA-256、文件名、页码与导入计数均在该包的 `import_manifest.json` 留存。因公开下载链接失效或受限的 Toray T700S/环氧和 EOS AlSi10Mg，保留原厂页面 URL、访问日期、产品/工艺条件，前端按“部分工况待补”呈现，不能当作无条件设计许用值。

| 体系 | 当前来源支持的材料身份 | 关键状态边界 |
|---|---|---|
| 结构/传动金属 | 7075-T6、2024-T3、17-4PH H900、42CrMo4 调质、20MnCr5、CuCrZr、C11000、AlSi10Mg、AZ91C-T4 | 20MnCr5 的密度/导热来自同一 Ovako 牌号，但强度必须绑定渗碳/淬回火及芯部状态；7075/C11000 的原始硬度为 HRB，单独展示，不与 HRC/HV 的通用硬度筛选混排 |
| 打印复材 | AON3D PEKK、PA12-CF、PPS-CF、CF/PEKK（AS4，60% 体积分数） | AON3D PEKK 保留 XY/ZX、打印条件；PPS-CF 保留 XY/Z 向；CF/PEKK 是打印并固结的连续纤维复材，不标成 FDM 短纤材料 |
| 连续纤维环氧 | T700S/环氧、IM7/8552 | 均保留 60% 纤维体积分数、0°方向、固化/干湿与测试标准；不能外推至任意铺层或纤维体积分数 |

## 四组覆盖的执行队列

| 组别 | 目标材料/体系 | 在线入库前必须具备的证据 | 当前状态 |
|---|---|---|---|
| 1：机械臂结构与传动金属 | 7075-T6、2024-T3、6082-T6、17-4PH、42CrMo/4140、20MnCr5/18CrNiMo7-6、CuCrZr、C11000、A356、AlSi10Mg | 指定牌号、产品形式、热处理、测试温度、来源页码；增材件另含打印方向/HIP 状态 | 7075、2024、17-4PH、42CrMo4、CuCrZr、C11000、AlSi10Mg、AZ91 已有来源支持；20MnCr5 已补密度/导热；6082、18CrNiMo7-6、A356 的同状态来源待补 |
| 2：增材聚合物 | PA12/PA11、PP、TPU、PEKK、PEI、PPS/PPS-CF、光固化韧性/耐温/弹性树脂 | 制造路线（FDM/SLS/SLA）、填充/方向、调湿/后固化、测试方法与温度 | ULTEM 9085、PEKK、PA12-CF、PPS-CF 已有来源支持；PA12/PA11/PP/TPU/PPS 保持 D 级预建模身份 |
| 3：碳纤维和连续纤维复材 | T700/环氧、IM7/8552、CF-PEKK、CF-PPS、PA12-CF、PC-CF | 树脂/纤维、纤维体积分数、铺层/方向、固化或打印条件、环境、标准和页码 | T700/环氧、IM7/8552、CF/PEKK、PA12-CF 已有来源支持；CF-PPS、PC-CF 保持 D 级预建模身份 |
| 4：热管理与铸造/轻量化分支 | CuCrZr、C11000、A356、AZ91，以及其制造状态 | 成分牌号、状态、导热/电导/力学的同源条件 | CuCrZr/C11000 已有来源支持；A356/AZ91 保持 D 级预建模身份 |

## 明确不入库的项目

- 仅有网页摘要、没有可留存原始 PDF/CSV 的材料；
- 未指明热处理、打印方向、调湿或层合板构型的复材/增材机械性能；
- 连续纤维“材料名称正确但树脂/铺层未知”的汇总数字；
- 由相近牌号、通用树脂或工程经验补齐的数值。

## 前端一致性

本批没有增加接口、事件、步骤 ID 或图片类型。材料仍经 `MatureMaterialCatalog` 查询并由既有 `src/catalog/presentation.py` 输出：身份、条件、性质、来源均在同一张材料性质汇总表中展示。新增方向专属属性维持既有命名约定（如 `z_axis_*`），不会被当成各向同性材料数值参与通用筛选。

## 复现命令

```bash
python scripts/import_stratasys_ultem_9085.py \
  --source data/raw/incoming/official_print_filaments/2026-08-25/MDS_Stratasys_ULTEM_9085_0925A.pdf \
  --output data/processed/material_core/2026-08-25_stratasys_ultem_9085_v1
python scripts/import_markforged_composite_bases.py \
  --source data/raw/incoming/official_print_filaments/2026-08-25/Markforged_Composites_Data_Sheet.pdf \
  --output data/processed/material_core/2026-08-25_markforged_composite_bases_v1
python scripts/build_robot_arm_estimate_seed.py \
  --output data/processed/material_core/2026-08-25_robot_arm_groups_1_4_d_estimates_v2
python scripts/import_robot_arm_priority_sources.py \
  --source-dir data/raw/incoming/robot_arm_priority/2026-08-25 \
  --output data/processed/material_core/2026-08-25_robot_arm_priority_sources_v9
PYTHONPATH=. pytest -q tests/test_mature_material_service.py
```
