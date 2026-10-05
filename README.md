# 材料计算服务仓库（Material-Computation）

本仓库维护四个独立后端服务，分别解决“核验已有材料、优化材料配比、生成晶体结构、检查计算依据”四类问题。`main` 包含完整项目：根目录的四个服务保存 2026-10-04 从 75 服务器核对的源码，`development/` 保存后续更新和独立实验；`backup` 固定保留此前的旧版本。

本页按“服务概览 → 环境与资源 → 入口与启动 → 代码和版本”的顺序说明项目。阅读代码再进入对应服务 README。判断某项功能是否已经上线，看[部署记录](docs/deployment_status.md)；了解下一版具体改动，看[开发改动清单](docs/development_changes.md)。服务器后来有更新时，按记录中的日期和检查命令重新核对。

75 服务目录与 GitHub 的对应关系、后续从服务器推送代码的步骤，见[代码同步说明](docs/code_synchronization.md)。

## 评审时先看什么

| 想了解的事情 | 阅读位置 | 能看到什么 |
| --- | --- | --- |
| 项目解决哪些问题，代码怎样组织 | 本页及[评审阅读指南](docs/review_guide.md) | 四个服务的分工、一次请求的执行顺序、主要代码入口。 |
| 哪些代码来自线上，哪些更新还在开发 | [部署记录](docs/deployment_status.md)及[开发改动清单](docs/development_changes.md) | 服务器来源、更新状态和实际不同的文件；无需逐个浏览重复文件。 |
| 别人接手或迁移时还需要什么 | [外部依赖说明](docs/external_dependencies.md)及[环境说明](env/README.md) | 模型、数据、Python 环境的位置，已记录的版本和仍需准备的事项。 |

当前记录确认了 1105、1111、1115 的服务进程及源码；1116 已核对源码，但尚未确认在线运行。详细测试结果分为[根目录和开发目录的检查记录](docs/MAIN_LAYOUT_VERIFICATION_20261004.md)与[此前版本的测试记录](docs/RELEASE_HANDOFF_20261004.md)。

## 1. 服务分工与当前状态

| 服务目录 | 部署端口 | 解决什么问题 | 输入与输出 | 当前实现状态 |
| --- | --- | --- | --- | --- |
| `material_database/` | 1105 | 根据应用条件核验、比较已有材料的性质与来源；包含导电润滑剂专项。 | 材料名称、牌号、工况及性质条件 → 目录匹配、性质证据和比较报告。 | 目录检索及流式报告已实现；数据覆盖以实际目录为准。 |
| `material_composition_optimization/` | 1111 | 在允许的组成范围内调整材料配比。 | 材料类别、组分范围、工况及目标 → 优选配比、模型预测性质和比较图。 | 八类专项计算已配置为正式路线；其他类别提供通用设计准备。 |
| `material_discovery/` | 1115 | 生成新的周期晶体结构。 | 化学式、元素范围或经确认的开放探索需求 → 候选结构、稳定性估算、性质预测及三维文件。 | 本目录对应 75 当前源码；后续报告与异常原因改进单独位于 `development/material_discovery/`。 |
| `material_validation/` | 1116 | 组织、检查已有计算与实验来源，判断验证证据是否齐全。 | 纯钨 W-14 标杆任务及已归档数据 → 四阶段证据报告和性质对标。 | 已实现标杆证据读取与报告；在线进程及 DeepMD/LAMMPS 执行器接入还需确认。 |

“周期晶体”指原子排列在空间中重复的固体结构；“配比优化”调整各组分含量；“结构生成”还要确定晶胞和原子位置。晶胞是描述这种重复排列的基本空间单元。

表中为部署端口，以各服务 `.env` 和网关配置为准。1115 的 `main.py` 在缺少 `PORT` 时使用历史默认值 `1107`，启动时明确设置 `PORT=1115`。GitHub 保存源码，服务器保存部署文件，服务进程执行启动时加载的代码。修改或推送文件后，仍需单独部署、重启和检查运行结果。

1105 的目录名沿用 `material_database`，实际面向已有材料选型和性质证据核验。在线读取已整理 CSV 和专项 SQLite；外部资料的收集、核验与导入另行进行。

## 2. 环境、数据和模型：运行前先准备什么

拉取源码后，需要同时准备各服务使用的环境、计算程序、模型和数据。完整位置、配置字段和检查步骤集中在[服务运行与复现说明](docs/service_setup.md)。

当前先按实际存放位置交接资源：已随 Git 提供的文件在仓库中查看，模型、大型数据和部分计算程序按表中服务器路径取得。后续统一发布资源时，再在清单补充下载地址、版本和访问方式。

| 服务 | GitHub 已提供 | 75 服务器上的额外运行资源 | 主要软件或模型 |
| --- | --- | --- | --- |
| 1105 | 查询代码、测试、通用材料 CSV、清洗记录及配置样例。 | 导电润滑剂 SQLite 在服务的 `data/processed/fluid_lubricant/2026-08-04_v1/`；原始工作簿和 PDF 在外部数据目录。 | FastAPI/Uvicorn 提供接口，CSV/SQLite 和规则比较提供材料证据，Matplotlib 绘图。 |
| 1111 | 接口、需求解析、专项适配、正式路线配置、报告和测试。 | `/data/se42/hea_surrogate` 的计算代码、数据与模型；固定优化包及正式报告模块仍在外部目录。 | PyTorch 性质模型、scikit-learn/joblib 专项模型、pymoo 的 NSGA-II 优化；组合按材料类别选择。 |
| 1115 | 输入解析、OMatG 适配、独立执行脚本、报告、测试和配置样例。 | OMatG 源码 `/data/third_party/OMatG`，权重 `/data/se42/omatg_local/checkpoints`，参考库 `/data/mattersim_reference_cache`。 | OMatG 生成；MatterSim + ASE 优化结构；pymatgen 检查和比较结构；ALIGNN 预测性质。 |
| 1116 | 证据流程、报告、测试、W-14 清单和内部对标记录。 | 训练归档在 `/data/se42/cache/DeepMD-W-14-commercial-level`，MD 输出在 `/data/se42/cache/DeepMD_W-14_commercial-level`；75 服务配置仍记录旧路径，联调前核对。 | NumPy 处理已有数值，Matplotlib 绘图；DeepMD/LAMMPS 是归档证据的来源软件。 |

1105 专项 SQLite 在 2026-10-05 核对时为 381,964,288 字节，约 364 MiB，未随当前 Git 提交提供；交接时单独取得该文件并放到指定目录。

| 环节 | 75 上已使用的解释器 | 环境说明 |
| --- | --- | --- |
| 主 Web 服务 | `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python` | Python 3.10.19；见[环境说明](env/README.md)。 |
| 1111 专项计算 / 1115 MatterSim | `/data/mamba/envs/mattergen-py310/bin/python` | 名称沿用历史版本；当前也用于专项计算和 MatterSim。 |
| 1111 固定优化包 | `/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python` | Python 3.13.11。 |
| 1115 OMatG | `/data/mamba/envs/omatg-py312/bin/python` | Python 3.12.14、OMatG 2.0.0、PyTorch 2.8.0+cu126。 |
| 1115 ALIGNN | `/home/ubuntu/miniconda3/envs/dsc2/bin/python` | 另加载 `/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages`。 |

75 的现成环境可以复用。新机器按[外部资源记录](docs/external_dependencies.md)准备软件、权重和数据，再验证安装与最小任务。1115 当前数值链使用 NVIDIA GPU；已有测试机器为 RTX 4090 24 GB，其他硬件单独检查运行和显存需求。

完整前端联调还需要语言模型接口及对象存储配置，凭据由管理者提供并保存在各服务本机 .env。语言模型用于文字组织和流式交付，材料性质来自对应数据或数值模型。

## 3. 服务入口、启动和最小验证

### 3.1 服务入口在哪里

各服务 `main.py` 启动 HTTP/WebSocket；`src/team_config.py` 提供编排或角色适配。当前接口的实际调用关系如下。

| 服务 | 请求入口 | 编排或角色入口 | 实际业务调用 |
| --- | --- | --- | --- |
| 1105 | [main.py](material_database/main.py) | [src/team_config.py](material_database/src/team_config.py) 的 MaterialMature | HTTP 查询和 WebSocket 主流程经 contract()/run() 进入材料目录或导电润滑剂流程。 |
| 1111 | [main.py](material_composition_optimization/main.py) | [src/team_config.py](material_composition_optimization/src/team_config.py) 的 AlloyCompositionOptimizationRole / Coding | HTTP/WebSocket 直接调用 src/alloy_workflow/runtime.py 的 RUNTIME；Role 入口也调用同一运行模块。 |
| 1115 | [main.py](material_discovery/main.py) | [src/team_config.py](material_discovery/src/team_config.py) 的 InorganicNewMaterialService / InorganicNewMaterialDiscoveryRole | WebSocket 经 Team/Role 调用 run_upstream_request()；HTTP 生成直接调用该函数，随后进入 pipeline.py 和 OMatG 流程。 |
| 1116 | [main.py](material_validation/main.py) | [src/team_config.py](material_validation/src/team_config.py) 的 execute_refractory_validation() | HTTP/WebSocket 经该函数调用 src/application/validation_workflow.py。 |

1115 内部类名沿用历史名称，面向用户称为“晶体结构发现”。1116 的函数入口可调用，角色注册类名需与实现对齐，详见[运行说明](docs/service_setup.md)。统一编排目标与新增代码要求见[架构规范](docs/material_service_architecture_standard.md)。

### 3.2 如何启动和检查

资源及本机配置准备好后，在独立联调副本启动相应服务；每个服务运行一个进程。端口先检查是否空闲，线上操作按部署记录安排。

| 服务 | 工作目录 | 75 环境下的启动命令 |
| --- | --- | --- |
| 1105 | `material_database/` | `PORT=1105 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py` |
| 1111 | `material_composition_optimization/` | `PORT=1111 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py` |
| 1115 | `material_discovery/` | `PORT=1115 NEW_MATERIAL_GENERATOR=omatg /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py` |
| 1116 | `material_validation/` | `PORT=1116 /home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py` |

新机器替换为本机已验证的 Web 解释器。完整命令、配置和最小检查见[运行说明](docs/service_setup.md)。启动后先访问对应端口的 /roles，再按服务 README 发送最小任务；1105、1111、1116 另提供 /health。注册接口响应表示 Web 服务可访问，模型和数据的可用性通过资源预检和真实任务检查确认。

## 4. 目录与职责

```text
Material-Computation/
├── material_database/                  # 1105：已有材料选型与性质核验
├── material_composition_optimization/   # 1111：配比优化
├── material_discovery/                  # 1115：晶体结构生成与评价
├── material_validation/                 # 1116：标杆证据核验
├── development/                        # 独立开发与待部署更新
│   ├── material_composition_optimization/ # 1111：后续整合更新
│   ├── material_discovery/               # 1115：报告及异常原因改进
│   └── 1111_physics/                     # 1111：离线玻璃物理约束实验
├── env/                                # 环境定义和历史依赖快照
├── tools/                              # 仓库结构和源码快照校验
└── docs/                               # 跨服务规范、版本交接与审阅记录
```

各服务的实际目录名略有差别，职责划分一致：

| 层次 | 主要位置 | 负责的事情 |
| --- | --- | --- |
| 请求与传输 | `main.py` | HTTP/WebSocket 路由、连接管理、请求校验和前端事件发送。 |
| 编排与角色适配 | `src/team_config.py` 及各服务实际业务入口 | 各接口按第 3 节调用解析、业务计算和交付模块。 |
| 业务规则与执行 | 1105 `src/catalog/`；1111 `src/alloy_workflow/`；1115 `src/material_workflow/`；1116 `src/application/` 等 | 目录查询、条件解析、模型调用、优化、结构评价或证据读取。 |
| 报告与文件处理 | 各服务的展示、协议与文件模块 | 组织 Markdown、表格、图像及三维文件，发送给前端并保存记录。 |
| 配置与测试 | `.env.example`、`config/`、`tests/` | 配置样例、功能版本选择、单元测试和接口兼容性测试。 |

根目录服务与开发副本都有完整入口，根目录服务不导入 `development/`。开发副本保留了相互依赖的更新，方便一起测试。部署完成并通过真实请求检查后，再将已部署代码同步到根目录服务，并更新部署记录；部署前的候选版本继续保留在开发目录。

阅读时从请求入口跟到执行流程，再看实际计算和报告模块。`src/team_config_en.py`、`src/llm_utils.py`、`alpha/` 等旧文件可能仍有其他代码调用；删除前先查引用并重跑相关测试。1111 的详细阅读路径见[代码审阅指南](material_composition_optimization/docs/alloy_composition_optimization_review_guide.md)。

## 5. 版本与阅读顺序

只用两条长期分支；线上与开发内容按目录区分，不需要逐功能维护长期分支：

| 分支 | 用途 |
| --- | --- |
| `main` | 默认维护分支，包含线上源码快照、开发内容、测试和交接说明。 |
| `backup` | 固定保存旧 main 提交 `34305b968445186d93cb37a96bcb1a338e845a86`，供查阅历史代码；不滚动更新。 |

获取当前完整项目：

```bash
git clone --branch main git@github.com:AI4SciFoundation/Material-Computation.git
```

建议按以下顺序阅读：

1. 本页第 1–3 节及[运行说明](docs/service_setup.md)：服务分工、依赖、资源位置、入口与启动。
2. [线上与开发状态](docs/deployment_status.md)：源码来源、开发任务、外部依赖与部署规则。
3. 对应服务 README：[1105](material_database/README.md)、[1111](material_composition_optimization/README.md)、[1115](material_discovery/README.md)、[1116](material_validation/README.md)。
4. [环境说明](env/README.md)及[外部依赖说明](docs/external_dependencies.md)：服务与模型环境的分工、资源版本和迁移准备。
5. [统一架构规范](docs/material_service_architecture_standard.md)：新增代码、前端协议与上线交接要求。
6. [开发改动清单](docs/development_changes.md)与[开发目录](development/README.md)：下一版不同的代码、测试及物理约束实验。

旧 MatterGen 训练包和离线工具的 README 标明历史用途。根目录 `HANDOFF_2026-08-21.md` 保留当时的工作记录；当前服务状态看部署记录，1115 当前生成器为 OMatG。

## 6. 代码、模型与输出文件分开管理

| 内容 | 管理位置 |
| --- | --- |
| 服务源码、测试、无密钥配置、文档和小型数据清单 | Git 仓库。 |
| 模型官方源码、训练数据、权重、科学计算参考库和 Python 环境 | 服务器外部目录，见各服务 README。 |
| 生产密钥和机器差异配置 | 每个服务自身的 `.env`，不入 Git。 |
| 任务结构、计算记录、日志、图片和三维文件 | 任务结果目录及对象存储，不作为源码提交。 |

新机器拉取仓库后可以阅读代码和测试逻辑；运行测试或真实计算前，还需准备相应依赖。模型、数据及环境版本见[外部依赖说明](docs/external_dependencies.md)。75 服务器新增下载、缓存和临时文件放在 `/data`，避免占用系统盘。

任务中的 `manifest` 是结果清单，用来记录输入、执行状态、模型版本、文件路径和失败原因。PNG 是静态图片，GIF 是动画，GLB 是网页三维模型，CIF 是晶体结构文件。

## 7. 运行、验证与部署

各服务读取自己的 `.env`。主 Web 进程使用实际服务环境，重型模型通过独立环境调用；准确路径和检查命令见对应 README。

75 当前运行目录为 `/data/se42/alpha_project/material_service_hub`。代码同步和推送使用独立的 `main` 仓库副本，具体步骤见[代码同步说明](docs/code_synchronization.md)。当前服务目录仍有未提交源码，直接拉取或合并可能影响这些文件。GitHub `backup` 保存历史代码；部署回退另行备份目标文件和本机配置。开发联调使用独立端口、配置和结果目录，并沿用已确认的模型与环境路径。

验证按三个层次进行：

1. 单元及接口兼容性测试：检查规则、字段、消息顺序和异常处理；这些测试中的模拟模型调用用于检查程序逻辑。
2. 环境预检：检查解释器、依赖、权重和参考库是否存在。
3. 真实请求检查：按批准的部署清单，检查健康接口、角色信息、实际计算、最终报告、图片和三维文件。

开发和联调使用 tmux；稳定服务可由运维统一登记为系统服务。启动或重启前确认端口和活动任务，避免重复进程。已有 Docker 文件、旧启动脚本或历史环境快照不等同于当前生产启动路径。

前端依赖 WebSocket 地址、消息字段和顺序、`[start]/[end]`、内容块标记及文件类型。修改这些约定时补接口兼容性测试，并与前端一起检查。模型预测、目录来源和实验记录在报告中分别标注，采用前按对应条件进行核验。

## 8. 后续提交怎样命名

提交标题使用英文类型前缀加中文说明，让阅读者直接知道改了什么。例如 `docs: 完善服务部署和环境说明`、`feat(1115): 补充结构计算失败原因`、`fix(1111): 修正候选含量显示`。`docs` 表示文档，`feat` 表示新功能，`fix` 表示修复问题。

分支名 `main`、`backup` 和代码目录名保持英文。已有提交历史保持原样；新提交说明具体改动，不写操作者或工具名称。
