# Step 5｜稳定性评估（ADiT + Pymatgen）

- taskid: `xdgkjr1U4V8cHqLRImk2I/nXfb1NNQUUR4UBj6eyGEJ`
- jobid: `H2O`
- generated_at: `2026-02-05T08:37:04.775069Z`

## Pymatgen 快速体检

### 核心指标

| 指标 | 数值 |
|---|---|
| num_sites | 12 |
| formula | H2O |
| is_ordered | True |
| charge | 0.0 |
| volume | 115.06958229398883 |
| density | 1.0398951897158608 |
| min_interatomic_dist | 0.9951709754818251 |
| spacegroup_symbol | Cmc2_1 |
| spacegroup_number | 36 |
| volume_per_atom | 9.589131857832403 |

**Pymatgen 结论（快速）**：

- is_ordered: `True`
- has_partial_occupancy: `None`

### Warnings

- ⚠️ Small min distance (0.995 Å). Please double-check.

## ADiT 合法性检查（轻量版本）

### 核心指标

| 指标 | 数值 |
|---|---|
| elements | ['H', 'O'] |
| atomic_numbers | [1, 8] |
| num_elements | 2 |
| num_sites | 12 |
| is_ordered | True |
| has_partial_occupancy | False |
| supported_elements | True |
| min_interatomic_dist | 0.9951709754818251 |
| pass_gate | False |
| gate_reason | min_dist_too_small |

### Gate（准入判定）

| 项目 | 结果 |
|---|---|
| pass_gate | ❌ False |
| gate_reason | min_dist_too_small |

**总体结论**：

- ❌ 未通过 ADiT 合法性检查（min_dist_too_small）。

### Warnings

- ⚠️ min_interatomic_dist too small (0.995 Å). Structure may be unphysical.

# Step 6｜下一步：仿真队列准备（Simulation Queue）

- 计算对象：基准 / 进阶
- 建议：根据 Gate 与关键指标，决定是否进入后续 MD / 性质计算。

