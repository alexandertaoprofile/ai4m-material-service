#!/usr/bin/env python3
"""Resume a MatterGen HEA fine-tuning run from a Lightning checkpoint.

The upstream ``mattergen-finetune`` entry point always calls ``trainer.fit``
with ``ckpt_path=None``.  This small wrapper keeps its model construction but
passes the training checkpoint through to Lightning so optimizer, scheduler,
epoch and callback state are restored together.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from omegaconf import OmegaConf, open_dict
from pytorch_lightning.callbacks import ModelCheckpoint

from mattergen.diffusion.run import maybe_instantiate
from mattergen.scripts.finetune import init_adapter_lightningmodule_from_pretrained


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Saved resolved config.yaml from the original run")
    parser.add_argument("--checkpoint", required=True, type=Path, help="Lightning last.ckpt (or an epoch checkpoint)")
    parser.add_argument("--dry-run", action="store_true", help="Validate paths and print the resume plan without loading GPU state")
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Run the configured held-out test split from the checkpoint without changing model weights.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    checkpoint_path = args.checkpoint.resolve()
    if not config_path.is_file():
        raise SystemExit(f"Missing saved configuration: {config_path}")
    if not checkpoint_path.is_file():
        raise SystemExit(f"Missing checkpoint: {checkpoint_path}")
    output_dir = config_path.parent
    print(f"[resume] config={config_path}")
    print(f"[resume] checkpoint={checkpoint_path}")
    print(f"[resume] output_dir={output_dir}")
    if args.dry_run:
        return

    # Preserve the original Hydra run directory so subsequent logs/checkpoints
    # remain beside the first epoch rather than creating an unrelated run.
    os.chdir(output_dir)
    cfg = OmegaConf.load(config_path)
    trainer = maybe_instantiate(cfg.trainer)
    datamodule = maybe_instantiate(cfg.data_module)
    module, resolved_module_cfg = init_adapter_lightningmodule_from_pretrained(
        cfg.adapter, cfg.lightning_module,
    )
    with open_dict(cfg):
        cfg.lightning_module = resolved_module_cfg
    for callback in trainer.callbacks:
        if isinstance(callback, ModelCheckpoint):
            callback.dirpath = str(checkpoint_path.parent)
    if args.test_only:
        trainer.test(model=module, datamodule=datamodule, ckpt_path=str(checkpoint_path))
    else:
        trainer.fit(model=module, datamodule=datamodule, ckpt_path=str(checkpoint_path))


if __name__ == "__main__":
    main()
