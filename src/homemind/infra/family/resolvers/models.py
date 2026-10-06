"""Shared dataclasses for family context resolvers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResolutionCandidate:
    """A single resolved (or ambiguous) target.

    Resolvers return ``[ResolutionCandidate]`` so the caller can flag
    ambiguities instead of arbitrarily picking one. A list of length 1
    means the resolver is confident; multi-child matches are flagged as
    ambiguity in the parent ``ResolvedFamilyContext``.
    """

    entity_type: str  # "MEMBER" | "RELATIONSHIP" | "EVENT" | "ASSET" | "MEMORY"
    entity_id: str
    label: str
    confidence: float  # 0.0–1.0
    reason: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedTimeRange:
    """Resolved (start_at, end_at) pair for a natural-language time expression.

    ``expression`` is the original Chinese / English phrase that produced the
    range so callers can surface it for debugging and telemetry.
    """

    start_at: int | None
    end_at: int | None
    expression: str | None
    confidence: float  # 0.0–1.0


__all__ = ["ResolutionCandidate", "ResolvedTimeRange"]