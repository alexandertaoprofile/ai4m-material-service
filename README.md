# alpha_material_sync

`alpha_material_sync` 是材料服务聚合仓，当前包含 3 个可独立运行的子服务：

- `inorganic_existing_material/`：无机已有材料筛选与性质补全
- `inorganic_new_material/`：无机新材料发现服务（演进中）
- `organic_existing_material/`：有机已有材料服务（OpenPoly 检索 + 性质补全主链）

## 仓库结构

```text
alpha_material_sync/
  inorganic_existing_material/
  inorganic_new_material/
  organic_existing_material/
  env/                         # 部署环境文件集中目录
```

## env 目录说明

`env/` 目录用于给部署机（如 113）提供统一的环境定义与快照文件，不放业务密钥。

## 运行与部署原则

1. 三个子服务各自独立启动、独立配置。
2. 代码与环境定义可入仓；`.env`、AK/SK、Token 等敏感配置不入仓。
3. 生产部署优先使用固定分支/tag，保证可回滚。

## 文档入口

- 无机已有材料服务：`inorganic_existing_material/README.md`
- 无机新材料服务：`inorganic_new_material/README.md`
- 有机已有材料服务：`organic_existing_material/README.md`

## 架构说明（交接版）

本仓库采用“聚合仓 + 子服务独立演进”模式。每个子目录（`inorganic_existing_material` / `inorganic_new_material` / `organic_existing_material`）都应满足：
- 可独立启动
- 可独立配置
- 可独立部署
- 共性能力可复用，但业务主链彼此解耦

### 全仓关系树（详细）

```text
alpha_material_sync/
├─ README.md
├─ env/                                  # 部署环境定义与快照（不放业务密钥）
│
├─ inorganic_existing_material/          # 无机已有材料：检索/筛选/补全/下发
│  ├─ main.py                            # 服务入口（API/WS）
│  ├─ team_config.py                     # 兼容桥接入口 -> src/team_config.py
│  ├─ config/                            # 配置文件（config.yaml 等）
│  ├─ src/
│  │  ├─ team_config.py                  # 主编排（当前在线主链核心）
│  │  ├─ llm_utils.py                    # LLM 封装
│  │  ├─ storage_utils.py                # 存储封装
│  │  ├─ oss_utils.py                    # OSS/对象存储封装
│  │  ├─ roles/                          # 角色提示词与角色定义
│  │  ├─ materials/                      # 材料侧 prompts/payloads
│  │  ├─ utils/                          # 主链工具模块（提取、筛选、子进程等）
│  │  └─ MNS_CaseHub/                    # case/pipeline/registry/结果目录约定
│  ├─ tools/                             # 离线脚本（导出、查询、转换）
│  └─ alpha/                             # 历史框架层（保留，逐步解耦）
│
├─ inorganic_new_material/               # 无机新材料：新材料发现主链（标杆方向）
│  ├─ main.py
│  ├─ team_config.py
│  ├─ config/
│  ├─ src/
│  │  ├─ team_config.py                  # 新材料主编排（建议重点治理）
│  │  ├─ llm_utils.py
│  │  ├─ storage_utils.py
│  │  ├─ utils/
│  │  └─ MNS_CaseHub/
│  ├─ tools/
│  └─ alpha/
│
└─ organic_existing_material/            # 有机已有材料：检索/补全主链
   ├─ main.py
   ├─ team_config.py
   ├─ config/
   ├─ src/
   │  ├─ team_config.py
   │  ├─ llm_utils.py
   │  ├─ storage_utils.py
   │  ├─ utils/
   │  └─ MNS_CaseHub/ (如该服务已接入)
   ├─ tools/
   └─ alpha/
```

### 单服务内部关系树（推荐标准）

```text
<service_root>/
├─ main.py                               # 对外入口（启动、路由、ws）
├─ team_config.py                        # 兼容入口（薄桥接）
├─ README.md                             # 服务文档（功能、配置、运行、排障）
├─ config/
│  ├─ config.yaml
│  └─ config2.yaml
├─ src/
│  ├─ team_config.py                     # 主编排：路由 + 主流程 + 结果组织
│  ├─ llm_utils.py                       # 模型调用、超时、重试
│  ├─ storage_utils.py                   # 文件/对象存储上传下载
│  ├─ oss_utils.py                       # OSS 访问封装
│  ├─ roles/                             # 角色定义与 prompt 常量
│  ├─ materials/                         # 材料业务专属 schema/payload/prompt
│  ├─ utils/                             # 纯函数与可复用能力
│  │  ├─ *_extractor.py                  # 信息抽取（候选、公式、字段）
│  │  ├─ *_selector.py                   # 候选筛选与归一
│  │  ├─ *_runner.py                     # 子进程/第三方工具执行
│  │  ├─ *_helpers.py                    # 通用 helper
│  │  └─ ws.py / ui_emitter.py           # 前端协议输出辅助
│  └─ MNS_CaseHub/
│     ├─ cases/                          # 业务 case/pipeline 实现
│     └─ registry/                       # case 注册表（dataset*.json）
├─ tools/                                # 离线/批处理工具（不直接走在线请求）
├─ alpha/                                # 历史通用框架（遗留层）
├─ start.sh                              # 本地启动脚本
└─ Dockerfile                            # 镜像构建
```

### 关键文件职责（泛化）

- `main.py`
  - 接收外部请求，初始化应用与路由，挂载静态资源与 ws 入口。
  - 仅负责“服务启动与接入层”，不放重业务逻辑。
- `src/team_config.py`
  - 子服务核心编排层，承接输入、调用业务模块、组织输出。
  - 是“流程控制中枢”，应通过调用 `src/utils`/`src/services` 组合能力。
- `team_config.py`（根目录）
  - 兼容桥接入口，保证旧调用路径稳定。
  - 原则上应保持薄封装，避免再次长大。
- `src/utils/*.py`
  - 纯工具或局部能力模块（如候选提取、子进程执行、格式转换、UI payload 组装）。
  - 可单测、低耦合、可复用，是后续维护成本控制的关键层。
- `tools/*.py`
  - 工具脚本层，用于批处理/导出/查询/转换，避免直接耦合到在线请求链路。
- `src/MNS_CaseHub/registry/*.json`
  - 用例与数据集注册信息，控制可用 case、标签与入口映射。
- `src/MNS_CaseHub/cases/*`
  - 具体业务案例实现，建议把“case 专属逻辑”限制在该层，避免散落到全局。
- `config/config.yaml`
  - 运行时配置入口（地址、密钥引用、路径参数等）。
- `README.md`（各子服务）
  - 交接文档基线：必须覆盖功能边界、主链路、关键配置、启动方式、常见排障。

### 维护原则（建议）

1. 新功能优先写入 `src/`，避免把业务逻辑堆在根目录。
2. 在线主链与离线脚本分离：在线逻辑在 `src/`，批处理逻辑在 `tools/`。
3. 兼容入口保留在 `team_config.py`（根），核心实现统一收口到 `src/team_config.py`。
4. 公共能力模块化：把“可复用、可测试”的能力下沉到 `src/utils/`。
5. 历史目录（如 `alpha/`）若暂不能移除，应明确标注用途与边界，避免新逻辑继续耦合。

### 子服务关系（当前）

```text
alpha_material_sync/
  inorganic_existing_material/   # 已有无机材料：检索、筛选、补全、结果下发
  inorganic_new_material/        # 无机新材料：新材料发现主链（建议作为架构标杆）
  organic_existing_material/     # 已有有机材料：有机体系检索与补全
  env/                           # 部署环境定义与快照（不放敏感密钥）
```
