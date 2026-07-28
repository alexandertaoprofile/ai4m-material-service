#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${MATURE_MATERIAL_IMAGE:-mature-material:local}"
CONTAINER_NAME="${MATURE_MATERIAL_CONTAINER:-mature-material}"
HOST_PORT="${MATURE_MATERIAL_HOST_PORT:-1105}"
CONTAINER_PORT=1105
ENV_FILE="${MATURE_MATERIAL_ENV_FILE:-$SCRIPT_DIR/.env}"
RESULTS_HOST_DIR="${MATURE_MATERIAL_RESULTS_HOST_DIR:-$SCRIPT_DIR/results/mature_material}"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "Container '$CONTAINER_NAME' already exists. Stop/remove it explicitly before starting a replacement." >&2
    exit 1
fi

mkdir -p "$RESULTS_HOST_DIR"
ENV_ARGS=()
if [[ -f "$ENV_FILE" ]]; then
    ENV_ARGS+=(--env-file "$ENV_FILE")
fi

RAW_DATA_ARGS=()
if [[ -n "${MATURE_MATERIAL_RAW_DATA_HOST_DIR:-}" ]]; then
    if [[ ! -d "$MATURE_MATERIAL_RAW_DATA_HOST_DIR" ]]; then
        echo "MATURE_MATERIAL_RAW_DATA_HOST_DIR does not exist: $MATURE_MATERIAL_RAW_DATA_HOST_DIR" >&2
        exit 1
    fi
    RAW_DATA_ARGS+=(
        -v "$MATURE_MATERIAL_RAW_DATA_HOST_DIR:/data/property-datasets:ro"
        -e PROPERTY_DATA_ROOT=/data/property-datasets
    )
fi

docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
docker run -d \
    --name "$CONTAINER_NAME" \
    "${ENV_ARGS[@]}" \
    -e PORT="$CONTAINER_PORT" \
    -e MATURE_MATERIAL_RESULTS_ROOT=/app/results/mature_material \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -v "$RESULTS_HOST_DIR:/app/results/mature_material" \
    "${RAW_DATA_ARGS[@]}" \
    "$IMAGE_NAME"
