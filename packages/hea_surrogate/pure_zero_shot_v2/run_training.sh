#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
"${PYTHON_BIN}" train_physics_v2.py
"${PYTHON_BIN}" -m unittest discover -s tests -v

echo "Training and smoke tests completed."
