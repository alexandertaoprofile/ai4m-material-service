# 导电润滑介质初筛服务：后续接入设计

## 目标与非目标

服务的目标是根据用户的材料问题，从规范证据库中检索、比较和呈现候选配方，并生成可追溯的报告式图表。它给出的是**材料初筛证据**，不是配方定型、长期可靠性承诺或机理验证结论。

因此，数据规范阶段不固化任何一个项目的“通过/不通过”。例如“室温”“电导率下限”“可接受黏度”和“耐温时长”都是一次用户任务的参数。

## 问题驱动的筛选流程

```text
用户自然语言问题
    -> 受约束的筛选请求（结构化 JSON）
    -> 参数校验、单位换算、可执行查询计划
    -> 查询规范证据库
    -> 质量与证据分级
    -> 候选表、图表、来源卡和结论边界
```

模型可以帮助将自然语言转成筛选请求、解释结果和提出追问；数值过滤、单位换算、记录关联和图表统计必须由确定性代码完成。服务不应让模型生成任意 SQL。

建议的内部请求形态如下（示例，并非固定项目标准）：

```json
{
  "object_scope": "mixture",
  "conditions": {"temperature_k": {"min": 293.15, "max": 303.15}},
  "property_constraints": [
    {"name": "conductivity", "operator": ">=", "value": 0.1, "unit": "S/m"},
    {"name": "dynamic_viscosity", "operator": ">=", "value": 130, "unit": "mPa*s"},
    {"name": "dynamic_viscosity", "operator": "<=", "value": 150, "unit": "mPa*s"}
  ],
  "evidence_policy": {
    "experimental_only": true,
    "composition": "complete_only",
    "manual_review": "exclude_or_separate"
  },
  "requested_views": ["funnel", "transport_scatter", "candidate_cards", "data_gaps"]
}
```

如果用户只问“有哪些室温电导数据”，请求中就没有黏度阈值；如果用户接受比例不完整的文献线索，则 `composition` 可改为 `include_flagged`，但结果必须分层显示。

## 候选状态（运行时生成）

每次请求都按本次条件生成下列状态，不写回原始或规范数据：

| 状态 | 含义 |
|---|---|
| `meets_requested_numeric_conditions` | 已满足本次问题的数值条件，尚未代表配方可用。 |
| `evidence_complete_for_initial_screen` | 数值、测试条件、配方完整性和来源均满足本次的初筛证据政策。 |
| `flagged_for_review` | 数值可比较，但配方不完整、相态不明、或人工复核标记未解除。 |
| `insufficient_evidence` | 缺少用户要求的性质、条件或稳定性证据。 |
| `outside_requested_conditions` | 有记录，但不满足本次问题的数值或工况范围。 |

即使是 `evidence_complete_for_initial_screen`，服务也应标注“仅初筛”；特别是热分解温度不能代替高温长期润滑/导电稳定性测试。

## 报告式可视化输出

服务按一次筛选任务生成一个不可变的结果包，而不是只返回聊天文字：

1. `summary.json`：筛选条件、数据快照版本、统计数、候选记录 ID。
2. `funnel.png/svg`：每项条件加入前后的记录数，显示数据缩减原因。
3. `transport_scatter.png/svg`：动态黏度—电导率散点，视觉编码温度和数据质量。
4. `temperature_series.png/svg`：仅在同一配方和来源条件可比时画原始温度测点。
5. `candidate_cards.json/html`：配方、条件、证据、来源、限制说明。
6. `data_gaps.png/svg`：缺失性质、比例完整性、温区覆盖和待复核分布。

PDF 样式的报告页面由这些确定性资产组装。图片必须带图题、单位、数据快照版本和查询摘要；点击候选或图例应能回到原始 `record_id` 与 `source_reference`。

## 接入 1105 成熟服务的顺序

1. 先把当前 SQLite 证据库作为只读数据源，定义查询 DTO 和结果 DTO。
2. 实现一个不含大模型的查询 API，并以固定请求做单元测试与结果快照测试。
3. 加入图表生成器与结果包存储；先验证 PNG/SVG 在服务端能稳定返回。
4. 再让成熟服务的编排层调用该 API：模型负责理解问题与叙述，筛选引擎负责事实。
5. 最后加入用户可见的追问：当需求缺少温度、黏度类型、是否接受待复核配方或“耐高温”的定义时，请用户选择，避免擅自假定。

## 当前已实现的最小接口

当前服务已挂载以下确定性接口，和原有成熟材料目录接口隔离：

- `POST /fluid-initial-screen/query`：提交结构化请求，写入一个结果包并返回候选、漏斗和图片 URL。
- `GET /fluid-initial-screen/tasks/{task_id}`：读取已生成的 `summary.json`。
- `GET /fluid-initial-screen/tasks/{task_id}/assets/{asset_name}`：返回本次查询生成的 PNG。

固定演示请求在 `tests/fixtures/fluid_initial_screen_example.json`。它仅用于验证查询流程，不是写死在服务中的业务标准。
