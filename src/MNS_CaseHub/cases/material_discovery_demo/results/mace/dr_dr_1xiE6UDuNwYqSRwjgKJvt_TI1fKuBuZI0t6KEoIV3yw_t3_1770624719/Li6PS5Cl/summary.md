## Step5.3 | MACE Sanity (mace-mp-0b2-medium)

- Status: DONE_NO_MD
- Atoms: 13
- Device: cuda  Dtype: float32
- Model: `mace-mp-0b2-medium.model`

### Relax
- Energy (eV): -53.363419 → -53.363419
- Max force (eV/Å): 0.012 → 0.012  (target 0.1)
- Min distance (Å): 2.057 → 2.057
- Files: `relaxed.cif`, `relaxed.extxyz`, `relax.log`

### Sanity MD (NVT Langevin)
- (skipped)

### Verdict
- RELAX PASS (fmax=0.012 <= 0.1)

