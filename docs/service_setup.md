# 服务运行与复现说明

本文说明四个服务的接口、资源来源及 75 原部署。新机器按[安装与复现指南](reproduction.md)执行；2026-10-08 已将专项计算源码、OMatG 源码及资源恢复工具纳入仓库，权重和运行数据通过私有 Release 交付。根目录服务源码仍对应 2026-10-04 核对的版本。

## 1. 拉取仓库后具备哪些条件

| 服务 | 随 Git 提供的部分 | 还需取得的部分 | 复现检查的重点 |
| --- | --- | --- | --- |
| 1105 | 查询与报告代码、通用材料 CSV、测试、配置样例。 | 导电润滑剂专项 SQLite；重建通用目录时还需原始工作簿及来源文件。 | 通用查询使用已有 CSV；专项查询在取得 SQLite 后检查。 |
| 1111 | 接口、适配、配置、测试及 `packages/` 中的专项计算、固定优化和报告程序。 | Release 中的模型和推理数据；独立计算环境。 | 先检查选定类别的全部资源，再运行该类任务。 |
| 1115 | 生成适配、执行脚本、评价、报告、测试及 `third_party/OMatG/` 源码。 | 独立环境；Release 中的生成、性质、MatterSim 权重与参考库。 | 环境预检通过后分别检查 CSP、DNG。 |
| 1116 | 四阶段证据流程、W-14 清单、内部对标记录和测试。 | 完整训练、模型与 MD 归档，以及正式实验/文献来源。 | 先检查纯钨归档证据；真实计算执行器需要单独开发和验收。 |

75 可复用已有资源；新副本使用安装、下载、恢复和启动工具。逐文件 SHA256、源路径和资源分组见[资源清单](../resources/runtime-manifest.json)，实际验证范围见[复现记录](REPRODUCTION_VERIFICATION_20261008.md)。

运行资源从[私有 Release](https://github.com/AI4SciFoundation/Material-Computation/releases/tag/runtime-20261008)下载，需要本仓库访问权限。下文中的 `/data/se42/...` 表示原部署来源；新机器配置由 `tools/prepare_runtime.py` 按本地目录生成。

## 2. 四个服务共用的准备步骤

1. 拉取 `main`，选择根目录的目标服务。`development/` 是下一版或独立实验，其说明和验证记录单独维护。
2. 准备 Web 环境及该服务的数值环境、数据与权重。75 Web 解释器为 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python`，Python 3.10.19；环境定义在 [env/](../env/README.md)。
3. 在新副本中参考该服务 `.env.example` 建立本机 `.env`，填入资源路径、结果目录和端口。1115 还合并 [.env.omatg.example](../material_discovery/.env.omatg.example) 中的模型配置。
4. 完整前端联调配置语言模型与对象存储凭据。各服务字段名以自己的样例和 README 为准；凭据由管理者提供。测试用占位凭据仅用于相应程序测试。
5. 核对目标端口和结果目录，再启动。已有生产 `.env` 保留；测试副本使用独立端口、结果目录和本机配置。

接口由 FastAPI/Uvicorn 提供，dotenv 读取本机配置，Matplotlib 绘制报告图像。语言模型负责文字组织及流式交付，材料数值由目录数据或相应科学模型提供。独立数值环境减少 PyTorch、图计算库和科学计算包的版本冲突。

新增模型、缓存、环境和临时文件使用 `/data`。表中的 `/home/ubuntu/miniconda3/...` 是已有解释器位置，迁移时按目标机器的数据盘安排环境。

## 3. 1105：已有材料选型与性质核验

### 3.1 环境、数据和软件

| 项目 | 位置或配置 | 获取与用途 |
| --- | --- | --- |
| Web 环境 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | 75 已有环境；服务依赖清单为 [requirements.minimal.txt](../material_database/requirements.minimal.txt)。 |
| 通用材料目录 | 服务 `data/processed/` 中的 `materials.csv`、`material_aliases.csv`、`property_points.csv`、`curve_data.csv`、`composition_long.csv` | 随 Git 提供，保存材料身份、别名、性质点、曲线及组成。 |
| 目录清洗记录 | `data/processed/catalog_cleaning_summary.json` | 随 Git 提供，记录来源处理与目录统计。 |
| 导电润滑剂证据库 | 服务 `data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite` | 从 Release 的 `1105` 资源组恢复，约 364 MiB。 |
| 原始数据 | `PROPERTY_DATA_ROOT`；资料在 `/data/se42/docs/property datasets/` | 原始工作簿与 PDF 等在仓库外；重建和增加数据时使用。 |
| 查询目录配置 | `MATURE_MATERIAL_CATALOG_ROOT`，默认 `data/processed` | 通用聊天查询读取该位置；独立流体 HTTP 路由另读取服务目录内的专项 SQLite，迁移时一并核对。 |
| 任务结果 | `MATURE_MATERIAL_RESULTS_ROOT`，默认 `results/mature_material` | 保存输入、核验结果、报告和图像。 |

完整 75 专项数据库位置为 `/data/se42/alpha_project/material_service_hub/material_database/data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite`。2026-10-05 文件大小为 381,964,288 字节；后续数据更新时重新记录版本。

服务使用 CSV/SQLite 查询、名称与单位规范化、工况比较和温度曲线处理。这里的“材料数据库”是已整理的材料证据目录；外部资料需先核验和导入，再进入在线查询。

### 3.2 入口、启动和检查

`main.py` 的 HTTP 查询及 WebSocket 主流程 → `src/team_config.py:MaterialMature.contract()/run()` → 通用目录或导电润滑剂流程 → 报告与任务记录。

在已配置的联调副本执行：

```bash
cd /path/to/Material-Computation/material_database
PORT=1105 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

在另一终端检查：

```bash
curl --fail http://127.0.0.1:1105/health
curl --fail http://127.0.0.1:1105/roles
```

先按[服务 README](../material_database/README.md)查询一条已收录材料，核对单位、材料状态和来源。导电润滑剂另检查 SQLite 文件及实际专项请求。`/health` 的 `catalog_ready` 检查通用目录文件存在，专项数据库可用性通过专项请求确认。

## 4. 1111：材料配比优化

### 4.1 环境、计算程序、模型和数据

| 项目 | 75 位置 | 用途与交接内容 |
| --- | --- | --- |
| Web 环境 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | 接口、需求解析和交付；服务依赖见 [requirements.minimal.txt](../material_composition_optimization/requirements.minimal.txt)。 |
| 专项数值环境 | `/data/mamba/envs/mattergen-py310/bin/python` | 对应 `HEA_SURROGATE_ENV_PREFIX`；保持专项包版本及调用路径。 |
| 专项代码、数据与模型 | `/data/se42/hea_surrogate` | `src/api/` 是专项计算模块，`models/` 存模型，`data/processed/` 存结构化来源、工艺状态与适用条件。 |
| 固定优化包 | `/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime` | `rsi_api.py` 入口、优化代码、模型和版本清单。 |
| 固定优化包环境 | `/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python` | Python 3.13.11；保留该环境的软件版本。 |
| 正式报告辅助程序 | `/data/se42/experiments/material_shadow_frontend_v2_20260928` | 当前正式调用的 `recipe_*` 模块及执行入口，与固定优化包一起交接。 |
| 正式路线配置 | 服务 `config/recipe_release.json` | 随 Git 提供；决定各材料类别采用的结果路线。 |
| 结果位置 | `ALLOY_RESULTS_ROOT`，默认 `results/alloy_composition_optimization` | 每个任务保存配比、预测结果、方法与图像清单。 |

PyTorch 加载已训练性质模型；scikit-learn/joblib 加载树模型等专项模型；pymoo 的 NSGA-II 在含量范围内搜索多目标配比。钙钛矿采用已定义的组成网格计算。具体请求按材料类别和配置选模型，训练数据用于模型准备及适用范围检查，任务执行复用已有权重。

以下是外部清单中已记录的模型位置，便于维护者逐类交接：

| 类别 | 模型或资源位置，相对于 `/data/se42/hea_surrogate` |
| --- | --- |
| HEA/MPEA | `models/yield_strength_ensemble.joblib`、`hardness_ensemble.joblib`、`phase_ensemble.joblib`。 |
| 镍基 | `models/ni_superalloy_cast_ds_sc_strength_v0/`、`ni_superalloy_cast_ds_sc_creep_rupture_v0/`、`ni_superalloy_cast_ds_sc_elongation_v0/`；按实际调用保留 serving 版本和来源表。 |
| 铜基 | `models/copper_hot_end_local_composition_v1_20260911/model.joblib`。 |
| 火箭不锈钢 | `data/processed/reusable_rocket_stainless_design/20260902_v2_extratrees/` 的模型、适用范围与说明，以及参考基线目录。 |
| 航空铝 | `models/aerospace_aluminum_composition_v1_20260914/model.joblib`。 |
| 芯片玻璃 | `models/chip_glass_cte_family_v1_20260902/`、`models/chip_glass_family_thermomechanical_candidate_v2_20260904/`，以及相应整理数据。 |
| 短碳纤维 | `models/short_cf_thermomechanical_rve_v1_20260907/model.joblib` 及其说明。 |
| 钙钛矿 | `src/api/perovskite_transport_stability_runtime.py` 及其实际读取的数据资源，随专项包核对。 |

固定优化包还包含 `models/alloy_v10/model_state.pt`、`models/glass_v1/model_state.pt`。包内 `EXTERNAL_DEPENDENCIES.json` 列出已核对的专项代码、权重和锚点数据；`PACKAGE_MANIFEST.json` 列出包文件及校验值；`FROZEN_INPUT_INVENTORY.json` 记录输入数据字段和来源。清单包含历史比较版本，实际计算使用的模型由当前路线及任务结果记录确认。取得文件时保留数据和配套说明，迁移前进一步核对专项模块实际读取的资源。

当前固定优化包、报告模块和解释器的默认绝对路径在 `src/alloy_workflow/recipe_shadow.py` 中定义。新机器复现需核对这些路径；选择不同的目录布局时，另行安排路径适配和验证。

### 4.2 入口、启动和检查

| 调用方式 | 实际执行关系 |
| --- | --- |
| HTTP `/alloy/propose-space`、WebSocket | `main.py` → `src/alloy_workflow/runtime.py:RUNTIME.propose()` → 专项适配、计算与报告。 |
| HTTP 单项/批量评价 | `main.py` → `RUNTIME.application` 的评价函数。 |
| Alpha Role 入口 | `src/team_config.py:AlloyCompositionOptimizationRole` → `Coding.run()` → 同一 `RUNTIME`。 |

因此 `src/team_config.py` 仍在服务内，但当前 HTTP/WebSocket 与 Role 的外层流程分别实现，共用内部计算运行模块。统一外层编排属于后续代码整理工作。

```bash
cd /path/to/Material-Computation/material_composition_optimization
PORT=1111 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

```bash
curl --fail http://127.0.0.1:1111/health
curl --fail http://127.0.0.1:1111/roles
```

检查[服务 README](../material_composition_optimization/README.md)中的资源预检和最小请求。先选择一种资源齐备的材料类别，确认实际计算、含量单位、输出性质和最终图像，再逐类扩展验收。

## 5. 1115：晶体结构发现

### 5.1 环境、模型和参考数据

| 项目 | 75 位置或字段 | 用途 |
| --- | --- | --- |
| Web 环境 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | 接口、编排、报告与流式交付。 |
| OMatG 源码 | `/data/third_party/OMatG`，`OMATG_SOURCE_DIR` | 固定组成 CSP 与探索组成 DNG。当前目录版本按已记录文件校验值识别。 |
| OMatG 环境 | `/data/mamba/envs/omatg-py312/bin/python`，`OMATG_PYTHON` | Python 3.12.14、OMatG 2.0.0、PyTorch 2.8.0+cu126。 |
| DNG 权重及配置 | 权重根目录下 `MP-20-DNG/Linear-SDE-Gamma/checkpoint.ckpt` 和 `train.yaml` | 探索组成与结构。 |
| CSP 权重及配置 | 权重根目录下 `Alex-MP-20-CSP/Trig-SDE-Gamma/checkpoint.ckpt` 和 `train.yaml` | 固定化学式生成结构。 |
| 权重根目录 | `/data/se42/omatg_local/checkpoints`，`OMATG_CHECKPOINT_ROOT` | OMatG 和 MatterSim 文件的现有位置。 |
| MatterSim 环境及模型 | `/data/mamba/envs/mattergen-py310/bin/python`；权重根目录下 `mattersim-v1.0.0-1M.pth` | 预测能量和力；通过 ASE 调整晶胞及原子位置。软件包版本与模型版本分别记录。 |
| 稳定性参考库 | `/data/mattersim_reference_cache/reference_MP2020correction` | 参考相能量库，已记录大小约 3.85 GB（十进制）；与模型能量组成混合参考估算。 |
| ALIGNN 环境 | `/home/ubuntu/miniconda3/envs/dsc2/bin/python`；另加载 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages` | 保持既有 NumPy/PyTorch/图计算依赖及加载顺序。 |
| ALIGNN 性质权重 | `/data/se42/omatg_local/checkpoints/alignn`，`ALIGNN_CHECKPOINT_ROOT` | 已记录带隙、体积模量、剪切模量、电子/空穴有效质量的五个性质头；实际可用性按预检确认。 |
| 缓存、锁和临时文件 | `/data/se42/omatg_local/runtime` | GPU 任务串行锁、参考相缓存和临时计算文件。 |

`pymatgen` 处理结构、组成、相图与相似结构比较；ASE 提供结构优化接口；MatterSim 提供能量与力；ALIGNN 从原子和键角图预测性质。服务 `tools/` 保存独立调用脚本，重型包在对应环境运行。GPU 需兼容所选 PyTorch 构建和图计算扩展。已有机器为 RTX 4090 24 GB，该配置是已使用的测试硬件记录。

当前生成执行器根据请求临时建立输入 LMDB，使用已训练权重推理。模型训练来源保存在配套 `train.yaml`；现有推理流程的数据资源主要为上述权重、性质模型和稳定性参考库。

### 5.2 配置、入口、启动和检查

先准备本机 `.env`，合并模型配置样例，并明确 `NEW_MATERIAL_GENERATOR=omatg`、`PORT=1115`。仅使用无模型配置的通用样例，可能选择历史后端；缺少 `PORT` 时代码默认 1107。

| 调用方式 | 实际执行关系 |
| --- | --- |
| WebSocket `/start`、`/new-material/start` | `main.py` → `src/team_config.py` 的 Service/Team/Role → `run_upstream_request()`。 |
| HTTP `/new-material/generate` | `main.py` → 解析生成请求 → `run_upstream_request()`。 |
| 共用数值链 | `upstream_api.py` → `pipeline.py` → `omatg_pipeline.py` → 生成、结构检查、优化、性质预测和交付。 |

在已准备资源的联调副本先预检；下面读取的是无密钥模型配置样例：

```bash
cd /path/to/Material-Computation/material_discovery
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python tools/preflight_omatg.py --env-file .env.omatg.example
```

样例中的路径需与该机器一致；本机配置使用其他路径时，预检相应配置文件。资源通过后启动：

```bash
PORT=1115 NEW_MATERIAL_GENERATOR=omatg /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

```bash
curl --fail http://127.0.0.1:1115/roles
```

按[服务 README](../material_discovery/README.md)分别测试固定 `BaTiO3` 的 CSP 请求和显式元素范围的 DNG 请求，使用独立任务号。检查组成约束、优化收敛、性质结果，以及 CIF、图像和三维结构是否对应同一候选。任务结果根目录由 `src/service_paths.py` 定义，当前默认路径见服务 README。

## 6. 1116：计算证据核验

### 6.1 环境和证据文件

| 项目 | 位置或配置 | 用途 |
| --- | --- | --- |
| Web 环境 | 75 可复用 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | FastAPI/Uvicorn、NumPy、Matplotlib 等；按环境定义与实际导入检查。 |
| 标杆文件 | 服务 `data/reference_cases/w14_phase_i/`，`REFERENCE_CASE_ROOT` | 随 Git 提供证据清单和用户提供的 300 K 内部对标记录。 |
| 训练及模型归档 | `/data/se42/cache/DeepMD-W-14-commercial-level`，配置为 `W14_MLIP_ROOT` | 已确认有 `input.json`、`frozen_300000.pb`、`lcurve.out`、`train/set.000/`；读取训练记录、模型与数据数组。 |
| 模拟输出归档 | `/data/se42/cache/DeepMD_W-14_commercial-level`，配置为 `W14_MD_ROOT` | 已确认有 300 K 的 NPT 平衡、弹性和热容时间序列等文件；目录另保留不同温度的原始输出与脚本。 |
| 结果目录 | 服务 `results/<taskid>/` | 证据报告、任务记录、诊断和图像。 |

2026-10-05 核对：75 服务的 `.env` 仍记录 `/data/se42/DeepMD-W-14-commercial-level` 与 `/data/se42/DeepMD_W-14_commercial-level`，这两处旧路径已不存在；归档目前位于上表的 `cache/` 目录。旧项目目录 `/data/se42/deprecated_service/repos-github/boss/ALPHA-Materials-Pipeline/` 下还保存 `DeepMD-W-14-commercial-level` 与 `Lammps_W-14-commercial-level` 两份归档，本次确认主要文件存在，副本内容一致性需在交接时核对。

联调副本可按已确认的归档位置配置两个变量，之后执行证据读取检查。目录名的连字符与下划线不同，分别对应训练和 MD 归档。无密钥样例保留为空，迁移时按目标机器的证据目录填写。

复现 `reference_case` 读取已有文件，模型文件作为证据核验对象。`execution_mode=execute` 的真实 DeepMD/LAMMPS 执行器仍需单独接入。

### 6.2 入口、启动和检查

HTTP/WebSocket → `src/team_config.py:execute_refractory_validation()` → `src/application/validation_workflow.py` → 证据读取与报告。

`/roles` 的 `addresses` 指向上述函数，但 `__module_class_name` 声明的 `RefractoryMultiscaleValidationRole` 当前没有对应类。若上游按该类名导入，需要先修正注册或实现适配；HTTP/WebSocket 函数入口和 Role 类导入分别验收。

先确认端口空闲，在配置好的联调副本执行：

```bash
cd /path/to/Material-Computation/material_validation
PORT=1116 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

```bash
curl --fail http://127.0.0.1:1116/health
curl --fail http://127.0.0.1:1116/roles
```

按[服务 README](../material_validation/README.md)执行 W-14 `reference_case` 请求。逐项检查归档来源、材料和温度条件；齐全的来源才支持相应性质对标。当前 75 的 1116 在线部署状态仍需单独确认。

## 7. 复现验收与仍需交接的内容

| 检查层次 | 检查方法 | 通过后说明什么 |
| --- | --- | --- |
| 仓库和说明 | 文档链接、配置样例、源码记录检查。 | 文件及阅读入口对应当前版本。 |
| 程序逻辑 | 各服务 README 的单元和接口兼容性测试。 | 输入解析、调用流程、协议与异常处理可验证。 |
| 资源准备 | 环境预检、权重/数据存在性与 SHA256 核对。 | 已取得对应版本的运行资源。 |
| 真实任务 | 最小请求、结果记录和最终图文检查。 | 指定服务及材料路线能在该机器完成实际任务。 |

完整迁移还需交接 1105 专项 SQLite、1111 各专项及固定包的全部读取资源、1115 的环境和权重，以及 1116 的证据归档。后续登记统一下载位置、访问方式与资源维护人，并在新机器上验证安装和最小任务，将成功步骤补入本文。

程序测试记录见[评审指南](review_guide.md)，现有版本与哈希见[外部资源说明](external_dependencies.md)和[资源版本 JSON](external_resources_20261004.json)，线上状态见[部署记录](deployment_status.md)。
