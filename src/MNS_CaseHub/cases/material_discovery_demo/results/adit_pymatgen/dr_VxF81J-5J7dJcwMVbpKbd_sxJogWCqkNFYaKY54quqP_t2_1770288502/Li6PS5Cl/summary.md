# Step 5｜稳定性评估（ADiT + Pymatgen）

- taskid: `dr_VxF81J-5J7dJcwMVbpKbd/sxJogWCqkNFYaKY54quqP_t2_1770288502`
- jobid: `Li6PS5Cl`
- generated_at: `2026-02-05T10:49:50.091494Z`

## Pymatgen 快速体检

### 核心指标

| 指标 | 数值 |
|---|---|
| num_sites | 13 |
| formula | Li6PS5Cl |
| is_ordered | True |
| charge | 0.0 |
| volume | 271.55370168980795 |
| density | 1.6412406350504896 |
| min_interatomic_dist | 2.056824480455743 |
| spacegroup_symbol | F-43m |
| spacegroup_number | 216 |
| volume_per_atom | 20.88874628383138 |

**Pymatgen 结论（快速）**：

- is_ordered: `True`
- has_partial_occupancy: `None`

## ADiT 合法性检查（轻量版本）

### 核心指标

| 指标 | 数值 |
|---|---|
| elements | ['Cl', 'Li', 'P', 'S'] |
| atomic_numbers | [3, 15, 16, 17] |
| num_elements | 4 |
| num_sites | 13 |
| is_ordered | True |
| has_partial_occupancy | False |
| supported_elements | True |
| min_interatomic_dist | 2.056824480455743 |
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

