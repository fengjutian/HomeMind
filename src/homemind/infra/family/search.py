"""Unified structured and keyword search across the family domain."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import (
    EmbeddingProvider,
    PhotoIntelligenceManager,
    PhotoReranker,
)
from octop.infra.users.identity import User


class SearchKind(StrEnum):
    MEMBER = "MEMBER"
    EVENT = "EVENT"
    MEMORY = "MEMORY"
    ASSET = "ASSET"


@dataclass(frozen=True)
class FamilySearchResult:
    kind: SearchKind
    id: str
    title: str
    snippet: str
    score: float
    metadata: dict[str, object]


class FamilySearchManager:
    def __init__(
        self,
        family: FamilyManager,
        context: FamilyContextManager,
        assets: FamilyAssetManager,
        photos: PhotoIntelligenceManager | None = None,
    ) -> None:
        self.family = family
        self.context = context
        self.assets = assets
        self.photos = photos

    def search(
        self,
        family_id: str,
        user: User,
        *,
        query: str,
        kinds: set[SearchKind] | None = None,
        asset_type: str | None = None,
        limit: int = 50,
        embedding: EmbeddingProvider | None = None,
        reranker: PhotoReranker | None = None,
    ) -> list[FamilySearchResult]:
        self.family.require_access(family_id, user)
        selected = kinds or set(SearchKind)
        normalized = query.strip().casefold()
        results: list[FamilySearchResult] = []
        resolved = self.context.resolve(family_id, user, query)

        if SearchKind.MEMBER in selected:
            for member in self.family.repo.list_members(family_id):
                score = self._score(normalized, member.display_name, member.role)
                if score > 0 or not normalized:
                    results.append(
                        FamilySearchResult(
                            SearchKind.MEMBER,
                            member.id,
                            member.display_name,
                            member.role,
                            score or 1,
                            {"role": member.role},
                        )
                    )

        if SearchKind.EVENT in selected:
            resolved_event_ids = set(resolved.event_ids)
            time_query = "去年" in query or "last year" in normalized
            for event in self.context.list_events(family_id, user):
                score = self._score(
                    normalized,
                    event.title,
                    event.location or "",
                    event.description,
                    event.event_type,
                )
                if time_query and event.id in resolved_event_ids:
                    score += 40
                if score > 0 or not normalized:
                    results.append(
                        FamilySearchResult(
                            SearchKind.EVENT,
                            event.id,
                            event.title,
                            event.location or event.description,
                            score or 1,
                            {
                                "event_type": event.event_type,
                                "start_at": event.start_at,
                                "end_at": event.end_at,
                                "location": event.location,
                            },
                        )
                    )

        if SearchKind.MEMORY in selected:
            for memory in self.context.search_memories(family_id, user):
                score = self._score(normalized, memory.content, memory.memory_type)
                if score > 0 or not normalized:
                    results.append(
                        FamilySearchResult(
                            SearchKind.MEMORY,
                            memory.id,
                            memory.content[:100],
                            memory.content,
                            score + memory.importance * 10 if score else 1,
                            {
                                "memory_type": memory.memory_type,
                                "importance": memory.importance,
                                "confidence": memory.confidence,
                            },
                        )
                    )

        if SearchKind.ASSET in selected:
            for asset in self.assets.search(
                family_id, user, asset_type=asset_type, limit=max(limit * 4, 100)
            ):
                metadata = json.loads(asset.metadata_json)
                score = self._score(
                    normalized,
                    asset.name,
                    str(metadata.get("relative_path") or ""),
                    asset.asset_type,
                )
                if score > 0 or not normalized:
                    results.append(
                        FamilySearchResult(
                            SearchKind.ASSET,
                            asset.id,
                            asset.name,
                            str(metadata.get("relative_path") or asset.uri),
                            score or 1,
                            {
                                "asset_type": asset.asset_type,
                                "captured_at": asset.captured_at,
                                "uri": asset.uri,
                            },
                        )
                    )

            if self.photos is not None and embedding is not None and normalized:
                for semantic in self.photos.search(
                    family_id,
                    user,
                    query=query,
                    embedding=embedding,
                    reranker=reranker,
                    limit=max(limit * 2, 20),
                ):
                    asset = self.assets.get(family_id, semantic.asset_id, user)
                    results.append(
                        FamilySearchResult(
                            SearchKind.ASSET,
                            asset.id,
                            asset.name,
                            semantic.description or semantic.location_name or asset.name,
                            (semantic.score + 1.0) * 50.0,
                            {
                                "asset_type": asset.asset_type,
                                "captured_at": asset.captured_at,
                                "uri": asset.uri,
                                "semantic_score": semantic.score,
                                "location_name": semantic.location_name,
                            },
                        )
                    )

        best: dict[tuple[SearchKind, str], FamilySearchResult] = {}
        for result in results:
            key = (result.kind, result.id)
            if key not in best or result.score > best[key].score:
                best[key] = result
        ranked = list(best.values())
        ranked.sort(key=lambda item: (-item.score, item.kind.value, item.title))
        return ranked[:limit]

    @staticmethod
    def _score(query: str, *fields: str) -> float:
        if not query:
            return 0
        score = 0.0
        for field in fields:
            candidate = field.casefold().strip()
            if not candidate:
                continue
            if query == candidate:
                score = max(score, 100)
            elif candidate in query:
                score = max(score, 70 + min(len(candidate), 20))
            elif query in candidate:
                score = max(score, 60 + min(len(query), 20))
        return score
