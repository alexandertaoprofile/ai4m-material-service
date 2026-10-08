# 服务推理与研究复现需要的不同资源

“运行服务”是加载已有模型处理新请求；“重新训练”还要重建数据、特征、划分和训练过程。当前[运行资源包](../resources/README.md)面向服务推理及已有 W-14 证据读取。本页记录研究复现的追加资源，便于后续按实验分别交接。

## 已交接的运行内容

| 服务 | 可取得的内容 | 原服务器来源 |
| --- | --- | --- |
| 1105 | 通用整理 CSV；专项 SQLite 快照；查询和报告代码。 | 服务目录 `material_database/data/processed`。 |
| 1111 | 八类专项的运行源码、权重和推理时使用的数据；固定优化包及报告模块。 | `/data/se42/hea_surrogate` 与两个版本化计算目录。 |
| 1115 | 固定 OMatG 源码及 CSP/DNG 权重；MatterSim；五个 ALIGNN 权重；稳定性参考库。 | `/data/third_party/OMatG`、`/data/se42/omatg_local/checkpoints`、旧 ALIGNN 环境和 `/data/mattersim_reference_cache`。 |
| 1116 | DeepMD 模型、训练日志和配置、整理的数据、W-14 MD 输出与分析脚本。 | `/data/se42/cache/DeepMD-W-14-commercial-level` 与 `/data/se42/cache/DeepMD_W-14_commercial-level`。 |

## 重新训练或重建数据时追加的内容

| 工作 | 需要补充什么 | 75 上的已核对位置 |
| --- | --- | --- |
| 重建 1105 目录 | 原始工作簿、PDF、来源定位与导入审核记录。 | `/data/se42/docs/property datasets/`；生产配置中的历史 `/data/se42/backend/property datasets` 路径已不存在。 |
| 重训 1111 各专项 | 各材料的数据集版本、成分分组划分、完整训练配置、特征生成输入和评价报告。 | `/data/se42/hea_surrogate/data/processed`、`tools`、`reports/models`；固定包的来源清单另在 `package/runtime` 中。 |
| 重训统一合金模型 | 合金训练表、预处理与特征字典、跨成分/跨来源划分、训练及消融报告。 | `/data/se42/experiments/alloy_foundation_v0_20260916` 及对应版本化实验目录。 |
| 复现玻璃 strict 基线 | 严格整理数据、训练/推理程序、划分规则和基线比较。 | `/data/se42/experiments/oxide_glass_transformer_v0_20260922`；主要数据为 `dataset/sciglass_v2_strict_database_observations.csv.gz`。 |
| 复现玻璃物理约束实验 | 消融结果、折外预测、成分/来源划分、端点挑战结果、适配器权重。 | `/data/se42/experiments/oxide_glass_physics_constraints_v1_20260924` 与 `/data/se42/experiments/oxide_glass_multitask_physics_v2_20260928`；部分代码已在 `development/1111_physics`。 |
| 重训 OMatG | 对应数据集 LMDB、训练配置、数据来源、验证划分及算力预算。 | `/data/third_party/OMatG/omg/data/`；已固定源码中保留配置。 |
| 重新完成 W-14 验证 | 原始 DFT 输入/输出与划分、DeepMD 势到 MD 输入的校验绑定、实验或论文的出处和测试条件。 | 当前 cache 中已有部分训练/MD 资料；完整 DFT 原始归档及对标来源仍由案例维护者确认。 |

原始论文、专利和外部数据集可能附带单独的使用要求。后续可按实验创建带版本和校验值的数据包，并根据授权决定存放在私有 Release、团队存储或公开数据平台。每个包同时给出具体训练命令、期望输出和评价方法。

1116 当前服务执行参考案例的证据读取与对标。新增任务调用 DFT、DeepMD 训练或 LAMMPS 的执行器接入是后续开发工作，见[1116 README](../material_validation/README.md)。
