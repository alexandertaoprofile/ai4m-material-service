# material_database

成熟已有材料服务。它查询已入库且可追溯的材料数据，不调用 MatterGen、MatterSim、Materials Project 或任何新材料生成链路。

## 当前接口

- `POST /mature-material/constraints`：校验上游请求。
- `POST /mature-material/query`：创建可追溯检索任务。
- `GET /mature-material/tasks/{taskid}`：获取 manifest。
- `WS /start`：与 `/mature-material/start` 完全相同的历史兼容入口。
- `WS /mature-material/start`：流式入口，依次输出需求解析、名称/牌号核验、性质比较、结论与图表事件。
- `GET /roles`：供上游/前端发现本服务能力的兼容注册信息。
- `GET /health`：服务和数据目录状态。
- `GET /mature-material/tasks/{taskid}/assets/{asset_name}`：查询结果图表。

请求以既有 envelope 包裹专用约束：

```json
{
  "taskid": "commodity-001",
  "idea": "寻找 600°C 下强度足够的耐蚀镍基合金",
  "user_name": "upstream-agent",
  "file_metadata": [],
  "mature_material": {
    "material_queries": ["IN718", "UNS N07718"],
    "material_families": ["镍基高温合金"],
    "service_temperature_C": 600,
    "property_constraints": [
      {"property": "density", "operator": "<=", "value": 8300, "unit": "kg/m³", "temperature_C": 21}
    ],
    "upstream_evidence": [
      {"material": "IN718", "property": "屈服强度", "value": 1034, "unit": "MPa", "condition": "室温", "source": "上游数据表"}
    ],
    "top_k": 10
  }
}
```

当前版本已支持材料名称、商品名、缩写和标准号的规范化匹配（例如 `IN718`、`Inconel 718`、`UNS N07718`），以及材料族、标准号、单点性质和温度曲线筛选。温度证据会标记为实测点、曲线范围内插值、最近实测点、超出范围或缺失数据。

`upstream_evidence` 是可选的上游材料证据整理字段。服务会原样展示其数值、工况和来源，但只有目录匹配数据才标记为已核验。目录未命中时，服务不会生成替代材料建议，而会以既有流式文本提示建议进入文献筛选。

相近牌号不会自动合并。例如 316、316L、特定 SRM 样品及不同热处理状态均保留为独立材料记录。PDF 尚未自动入库，服务不会从原始 PDF 臆造性质。别名映射保存在 `data/processed/material_aliases.csv`；若一个别名映射到多个材料状态，服务返回歧义信息而不擅自选择。

当前可清洗已有的结构化商品工作簿（PDF 将单独进行可追溯抽取）：

```bash
python scripts/clean_catalog.py \
  --workbook "/data/se42/backend/property datasets/alloy_material_dataset_v0.1.xlsx"
```

## 运行

```bash
pip install -r requirements.minimal.txt
bash start.sh
```

使用 `PROPERTY_DATA_ROOT` 设置原始商品材料数据目录；使用 `MATURE_MATERIAL_RESULTS_ROOT` 设置任务结果目录。

## 回归测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖目录查询编排、数据目录完整性，以及既定 WebSocket 文本边界、唯一 progress、图片事件、最终结果事件和 `/roles` 注册字段。

部署、数据更新、前端事件约定和新人接入说明见 [服务手册](docs/mature_material_service_guide.md)。
