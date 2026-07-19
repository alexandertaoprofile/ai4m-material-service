#!/usr/bin/env bash
# Download the official MatterGen MP2020/Alexandria reference phases for
# MatterSim energy-above-hull evaluation.  Kept separate from MatterGen setup
# because the LFS payload is large (~0.83 GB).
set -euo pipefail

ENV_PREFIX="${MATTERGEN_ENV_PREFIX:-/data/mamba/envs/mattergen-py310}"
MATTERGEN_SOURCE_ROOT="${MATTERGEN_SOURCE_ROOT:-/data/third_party/mattergen}"
REFERENCE="data-release/alex-mp/reference_MP2020correction.gz"

if [[ ! -d "${MATTERGEN_SOURCE_ROOT}/.git" ]]; then
  echo "MatterGen checkout is missing: ${MATTERGEN_SOURCE_ROOT}" >&2
  exit 1
fi

# Install LFS in the isolated GPU environment; no system-wide apt/sudo change.
micromamba install -y -p "${ENV_PREFIX}" -c conda-forge git-lfs
micromamba run -p "${ENV_PREFIX}" git -C "${MATTERGEN_SOURCE_ROOT}" lfs install --local
# MatterGen setup deliberately excludes all LFS objects.  Override it only for
# the reference archive needed by the evaluation stage.
micromamba run -p "${ENV_PREFIX}" git -C "${MATTERGEN_SOURCE_ROOT}" config lfs.fetchexclude ""
micromamba run -p "${ENV_PREFIX}" git -C "${MATTERGEN_SOURCE_ROOT}" lfs pull --include="${REFERENCE}" --exclude=""

if [[ "$(head -c 7 "${MATTERGEN_SOURCE_ROOT}/${REFERENCE}" || true)" == "version " ]]; then
  echo "Reference file is still an LFS pointer; download did not complete." >&2
  exit 1
fi
echo "MatterSim reference dataset is ready: ${MATTERGEN_SOURCE_ROOT}/${REFERENCE}"
