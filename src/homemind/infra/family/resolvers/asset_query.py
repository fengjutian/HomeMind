"""Resolve assets against a parsed query."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from homemind.infra.db.repos.family_assets import (
    FamilyAssetRepo,
    FamilyAssetRow,
)
from homemind.infra.family.resolvers.models import (
    ResolutionCandidate,
    ResolvedTimeRange,
)


@dataclass(frozen=True)
class AssetMatch:
    candidate: ResolutionCandidate
    asset: FamilyAssetRow


@dataclass(frozen=True)
class AssetQueryResolver:
    repo: FamilyAssetRepo

    def search(
        self,
        family_id: str,
        *,
        text: str | None = None,
        member_ids: Iterable[str] | None = None,
        space_ids: Iterable[str] | None = None,
        asset_type: str | None = None,
        time_range: ResolvedTimeRange | None = None,
        min_confidence: float = 0.05,
    ) -> list[AssetMatch]:
        members = set(member_ids or ())
        spaces = set(space_ids or ())
        normalized = (text or "").casefold()
        matches: list[AssetMatch] = []
        rows = self.repo.search(
            family_id,
            query=text,
            asset_type=asset_type,
            space_id=(next(iter(spaces)) if len(spaces) == 1 else None),
        )
        for asset in rows:
            score, breakdown = _score_asset(
                asset,
                text=normalized,
                member_ids=members,
                space_ids=spaces,
                start_at=time_range.start_at if time_range else None,
                end_at=time_range.end_at if time_range else None,
            )
            if score < min_confidence:
                continue
            matches.append(
                AssetMatch(
                    candidate=ResolutionCandidate(
                        entity_type="ASSET",
                        entity_id=asset.id,
                        label=asset.name,
                        confidence=score,
                        reason="; ".join(breakdown) or "no_signal",
                        metadata={
                            "asset_type": asset.asset_type,
                            "captured_at": asset.captured_at,
                            "visibility": asset.visibility,
                        },
                    ),
                    asset=asset,
                )
            )
        matches.sort(key=lambda match: match.candidate.confidence, reverse=True)
        return matches


def _score_asset(
    asset: FamilyAssetRow,
    *,
    text: str,
    member_ids: set[str],
    space_ids: set[str],
    start_at: int | None,
    end_at: int | None,
) -> tuple[float, list[str]]:
    score = 0.0
    breakdown: list[str] = []
    if text and text in asset.name.casefold():
        score += 0.3
        breakdown.append("name_match")
    if space_ids and asset.space_id in space_ids:
        score += 0.2
        breakdown.append("space_match")
    if start_at is not None and asset.captured_at >= start_at:
        score += 0.2
        breakdown.append("captured_after_start")
    if end_at is not None and asset.captured_at <= end_at:
        score += 0.2
        breakdown.append("captured_before_end")
    return min(score, 1.0), breakdown


__all__ = ["AssetMatch", "AssetQueryResolver"]