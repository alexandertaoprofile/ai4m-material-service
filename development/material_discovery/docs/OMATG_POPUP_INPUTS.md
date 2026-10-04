# 晶体结构发现服务（1115）：上游输入与弹窗说明

## 选择哪条路线

| 用户已给信息 | 执行路线 | 弹窗需要确认 |
|---|---|---|
| 明确整数化学式，如 LiAlO2 | CSP | 化学式、每胞化学式单位 Z；原子数不能超过20 |
| 元素组合，如 Li、Al、O，但无计量比 | DNG → 直接松弛与性质筛选 | 哪些只是允许、哪些必须出现；每胞原子数 |
| 只有材料大类/性质，如“半导体、较大带隙” | 用户明确同意开放化学空间后走 DNG | 开放发现开关、探索方向、可评估的性质目标 |
| 只有已有合金 wt.% / at.% 配方或牌号 | 尚不足以进入固定计量 CSP | 先确认整数原子计数的代表化学式，不默默把比例取整 |
| 合金配比优化、聚合物/树脂、独立小分子、非晶、合成路线 | 转对应服务/专项模型 | 按生成对象和任务类型选择对应计算流程 |

材料大类用于说明应用方向；组成和性质条件通过下表字段设置。“钙钛矿”结构类型需结合生成后的晶体核验，压电方向需进一步计算或测量压电响应。“较稳定/高带隙”作为比较方向，具体数值门槛由用户确认。默认 E_hull=0.05 eV/atom 是稳定性参考线；超过仍继续结构和性质评价，并提示核验。用户明确设置的硬门槛用于结果判定。

## 建议弹窗字段

| 字段 | CSP | DNG | 说明/默认 |
|---|---|---|---|
| `generation_mode` | `csp` | `dng` | 可填`auto`，有化学式则CSP，无则DNG |
| `target_formula` | 必须 | 不填 | 整数计量，支持括号；小数占位、部分占位和wt.%不可直接使用 |
| `formula_units` | 确认，默认1 | 不填 | Z：晶胞含几个给定化学式单位；候选数量由 `max_candidates` 设置 |
| `allowed_elements` | 可由化学式解析 | 限域发现必须 | “只能出现这些元素”；可以只出现其中一部分 |
| `required_elements` | 可选校验 | 建议提供独立开关/多选 | “每个列出元素都必须出现”；未填则不强制全含 |
| `excluded_elements` | 可选 | 可选 | 不得与允许或必含集合冲突 |
| `num_atoms` | 由化学式×Z计算 | 默认8，范围2–20 | 当前模型每胞最多20原子；超出范围时提示调整输入 |
| `max_candidates` | 默认10 | 默认10 | 当前范围1–10；默认 DNG 只生成这10个，后处理不另生成结构 |
| `seed` | 默认42 | 默认42 | 用于复现实验；GPU数值环境可能使近阈值结果变化 |
| `refine_with_csp` | 不需要 | 可选，默认`false` | 只有明确设置`true`，才选取DNG松弛初筛中凸包能估算最低的合规组成，再用seed+1追加同样数量的CSP构型 |
| `material_family` | 可选背景 | 推荐背景 | 描述任务，不自动强制组成或结构拓扑 |
| `allow_open_chemical_space` | 不需要 | 无元素时必须明确确认`true` | 由用户授权开放元素空间；已有上文用于说明场景 |
| `target_properties.energy_above_hull` | 稳定性参考线 | 稳定性参考线 | 单位eV/atom，默认0.05；用于生成后的稳定性评价。超过仍继续计算并提示核验，明确硬门槛时按门槛判定 |
| `target_properties`中其他接入性质 | 可选比较目标 | 可选比较目标 | 标量作为参考目标；范围门槛放在 `property_constraints` |
| `property_constraints` | 可选硬范围 | 可选硬范围 | 明确min/max和单位，缺预测值不算通过 |
| `symmetry_constraints` | 可选后筛 | 可选后筛 | `non_centrosymmetric`布尔、`space_group_number`1–230；以松弛后结构判定 |
| `validation_targets` | 可选后续关注 | 可选后续关注 | 如`{"ionic_conductivity":null}`，记录后续需要计算或实验核验的性质 |

元素最多8种，当前物种索引支持原子序数1–100；各组合的训练覆盖和参考相数据需按实际模型及参考库核对。元素限定通过服务采样掩码实现，生成后逐项检查必含条件。若本次候选均未满足条件，报告具体原因并保留原输入，供用户调整探索范围。

### 可作为硬范围的性质

当前在75服务器实测可用：`band_gap`（eV）、`bulk_modulus` / `shear_modulus`（GPa）、`electron_effective_mass` / `hole_effective_mass`（m0）。这些是 ALIGNN 结构模型快速预测，用于初步比较。屈服、蠕变、离子电导、压电系数、导热率等需求记录为后续计算或实验核验事项。

契约预留 `dielectric_constant_x`（单位`1`，x方向分量）。当前缺少对应模型权重，官方下载入口在该服务器返回403；补齐并验证权重后启用该选项。已有请求若包含该硬门槛，结果明确标注缺少预测值及原因，等待补充计算。

对称性条件根据优化后的实际结构判定。压电应用还需进一步计算或测量压电系数，并核验工作条件。

## 上文如何传递

保留完整当前需求/上文用于解释，但将当前弹窗确认的条件放进现有`new_material`对象。当前明确结构化条件优先。不要把旧服务的输出、代码、其他候选公式或模型的猜测当成已确认化学式。若要沿用上文某一个候选，在弹窗预填并让用户确认该候选的组成。

先调用已有 `POST /new-material/constraints`，做无GPU预校验；422时显示具体冲突/缺失原因。确认后沿用现有 `/start` 或 `/new-material/start` 信封提交，不新增WebSocket事件。现有一次性弹窗已扩展为“化学式 / 元素 / 明确开放探索”三种文本写法，事件结构和 `element_system` 回答 ID 不变，详见 `REQUIREMENT_REVIEW.md`。

### CSP 请求示例

```json
{
  "taskid": "csp-example",
  "idea": "探索LiAlO2的低凸包能构型，比较带隙",
  "new_material": {
    "generation_mode": "csp",
    "target_formula": "LiAlO2",
    "formula_units": 2,
    "max_candidates": 10,
    "seed": 73,
    "target_properties": {"energy_above_hull": 0.05},
    "property_constraints": {"band_gap": {"min": 2.0, "unit": "eV"}}
  }
}
```

这里是8原子晶胞。带隙下限是该示例用户明确给出的门槛，不是服务默认值。

### 元素 DNG 请求示例

```json
{
  "taskid": "dng-example",
  "idea": "在Li-Al-O白名单内探索低凸包能晶体，Li和O必须出现，Al可以出现",
  "new_material": {
    "generation_mode": "dng",
    "allowed_elements": ["Li", "Al", "O"],
    "required_elements": ["Li", "O"],
    "num_atoms": 8,
    "max_candidates": 10,
    "seed": 51,
    "target_properties": {"energy_above_hull": 0.05}
  }
}
```

若三种元素均必须出现，将`required_elements`也设置为三种。DNG候选同时包含组成与晶体结构，默认直接松弛、计算已接入性质并筛选，不再额外跑CSP；限定元素与开放化学空间都遵循此规则。

### 选定组成后继续探索

通常先让用户查看DNG候选，在弹窗确认某个候选的整数计量化学式及Z，再提交新的CSP请求。这时服务探索该组成下的其他晶格与原子排布，独立任务保留来源候选ID供追踪。

若用户明确希望同一次任务自动追加，可以传`refine_with_csp: true`。它按松弛收敛与凸包能可评估的DNG候选中最低凸包能选择组成，再生成10个CSP构型，故总预算可为DNG 10+CSP 10；DNG零合规时不启动CSP，也不换元素。此选择尚未证明所有性质均达标，最终仍逐项后筛，且重新采样不保证优于来源DNG。

### 无元素的开放 DNG

```json
{
  "taskid": "open-dng-example",
  "idea": "开放探索半导体晶体，关注带隙和结构稳定性",
  "new_material": {
    "generation_mode": "dng",
    "allow_open_chemical_space": true,
    "material_family": "半导体",
    "num_atoms": 8,
    "max_candidates": 10,
    "property_constraints": {"band_gap": {"min": 2.0, "unit": "eV"}}
  }
}
```

这是先在模型化学空间生成候选，再按已接入性质评价的流程。缺少组成线索时，先请用户选择化学式、元素范围或授权开放探索，并说明各路线的计算内容。
