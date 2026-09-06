# 可回收火箭不锈钢配方设计：服务说明 v1

## 可以直接使用的能力

该服务已作为 1111“合金成分设计与服役性能筛选”的第三个模型域接入，服务名为 **可回收火箭不锈钢配方设计**。

它面向 LOX/LCH4 贮箱、箭体承压壳体、穹顶和加强件等奥氏体不锈钢结构，在已知成分—固溶处理—温度的条件下，生成受约束候选并比较：

| 输出 | 当前用途 | 覆盖条件 |
|---|---|---|
| 0.2% 屈服强度 | 比较抗永久变形能力 | 293–1273 K 短时拉伸筛选。 |
| 抗拉强度 UTS | 比较最大承载能力 | 同上。 |
| 延伸率 | 比较短时拉伸延性取舍 | 同上。 |
| 成分邻域状态 | 判断候选是否接近训练数据 | 训练邻域内 / 需要复核 / 范围外。 |
| 低温参考与验证优先级 | 支持贮箱低温研发决策 | 低于 293 K 时自动进入此模式。 |

模型验证采用完整成分—热机械状态分组的 3 折交叉验证：屈服强度 R² 0.769、MAE 16.2 MPa；UTS R² 0.922、MAE 16.3 MPa；延伸率 R² 0.833、MAE 3.29%。这些误差会随每个候选一并展示。

## 两种结果模式

| 用户目标温度 | 服务模式 | 用户得到的结果 |
|---|---|---|
| 293–1273 K | **短时拉伸配方筛选** | 候选 wt.%、屈服、UTS、延伸率、误差、成分邻域、验证重点。 |
| 20–292 K | **低温参考与验证优先级** | 301/304L 可追溯参考记录、目标温度试验建议；不输出自由配方强度。 |
| >1273 K | **需要扩展数据** | 保留需求，提示转入耐热钢或高温镍基路径进一步核对。 |

焊缝/热影响区、低温断裂韧性、疲劳和 LOX 相容性不会由拉伸预测代替；服务把它们作为与优先候选绑定的验证项输出。

## 用户输入

最简输入可只说明火箭不锈钢场景，服务会显示一套可覆盖的初始边界和工艺默认条件：293 K、固溶退火（1323 K × 3600 s、水淬）母材状态用于数值筛选；90 K（LOX）与 111 K（LCH4）固定列为低温验证关卡。板厚保持“待补充”，不虚构为模型输入。实际项目建议提供下列条件：

| 输入 | 说明 |
|---|---|
| `component` | 贮箱筒段、壳体、穹顶、加强件等。 |
| `test_temperature_K` | 评价温度；低于 293 K 自动进入低温参考模式。 |
| `element_bounds_wt_percent` | Cr、Ni 必填；Mn、Si、C、N 等可选的 `[最小值, 最大值]`。Fe 自动平衡至 100 wt.%。 |
| `processing` | `solution_treatment_temperature_K`、`solution_treatment_time_s`、`quench`（`water` 或 `air`），以及产品形态 / 熔炼路线代码。 |
| `weld_state`、`thickness_mm` | 记录结构状态，指导验证计划；当前不把它们伪装为已训练的性能特征。 |
| `objectives` | `yield_strength`、`uts`、`elongation` 的相对权重。 |

## HTTP 调用示例

### 常温至高温的候选筛选

```json
POST /alloy/propose-space

{
  "taskid": "rocket-tank-293k-001",
  "idea": "为可回收火箭 LOX/LCH4 贮箱筒段设计不锈钢候选配方",
  "alloy_optimization": {
    "model_domain": "reusable_rocket_stainless",
    "component": "LOX/LCH4 贮箱筒段",
    "test_temperature_K": 293,
    "weld_state": "base_metal",
    "thickness_mm": 4.0,
    "element_bounds_wt_percent": {
      "Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0],
      "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08]
    },
    "processing": {
      "material_state": "solution_annealed",
      "solution_treatment_temperature_K": 1323,
      "solution_treatment_time_s": 3600,
      "quench": "water",
      "product_form_code": 1,
      "melting_route_code": 1
    },
    "objectives": {"yield_strength": 2, "uts": 1, "elongation": 1},
    "verification_focus": ["cryogenic_toughness", "weld", "fatigue", "LOX_compatibility"]
  }
}
```

服务返回最多 12 个候选卡，并生成“强度—延性取舍”和“优先候选成分”两张 PNG。每张候选卡的 `applicability_domain.level` 为：

- `inside`：可在当前训练邻域内比较；
- `boundary`：保留为探索候选，需要人工复核；
- `outside`：不参与自动优先排序。

### 低温贮箱的参考与验证规划

```json
POST /alloy/propose-space

{
  "taskid": "rocket-lox-90k-001",
  "idea": "为可回收火箭 LOX 贮箱规划 90 K 不锈钢验证路线",
  "alloy_optimization": {
    "model_domain": "reusable_rocket_stainless",
    "component": "LOX 贮箱",
    "test_temperature_K": 90,
    "weld_state": "base_metal"
  }
}
```

响应为 `mode: "cryogenic_reference"`，包含最近的 301/304L 低温参考记录及“母材拉伸 → 焊缝/HAZ → 低温韧性 → 疲劳 → LOX 相容性”的建议顺序。

## 结果如何解读

> 针对 **指定部件**，在 **指定温度和固溶处理状态** 下，优先评估候选 RSS-xxx；它在当前可比较候选中取得更好的强度—延性取舍。该结果用于安排下一轮材料、工艺与验证试验，不是商品牌号、30X 性能声明或工程放行结论。

候选成分只在已记录的奥氏体不锈钢邻域内产生。服务不把 304L/301 参考性能写成候选或 30X 的实测性能。

## 已完成的服务接入

- `model_domain: reusable_rocket_stainless` 的请求解析、默认模板、REST 与 WebSocket 路径；
- 隔离数值 runner、v2 Extra Trees 模型、成分邻域门槛与低温参考层；
- 任务 manifest、Markdown 候选卡及两张既有 `MaterialsPNG` 类型图表；
- 1111 的既有 `/start`、`/alloy/start`、固定步骤 ID 与事件顺序保持不变。

## 当前数据扩展方向

为了把“低温参考与验证优先级”进一步升级为“低温自由配方预测”，需要补入同一材料状态下的准确成分、冷作量、晶粒度、板厚、焊态，以及 77/90/111 K 的屈服、UTS、延伸率。焊接、韧性、疲劳和 LOX 相容性应保留为独立模型与独立标签。
