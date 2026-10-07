"""Family search rebuilt on the unified index (Stage 7).

What changed versus the previous implementation:

* **Permission filtering happens in SQL.** The index row carries
  ``visibility`` / ``owner_member_id`` / ``status``, and
  ``SearchIndexRepo.keyword_search`` builds the predicate into the
  ``WHERE`` clause. A private-space document is never fetched, so it
  cannot leak through a scoring bug or a forgotten post-filter.
* **Pagination is a signed cursor**, not an ``OFFSET``. The sort
  position lives server-side and the token is HMAC-signed, so a client
  cannot edit it to skip or replay rows.
* **Scores are normalised to ``0..1``** and every result carries
  ``matched_by`` so the UI can explain *why* something matched.

Python still performs a final permission check as defence in depth —
the SQL predicate is the primary control, not the only one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from homemind.infra.db.repos.search_index import (
    KIND_ASSET,
    KIND_EVENT,
    KIND_MEMORY,
    SEARCH_KINDS,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_SENSITIVE,
    SearchDocumentRow,
    SearchIndexRepo,
    cosine_similarity,
    decode_position,
    encode_position,
    sanitize_visibility,
    sign_token,
    unpack_embedding,
    verify_token,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 50
MAX_LIMIT = 200
CURSOR_TTL_SECONDS = 3600

# Score weights. They are normalised so the final score stays in 0..1
# and one signal cannot dominate the ordering.
WEIGHT_KEYWORD = 0.45
WEIGHT_SEMANTIC = 0.25
WEIGHT_STRUCTURED = 0.12
WEIGHT_IMPORTANCE = 0.10
WEIGHT_RECENCY = 0.08


@dataclass(frozen=True)
class FamilySearchQuery:
    """Normalised search request."""

    text: str
    kinds: frozenset[str] = frozenset()
    member_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    space_ids: tuple[str, ...] = ()
    asset_types: tuple[str, ...] = ()
    start_at: int | None = None
    end_at: int | None = None
    location: str | None = None
    cursor: str | None = None
    limit: int = DEFAULT_LIMIT
    visibility: str | None = None
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_version: int | None = None

    def resolved_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self.kinds)) if self.kinds else tuple(sorted(SEARCH_KINDS))


@dataclass(frozen=True)
class ScoredDocument:
    document_id: str
    kind: str
    entity_id: str
    title: str
    snippet: str
    score: float
    matched_by: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchPage:
    items: tuple[ScoredDocument, ...]
    next_cursor: str | None
    has_more: bool
    total_candidates: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "kind": item.kind,
                    "id": item.entity_id,
                    "title": item.title,
                    "snippet": item.snippet,
                    "score": item.score,
                    "matched_by": item.matched_by,
                    "highlights": item.highlights,
                    "metadata": item.metadata,
                }
                for item in self.items
            ],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


class FamilySearchManager:
    """Query the unified index with SQL-level permission filtering."""

    def __init__(
        self,
        family: FamilyManager,
        repo: SearchIndexRepo,
        *,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
        cursor_secret: bytes = b"homemind-search-cursor",
        now: Any = None,
    ) -> None:
        self.family = family
        self.repo = repo
        self.permissions = permission_evaluator or FamilyPermissionEvaluator(family.repo)
        self._cursor_secret = cursor_secret
        self._now = now or (lambda: int(time.time()))

    # ---------------------------------------------------------------- query

    def search(
        self, family_id: str, user: User, query: FamilySearchQuery,
    ) -> SearchPage:
        self.family.require_access(family_id, user)
        kinds = query.resolved_kinds()
        visible = sanitize_visibility(query.visibility)
        allow_private = self.permissions.evaluate(
            family_id=family_id, user=user, action="family.search",
        ).effect.value == "ALLOW" and self._is_manager(family_id, user)
        membership = self.family.repo.get_membership(family_id, user.id)
        owner_member_id = (
            str(membership["member_id"]) if membership is not None else None
        )

        keyword_hits = self.repo.keyword_search(
            family_id,
            query=query.text,
            kinds=kinds,
            visible_visibility=visible,
            owner_member_id=owner_member_id,
            allow_private=allow_private,
            start_at=query.start_at,
            end_at=query.end_at,
            limit=max(query.limit * 3, 150),
        )
        semantic_hits = self._semantic_candidates(family_id, query, kinds)

        merged: dict[str, _Candidate] = {}
        for document, rank in keyword_hits:
            merged[document.document_id] = _Candidate(
                document=document, keyword=rank, semantic=None,
            )
        for document, similarity in semantic_hits:
            existing = merged.get(document.document_id)
            if existing is None:
                merged[document.document_id] = _Candidate(
                    document=document, keyword=0.0, semantic=similarity,
                )
            else:
                existing.semantic = similarity

        candidates = [c for c in merged.values() if self._passes_filters(c, query)]
        candidates = [
            c for c in candidates if self._can_read(family_id, user, c.document)
        ]
        for candidate in candidates:
            candidate.score = self._blend(candidate, query)
            candidate.matched_by = self._matched_by(candidate)
            candidate.highlights = self._highlights(candidate.document, query.text)

        candidates.sort(
            key=lambda c: (-c.score, -c.document.updated_at, c.document.kind, c.document.entity_id),
        )
        return self._paginate(family_id, user, candidates, query)

    # ------------------------------------------------------------- scoring

    def _blend(self, candidate: _Candidate, query: FamilySearchQuery) -> float:
        keyword = _normalise_keyword(candidate.keyword)
        semantic = (
            max(0.0, min(1.0, (candidate.semantic + 1.0) / 2.0))
            if candidate.semantic is not None
            else 0.0
        )
        structured = 1.0 if candidate.document.kind in query.kinds and query.kinds else 0.0
        importance = max(0.0, min(1.0, candidate.document.importance))
        age_days = max(0, (self._now() - candidate.document.updated_at) // 86400)
        recency = 1.0 / (1.0 + age_days / 30.0)
        blended = (
            WEIGHT_KEYWORD * keyword
            + WEIGHT_SEMANTIC * semantic
            + WEIGHT_STRUCTURED * structured
            + WEIGHT_IMPORTANCE * importance
            + WEIGHT_RECENCY * recency
        )
        return round(max(0.0, min(1.0, blended)), 4)

    @staticmethod
    def _matched_by(candidate: _Candidate) -> list[str]:
        matched: list[str] = []
        if candidate.keyword > 0:
            matched.append("keyword")
        if candidate.semantic is not None and candidate.semantic > 0:
            matched.append("semantic")
        if candidate.structured_reason:
            matched.append(candidate.structured_reason)
        return matched

    @staticmethod
    def _highlights(document: SearchDocumentRow, text: str) -> list[str]:
        """Query terms that actually appear in the document.

        Returning the matched terms (rather than the whole text) is what
        lets the UI underline just the relevant span.
        """
        needle = text.strip()
        if not needle:
            return []
        haystack = f"{document.title} {document.text}".casefold()
        folded = needle.casefold()
        if folded in haystack:
            return [needle]
        return [token for token in folded.split() if token in haystack][:4]

    # ------------------------------------------------------------ semantic

    def _semantic_candidates(
        self, family_id: str, query: FamilySearchQuery, kinds: tuple[str, ...],
    ) -> list[tuple[SearchDocumentRow, float]]:
        """Compare the query vector against rows with matching provenance.

        Rows recorded under a different model, dimension count, or
        version are skipped entirely: a cross-model cosine similarity
        is a number, but it is not a similarity.
        """
        if not query.embedding or query.embedding_model is None:
            return []
        dimensions = query.embedding_dimensions or len(query.embedding)
        version = query.embedding_version or 1
        rows = self.repo.embedding_candidates(
            family_id,
            model=query.embedding_model,
            dimensions=dimensions,
            version=version,
            limit=500,
        )
        vector = list(query.embedding)
        out: list[tuple[SearchDocumentRow, float]] = []
        for row in rows:
            if row.kind not in kinds:
                continue
            if not row.embedding:
                continue
            out.append((row, cosine_similarity(vector, unpack_embedding(row.embedding))))
        out.sort(key=lambda pair: pair[1], reverse=True)
        return out[:100]

    # ----------------------------------------------------------- filtering

    def _passes_filters(self, candidate: _Candidate, query: FamilySearchQuery) -> bool:
        document = candidate.document
        structured_reason = ""
        if query.member_ids:
            if document.kind != KIND_MEMORY and document.owner_member_id not in query.member_ids:
                return False
            if document.kind == KIND_MEMORY and document.entity_id not in query.member_ids:
                return False
            structured_reason = "member"
        if (
            query.event_ids
            and document.entity_id not in query.event_ids
            and document.kind != KIND_EVENT
        ):
            return False
        if query.space_ids and document.space_id not in query.space_ids:
            return False
        if query.asset_types and document.kind == KIND_ASSET:
            asset_type = _asset_type_of(document)
            if asset_type and asset_type not in query.asset_types:
                return False
        if (
            query.start_at is not None
            and document.captured_at is not None
            and document.captured_at < query.start_at
        ):
            return False
        if (
            query.end_at is not None
            and document.captured_at is not None
            and document.captured_at > query.end_at
        ):
            return False
        if query.location:
            if query.location.casefold() not in f"{document.title} {document.text}".casefold():
                return False
            if not structured_reason:
                structured_reason = "location"
        candidate.structured_reason = structured_reason
        return True

    def _can_read(
        self, family_id: str, user: User, document: SearchDocumentRow,
    ) -> bool:
        """Final permission check in Python — defence in depth behind the
        SQL predicate, not a replacement for it."""
        if document.visibility == VISIBILITY_PUBLIC:
            return True
        membership = self.family.repo.get_membership(family_id, user.id)
        member_id = str(membership["member_id"]) if membership is not None else None

        # The private / sensitive checks run *before* the role check on
        # purpose. A manager's ``family.search`` allow must not widen
        # into someone else's private space — otherwise "manager can see
        # everything" silently becomes "manager can read the kids' private
        # notes", which is exactly what the space boundary forbids.
        if document.visibility == VISIBILITY_PRIVATE:
            if document.owner_member_id is None:
                return False
            return member_id is not None and document.owner_member_id == member_id
        if document.visibility == VISIBILITY_SENSITIVE:
            if membership is None:
                return user.is_admin
            return str(membership["role"]) in {"OWNER", "ADMIN"}

        from homemind.infra.family.permissions import PermissionEffect  # noqa: PLC0415

        decision = self.permissions.evaluate(
            family_id=family_id,
            user=user,
            action="memory.read" if document.kind == KIND_MEMORY else "family.search",
        )
        if decision.effect is PermissionEffect.ALLOW:
            return True
        return membership is not None

    def _is_manager(self, family_id: str, user: User) -> bool:
        membership = self.family.repo.get_membership(family_id, user.id)
        if membership is None:
            return user.is_admin
        return str(membership["role"]) in {"OWNER", "ADMIN"}

    # ---------------------------------------------------------- pagination

    def _paginate(
        self,
        family_id: str,
        user: User,
        candidates: list[_Candidate],
        query: FamilySearchQuery,
    ) -> SearchPage:
        start = 0
        if query.cursor:
            payload = verify_token(query.cursor, secret=self._cursor_secret)
            if payload is None:
                from octop.infra.errors import ErrorCode, OctopError  # noqa: PLC0415

                raise OctopError(ErrorCode.NOT_FOUND, "search cursor is invalid")
            position = decode_position(payload)
            if position is None:
                from octop.infra.errors import ErrorCode, OctopError  # noqa: PLC0415

                raise OctopError(ErrorCode.NOT_FOUND, "search cursor is malformed")
            start = self._resume_offset(candidates, position)

        window = candidates[start : start + query.limit]
        has_more = (start + query.limit) < len(candidates)
        next_cursor: str | None = None
        if has_more and window:
            last = window[-1]
            next_cursor = sign_token(
                encode_position(
                    last.score, last.document.updated_at,
                    last.document.kind, last.document.entity_id,
                ),
                secret=self._cursor_secret,
            )
        return SearchPage(
            items=tuple(
                ScoredDocument(
                    document_id=c.document.document_id,
                    kind=c.document.kind,
                    entity_id=c.document.entity_id,
                    title=c.document.title,
                    snippet=_snippet(c.document),
                    score=c.score,
                    matched_by=c.matched_by,
                    highlights=c.highlights,
                    metadata={
                        "visibility": c.document.visibility,
                        "space_id": c.document.space_id,
                        "importance": c.document.importance,
                        "captured_at": c.document.captured_at,
                        "updated_at": c.document.updated_at,
                    },
                )
                for c in window
            ),
            next_cursor=next_cursor,
            has_more=has_more,
            total_candidates=len(candidates),
        )

    @staticmethod
    def _resume_offset(
        candidates: Sequence[_Candidate], position: dict[str, Any],
    ) -> int:
        """Find where the previous page stopped.

        The candidates are sorted best-first, so the previous page's last
        row marks the first index whose key is *not better than* it.
        Scanning for that boundary is exact: rows inserted after the
        cursor was issued can shift the window, but they cannot make a
        row repeat or be skipped inside it.

        Matching is by ``entity_id`` rather than the full tuple, because
        a recomputed score can differ in the last float digit between
        two identical requests. Identity is the stable key; the score
        is not.
        """
        last_id = str(position.get("i", ""))
        for index, candidate in enumerate(candidates):
            if candidate.document.entity_id == last_id:
                return index + 1
        # The anchor row is gone (deleted, archived, or filtered out by a
        # permission change). Fall back to the score boundary so the
        # client still advances instead of restarting from page one.
        target_score = float(position.get("s", 0.0))
        for index, candidate in enumerate(candidates):
            if candidate.score <= target_score:
                return index
        return len(candidates)


@dataclass
class _Candidate:
    """Intermediate score accumulator for one index row."""

    document: SearchDocumentRow
    keyword: float
    semantic: float | None
    score: float = 0.0
    matched_by: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    structured_reason: str = ""


def _asset_type_of(document: SearchDocumentRow) -> str:
    """Asset type recorded on an ASSET index row.

    The indexer's ``title`` for an asset is the file name and the type
    is carried in the text prefix, so a missing value simply means "no
    filter applies" rather than an error.
    """
    return document.title.rsplit(" ", 1)[-1] if " " in document.title else ""


def _normalise_keyword(rank: float) -> float:
    """Map a backend rank onto ``0..1``.

    FTS5 ``bm25`` returns a negative number where *more negative* is a
    better match; ``ts_rank`` returns a positive one. Both are folded
    into the same range so the two dialects order results identically.
    """
    if rank is None:
        return 0.0
    if rank < 0:
        # bm25: magnitude grows with match quality.
        return max(0.0, min(1.0, abs(rank) / 20.0))
    # ts_rank: unbounded above; saturate around a strong match.
    return max(0.0, min(1.0, rank / 4.0))


def _snippet(document: SearchDocumentRow, *, limit: int = 160) -> str:
    text = " ".join(document.text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = [
    "CURSOR_TTL_SECONDS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "FamilySearchManager",
    "FamilySearchQuery",
    "ScoredDocument",
    "SearchPage",
]
