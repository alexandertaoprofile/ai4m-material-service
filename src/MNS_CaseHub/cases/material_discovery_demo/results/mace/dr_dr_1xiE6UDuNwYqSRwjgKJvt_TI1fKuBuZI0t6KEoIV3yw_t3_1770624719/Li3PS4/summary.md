## Step5.3 | MACE Sanity (mace-mp-0b2-medium)

- Status: DONE_NO_MD
- Atoms: 32
- Device: cuda  Dtype: float32
- Model: `mace-mp-0b2-medium.model`

### Relax
- Energy (eV): -140.318695 → -140.346313
- Max force (eV/Å): 0.269 → 0.042  (target 0.1)
- Min distance (Å): 2.027 → 2.040
- Files: `relaxed.cif`, `relaxed.extxyz`, `relax.log`

### Sanity MD (NVT Langevin)
- (skipped)

### Verdict
- RELAX PASS (fmax=0.042 <= 0.1)

