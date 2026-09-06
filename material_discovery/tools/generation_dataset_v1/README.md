# Generation Dataset v1

Standalone MatterGen batch generation and structural dataset normalisation.
It is deliberately separate from the 1115 service and does not call ALIGNN,
MatterSim, CALPHAD, DFT, SQS, MD, or phonon workflows.

Run `generate_dataset.py` in the MatterGen environment with a local checkpoint
directory. The output directory must be a new path (the tool refuses to reuse
an existing directory). `num-samples` must be
divisible by `batch-size` so the requested candidate count is exact.

For `aerospace_alloy`, v1 is fixed to `Co-Cr-Fe-Mn-Ni` and the v2 epoch-18
checkpoint. For `chip_packaging`, v1 accepts only the `Al-Si-B-N-O` element
space and uses a local copy of the official conditional checkpoint.
