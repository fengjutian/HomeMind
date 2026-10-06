"""Resolve family events against a parsed query."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from homemind.infra.db.repos.family_context import (
    FamilyContextRepo,
    FamilyEventRow,
)
from homemind.infra.family.resolvers.models import (
    ResolutionCandidate,
    ResolvedTimeRange,
)


@dataclass(frozen=True)
class EventMatch:
    candidate: ResolutionCandidate
    event: FamilyEventRow


@dataclass(frozen=True)
class EventResolver:
    repo: FamilyContextRepo

    def search(
        self,
        family_id: str,
        *,
        text: str | None = None,
        member_ids: Iterable[str] | None = None,
        event_type: str | None = None,
        time_range: ResolvedTimeRange | None = None,
        min_confidence: float = 0.0,
    ) -> list[EventMatch]:
        members = set(member_ids or ())
        normalized = (text or "").casefold()
        candidates: list[EventMatch] = []
        for event in self.repo.list_events(
            family_id,
            start_at=time_range.start_at if time_range else None,
            end_at=time_range.end_at if time_range else None,
        ):
            score, breakdown = _score_event(
                event,
                text=normalized,
                member_ids=members,
                event_type=event_type,
            )
            if score < min_confidence:
                continue
            candidates.append(
                EventMatch(
                    candidate=ResolutionCandidate(
                        entity_type="EVENT",
                        entity_id=event.id,
                        label=event.title,
                        confidence=score,
                        reason="; ".join(breakdown) or "no_signal",
                        metadata={
                            "event_type": event.event_type,
                            "start_at": event.start_at,
                            "end_at": event.end_at,
                            "location": event.location or "",
                        },
                    ),
                    event=event,
                )
            )
        candidates.sort(key=lambda match: match.candidate.confidence, reverse=True)
        return candidates


def _score_event(
    event: FamilyEventRow,
    *,
    text: str,
    member_ids: set[str],
    event_type: str | None,
) -> tuple[float, list[str]]:
    score = 0.0
    breakdown: list[str] = []
    if event_type and event.event_type == event_type:
        score += 0.4
        breakdown.append(f"event_type:{event_type}")
    if text:
        if text in event.title.casefold():
            score += 0.35
            breakdown.append("title_match")
        if event.location and text in event.location.casefold():
            score += 0.2
            breakdown.append("location_match")
        if event.description and text in event.description.casefold():
            score += 0.1
            breakdown.append("description_match")
    if member_ids:
        try:
            import json as _json
            metadata = _json.loads(event.metadata_json) if event.metadata_json else {}
            linked = set(metadata.get("member_ids", []) if isinstance(metadata, dict) else [])
        except (TypeError, ValueError):
            linked = set()
        if linked & member_ids:
            score += 0.3
            breakdown.append("member_match")
    return min(score, 1.0), breakdown


__all__ = ["EventMatch", "EventResolver"]