"""Stable filesystem locations owned by this service.

The legacy ``MNS_CaseHub`` segment is deliberately centralized here: it is a
currently deployed task-artifact location, not an active service identity or
an executable case pipeline.
"""

from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
NEW_MATERIAL_RESULTS_ROOT = (
    SERVICE_ROOT / "src" / "MNS_CaseHub" / "cases" / "material_discovery_demo" / "results" / "new_material"
)
