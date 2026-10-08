#!/usr/bin/env bash
set -euo pipefail

# Bounded single-4090 preflight for the HEA conditional fine-tuning route.
# The official conditional checkpoint already contains an adapter, so this
# run tunes all copied model parameters while the official base stays immutable.
HEA_ROOT="/data/se42/hea_surrogate"
BASE_MODEL="$HEA_ROOT/models/generative/mattergen_chemical_system_ehull_official_v1"
DATA_CACHE="$HEA_ROOT/data/processed/hea_mattergen_v1/mattergen_cache/cantor_hea_5plus_32_v1"
export OUTPUT_DIR="${OUTPUT_DIR:-$HEA_ROOT/models/generative/hea_mattergen_v1/outputs/smoke_fullft_20260820}"
mkdir -p "$OUTPUT_DIR"
exec >"$OUTPUT_DIR/console.log" 2>&1

exec /data/mamba/envs/mattergen-py310/bin/mattergen-finetune \
  "adapter.model_path=$BASE_MODEL" \
  "adapter.pretrained_name=null" \
  "adapter.full_finetuning=true" \
  "data_module.root_dir=$DATA_CACHE" \
  "data_module.properties=[energy_above_hull]" \
  "data_module.batch_size.train=2" \
  "data_module.batch_size.val=2" \
  "data_module.batch_size.test=2" \
  "data_module.max_epochs=1" \
  "trainer.max_epochs=1" \
  "trainer.devices=1" \
  "trainer.num_nodes=1" \
  "trainer.precision=bf16-mixed" \
  "trainer.accumulate_grad_batches=8" \
  "trainer.check_val_every_n_epoch=1" \
  "+trainer.limit_train_batches=10" \
  "+trainer.limit_val_batches=4" \
  "trainer.strategy=auto" \
  "~trainer.logger" \
  "hydra.run.dir=$OUTPUT_DIR"
