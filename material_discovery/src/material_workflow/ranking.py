"""Deterministic ranking helpers for new-material candidates."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .schemas import GeneratedCandidate, RankedCandidate, ValidationResult


def _score_candidate(candidate: GeneratedCandidate, validation: Optional[ValidationResult]) -> float:
    """Score a candidate using only available generated/validated fields."""
    score = 0.0
    if candidate.generation_score is not None:
        score += float(candidate.generation_score)
    if validation is None:
        return score
    if validation.is_valid is True:
        score += 10.0
    if validation.energy_above_hull is not None:
        score += max(0.0, 2.0 - float(validation.energy_above_hull))
    if validation.band_gap is not None:
        score += min(float(validation.band_gap), 5.0) * 0.1
    return score


def rank_candidates(
    candidates: Iterable[GeneratedCandidate],
    validations: Iterable[ValidationResult] = (),
) -> List[RankedCandidate]:
    """Rank candidates deterministically without random tie-breaking."""
    validation_by_id: Dict[str, ValidationResult] = {item.candidate_id: item for item in validations}
    scored: List[RankedCandidate] = []

    for candidate in candidates:
        validation = validation_by_id.get(candidate.candidate_id)
        reasons: List[str] = []
        if candidate.generation_score is not None:
            reasons.append("generation_score")
        if validation and validation.is_valid is True:
            reasons.append("validation_passed")
        if validation and validation.energy_above_hull is not None:
            reasons.append("energy_above_hull_available")
        scored.append(
            RankedCandidate(
                candidate=candidate,
                rank=0,
                score=_score_candidate(candidate, validation),
                validation=validation,
                reasons=reasons,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.candidate.candidate_id))
    for index, item in enumerate(scored, start=1):
        item.rank = index
    return scored
