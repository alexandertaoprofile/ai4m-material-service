#!/usr/bin/env bash
set -euo pipefail

# Installs the official MatterGen v1.0.3 CUDA 11.8 runtime in an isolated env.
# Usage: bash tools/setup_mattergen_env.sh [env-prefix] [install-root]
# Set MATTERGEN_REFRESH_SOURCE=1 only when an existing checkout must be refreshed.
ENV_PREFIX="${1:-/data/mamba/envs/mattergen-py310}"
INSTALL_ROOT="${2:-/data/third_party}"
MATTERGEN_ROOT="$INSTALL_ROOT/mattergen"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/../env/mattergen-py310.yml"
# CUDA wheels are large and this deployment link can be slow. Callers may
# override either value, e.g. UV_HTTP_TIMEOUT=900 bash tools/setup_mattergen_env.sh.
UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}"
UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-4}"

if [[ ! -x "$ENV_PREFIX/bin/python" ]]; then
  micromamba create -y -f "$ENV_FILE" -p "$ENV_PREFIX"
fi
mkdir -p "$INSTALL_ROOT"
if [[ ! -d "$MATTERGEN_ROOT/.git" ]]; then
  git -c http.version=HTTP/1.1 clone --branch v1.0.3 --depth 1 https://github.com/microsoft/mattergen.git "$MATTERGEN_ROOT"
elif [[ "${MATTERGEN_REFRESH_SOURCE:-0}" == "1" ]]; then
  git -C "$MATTERGEN_ROOT" fetch --depth 1 origin tag v1.0.3
  git -C "$MATTERGEN_ROOT" checkout --detach v1.0.3
fi

# Upstream uses uv sources to select the required CUDA 11.8 PyTorch/PyG wheels.
# --system means the active micromamba prefix, not an extra uv virtualenv.
micromamba run -p "$ENV_PREFIX" bash -lc "cd '$MATTERGEN_ROOT' && UV_HTTP_TIMEOUT='$UV_HTTP_TIMEOUT' UV_CONCURRENT_DOWNLOADS='$UV_CONCURRENT_DOWNLOADS' uv pip install --system -e ."
# Lightning 2.0 imports pkg_resources. New setuptools releases removed it, so
# pin the compatibility package explicitly even if a base environment supplied
# a newer setuptools before MatterGen was installed.
micromamba run -p "$ENV_PREFIX" bash -lc "UV_HTTP_TIMEOUT='$UV_HTTP_TIMEOUT' uv pip install --system --reinstall 'setuptools<81'"
# MatterSim 1.1.2 imports ase.constraints.Filter, removed by ASE 3.29.  Keep
# ASE on the latest compatible 3.24 line for MatterGen v1.0.3 evaluation.
micromamba run -p "$ENV_PREFIX" bash -lc "UV_HTTP_TIMEOUT='$UV_HTTP_TIMEOUT' uv pip install --system --reinstall 'ase==3.24.0' 'numpy<2'"
# Real candidate GLB assets are rendered from CIFs with the repository's
# structure_to_glb helper, not generated decorative media.
micromamba run -p "$ENV_PREFIX" bash -lc "UV_HTTP_TIMEOUT='$UV_HTTP_TIMEOUT' uv pip install --system 'trimesh>=4.0'"
micromamba run -p "$ENV_PREFIX" mattergen-generate --help >/dev/null
printf 'MatterGen installed in %s. Model checkpoints download on first generation.\n' "$ENV_PREFIX"
