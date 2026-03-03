## Step5.3 | MACE Sanity (mace-mp-0b2-medium)

- Status: DONE
- Atoms: 32
- Device: cuda  Dtype: float32
- Model: `mace-mp-0b2-medium.model`

### Relax
- (skipped)

### Sanity MD (NVT Langevin)
- Steps: 150  dt: 0.25 fs  T: 300.0 K  friction: 0.05
- Final max force (eV/Å): 0.6678645014762878
- Final min distance (Å): 2.0277367792433676
- Files: `md_traj.extxyz`, `md_log.csv`, `md_final.cif`

### Verdict
- MD PASS (min_dist=2.028 Å >= 1.8 Å)

