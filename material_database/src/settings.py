"""Runtime settings for the mature-material service.

Only environment variables belong here.  Transport and catalogue business
rules must not depend on machine-specific paths or deployment defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatureMaterialSettings:
    service_name: str
    port: int
    results_root: Path
    raw_data_root: Path
    catalog_root: Path

    @classmethod
    def from_env(cls) -> "MatureMaterialSettings":
        return cls(
            service_name=os.getenv("MATURE_MATERIAL_SERVICE_NAME", "mature-material"),
            port=int(os.getenv("PORT", "1105")),
            results_root=Path(os.getenv("MATURE_MATERIAL_RESULTS_ROOT", "results/mature_material")),
            raw_data_root=Path(os.getenv("PROPERTY_DATA_ROOT", "data/raw")),
            catalog_root=Path(os.getenv("MATURE_MATERIAL_CATALOG_ROOT", "data/processed")),
        )
