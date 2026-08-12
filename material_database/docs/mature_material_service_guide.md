# 成熟材料服务手册

## 服务边界

本服务只查询已清洗、可追溯的成熟/商品材料目录，用于材料名称、牌号、标准、材料族、服役温度和性质条件的核验与对比。

它不生成新晶体、不调用 MatterGen、MatterSim 或 Materials Project，也不进行高熵合金/配方的成分优化。此类需求分别交给新材料服务和合金配比优化服务。

## 调度输入与输出

上游只在已经掌握“已有材料锚点”时调用本服务：材料名称、厂家/牌号、标准号，或带有来源和测试工况的材料性质。`mature_material.upstream_evidence` 可传入上游整理的材料、性质、数值、单位、工况和来源；这些信息会被整理展示，但不会自动升级为目录事实。

服务产生以下业务结果：

- `catalog_matched`：目录中存在匹配材料，返回已核验性质、来源和缺失项；
- `catalogue_no_eligible_candidates`：已按明确约束评估目录候选，但没有候选同时通过；仍返回本轮约束、筛选策略及逐项通过/不通过/缺失证据，不自行放宽条件。
- `upstream_evidence_only`：已整理上游证据，但目录没有对应记录；
- `needs_literature_screening`：没有可承接的材料证据或目录记录；页面以流式 Markdown 提示建议进入文献筛选。
- `needs_screening_criteria`：用户提出开放式选材，但未给出可比较的应用、工况或性能条件；服务先收集条件，不要求用户预先知道商品牌号。

导电液体路径在数值窗口归零时返回 `fluid_no_matching_evidence`：保留温度、电学、黏度和证据质量的完整筛选漏斗，明确显示在哪一步归零，不擅自放宽窗口或推荐替代配方。上述非通过结果都不会生成替代材料、商品牌号或性质推荐。结果状态仅记录在最终 manifest 的 `data_status.outcome` 中；前端保持既有文本流、`progress` 和 `result` 事件，不需要增加新事件类型。

## 内部筛选流程

服务保留两条内部工作流。默认路径为 `mature_material_catalogue_initial_screen`：它将本轮材料、标准、温度和性质阈值保存为可审计的 `screening_request`，仅评估已清洗目录中的来源证据，并记录候选评估数和满足数。高温严格导入包属于这一通用目录路径，不单独增加前端路由或事件。

通用目录初筛不假设用户必须一次给齐参数，而按已明确的筛选维度处理：0 项只收集条件；1 项输出单条件证据地图，不作综合优先级；2 项执行交叉过滤；3 项及以上执行严格多条件证据筛选。维度包括材料锚点/体系、服役温度、应用、环境、制造约束，以及每一项独立的性能目标（两个不同性质阈值计作两个维度）。自然语言中明确写出的“指标 + 比较符 + 数值 + 单位”会转为硬阈值，例如“导热率≥100 W/(m·K)、屈服强度≥600 MPa”；“指标 + 下限-上限 + 单位”会展开为两条上下界，例如“屈服强度600-800 MPa”。裸数字或没有单位的定性表述不会被猜成阈值。每个 manifest 的 `screening.strategy` 记录实际模式，`screening.next_action` 明确为 `await_user_criteria` 或 `return_catalogue_evidence`；上游在前者时必须继续追问，不能假设材料体系、工况或转入文献检索。

“越高越好”“越低越好”是方向偏好，不是硬阈值。它们写入 `preference_goals`：若同时存在硬阈值，服务先按阈值过滤、再按偏好排序；若只有偏好，则返回 `catalogue_evidence_landscape` 或 `fluid_evidence_landscape`，只展示来源证据的方向排序，不声称任何候选已经通过选型。通用目录支持如抗拉/屈服强度、导热率、硬度、延伸率、密度等已映射性质；导电液体路径支持电导率（最大化）、电阻率和动态黏度（最小化）。

当请求同时明确润滑与导电意图时，服务改用 `conductive_lubricant_initial_screen`。该路径使用液体证据库的电学/黏度初筛口径，不替代通用材料目录，也不把初筛结果宣称为长期润滑验证。

两条工作流共用数值语言的比较符和区间展开组件，确保 `≥`、`≤`、`-`、`–`、`~`、`至`、`到`、`和`、`与` 等写法含义一致；各自仍独立维护可接受的性质名称、单位和证据库查询语义。

上游执行摘要中的材料缩写与明确阈值也会被保留为本轮条件。例如 `PEEK/碳纤维`、`导热≥10 W/(m·K)`、`层间结合力≥20 MPa` 分别形成材料锚点、导热系数和界面结合力约束；若指定体系或该性质证据尚未入库，服务报告“目录未匹配/性质缺失”，而不误报为“没有提供筛选条件”。

通用目录通过 `src/catalog/property_vocabulary.py` 统一维护请求性质词典。目前覆盖密度；拉伸、屈服、压缩、弯曲、剪切/界面结合、疲劳强度；弹性/剪切模量、硬度、延伸率；导热系数、比热、热扩散率、热膨胀系数、HDT、Tg；电导率、电阻率、介电常数/强度；吸水率、表面粗糙度与单位质量成本。词典负责同义词和单位识别，不把未入库性质伪造成数据事实。

通用目录在存在性能约束时也采用初筛展示，而不是旧式全量性质清单：依次输出本轮筛选条件、逐步保留数的筛选漏斗、每项约束的 `pass/fail/missing` 汇总、候选核验表和结论；漏斗图直接复用导电液体的锥形分层视觉模板，即使因性质缺失而归零也会显示。若该性质存在可比较数值，则额外生成候选性质分布图，绿色/橙色区分该性质通过与不通过，红色虚线标出筛选边界。

## 目录职责

```text
main.py                 HTTP/WebSocket 传输与前端事件
src/team_config.py      MaterialMature：请求解析、目录查询编排、manifest
src/catalog/            目录查询、性质证据、图表与叙述
src/settings.py         环境变量和路径
src/storage_utils.py    MinIO/S3 图片发布
data/processed/         已清洗的可查询目录
scripts/clean_catalog.py 工作簿清洗工具
tests/                  业务、数据与前端协议回归测试
```

`alpha/`、`src/team_config_en.py`、`src/llm_utils.py` 和 `config/config.yaml` 是共用框架保留内容，不属于本服务的运行主链，不应在成熟材料业务代码中新增对它们的依赖。

## 配置与本地启动

复制 `.env.example` 为本机 `.env`，填写 MinIO 变量；`.env` 已被 Git 忽略，不能提交。默认服务端口为 `1105`。

```bash
cd /path/to/material_database
tmux new-session -s material-database-1105
python main.py
python -m unittest discover -s tests -v
```

调试与联调使用 tmux。稳定运行的实例由运维配置后通过
`service material-database-1105` 管理；不得通过 `start.sh`、`nohup` 或 Docker 常驻。

关键变量：

- `MATURE_MATERIAL_CATALOG_ROOT`：已清洗目录，默认 `data/processed`。
- `MATURE_MATERIAL_RESULTS_ROOT`：manifest 和临时图表目录。
- `PROPERTY_DATA_ROOT`：原始材料数据位置，仅用于数据状态与离线清洗，不直接作为在线查询来源。
- `MINIO_*`、`PICTURE_PUBLIC_BASE_URL`：图表上传与前端图片访问。

### 合金需求的目录基准映射

当上游先需要为自定义合金建立商品材料对照、再进入成分优化时，可以只传合金体系或
元素组合。例如 `Fe-Cu-Al-Ni-Co` 的高温多主元合金会映射到当前目录中已入库的镍基高温
合金和奥氏体不锈钢记录。输出明确标为“目录基准”，不宣称这些商品牌号就是目标成分。

数据库不会从长历史上下文中扫描旧的 `PLA`、`ASA` 等别名作为当前候选。若要检索这些
商品材料，调用方必须在当前 `mature_material.material_queries` 或当前执行说明中明确给出。
元素比例、原子百分比、成分空间和候选配比计算仍应在基准检索后交由材料配比优化服务。

## 前端事件契约

保留两个等价 WebSocket 地址：`/start`、`/mature-material/start`。

正常成功流程依次发送：

1. 文本 `[start]`；
2. 一条 `type: "progress"` JSON，步骤 ID 固定为 `FILAMENT_SELECTION_OPTIMIZATION`；
3. 两段由 `<<<CONTENT_START/END:FILAMENT_SELECTION_OPTIMIZATION>>>` 包围的 Markdown；
4. 若图表发布成功，一条 `type: "MaterialsPNG"` JSON；
5. 一条 `type: "result"` JSON；
6. 文本 `[end]`。

`/roles` 继续使用既有字段结构；规范角色类名为 `src.team_config.MaterialMature`。修改这些字段、文本边界、图片类型或事件顺序前，必须先更新 `tests/test_mature_material_service.py` 并完成前端联调。

## 更新材料目录

1. 使用原始工作簿生成候选目录：

   ```bash
   python scripts/clean_catalog.py --workbook /path/to/catalog.xlsx --output data/processed
   ```

2. 运行回归测试，特别是数据完整性测试。
3. 抽查材料 ID、别名、来源定位、单位和温度；不得将相近牌号或不同热处理状态强行合并。
4. 在前端验证至少一个名称查询和一个带性质阈值的查询。

PDF 不能直接作为在线事实来源；完成可追溯抽取并入库后才可参与查询。

## 运行方式边界

Docker 与 `start_docker.sh` 不属于本服务的标准开服路径，也不得用于常驻运行。
调试、联调使用 tmux；稳定实例由运维通过 `service material-database-1105` 管理。
任何容器化迁移都必须另行完成前端协议、数据挂载、`.env`、资产发布和端口的验收，不能用
临时脚本替代现役服务管理。
