# Step2 | ADiT + Pymatgen 结构评估

- taskid: `dr_BDsOYRkKm7tsswPTWXUZs/S9A6rk1ZA45bgOQdf_gAh_t2_1770273013`
- jobid: `Li3PS4`
- generated_at: `2026-02-05T06:30:49.738152Z`

## 上一步（MP）摘要（引用）

# Li3PS4 (selected candidates <= 5)

- Generated at: 2026-02-05T14:30:33.559574

- Count (displayed): 3


| rank | material_id | legacy_id | symmetry | nsites | stable | e_above_hull (eV/atom) | E_form (eV/atom) | band_gap (eV) | reason |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | mp-cebzb |  | Orthorhombic/Pnma/62 | 32 | True | 0.0 | -1.181772862142856 | 2.8091 | ranked_topk |
| 2 | mp-fuprn |  | Tetragonal/I-42m/121 | 8 | False | 0.0074205953125 | -1.174352266830356 | 2.9579 | ranked_topk |
| 3 | mp-ckkvs |  | Orthorhombic/Pnma/62 | 32 | False | 0.013829766249999001 | -1.167943095892857 | 2.4787 | ranked_topk |



## Pymatgen 快速体检

### Metrics
- num_sites: 32
- formula: Li3PS4
- is_ordered: True
- charge: 0.0
- volume: 644.980015636747
- density: 1.8542669892124035
- min_interatomic_dist: 2.0273891312061836
- spacegroup_symbol: Pnma
- spacegroup_number: 62
- volume_per_atom: 20.155625488648344

## ADiT 合法性检查（轻量版本）

### Metrics
- elements: ['Li', 'P', 'S']
- atomic_numbers: [3, 15, 16]
- num_elements: 3
- num_sites: 32
- is_ordered: True
- has_partial_occupancy: False
- supported_elements: True
- min_interatomic_dist: 2.0273891312061836
- pass_gate: True
- gate_reason: basic_checks_passed

### Gate
- pass_gate: ✅ True
- gate_reason: basic_checks_passed

