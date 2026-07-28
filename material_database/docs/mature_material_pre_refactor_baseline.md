# mature_material 改造前基线与清理边界

> 状态：代码改造前确认稿。本文只记录现状、边界和迁移约束，不改变运行行为。

## 1. 服务的唯一职责

`mature_material` 是**成熟/商品材料性质库查询服务**。它仅从本服务已经清洗、可追溯的结构化材料目录中，按材料名称、牌号、标准号、材料族、服役温度和性质条件检索，返回材料性质、筛选证据、来源和数据缺口。

它**不负责**下列工作：

- 新材料生成、晶体生成或结构预测；
- MatterGen、MatterSim、pymatgen、Materials Project 调用；
- 高熵合金（HEA/MPEA）元素配比、配方生成、成分空间搜索或性能代理优化；
- 从未入库的 PDF、网页或缺失数据中臆造材料性质。

对应的业务分流原则：

| 需求 | 应进入的服务 |
|---|---|
| 已有牌号/商品材料的性质查询、选型、比较 | `mature_material` |
| 新晶体/新材料结构生成与筛选 | `inorganic_new_material` |
| HEA/MPEA 成分设计、元素比例或配方优化 | `alloy_composition_optimization` |

## 2. 当前生产主线

当前实际执行链路为：

```text
main.py
  -> src/catalog/query.py          本地目录检索、别名解析、性质条件判断
  -> src/catalog/presentation.py   Markdown 与基于事实的图表
  -> src/catalog/narration.py      流式说明、受限 LLM 辅助表达
  -> src/catalog/assets.py         图表发布
  -> src/storage_utils.py          对象存储上传
  -> data/processed/*.csv          已清洗的可追溯材料目录
```

其中 `data/processed/` 是当前服务唯一的材料事实来源。原始数据目录只用于数据入库/清洗流程，不应在在线查询时被直接解析。

## 3. 对外接口与前端协议（当前冻结）

### 3.1 HTTP 接口

| 接口 | 用途 |
|---|---|
| `GET /` | 服务存活与契约标识 |
| `GET /health` | 服务与目录就绪状态 |
| `GET /roles` | 上游/网关发现该服务的角色元数据 |
| `POST /mature-material/constraints` | 请求约束规范化预览 |
| `POST /mature-material/query` | 同步执行目录查询并返回 manifest |
| `GET /mature-material/tasks/{taskid}` | 读取已落盘的任务 manifest |
| `GET /mature-material/tasks/{taskid}/assets/{asset_name}` | 读取本地生成的图表 |

### 3.2 WebSocket 路径说明

`/start` 和 `/mature-material/start` 不是两套不同业务：它们当前绑定到**同一个 WebSocket 处理函数**，请求、计算和返回内容完全相同。

- `/mature-material/start` 是成熟材料服务的语义化、规范路径；
- `/start` 是历史兼容路径，可能被已有前端、网关或其他上游调用方使用。

因此它们不只可能被“前端”读取；任何直接连接本服务的客户端都可能使用。当前阶段保留两个路径，后续如需删除 `/start`，必须先确认全部调用方已迁至规范路径。

### 3.3 WebSocket 返回序列

当前前端协议是混合型协议：文本边界标记、Markdown 流和 JSON 事件会交错发送。以下内容冻结，不得在清理遗留代码时改名、删除或调整顺序：

1. 文本：`[start]`
2. JSON 进度事件：`type: "progress"`
3. 文本：`<<<CONTENT_START:FILAMENT_SELECTION_OPTIMIZATION>>>`
4. 材料名称/牌号匹配的 Markdown 流
5. 文本：`<<<CONTENT_END:FILAMENT_SELECTION_OPTIMIZATION>>>`
6. 第二段进度事件及候选材料/性质 Markdown 流
7. 图表 JSON 事件，图片类型为 `MaterialsPNG`
8. 客户结论的 Markdown/文本流
9. JSON 最终结果：`type: "result"`，`data` 为完整 manifest
10. 文本：`[end]`

异常时仍发送 `type: "error"` 的 JSON 事件。

### 3.4 请求核心结构

```json
{
  "taskid": "commodity-001",
  "idea": "查询 IN718 在常温下的材料性质",
  "user_name": "upstream-agent",
  "file_metadata": [],
  "mature_material": {
    "material_queries": ["IN718", "UNS N07718"],
    "material_families": ["镍基高温合金"],
    "service_temperature_C": 21,
    "property_constraints": [
      {"property": "density", "operator": "<=", "value": 8300, "unit": "kg/m³"}
    ],
    "top_k": 10
  }
}
```

为兼容既有上游，请求中 `constraints` 及部分历史字段别名仍被接受；在协议迁移完成前不得收紧。

## 4. 角色名称迁移记录

生产查询逻辑的规范名称为 `MaterialMature`。在前端完成 `/roles` 联调验证后，已移除
`XIMUAlpha_MNS` 和 `MatureMaterialCatalogService` 的兼容 alias，以及根目录的旧转发模块。

仍保留的旧英文编排文件及 `alpha/` 框架不属于当前运行链路；它们会在单独确认后整体移除，避免将历史保留内容误当作成熟材料主线。

## 5. 遗留代码分类

### 5.1 保留：当前主线所需

- `main.py`
- `src/catalog/`
- `src/storage_utils.py`
- `src/team_config.py` 与根目录 `team_config.py`（当前为角色兼容层；后续将成为服务编排入口并保留兼容映射至协议迁移完成）
- `data/processed/`
- `README.md`、`.env.example`、`requirements.minimal.txt`、部署文件（内容需后续收敛）

### 5.2 迁移后删除：不属于成熟材料服务

- `alpha/`：旧 AI4PDE 通用框架；当前成熟材料生产入口未直接 import，目标为删除，但须先完成外部动态 import 排查与启动/接口冒烟验证；
- `src/material_workflow/`：新材料生成、验证、排序完整链路；
- `src/MNS_CaseHub/`：历史材料发现案例；
- `tools/` 中 MatterGen、MatterSim、pymatgen、Materials Project、结构渲染相关工具；`tools/clean_catalog.py` 是目录数据清洗工具，应保留并在后续迁至 `scripts/`；
- `src/team_config_en.py`：旧 MNS/AI4PDE Agent 链路，按当前约定暂不处理；
- `src/llm_utils.py`：旧 `SeLLM`/Alpha LLM 适配；生产主线未用，但仍被暂不处理的 `src/team_config_en.py` import，因此第一轮保留，待该旧配置迁移或删除后再删除；
- `src/oss_utils.py`：旧阿里云 OSS 工具，当前没有实际 import；生产使用 `src/storage_utils.py` 的 MinIO 上传，可在第一轮验证后删除；
- `ai4pde_env_full.yml`、`pip_requirements.txt`、`setup.py`：旧 AI4PDE 环境与包定义；
- `docs/new_material_pipeline_plan.md`：与本服务职责冲突的文档；
- `config/config2.yaml`、`config/puppeteer-config.json`：当前主线未使用的旧配置。

删除前应进行一次 import/运行冒烟验证，确保没有外部启动脚本仍直接引用这些路径。

## 6. 配置与运行产物治理

后续改造应完成以下收敛，但不改变当前对外协议：

- 当前图片链路为“本地 PNG → `src/storage_utils.py` 的 S3/MinIO 上传 → 公开 URL → WebSocket `MaterialsPNG` 事件 → 前端渲染”。`src/storage_utils.py` 中的 `oss_upload` 只是历史函数名，实际使用 `boto3` 和 `MINIO_*` 配置访问 MinIO；
- `src/oss_utils.py` 是另一套旧阿里云 OSS (`oss2`) 实现，含硬编码 endpoint，当前生产链路未调用；不得把它与上述 MinIO 图片链路混为两套必需依赖；
- 所有部署差异通过环境变量提供；移除代码中机器专属的 `/data/...` 默认路径；
- 将对象存储 bucket、公开 URL、凭据文件路径集中为成熟材料服务配置；
- 修正 `.env.example` 中指向已弃用 `inorganic_existing_material` 的凭据路径；
- `results/` 是运行产物，继续保持 Git 忽略；为保留期、清理策略和回归样本另立约定；
- 为 manifest 增加 `schema_version`，并定义 REST 查询与 WebSocket 完成结果的结构关系；
- 逐步统一 progress、asset、result 事件的公共元数据字段，但这属于后续前端协议版本升级，不属于本轮清理。

## 7. 改造顺序

1. 以本文件为边界，建立遗留文件的逐项删除清单；
2. 先移除与新材料/配比优化无关且不被主线 import 的代码、工具、文档和旧环境文件；
3. 对 `main.py`、最小依赖、Dockerfile、环境变量示例做成熟材料专属化；
4. 添加目录检索和 WebSocket 协议的最小回归测试；
5. 与前端/网关确认后，单独升级 `/roles`、`/start`、步骤 ID 和事件 schema；
6. 最后更新新人接入文档与部署手册。

## 8. 本轮明确不做的改动

- 不改 `/start`、`/mature-material/start`；
- 不改 `[start]`、`[end]`、`<<<CONTENT_START/END:...>>>`；
- 不改 `MaterialsPNG`；
- 不改 `FILAMENT_SELECTION_OPTIMIZATION`；
- 不改 `/roles` 的返回字段结构；
- 不在未完成契约升级前变更 `/roles`、`/start` 和 WebSocket 事件字段；
- 不改变任何现有 HTTP/WebSocket JSON 字段或事件顺序。
