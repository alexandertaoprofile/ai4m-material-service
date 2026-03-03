# src/runner.py
# -*- coding: utf-8 -*-
"""
统一实验入口：
- 负责把 CLI 传进来的 args 整理成 config
- 调用 data_utils / pinn_model / trainer / postprocess
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TrainConfig:
    mode: str           # "quick" / "train" / "eval"
    device: str         # "cuda" / "cpu"
    seed: int
    output_dir: str

    # 训练超参数（可以根据需要调整）
    max_epochs: int = 8000
    lr: float = 1e-3

    # 采样点数
    n_pde: int = 15000
    n_ic: int = 6000
    n_bc: int = 2000

    # 物理域尺寸（mm / ms）
    Lx: float = 70.0
    Ly: float = 150.0
    t_max: float = 10.0


def run_experiment(args):
    """
    args 来自 main() 的 argparse，转成 TrainConfig 后统一调度
    """
    cfg = TrainConfig(
        mode=args.mode,
        device=args.device,
        seed=args.seed,
        output_dir=args.output_dir,
    )

    # quick 模式：少跑一点 epoch，方便 demo
    if cfg.mode == "quick":
        cfg.max_epochs = 2000
        cfg.lr = 1e-3

    from src import data_utils, pinn_model, trainer, postprocess

    print("=== Smartphone Drop-Impact PINN Demo ===")
    print(f"Mode       : {cfg.mode}")
    print(f"Device     : {cfg.device}")
    print(f"Seed       : {cfg.seed}")
    print(f"Output dir : {cfg.output_dir}")
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)

    # 1) 准备训练点（PDE / IC / BC）
    train_data = data_utils.build_training_points(cfg)

    # 2) 构建 PINN 模型
    model = pinn_model.build_model(cfg)

    # 3) 训练 / 评估
    if cfg.mode in ("quick", "train"):
        trainer.train(model, train_data, cfg)

    # 4) 结果可视化（用训练好的 model 做推理）# src/runner.py
import argparse
from pathlib import Path

import yaml
import torch

from .data_utils import set_random_seed, get_device
from .pinn_model import build_model
from .trainer import train_model
from .postprocess import (
    find_best_time_for_bottom_contrast,
    compute_max_stress_time_curve,
    plot_max_stress_curve,
    plot_stress_phone,
    plot_multiple_frames,
)


def load_config(project_root: Path) -> dict:
    cfg_path = project_root / "data" / "phone_drop_case.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def build_time_lists(cfg: dict):
    vis_cfg = cfg.get("visualization", {})
    triptych_ms = vis_cfg.get("triptych_ms", [0.25, 1.0, 5.0])

    gif_cfg = vis_cfg.get("gif_ms", {})
    if isinstance(gif_cfg, dict):
        start = gif_cfg.get("start", 0.25)
        end = gif_cfg.get("end", 7.5)
        step = gif_cfg.get("step", 0.25)
        n = int((end - start) / step) + 1
        gif_ms = [start + i * step for i in range(n)]
    else:
        gif_ms = gif_cfg
    return triptych_ms, gif_ms


def run_eval_and_visualization(
    model: torch.nn.Module,
    cfg: dict,
    device: torch.device,
    output_dir: Path,
):
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 自动搜索最佳“底部/顶部”应力对比时刻
    vis_cfg = cfg.get("visualization", {})
    t_min_ms = vis_cfg.get("t_min_search_ms", 0.5)
    t_best, score = find_best_time_for_bottom_contrast(
        model,
        cfg,
        device,
        num_t=80,
        bottom_ratio=0.2,
        top_ratio=0.2,
        t_min=t_min_ms * 1e-3,
    )
    print(
        f"[Auto] Best time for bottom contrast: "
        f"t={t_best*1e3:.2f} ms, bottom/top={score:.2f}"
    )

    # 2. 最大等效应力-时间曲线
    t_samples, max_stress_norm = compute_max_stress_time_curve(
        model, cfg, device, num_t=150
    )
    plot_max_stress_curve(
        t_samples,
        max_stress_norm,
        output_dir / "max_stress_vs_time.png",
    )

    # 3. 主视图（用 t_best）
    from .postprocess import evaluate_stress_field

    X, Y, Svm_star_t = evaluate_stress_field(
        model, cfg, device, t_best, nx=120, ny=240
    )
    Svm_norm = Svm_star_t / (Svm_star_t.max() + 1e-12)
    plot_stress_phone(
        cfg,
        X,
        Y,
        Svm_norm,
        t_best,
        savepath=output_dir / "phone_drop_t_best.png",
        show=False,
    )

    # 4. 多帧（triptych + 全时序）
    triptych_ms, gif_ms = build_time_lists(cfg)

    # Triptych 三张图（用统一 prefix）
    for i, t_ms in enumerate(triptych_ms):
        t_val = t_ms * 1e-3
        X, Y, Svm_star_t = evaluate_stress_field(model, cfg, device, t_val, 120, 240)
        S_norm = Svm_star_t / (Svm_star_t.max() + 1e-12)
        plot_stress_phone(
            cfg,
            X,
            Y,
            S_norm,
            t_val,
            savepath=output_dir / f"stress_field_t{t_ms:.2f}ms_simple.png",
            show=False,
        )

    # 全时序帧（方便之后生成 GIF）
    prefix = output_dir / "phone_drop_"
    plot_multiple_frames(model, cfg, device, gif_ms, prefix=prefix)


def run_experiment(args: argparse.Namespace):
    """
    主调度函数：
    - quick: 少步数快速训练 + 可视化
    - train: 完整训练 + 可视化
    - eval: 仅加载已有模型做可视化
    """
    # 推断 project_root = Smartphone_DropImpact_PINN/
    project_root = Path(__file__).resolve().parent.parent
    cfg = load_config(project_root)

    set_random_seed(args.seed)
    device = get_device(args.device)
    print(f"[INFO] Using device: {device}")

    output_dir = Path(args.output_dir)
    model_dir = project_root / "model"

    model = build_model(cfg, device)

    if args.mode in ("quick", "train"):
        model = train_model(
            model=model,
            cfg=cfg,
            device=device,
            mode=args.mode,
            output_dir=output_dir,
            model_dir=model_dir,
        )

    elif args.mode == "eval":
        ckpt_path = model_dir / "pinn_phone_drop.pt"
        if not ckpt_path.exists():
            raise FileNotFoundError(
                f"Model checkpoint not found: {ckpt_path}, "
                f"please run with --mode quick/train first."
            )
        state = torch.load(ckpt_path, map_location=device)
        model.load_state_dict(state)
        model.to(device)
        model.eval()
        print(f"[INFO] loaded model from {ckpt_path}")

    else:
        raise ValueError(f"Unknown mode: {args.mode}")

    # 统一：训练完或 eval 都跑一遍可视化
    run_eval_and_visualization(model, cfg, device, output_dir)
    print("=== Done ===")
