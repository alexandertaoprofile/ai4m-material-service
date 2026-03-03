# Step 5｜稳定性评估（ADiT + Pymatgen）

- taskid: `dr_HjPZQ_k7Vh_H7FfyN_EB-/jGfWKXOyi5Xq0_-F06rST_t2_1770357921`
- jobid: `LiNi0`
- generated_at: `2026-02-06T06:06:36.301353Z`

## Pymatgen 快速体检

### 核心指标

| 指标 | 数值 |
|---|---|
| num_sites | 3 |
| formula | Li |
| is_ordered | True |
| charge | 0.0 |
| volume | 61.20566011076459 |
| density | 0.564938029743426 |
| min_interatomic_dist | 3.04204092263402 |
| spacegroup_symbol | R-3m |
| spacegroup_number | 166 |
| volume_per_atom | 20.401886703588197 |

**Pymatgen 结论（快速）**：

- is_ordered: `True`
- has_partial_occupancy: `None`

## ADiT 合法性检查（轻量版本）

### 核心指标

| 指标 | 数值 |
|---|---|
| elements | ['Li'] |
| atomic_numbers | [3] |
| num_elements | 1 |
| num_sites | 3 |
| is_ordered | True |
| has_partial_occupancy | False |
| supported_elements | True |
| min_interatomic_dist | 3.04204092263402 |
| pass_gate | True |
| gate_reason | basic_checks_passed |

### Gate（准入判定）

| 项目 | 结果 |
|---|---|
| pass_gate | ✅ True |
| gate_reason | basic_checks_passed |

**总体结论**：

- ✅ 通过 ADiT 合法性检查（basic_checks_passed）。

