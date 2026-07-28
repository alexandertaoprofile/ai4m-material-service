#!/usr/bin/env bash
set -euo pipefail
ENV_PREFIX="${HEA_SURROGATE_ENV_PREFIX:-/data/mamba/envs/hea-surrogate-py310}"
# Keep solver/package caches on the data disk as well.  Without this, a
# micromamba binary defaults to ~/.local/share/mamba and can fill the OS disk
# even when the target environment itself is under /data.
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-/data/mamba}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../hea_surrogate" && pwd)"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/env/hea-surrogate-py310.yml"
# Do not inherit the workstation's default Anaconda/R channels.  The runner
# environment is intentionally small and fully specified by conda-forge.
micromamba create -y --override-channels -c conda-forge -p "$ENV_PREFIX" -f "$ENV_FILE"
micromamba run -p "$ENV_PREFIX" bash -lc "cd '$REPO_ROOT' && python -m src.models.train_baselines --task all"
echo "HEA runner ready: $ENV_PREFIX"
