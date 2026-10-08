# 运行资源、来源和取得方式

2026-10-08 已把四个服务日常运行需要的外部源码、模型、数据及参考证据整理成版本化交付。新机器的安装命令见[复现指南](reproduction.md)，实际检查见[验证记录](REPRODUCTION_VERIFICATION_20261008.md)。

## 1. 到哪里取得资源

| 内容 | 取得位置 | 如何使用 |
| --- | --- | --- |
| 四个服务及开发副本 | 本仓库根目录和 `development/`。 | 根目录保存核对过的部署源码，开发更新单独说明。 |
| 1111 专项计算、固定优化和报告程序 | [packages/](../packages/README.md)。 | 恢复工具按原相对目录组合源码与模型。 |
| OMatG 2.0.0 源码、推理配置、许可证 | [third_party/OMatG/](../third_party/README.md)。 | 安装到独立 OMatG 环境，并配置源码路径。 |
| 权重、推理数据、参考库及 W-14 证据 | [私有运行资源 Release](https://github.com/AI4SciFoundation/Material-Computation/releases/tag/runtime-20261008)。 | 有仓库权限的账户登录 GitHub CLI，再用资源工具下载。 |
| 环境依赖及兼容性调整 | [env/](../env/README.md)、`tools/install_environment.py`。 | 分别创建 Web、数值、优化、OMatG、ALIGNN 环境。 |
| 训练和研究追加资料 | [研究资源说明](research_resources.md)。 | 重新训练、重建目录或复核完整研究时按表取得。 |

资源清单 [runtime-manifest.json](../resources/runtime-manifest.json)逐项记录原位置、恢复位置、大小、SHA256、分组和是否随 Git 提供。文件恢复后用 `tools/runtime_resources.py verify` 核对。SHA256 相同表示文件内容一致。

## 2. 分服务运行资源与 75 来源

下表保留原服务器路径，方便核验来源。新机器可以换目录，`tools/prepare_runtime.py` 会生成本机配置。

| 服务或环节 | 75 原位置 | 本次交付内容 |
| --- | --- | --- |
| 1105 导电润滑剂查询 | `/data/se42/alpha_project/material_service_hub/material_database/data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite` | 事务一致的 SQLite 快照，381,964,288 字节；通用材料 CSV 已在 Git 中。 |
| 1105 增量材料目录 | 同一服务的 `data/processed/`，包括 `material_core/` 与 `high_temperature/`。 | 130 个 CSV/JSON；独立资源包恢复到 `data/1105/catalog/`，配置指定此目录。 |
| 1111 专项计算 | `/data/se42/hea_surrogate` | `src/`、`tools/`、模型元信息及八类专项使用的模型和推理数据。 |
| 1111 固定优化包 | `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` | 固定优化源码、合金和玻璃 Transformer 权重、依赖和冻结清单。 |
| 1111 冻结专项 | 上述包内 `specialists/` | 优化所需的固定专项模型、数据及相应版本程序；与当前专项目录分开保留。 |
| 1111 报告 | `/data/se42/experiments/material_shadow_frontend_v2_20260928` | 报告、组分图和配比比较所需的顶层 Python 模块。 |
| 1115 OMatG | `/data/third_party/OMatG`、`/data/se42/omatg_local/checkpoints` | 固定源码；CSP/DNG 各自的 checkpoint 与训练配置。 |
| 1115 结构松弛 | `/data/se42/omatg_local/checkpoints/mattersim-v1.0.0-1M.pth` | MatterSim 模型；MatterSim + ASE 调整晶胞和原子位置。 |
| 1115 稳定性比较 | `/data/mattersim_reference_cache/reference_MP2020correction` | 3,848,802,304 字节参考库；分为八个资源文件，恢复工具自动拼接校验。 |
| 1115 性质预测 | 原位置逐项见资源清单。 | 五个 ALIGNN 性质模型：带隙、体积模量、剪切模量、电子及空穴有效质量。 |
| 1116 W-14 参考证据 | `/data/se42/cache/DeepMD-W-14-commercial-level`、`/data/se42/cache/DeepMD_W-14_commercial-level` | 模型与训练摘要、MD 输出及测试证据；服务读取它们生成参考案例报告。 |

OMatG 原目录没有 `.git` 信息，采用逐文件校验固定这份实际使用的源码，并保留上游许可证。安装另一版官方源码会形成不同版本，应重新验证生成与模型加载。

## 3. 环境和访问配置

原部署 Web Python 3.10、专项/MatterSim Python 3.10、固定优化 Python 3.13、OMatG Python 3.12、ALIGNN Python 3.10。原解释器路径见[环境说明](../env/README.md)；新环境依赖及适配记录按复现指南创建。

语言模型接口、对象存储、公开文件地址及凭据由部署方配置。结构化数值检查可直接调用本地执行器；完整用户对话与前端图片交付还需要相应连接。资源 Release 沿用公司私有仓库的访问权限。

## 4. 推理与重新训练的区别

服务运行主要需要模型、模型类定义、预处理规则、推理参考数据及兼容环境。某些流程还读取参考相数据库或来源表，这些已按实际依赖打包。

重新训练需要原始训练标签、数据划分、筛选过程、训练参数和研究记录；这部分另列在[研究资源说明](research_resources.md)。1116 当前交付参考案例的证据读取流程，完整 DFT 研究数据和真实 DeepMD/LAMMPS 执行接入分别准备。

2026-10-04 的初次核对保留在[历史资源记录](external_resources_20261004.json)，2026-10-08 交付以新的资源清单及验证记录为准。
