"""Stable internal contracts for refractory multiscale validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationRequest:
    taskid: str
    raw_requirement: str
    material_system: str
    structure_source: str
    temperature_K: list[float]
    target_properties: list[str]
    execution_mode: str
    file_metadata: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceStatus:
    stage: str
    status: str
    evidence_level: str
    summary: str
    available_artifacts: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    taskid: str
    status: str
    request: ValidationRequest
    stages: list[EvidenceStatus]
    scientific_conclusion: dict[str, Any]
    evidence_manifest: dict[str, Any]
    presentation: dict[str, Any] = field(default_factory=lambda: {"assets": []})

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskid": self.taskid,
            "status": self.status,
            "request": self.request.to_dict(),
            "stages": [stage.to_dict() for stage in self.stages],
            "scientific_conclusion": self.scientific_conclusion,
            "evidence_manifest": self.evidence_manifest,
            "presentation": self.presentation,
        }
