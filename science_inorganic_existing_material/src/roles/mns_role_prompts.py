# -*- coding: utf-8 -*-
"""MNS role prompt constants.

当前先复用现有 prompts 定义，避免行为变化。
后续可逐步将内容从 team_config 中完全收敛到本模块。
"""

from src.materials.prompts import (
    XIMU_MNS_ENGINEERING_PROMPT,
    XIMU_MNS_MATERIAL_PROMPT,
    XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT,
)

__all__ = [
    "XIMU_MNS_ENGINEERING_PROMPT",
    "XIMU_MNS_MATERIAL_PROMPT",
    "XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT",
]
