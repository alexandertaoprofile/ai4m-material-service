# Step2 | ADiT + Pymatgen 结构评估

- taskid: `dr_BDsOYRkKm7tsswPTWXUZs/S9A6rk1ZA45bgOQdf_gAh_t2_1770273013`
- jobid: `Li6PS5Cl`
- generated_at: `2026-02-05T06:30:50.250653Z`

## 上一步（MP）摘要（引用）

# Li6PS5Cl (selected candidates <= 5)

- Generated at: 2026-02-05T14:30:46.077046

- Count (displayed): 1


| rank | material_id | legacy_id | symmetry | nsites | stable | e_above_hull (eV/atom) | E_form (eV/atom) | band_gap (eV) | reason |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | mp-cebzk |  | Cubic/F-43m/216 | 13 | False | 0.08294630153846101 | -1.320599137087911 | 2.2955 | ranked_topk |



## Pymatgen 快速体检

### Metrics
- num_sites: 13
- formula: Li6PS5Cl
- is_ordered: True
- charge: 0.0
- volume: 271.55370168980795
- density: 1.6412406350504896
- min_interatomic_dist: 2.056824480455743
- spacegroup_symbol: F-43m
- spacegroup_number: 216
- volume_per_atom: 20.88874628383138

## ADiT 合法性检查（轻量版本）

### Metrics
- elements: ['Cl', 'Li', 'P', 'S']
- atomic_numbers: [3, 15, 16, 17]
- num_elements: 4
- num_sites: 13
- is_ordered: True
- has_partial_occupancy: False
- supported_elements: True
- min_interatomic_dist: 2.056824480455743
- pass_gate: True
- gate_reason: basic_checks_passed

### Gate
- pass_gate: ✅ True
- gate_reason: basic_checks_passed

