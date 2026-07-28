#!/usr/bin/env bash
set -euo pipefail
SERVICE_PY="${AI4M_SERVICE_PYTHON:-/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"
exec "$SERVICE_PY" main.py
