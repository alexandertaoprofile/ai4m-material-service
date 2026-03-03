# Step2 | ADiT + Pymatgen 结构评估

- taskid: `dr_BDsOYRkKm7tsswPTWXUZs/S9A6rk1ZA45bgOQdf_gAh_t2_1770273013`
- jobid: `LiNi0`
- generated_at: `2026-02-05T06:30:49.229494Z`

## 上一步（MP）摘要（引用）

# LiNi0 (selected candidates <= 5)

- Generated at: 2026-02-05T14:30:23.122919

- Count (displayed): 5


| rank | material_id | legacy_id | symmetry | nsites | stable | e_above_hull (eV/atom) | E_form (eV/atom) | band_gap (eV) | reason |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | mp-cfyda |  | Trigonal/R-3m/166 | 3 | True | 0.0 | 0.0 | 0.0 | ranked_topk |
| 2 | mp-bz |  | Cubic/Fm-3m/225 | 1 | False | 0.001672373333333 | 0.001672373333333 | 0.0 | ranked_topk |
| 3 | mp-cdokh |  | Hexagonal/P6_3/mmc/194 | 4 | False | 0.005889090833333 | 0.005889090833333 | 0.0 | ranked_topk |
| 4 | mp-pbh |  | Hexagonal/P6_3/mmc/194 | 2 | False | 0.005987748333333 | 0.005987748333333 | 0.0 | ranked_topk |
| 5 | mp-bghgr |  | Cubic/I-43d/220 | 8 | False | 0.008819463333333001 | 0.008819463333333001 | 0.0 | ranked_topk |



## Pymatgen 快速体检

### Metrics
- num_sites: 3
- formula: Li
- is_ordered: True
- charge: 0.0
- volume: 61.20566011076459
- density: 0.564938029743426
- min_interatomic_dist: 3.04204092263402
- spacegroup_symbol: R-3m
- spacegroup_number: 166
- volume_per_atom: 20.401886703588197

## ADiT 合法性检查（轻量版本）

### Metrics
- elements: ['Li']
- atomic_numbers: [3]
- num_elements: 1
- num_sites: 3
- is_ordered: True
- has_partial_occupancy: False
- supported_elements: True
- min_interatomic_dist: 3.04204092263402
- pass_gate: True
- gate_reason: basic_checks_passed

### Gate
- pass_gate: ✅ True
- gate_reason: basic_checks_passed

