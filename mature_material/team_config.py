"""Backward-compatible export for the mature-material service role metadata.

The actual service is ``main.py``.  No generic Alpha Team is constructed here,
because mature-material lookup is deterministic catalogue access rather than a
new-material agent workflow.
"""
from src.team_config import MatureMaterialCatalogQuery, XIMUAlpha_MNS

__all__ = ["MatureMaterialCatalogQuery", "XIMUAlpha_MNS"]
