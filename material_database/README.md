# 已有材料选型与性质核验服务（1105）

本服务从已整理的材料目录中查询和比较材料，回答“已有哪个材料符合条件、数据从哪里来”。牌号是材料的规格或型号，例如 IN718。服务保留材料状态、测试条件和来源，不生成新配比或晶体结构。

## 1. 当前能力与状态

- 支持材料名称、商品名、缩写和标准号匹配，例如 `IN718`、`Inconel 718`、`UNS N07718`。
- 支持材料类别、标准号、单点性质和温度曲线比较。
- 温度相关证据区分实测点、曲线范围内插值、最近实测点、超出范围和缺失数据；插值是依据相邻实测点计算的中间值。
- 未匹配到材料时说明目录覆盖情况，并提示继续文献检索；结果仍保留用户条件。
- 同时明确提出导电和润滑需求时，进入本服务的导电润滑剂证据流程，读取专项性质库并比较条件；普通材料继续走通用目录查询。

相近牌号和不同状态分别管理。例如 316、316L、特定样品及不同热处理状态可能具有不同性质，查询时保留这种区别。原始 PDF 尚未自动入库，来源核验及结构化导入另行进行。

## 2. 运行环境、数据与启动

随 Git 提供的通用数据包括 materials.csv、material_aliases.csv、property_points.csv、curve_data.csv、composition_long.csv 及清洗记录。导电润滑剂专项 SQLite 仅在 75 运行目录，2026-10-05 核对为 381,964,288 字节，约 364 MiB，迁移时需单独取得。原始工作簿与 PDF 用于重建和导入，存放在外部数据目录。

软件分工为 FastAPI/Uvicorn 接收请求，CSV/SQLite 查询和规则比较提供材料证据，Matplotlib 生成图像。环境定义见[环境说明](../env/README.md)，完整资源交接和最小检查见[运行说明](../docs/service_setup.md)。

| 配置 | 用途 |
| --- | --- |
| `PORT` | 默认端口 1105。 |
| `PROPERTY_DATA_ROOT` | 原始材料数据目录。 |
| `MATURE_MATERIAL_CATALOG_ROOT` | 已整理目录，默认 `data/processed`。 |
| `MATURE_MATERIAL_RESULTS_ROOT` | 任务结果目录，默认 `results/mature_material`。 |
| `MINIO_*`、`PICTURE_PUBLIC_BASE_URL` | 图片对象存储和公开访问配置，凭据放本服务 `.env`。 |

75 服务器 Web 环境为 `/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python`。在其他机器上先准备对应环境和目录，参考 `.env.example` 配置本机变量。`requirements.minimal.txt` 是依赖清单；已有生产环境复用，不在系统 Python 中直接安装。

导电润滑剂证据使用版本化 SQLite 文件 `data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite`。聊天流程按目录配置读取；独立流体 HTTP 路由当前使用服务目录下的该文件，迁移时两处路径需同时核对。

配置完成、端口空闲后，在单独联调 tmux 会话内启动：

```bash
cd /path/to/material_database
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python main.py
```

目录重建是数据维护操作，会更新整理结果；只有准备了来源文件并确认目标目录后才执行。例如：

```bash
python scripts/clean_catalog.py \
  --workbook "/data/se42/backend/property datasets/alloy_material_dataset_v0.1.xlsx"
```

原始文件、任务结果、日志和凭据与源码分开管理。正式验收后可由运维登记 `material-database-1105` 系统服务；避免与 tmux 在同一端口重复运行。

## 3. 一次请求与代码分层

```text
材料名称/类别 + 工况 + 性质要求
  → 规范化名称和单位
  → 查询已整理目录，检查材料状态及温度证据
  → 比较条件、报告通过/不通过/数据缺失的原因
  → 保存来源和任务记录，交付表格、图像及结论
```

| 位置 | 职责 |
| --- | --- |
| `main.py`、`src/team_config.py` | HTTP/WebSocket 入口和查询执行顺序。 |
| `src/settings.py` | 将环境变量转换为配置。 |
| `src/catalog/query.py`、`src/catalog/property_vocabulary.py` | 目录匹配、性质名称/单位和条件比较。 |
| `src/catalog/cleaning.py`、`scripts/clean_catalog.py` | 清洗结构化原始数据；不属于每次查询的在线步骤。 |
| `src/catalog/presentation.py`、`src/catalog/narration.py`、`src/catalog/assets.py` | 可追溯报告、文字组织和图表。 |
| `src/fluid_lubricant/` | 导电润滑剂专项查询、证据比较、短名单和图文组织。 |
| `data/processed/`、`tests/` | 整理后的目录、别名映射和已有测试。 |

HTTP 查询与 WebSocket 主流程从 main.py 调用 src/team_config.py 中的 MaterialMature.contract() 和 run()。后者选择通用目录或导电润滑剂流程；独立流体 HTTP 路由保留自己的查询适配。

数据接入与前端事件详情见[服务手册](docs/mature_material_service_guide.md)。

## 4. 输入与接口

| 接口 | 用途 |
| --- | --- |
| `POST /mature-material/constraints` | 校验请求和条件。 |
| `POST /mature-material/query` | 创建目录查询任务。 |
| `GET /mature-material/tasks/{taskid}` | 获取任务清单，即输入、状态、证据和结果记录。 |
| `GET /mature-material/tasks/{taskid}/assets/{asset_name}` | 获取任务图表。 |
| `WS /start`、`WS /mature-material/start` | 流式需求解析、名称核验、性质比较和结果。 |
| `GET /roles`、`GET /health` | 服务能力注册和目录状态检查。 |

另保留 `POST /fluid-initial-screen/query`、`GET /fluid-initial-screen/tasks/{task_id}` 和其资产路由，供结构化流体初筛调用。该 HTTP 入口与通用聊天入口分别维护，输入约定见[专项设计文档](docs/fluid_lubricant_service_design.md)。

通用任务字段外，结构化条件放在 `mature_material` 中。下面为请求格式示例：

```json
{
  "taskid": "commodity-example-001",
  "idea": "比较 IN718 的已收录密度和温度相关性能",
  "user_name": "upstream-agent",
  "file_metadata": [],
  "mature_material": {
    "material_queries": ["IN718", "UNS N07718"],
    "material_families": ["镍基高温合金"],
    "service_temperature_C": 600,
    "property_constraints": [
      {"property": "density", "operator": "<=", "value": 8300, "unit": "kg/m³", "temperature_C": 21}
    ],
    "top_k": 10
  }
}
```

可选 `upstream_evidence` 接收上游给出的材料、性质、数值、工况和来源；这些记录原样保留，只有与目录核对后的数据才标记为已核验。别名表 `data/processed/material_aliases.csv` 若匹配多个材料状态，返回歧义信息供确认。

## 5. 验证与交接

在本服务目录和已配置的服务环境中执行：

```bash
python -m unittest discover -s tests -v
```

这些测试覆盖查询流程、目录完整性、WebSocket 文本边界、进度与资产事件、最终结果及角色注册。部署或数据更新后，再检查 `/health` 和真实查询的来源、温度条件及图像展示。

维护材料数据时记录原始文件版本、清洗命令、保留/排除数据及来源定位，详见[服务手册](docs/mature_material_service_guide.md)。
