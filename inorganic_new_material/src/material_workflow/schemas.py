"""Shared schemas for the inorganic new-material discovery pipeline.

This module is intentionally dependency-light. It defines the contracts between
future MatterGen generation, ADiT/pymatgen validation, ranking, and frontend
emission without wiring those stages into the running service yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


JsonDict = Dict[str, Any]


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for generated manifests."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stringify_path(value: Optional[Path]) -> Optional[str]:
    return str(value) if value is not None else None


@dataclass
class GenerationConstraint:
    """User-facing constraints parsed for a new-material generation task."""

    taskid: str
    raw_requirement: str = ""
    target_formula: Optional[str] = None
    allowed_elements: List[str] = field(default_factory=list)
    excluded_elements: List[str] = field(default_factory=list)
    target_properties: JsonDict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class GeneratedCandidate:
    """A candidate material proposed by a generator such as MatterGen."""

    candidate_id: str
    formula_pretty: Optional[str] = None
    cif_path: Optional[Path] = None
    structure_path: Optional[Path] = None
    source: str = "mattergen"
    generation_score: Optional[float] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        data["cif_path"] = _stringify_path(self.cif_path)
        data["structure_path"] = _stringify_path(self.structure_path)
        return data


@dataclass
class GenerationManifest:
    """Output contract for the candidate generation stage."""

    taskid: str
    status: str
    created_at: str = field(default_factory=utc_now_iso)
    backend: str = "mattergen"
    candidates: List[GeneratedCandidate] = field(default_factory=list)
    message: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        data["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return data


@dataclass
class ValidationResult:
    """Validation and property-completion result for a generated candidate."""

    candidate_id: str
    status: str
    validator: str = "adit_pymatgen"
    is_valid: Optional[bool] = None
    formula_pretty: Optional[str] = None
    space_group: Optional[str] = None
    band_gap: Optional[float] = None
    energy_above_hull: Optional[float] = None
    formation_energy_per_atom: Optional[float] = None
    density: Optional[float] = None
    errors: List[str] = field(default_factory=list)
    artifacts: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class RankedCandidate:
    """A generated candidate plus normalized ranking information."""

    candidate: GeneratedCandidate
    rank: int
    score: float
    validation: Optional[ValidationResult] = None
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "candidate": self.candidate.to_dict(),
            "rank": self.rank,
            "score": self.score,
            "validation": self.validation.to_dict() if self.validation else None,
            "reasons": list(self.reasons),
        }


@dataclass
class NewMaterialPipelineResult:
    """Top-level manifest emitted by the new-material discovery pipeline."""

    taskid: str
    status: str
    constraints: GenerationConstraint
    generation: GenerationManifest
    validations: List[ValidationResult] = field(default_factory=list)
    ranked_candidates: List[RankedCandidate] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    message: str = ""
    artifacts: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "taskid": self.taskid,
            "status": self.status,
            "constraints": self.constraints.to_dict(),
            "generation": self.generation.to_dict(),
            "validations": [item.to_dict() for item in self.validations],
            "ranked_candidates": [item.to_dict() for item in self.ranked_candidates],
            "created_at": self.created_at,
            "message": self.message,
            "artifacts": dict(self.artifacts),
        }
