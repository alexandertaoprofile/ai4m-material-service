# Step 5｜稳定性评估（ADiT + Pymatgen）

- taskid: `dr_VxF81J-5J7dJcwMVbpKbd/9HSXudBiNl8FNGLql5SBd_t2_1770280355`
- jobid: `Li3PS4`
- generated_at: `2026-02-05T08:33:00.584488Z`

## Pymatgen 快速体检

### 核心指标

| 指标 | 数值 |
|---|---|
| num_sites | 32 |
| formula | Li3PS4 |
| is_ordered | True |
| charge | 0.0 |
| volume | 644.980015636747 |
| density | 1.8542669892124035 |
| min_interatomic_dist | 2.0273891312061836 |
| spacegroup_symbol | Pnma |
| spacegroup_number | 62 |
| volume_per_atom | 20.155625488648344 |

**Pymatgen 结论（快速）**：

- is_ordered: `True`
- has_partial_occupancy: `None`

## ADiT 合法性检查（轻量版本）

### 核心指标

| 指标 | 数值 |
|---|---|
| elements | ['Li', 'P', 'S'] |
| atomic_numbers | [3, 15, 16] |
| num_elements | 3 |
| num_sites | 32 |
| is_ordered | True |
| has_partial_occupancy | False |
| supported_elements | True |
| min_interatomic_dist | 2.0273891312061836 |
| pass_gate | True |
| gate_reason | basic_checks_passed |

### Gate（准入判定）

| 项目 | 结果 |
|---|---|
| pass_gate | ✅ True |
| gate_reason | basic_checks_passed |

**总体结论**：

- ✅ 通过 ADiT 合法性检查（basic_checks_passed）。

# Step 6｜下一步：仿真队列准备（Simulation Queue）

- 计算对象：基准 / 进阶
- 建议：根据 Gate 与关键指标，决定是否进入后续 MD / 性质计算。

