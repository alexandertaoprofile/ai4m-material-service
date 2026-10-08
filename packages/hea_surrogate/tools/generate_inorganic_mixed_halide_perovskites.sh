#!/usr/bin/env bash
# Generate raw inorganic mixed-cation / mixed-halide perovskite candidates
# with the official chemical_system + energy_above_hull MatterGen checkpoint.
#
# Usage on gpu-local-120:
#   bash generate_inorganic_mixed_halide_perovskites.sh <run_label> <gpu_id> <chemical_system_json> [sample_count]
# Example:
#   bash generate_inorganic_mixed_halide_perovskites.sh cs_pb_ibr 2 '["Cs","Pb","I","Br"]' 500

set -Eeuo pipefail

RUN_LABEL="${1:?run label required}"
GPU_ID="${2:?physical GPU id required}"
CHEMICAL_SYSTEM_JSON="${3:?JSON element list required}"
SAMPLE_COUNT="${4:-500}"

WORK_ROOT="${WORK_ROOT:-/data/sdb/taoquanming}"
MICRO_BIN="${MICRO_BIN:-$WORK_ROOT/bin}"
ENV_ROOT="${ENV_ROOT:-$WORK_ROOT/mamba/envs/mattergen-py310}"
MODEL_DIR="${MODEL_DIR:-$WORK_ROOT/hea_surrogate/models/generative/mattergen_chemical_system_ehull_official_v1}"
RUN_ROOT="${RUN_ROOT:-$WORK_ROOT/generated_candidates/perovskite_mixed_halide_v1}"
BATCH_SIZE="${BATCH_SIZE:-5}"
SEED="${SEED:-20260908}"

if ! [[ "$RUN_LABEL" =~ ^[a-z0-9_]+$ ]]; then
  echo "run label must use lowercase letters, digits and underscores" >&2
  exit 2
fi
if ! [[ "$GPU_ID" =~ ^[0-9]+$ ]] || ! [[ "$SAMPLE_COUNT" =~ ^[0-9]+$ ]]; then
  echo "GPU id and sample count must be non-negative integers" >&2
  exit 2
fi
if (( SAMPLE_COUNT < BATCH_SIZE || SAMPLE_COUNT % BATCH_SIZE != 0 )); then
  echo "sample count must be divisible by BATCH_SIZE=$BATCH_SIZE" >&2
  exit 2
fi

OUT_DIR="$RUN_ROOT/${RUN_LABEL}_n${SAMPLE_COUNT}_seed${SEED}"
LOG_FILE="$RUN_ROOT/${RUN_LABEL}_n${SAMPLE_COUNT}_seed${SEED}.log"
CONDITIONS="{\"chemical_system\":${CHEMICAL_SYSTEM_JSON},\"energy_above_hull\":0.05}"

test -x "$MICRO_BIN/micromamba"
test -x "$ENV_ROOT/bin/python"
test -f "$MODEL_DIR/config.yaml"
test -d "$MODEL_DIR/checkpoints"

if [[ -e "$OUT_DIR" ]]; then
  echo "Refusing to reuse existing output directory: $OUT_DIR" >&2
  exit 3
fi
mkdir -p "$RUN_ROOT"

echo "run_label=$RUN_LABEL"
echo "physical_gpu=$GPU_ID"
echo "chemical_system=$CHEMICAL_SYSTEM_JSON"
echo "condition=$CONDITIONS"
echo "raw_samples=$SAMPLE_COUNT"
echo "output=$OUT_DIR"

# Each process sees one physical GPU as logical cuda:0.  This is the full
# 1000-step sampler used in the prior HEA batches, not the 100-step web demo.
CUDA_VISIBLE_DEVICES="$GPU_ID" \
PYTHONHASHSEED="$SEED" \
"$MICRO_BIN/micromamba" run -p "$ENV_ROOT" \
  mattergen-generate "$OUT_DIR" \
  --model_path "$MODEL_DIR" \
  --checkpoint_epoch last \
  --batch_size "$BATCH_SIZE" \
  --num_batches "$((SAMPLE_COUNT / BATCH_SIZE))" \
  --record_trajectories false \
  --diffusion_guidance_factor 1.0 \
  --properties_to_condition_on "$CONDITIONS" \
  2>&1 | tee "$LOG_FILE"

echo
echo "Generation completed. Raw outputs:"
find "$OUT_DIR" -maxdepth 2 -type f -printf '%P\n' | sort
