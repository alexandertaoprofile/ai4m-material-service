"""Construct two independent, opt-in 1111 model shadow channels.

Deployment must provide versioned private runtime directories and a Python
with the model dependencies. This module never changes public model routing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.alloy_workflow.private_shadow_dispatcher import Channel


GLASS_CHECKPOINT_SHA = "601fefce1d36aabb4e1de77f85baf96a7eec6cc8daebfedc8490d0535b6976a6"
GLASS_INFER_SHA = "d5ddd559249e0282338bbc940f1240674a5044bbff4a2708a1ad27e194750bff"
GLASS_TRAIN_SHA = "b72f217c4ee7ed9f2aa83e46591f3a5966b427c196a4edc4eb46dc6bb9985e64"


def model_shadow_channels(
    *,
    alloy_python: Path,
    alloy_runtime: Path,
    alloy_items: Callable[[dict[str, Any]], list[dict[str, Any]]],
    glass_python: Path,
    glass_runtime: Path,
    glass_checkpoint: Path,
    glass_code: Path,
    glass_builder: Callable[[dict[str, Any]], tuple[dict[str, Any], Any]],
    glass_merger: Callable[[Any, dict[str, Any]], dict[str, Any]],
) -> list[Channel]:
    def build_alloy(result: dict[str, Any]) -> tuple[dict[str, Any], None]:
        return {"items": alloy_items(result)}, None

    def alloy_command(request: Path, response: Path) -> list[str]:
        return [str(alloy_python), str(alloy_runtime / "runner.py"),
                "--request", str(request), "--response", str(response),
                "--mode", "shadow", "--service-comparison", "--threads", "1"]

    def glass_command(request: Path, response: Path) -> list[str]:
        return [str(glass_python), str(glass_runtime / "oxide_glass_shadow_runner.py"),
                "--checkpoint", str(glass_checkpoint), "--model-code-dir", str(glass_code),
                "--expected-sha256", GLASS_CHECKPOINT_SHA,
                "--expected-infer-sha256", GLASS_INFER_SHA,
                "--expected-train-sha256", GLASS_TRAIN_SHA,
                "--request", str(request), "--response", str(response)]

    return [
        Channel(name="alloy_v10", model_domain="aerospace_aluminum_composition_v1",
                enabled_env="ALLOY_V10_SHADOW_ENABLED", builder=build_alloy,
                command=alloy_command, timeout_seconds=60, queue_size=8),
        Channel(name="alloy_v10_stainless", model_domain="reusable_rocket_stainless",
                enabled_env="ALLOY_V10_SHADOW_ENABLED", builder=build_alloy,
                command=alloy_command, timeout_seconds=60, queue_size=8),
        Channel(name="alloy_v10_nickel", model_domain="ni_superalloy_hot_end",
                enabled_env="ALLOY_V10_SHADOW_ENABLED", builder=build_alloy,
                command=alloy_command, timeout_seconds=60, queue_size=8),
        Channel(name="glass_v1", model_domain="chip_glass_thermomechanical_family_v1",
                enabled_env="GLASS_TRANSFORMER_SHADOW_ENABLED", builder=glass_builder,
                command=glass_command, merger=glass_merger, timeout_seconds=60,
                queue_size=8),
    ]


def optimization_shadow_channels(python: Path, runtime: Path) -> list[Channel]:
    from src.alloy_workflow.optimization_contract import build_optimization_shadow
    domains = ("aerospace_aluminum_composition_v1", "reusable_rocket_stainless", "ni_superalloy_hot_end",
               "copper_hot_end_local_composition_v1", "hea_mpea", "chip_glass_thermomechanical_family_v1",
               "short_cf_thermomechanical_rve_v1")
    def command(request: Path, response: Path) -> list[str]:
        return [str(python), str(runtime / "optimization_runner.py"),
                "--request", str(request), "--response", str(response)]
    return [Channel(name=f"nsga_{i}", model_domain=domain, enabled_env="NSGA_SHADOW_ENABLED",
                    builder=build_optimization_shadow, command=command, timeout_seconds=60,
                    workers=1, queue_size=2) for i, domain in enumerate(domains)]
