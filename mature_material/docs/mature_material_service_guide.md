# 成熟材料服务手册

## 服务边界

本服务只查询已清洗、可追溯的成熟/商品材料目录，用于材料名称、牌号、标准、材料族、服役温度和性质条件的核验与对比。

它不生成新晶体、不调用 MatterGen、MatterSim 或 Materials Project，也不进行高熵合金/配方的成分优化。此类需求分别交给新材料服务和合金配比优化服务。

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
bash start.sh
python -m unittest discover -s tests -v
```

关键变量：

- `MATURE_MATERIAL_CATALOG_ROOT`：已清洗目录，默认 `data/processed`。
- `MATURE_MATERIAL_RESULTS_ROOT`：manifest 和临时图表目录。
- `PROPERTY_DATA_ROOT`：原始材料数据位置，仅用于数据状态与离线清洗，不直接作为在线查询来源。
- `MINIO_*`、`PICTURE_PUBLIC_BASE_URL`：图表上传与前端图片访问。

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

## Docker

构建并启动：

```bash
bash start_docker.sh
```

脚本默认将容器内部 `1105` 映射到主机 `1105`，并把结果持久化到 `results/mature_material`。可用下列变量覆盖而不修改脚本：

```bash
MATURE_MATERIAL_HOST_PORT=20161 \
MATURE_MATERIAL_RESULTS_HOST_DIR=/srv/mature-results \
MATURE_MATERIAL_RAW_DATA_HOST_DIR='/data/se42/backend/property datasets' \
bash start_docker.sh
```

最后一个变量可选；提供后会以只读方式挂载到容器。`.dockerignore` 会排除 `.env`、运行结果和本地缓存，避免密钥被打进镜像。
