# 合金成分优化服务：改造前基线

> 状态：盘点确认稿（2026-07-23）。本文只记录当前代码、运行产物和可见调用关系，不修改业务代码、运行配置、接口或文件。后续整改以 `../docs/material_service_architecture_standard.md` 为目标，但以本文件记录的真实协议和依赖为迁移前提。

## 1. 服务边界

`alloy_composition_optimization` 的生产职责应限定为：HEA/MPEA（以及明确要求成分设计的高温合金）的元素空间、原子百分比边界、工艺/温度条件、性能代理评估、不确定性/适用域判断和候选配比提议。

当前主线已经在请求判定中拒绝非合金成分设计：没有 HEA/MPEA/高熵/多主元或高温合金配比意图的请求会报错；`/roles` 也把已有牌号/商品材料查询、FDM/FFF 耗材筛选和明确化学式的新晶体生成列为排除项。

本服务不应承担：

- 成熟/商品材料牌号、性质或标准事实库查询；该职责属于 `mature_material`。
- 任意无机晶体生成、MatterGen、MatterSim、pymatgen 结构验证或 Materials Project 竞争相查询；该职责属于 `inorganic_new_material`。
- 将代理模型输出、训练域判断、模板推断或外部模型结果包装为实验、DFT 或工程定型结论。

现有结果文案已明确“适合进入下一步验证，不等同于已确认的工程用材”；历史审计也确认当前 HEA/MPEA 链路是实验数据驱动的代理评价器，不是 CALPHAD 或 PINN。此表述必须保留并在后续测试中固化。

## 2. 当前真实执行链路（A）

### 2.1 直接 HTTP / WebSocket 主线

```text
tmux / start.sh
  -> 指定的 ai4m-service Python 执行 main.py
     -> FastAPI HTTP 与 WebSocket 路由
        -> _requirement_plan()：规则模板推断、待确认项和来源标记
        -> _contract()：合金边界检查、请求标准化
        -> _run_runner()：写入 task 级 runner_request.json
           -> micromamba run -p $HEA_SURROGATE_ENV_PREFIX
              $HEA_SURROGATE_ROOT/tools/service_runner.py
           -> task 级 runner_response.json
        -> _enrich()：模型证据、代理评价函数、结论和机器交接字段
        -> _render()：本地 PNG 与 summary.md
        -> manifest.json
        -> MinIO 发布（可配置；失败不终止结果）
        -> Markdown 流 + MaterialsPNG 事件 + result JSON
```

`main.py` 是目前真正的 HTTP/WebSocket 传输入口，且它同时包含请求解析、服务编排、子进程调用、图表渲染、任务落盘、资产发布和路由。这与目标的“薄入口”不符，但没有证据表明可以在当前轮直接移动任何一项。

数值模型不在本仓库：主服务默认使用 `$HEA_SURROGATE_ROOT`（代码默认 `/data/se42/hea_surrogate`）中的 `tools/service_runner.py` 与 `models/*_ensemble.joblib`。`_runner_ready()` 要求屈服强度、硬度、相分类三个 ensemble 同时存在；未就绪时返回错误而不生成预测。当前仓内运行产物显示 runner 的请求、响应和 manifest 会一并落在 `results/alloy_composition_optimization/<taskid>/`。

当前首条已实施训练线是 `hea_mpea`。代码也接受 `conventional_alloy` 与 `refractory_calculated` 域名，但本仓没有其独立模型、数据契约或验收测试；因此不能将“接口可接受”当成“已完成可用”。

### 2.2 母服务/角色发现链路

```text
GET /roles
  -> 返回 __module_class_name = src.team_config.XIMUAlpha_MNS
     -> src.team_config.XIMUAlpha_AlloyCompositionOptimization
        -> Coding.run()
           -> 与直连 WS 共用 _proposal()
           -> prepare_public_assets() / emit_result_content()
           -> emit_public_asset_events() / result JSON
```

`src/team_config.py` 是当前实际的服务级角色编排入口，但仍直接依赖 `main.py` 的私有函数和结果路径，且本身负责 WebSocket 发送。因此它还不是统一规范所定义的纯服务编排层。根目录 `team_config.py` 则是历史导入桥接和 CLI：它加载 `alpha.Team`，再 `from src.team_config import *`，并导出 `XIMUAlpha_MNS`。

`XIMUAlpha_MNS` 目前是 `XIMUAlpha_AlloyCompositionOptimization` 的兼容 alias；`/roles` 的 `addresses` 与 `__module_class_name` 都显式发布该名称。因此在前端/母服务完成动态导入和角色发现验证前，不能删除或全局替换它。

### 2.3 展示和资产链路

`_render()` 在任务的 `presentation/` 目录生成：

- `screening_funnel.png`；
- `strength_hardness_tradeoff.png`（有候选时）；
- `composition_percentiles.png`（有候选时）；
- `summary.md`。

`src/alloy_workflow/assets.py` 仅发布 PNG：本地文件经 `src.storage_utils.oss_upload()` 上传至 S3 API 兼容的 MinIO，bucket 固定传入 `alpha`，object key 为 `materials/modelfiles/image/<taskid>/alloy_composition_optimization/<filename>`，再以 `PICTURE_PUBLIC_BASE_URL` 拼接公开 HTTPS URL。任务 manifest 中的 `presentation.assets` 是本地 `/alloy/tasks/.../assets/...` URL；公网 URL 当前只用于 WebSocket 资产事件和嵌入 Markdown，**不会回写 manifest**。

`src/storage_utils.py` 的 `oss_upload` 是历史命名，实际实现为 `boto3` S3 client，读取 `MINIO_*` 环境变量；这是现役 MinIO 调用路径。`src/oss_utils.py` 则是另一套 `oss2`、硬编码北京 endpoint 的阿里云 OSS 实现，当前生产根 `main.py`、`src/team_config.py` 和 `src/alloy_workflow/` 无 import。两者不能仅凭文件名合并或删除；本轮证据支持“MinIO 为现役资产发布、旧 OSS 未被现役根引用”，但尚未获得线上上传与公网读取成功的验收记录。

### 2.4 当前非主线但可达的耦合

当 `ALLOY_PRESENTATION_LLM` 未禁用（默认 `true`）且 `config/config.yaml` 可读时，`src/alloy_workflow/presentation.py` 会使用 OpenAI 兼容客户端逐字转发已确定的 Markdown，并在运行时 import `src.material_workflow.llm_streaming.stream_llm_response`。该无机模块不是合金领域计算，但在默认展示配置下可达；迁移展示流之前必须替换或隔离此依赖并做协议回归。

## 3. 当前对外协议与前端资产（B，冻结）

### 3.1 HTTP 接口

| 接口 | 当前作用 |
|---|---|
| `GET /` | 服务名、状态、runner 就绪状态 |
| `GET /health` | runner 就绪状态和环境前缀 |
| `GET /roles` | 上游/网关角色发现元数据 |
| `POST /alloy/requirements/preview` | 只返回规则模板推断与待确认项 |
| `POST /alloy/propose-space` | 提议候选、生成本地展示产物并返回完整结果 |
| `POST /alloy/evaluate` | 单个约束评估；落盘 manifest |
| `POST /alloy/evaluate-batch` | 批量候选评估；直接返回结果 |
| `GET /alloy/tasks/{taskid}` | 读取任务 manifest |
| `GET /alloy/tasks/{taskid}/assets/{asset_name}` | 内联读取任务本地展示资产 |

WebSocket 的 `/start` 和 `/alloy/start` 绑定同一处理函数：前者是历史兼容路径，后者是语义路径。没有调用方证明前，两者都必须保留。

请求 envelope 当前接受 `taskid`、`idea`/`content`/`query` 等上下文字段、`user_name`、`file_metadata`，并优先读取 `alloy_optimization`，兼容 `hea_optimization` 与 `constraints`。核心计算字段包括 `model_domain`、`composition`、`allowed_elements`、`element_bounds_at_pct`、`processing_method`、`test_temperature_C`、`objectives` 与 `constraints`。不可在迁移时收紧这些兼容入口。

### 3.2 WebSocket 正常事件顺序

下列消息类型、标记、字段和顺序是当前代码事实，应作为后续契约测试基线：

1. 文本：`[start]`。
2. JSON：`version: "1.0.0"`、`agent: "alloy_composition_optimization"`、`type: "progress"`；`data.id` 与 `data.stepId` 均为 `FILAMENT_SELECTION_OPTIMIZATION`，状态 `completed`，并携带需求模板 `result`。
3. 文本（同一次发送）：`<<<CONTENT_START:FILAMENT_SELECTION_OPTIMIZATION>>>`、合金需求解读 Markdown、`<<<CONTENT_END:FILAMENT_SELECTION_OPTIMIZATION>>>`。
4. JSON progress：同一 agent/request_id/step ID，状态 `in_progress`。
5. 结果 Markdown 文本块：再次使用同一 `<<<CONTENT_START/END:FILAMENT_SELECTION_OPTIMIZATION>>>` 边界；若 MinIO 成功，块中包含 `<img src="公网 URL" ...>`。
6. 每个成功发布的 PNG 一条 JSON 资产事件：`step_id`、`stepId`、`title`、`name`、`docs`、`url`、`type: "MaterialsPNG"`、`description`。当前没有 GLB、GIF、CIF 或其他资产事件。
7. JSON 最终事件：`type: "result"`，`data` 是完整计算结果/manifest 内容。
8. 文本：`[end]`。

出错时尝试发送 `type: "error"`、`agent: "alloy_composition_optimization"`、`data: <错误字符串>`。如果资产发布失败，计算仍继续，但会额外先发一条 `progress`（`status: "failed"`）；该行为同样需要在回归测试中覆盖。

角色 `Coding.run()` 的序列不同于直连 WebSocket：它不发送 `[start]`、需求解读块、第二条 in-progress 事件或 `[end]`，而是先发一条 `in_progress` progress，随后结果块、资产事件和 result JSON。这是两条既有入口的协议差异，不应在无联调证据时“顺手统一”。

### 3.3 结果事实与限制

结果包含候选成分（at.%）、强度/硬度预测均值与集成离散度、相概率、相风险、训练适用域、筛选条件、采样统计、候选分位区间、模型证据和下游交接字段。现有 manifest 没有 `schema_version`，且 HTTP `evaluate` 的 manifest envelope 与 `propose-space` 的结果结构不同；这是协议治理风险，不能通过静默字段改名修复。

## 4. 与两个已整改服务的差异（C）

| 方面 | `mature_material` | `inorganic_new_material` | 当前合金服务 |
|---|---|---|---|
| 领域实现 | `src/catalog/`，本地清洗 CSV 事实库 | `src/material_workflow/`，晶体生成/验证/排序 | `main.py` 内嵌规则、runner、渲染；仅有小型 `src/alloy_workflow/` |
| 主线编排 | `src/team_config.py` + 专用目录模块 | `src/team_config.py` + workflow | HTTP 主线绕过 `src/team_config.py`；角色入口反向 import `main.py` |
| 数据/模型 | 服务内 `data/processed/` | 外部模型与任务产物 | 外部 `hea_surrogate` 模型/报告；本仓无正式 `data/` 数据契约 |
| 测试 | 有服务测试 | 有契约与约束提取测试 | 没有 `tests/` 目录 |
| 资产 | MinIO/S3 适配 + PNG | PNG/GIF/GLB 等 | 当前仅本地/MinIO PNG；无 GLB 需求 |
| 旧结构 | 已有较多清理记录 | 仍保留必要的 MatterGen 兼容链 | 同时存在完整 `alpha/`、`MNS_CaseHub`、无机 workflow、旧 MNS 配置 |

因此改造不能照搬无机服务的 MatterGen/MatterSim/GLB 职责，也不能把成熟材料 CSV 检索复制进来。应只借鉴其目录职责、测试和契约管理方式。

## 5. 遗留、兼容与风险分类（D）

### 5.1 当前主线必须保留

- `main.py`、`src/team_config.py`、`src/alloy_workflow/`、`src/storage_utils.py`：均在直连或角色主线有明确 import/调用。
- `start.sh`、服务本机 `.env`、`.env.example`、`deploy/`：当前运行/部署路径需要先逐项核验；`.env` 已被 Git ignore，不得提交。
- `results/`：当前运行产物和回归证据，根仓 `.gitignore` 已忽略；不得作为源码改造删除对象。
- `XIMUAlpha_MNS` alias、`/roles` 字段、`/start`、`FILAMENT_SELECTION_OPTIMIZATION`、文本边界标记和 `MaterialsPNG`：现有发现/前端兼容面。
- `alpha/`：`src/team_config.py`、根 `team_config.py` 仍 import `Action`、`Role`、`Team` 等；在替换角色兼容层前不能删除。
- `src/material_workflow/llm_streaming.py`：默认 LLM 展示路径运行时引用。其余无机模块是否可删需要先完成该展示依赖的本地替代和 import 验证。
- `config/config.yaml`：默认展示 LLM 路径读取；该文件目前被 Git 跟踪且含 API 配置/机器地址，属于高优先级密钥与部署配置治理风险，不能直接删。

### 5.2 有明确删除候选、但必须先取得删除证据

- `src/team_config_inorganic_legacy.py`、`src/material_workflow/` 中除 `llm_streaming.py` 外的无机晶体/MatterGen/MP/ALIGNN 代码，以及 `src/MNS_CaseHub/`：当前生产根没有 import；但仓内旧脚本/旧配置仍引用，必须先做外部动态 import、tmux 启动器和前端调用方核验。
- `tools/mp_*`、`tools/run_mattersim_evaluation.py`、`tools/run_pymatgen_validation.py`、`tools/render_new_material_assets.py`、`tools/structure_to_glb.py`、`tools/extxyz_to_animated_glb.py`、`tools/adit_pymatgen_eval.py`、`tools/run_refractory_alloy_demo.py`：属于无机/MP/MatterGen/结构资产链路，主线未调用。`tools/setup_hea_surrogate_env.sh` 是合金 runner 环境工具，应保留；`tools/run_case_entry.py` 的实际使用需单独确认。
- `src/oss_utils.py`：旧阿里云 OSS 实现，现役 MinIO 路径不引用；删除前要检查外部脚本和未迁移的旧配置。
- `src/team_config_en.py`、`src/llm_utils.py`、`config/config2.yaml`、`config/puppeteer-config.json`、`ai4pde_env_full.yml`、`pip_requirements.txt`、`setup.py`、根 `utils.py`、Dockerfile、`start_docker.sh`：存在明显旧 AI4PDE/MNS 或 Docker 遗留特征，但统一原则要求先保留 `src/team_config_en.py`、`src/llm_utils.py`、`config/config.yaml` 和 `alpha/`。其余项也须在启动器、CI、动态 import 和依赖安装审计后再定删除。

### 5.3 风险项

- **分层风险**：`main.py` 承担计算、文件、渲染、对象存储和协议；`src/team_config.py` 反向依赖它。移动前需先抽内部请求/结果 schema、领域服务和基础设施适配器。
- **前端契约风险**：直连 WS 与角色入口的事件顺序不同；资产发布失败还会插入 failed progress。未经抓包和契约测试不能统一。
- **对象存储风险**：现役 MinIO 与旧 OSS 并存，`oss_upload` 名称容易误导；公网 URL 并未写入 manifest；尚无本机真实上传/读取验收。
- **配置与安全风险**：`.env.example` 含机器专属路径和公网 URL；代码默认值也包含 `/data/...` 路径；Git 跟踪的 `config/config.yaml` 含 API 配置，需在不泄露密钥的前提下迁移至本机 `.env`，并提供无密钥 `.env.example`。
- **运行方式风险**：用户要求 tmux 启动；当前存在 Dockerfile、`start_docker.sh` 和 SysV wrapper，均不应在本轮新增或作为目标运行方式。现有部署文件是否仍被运维使用未确认。
- **模型边界风险**：规则模板可自动填补元素、工艺与温度；默认阈值也可能自适应生成。必须持续以 `field_provenance`、`default_assumptions`、适用域、不确定性和“非最终配方”文案区分用户输入、推断和预测。
- **领域污染风险**：仓内 379 个 `alpha/` 文件、约 25 个无机 workflow 文件和 MNS case 目录使误导入、误删或误路由风险很高。

## 6. 分阶段整改计划（E）

### 阶段 0：冻结基线与取证

1. 将本文作为删除和重构的前置清单；记录 `main.py`、角色入口和资产入口的引用证据。
2. 从真实前端/母服务采集直连 `/alloy/start`、历史 `/start` 与 `Coding.run()` 三条链路的完整事件序列；确认 `/roles` 的动态导入方。
3. 用一张真实 PNG 验证 MinIO 上传、公开 URL 读取和前端渲染，并记录不含凭据的验收证据。
4. 清点 tmux 实际启动命令、服务本机 `.env` 读取顺序和外部 `hea_surrogate` 的数据/模型版本来源。

### 阶段 1：先加测试和可迁移契约，不改外部行为

1. 新增 HTTP、WebSocket 和角色入口的契约测试：路径、事件顺序、`[start]/[end]`、内容边界、step ID、`MaterialsPNG`、失败资产事件与 result envelope。
2. 固化内部请求、候选、模型证据、资产和 manifest 的 schema；为新 manifest 引入版本字段时，同时保留现有字段。
3. 建立 HEA/MPEA 数据说明、训练/报告定位和“代理非实验/DFT”断言测试；为尚未落地的 `conventional_alloy`/`refractory_calculated` 明确拒绝或独立准备状态。

### 阶段 2：按职责迁移主线

1. 建立合金专用包（目标为 `src/alloy_composition_optimization/`）：`api/`、`application/`、`domain/`、`infrastructure/`、`schemas/`、`compat/`、`settings.py`。
2. 将 HEA 请求解析、成分约束与结果模型移到 domain/schemas；将外部 runner、任务文件、图表和 MinIO 分别移到 infrastructure；将提议、评估和 manifest 编排移到 application。
3. 让新的 `src/.../team_config.py` 成为唯一服务级编排入口；`main.py` 只负责创建 app、注册 HTTP/WS 路由；历史 Alpha `Role` 与 `/roles` 映射放入 compat。
4. 先以适配层保持现有 URL、事件字段、标记文本、资产类型和顺序不变，再切换调用点。

### 阶段 3：配置、存储和运行治理

1. 将实际读取的环境变量集中到 settings；移除代码中的机器路径、bucket 和公网域名默认值，保留不含密钥的 `.env.example`。
2. 将 `config/config.yaml` 的机密/部署内容迁至本服务本机 `.env`，确认 Git 追踪历史处理方案后再改；不在文档或提交中暴露值。
3. 把 MinIO/S3 适配器改为中性命名并明确 object key/公开 URL 策略；确认后只保留一套现役适配器。删除旧 OSS 前保留引用报告。
4. 规定结果保留期、清理方式和回归样本；不提交运行结果。

### 阶段 4：逐项清理与交接

1. 对每个候选删除项出具“仓内引用、外部启动器、动态 import、冒烟测试、前端契约测试”证据后再删。
2. 优先清理无机生成/MP/GLB/MNS 业务遗留，随后处理 Docker/旧环境/旧配置；遵守不新增 Docker 或启动脚本、以 tmux 为实际运行方式的约束。
3. 在确认前端和母服务不再依赖后，再处理 `XIMUAlpha_MNS`、`/start`、旧 `team_config` 桥接和 `alpha/`。
4. 最后补齐 README、运行手册、数据/模型说明、前端事件契约文档、故障处理与交接指南。

## 7. 本轮明确不做

- 不修改任何业务代码、接口、事件、资产类型、目录或启动文件；
- 不删除 `alpha/`、`src/team_config_en.py`、`src/llm_utils.py`、`config/config.yaml` 或任何旧文件；
- 不运行 HEA runner、MatterGen、MatterSim、MP、GPU 作业或真实对象存储写入；
- 不推送 Git，不提交 `.env`，不新增 Docker 或启动脚本；
- 不把 GitHub 已删除的旧服务恢复到本服务。
