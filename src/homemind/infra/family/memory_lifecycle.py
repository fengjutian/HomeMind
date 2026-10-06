"""Memory candidate lifecycle (Stage 4).

Every conversation-derived or agent-generated fact becomes a Candidate
that requires a family manager to approve. Approved candidates are
promoted to ``FamilyMemoryRow`` via ``FamilyContextManager.create_memory``
and may carry evidence rows from multiple sources.

Sensitive categories (medical / financial / passwords / identity
documents) are *never* auto-approved; they are still produced as
candidates and must be reviewed by a human.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from homemind.infra.db.repos.family_context import (
    FamilyContextRepo,
    FamilyMemoryRow,
)
from homemind.infra.db.repos.memory_candidates import (
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_EXPIRED,
    CANDIDATE_STATUS_MERGED,
    CANDIDATE_STATUS_PENDING,
    CANDIDATE_STATUS_REJECTED,
    MemoryCandidateRepo,
    MemoryCandidateRow,
    MemoryEvidenceRepo,
)
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


# Sensitive topics that MUST NOT be auto-approved. They are still
# produced as Candidates but always require manual review.
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(身份证|护照|驾照|证件号|ssn|passport)"),
    re.compile(r"(密码|password|secret\s*=|api\s*key)", re.IGNORECASE),
    re.compile(r"(信用卡|银行卡|银行账号|account\s*number)", re.IGNORECASE),
    re.compile(r"(诊断|处方|血压|血糖|病历|医疗)", re.IGNORECASE),
    re.compile(r"(收入|工资|薪资|薪水|资产\s*总额)", re.IGNORECASE),
)


def is_sensitive(content: str) -> bool:
    """Return True if ``content`` matches a sensitive category."""
    return any(pattern.search(content) for pattern in _SENSITIVE_PATTERNS)


def content_hash(content: str) -> str:
    """Stable hash for memory content used by the evidence table."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateSpec:
    """Input to ``MemoryLifecycleManager.create_candidate``."""

    family_id: str
    subject_type: str
    subject_id: str | None
    content: str
    memory_type: str
    importance: float = 0.5
    confidence: float = 0.5
    visibility: str = "FAMILY"
    source_type: str = "AGENT"
    source_id: str | None = None


class MemoryLifecycleManager:
    """Owns the candidate / evidence / merge workflow for family memories."""

    def __init__(
        self,
        family: FamilyManager,
        context: FamilyContextManager,
        candidates: MemoryCandidateRepo,
        evidence: MemoryEvidenceRepo,
    ) -> None:
        self.family = family
        self.context = context
        self.candidates = candidates
        self.evidence = evidence

    # ---------------------------------------------------------------- write

    def create_candidate(
        self,
        spec: CandidateSpec,
        *,
        creator: User,
    ) -> MemoryCandidateRow:
        """Insert a new PENDING candidate. Sensitive content is forced
        to require human review (status stays PENDING with a marker)."""
        self.family.require_access(spec.family_id, creator)
        membership = self.family.repo.get_membership(spec.family_id, creator.id)
        if membership is None and not creator.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "family membership required")
        content = spec.content.strip()
        if not content:
            raise OctopError(ErrorCode.BAD_REQUEST, "candidate content cannot be empty")
        candidate = self.candidates.create(
            spec.family_id,
            subject_type=spec.subject_type,
            subject_id=spec.subject_id,
            content=content,
            memory_type=spec.memory_type,
            importance=spec.importance,
            confidence=spec.confidence,
            visibility=spec.visibility,
            source_type=spec.source_type,
            source_id=spec.source_id,
            created_by=creator.id,
        )
        # Always log the originating evidence row so auditability is
        # uniform across auto and human candidates.
        self.evidence.add(
            candidate.id,
            memory_id=None,
            source_type=spec.source_type,
            source_id=spec.source_id,
            content_hash=content_hash(content),
            confidence_delta=spec.confidence,
        )
        _hm_inc("memory_candidate_create_total")
        if is_sensitive(content):
            _hm_inc("memory_candidate_sensitive_flagged_total")
        return candidate

    def approve_candidate(
        self,
        family_id: str,
        candidate_id: str,
        *,
        reviewer: User,
    ) -> tuple[MemoryCandidateRow, FamilyMemoryRow]:
        """Promote a PENDING candidate into a real ``FamilyMemoryRow``."""
        self.family.require_manager(family_id, reviewer)
        candidate = self._pending_candidate(family_id, candidate_id)
        memory = self.context.create_memory(
            family_id,
            reviewer,
            subject_type=candidate.subject_type,
            subject_id=candidate.subject_id,
            content=candidate.content,
            memory_type=MemoryType(candidate.memory_type),
            importance=candidate.importance,
            confidence=candidate.confidence,
            visibility=candidate.visibility,
            source_type=candidate.source_type,
            source_id=candidate.source_id,
            expires_at=None,
        )
        updated = self.candidates.decide(
            candidate.id,
            status=CANDIDATE_STATUS_APPROVED,
            reviewer_id=reviewer.id,
            merged_into=memory.id,
        )
        # Anchor existing evidence rows to the new memory row.
        for row in self.evidence.list_for_candidate(candidate.id):
            self.evidence.add(
                candidate.id,
                memory_id=memory.id,
                source_type=row.source_type,
                source_id=row.source_id,
                content_hash=row.content_hash,
                confidence_delta=row.confidence_delta,
            )
        return updated, memory  # type: ignore[return-value]

    def reject_candidate(
        self,
        family_id: str,
        candidate_id: str,
        *,
        reviewer: User,
        reason: str | None = None,
    ) -> MemoryCandidateRow:
        self.family.require_manager(family_id, reviewer)
        candidate = self._pending_candidate(family_id, candidate_id)
        updated = self.candidates.decide(
            candidate.id,
            status=CANDIDATE_STATUS_REJECTED,
            reviewer_id=reviewer.id,
            rejection_reason=reason,
        )  # type: ignore[return-value]
        _hm_inc("memory_candidate_reject_total")
        return updated

    # ---------------------------------------------------------------- search

    def find_similar_memory(
        self,
        family_id: str,
        *,
        content: str,
        subject_type: str,
        subject_id: str | None,
        memory_type: str,
    ) -> list[FamilyMemoryRow]:
        """Return active memories in the same family that look like the
        given candidate. The current implementation does a token-based
        heuristic; later stages can swap in vector similarity."""
        target = content.casefold()
        target_tokens = set(_tokenize(target))
        scored: list[tuple[float, FamilyMemoryRow]] = []
        for memory in self.context.repo.list_all_memories(family_id):
            if memory.memory_type != memory_type:
                continue
            if memory.subject_type != subject_type:
                continue
            if subject_id is not None and memory.subject_id != subject_id:
                continue
            haystack_tokens = set(_tokenize(memory.content.casefold()))
            if not target_tokens:
                continue
            overlap = len(target_tokens & haystack_tokens) / len(target_tokens)
            if overlap >= 0.5:
                scored.append((overlap, memory))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [memory for _, memory in scored]

    # ---------------------------------------------------------------- merge

    def merge_candidate(
        self,
        family_id: str,
        candidate_id: str,
        target_memory_id: str,
        *,
        reviewer: User,
    ) -> tuple[MemoryCandidateRow, FamilyMemoryRow]:
        """Fold a candidate into an existing memory row rather than
        creating a new one. Evidence rows are preserved.
        """
        self.family.require_manager(family_id, reviewer)
        candidate = self._pending_candidate(family_id, candidate_id)
        target = self.context._memory(family_id, target_memory_id)
        # Naive merge: bump confidence by the candidate's evidence
        # contributions and refresh importance by max().
        new_confidence = min(1.0, target.confidence + candidate.confidence * 0.1)
        new_importance = max(target.importance, candidate.importance)
        updated_target = self.context.update_memory(
            family_id,
            target.id,
            reviewer,
            {
                "confidence": new_confidence,
                "importance": new_importance,
            },
        )
        decided = self.candidates.decide(
            candidate.id,
            status=CANDIDATE_STATUS_MERGED,
            reviewer_id=reviewer.id,
            merged_into=target.id,
        )
        for row in self.evidence.list_for_candidate(candidate.id):
            self.evidence.add(
                candidate.id,
                memory_id=target.id,
                source_type=row.source_type,
                source_id=row.source_id,
                content_hash=row.content_hash,
                confidence_delta=row.confidence_delta,
            )
        _hm_inc("memory_candidate_merge_total")
        return decided, updated_target  # type: ignore[return-value]

    # ----------------------------------------------------------- maintenance

    def decay_memories(
        self,
        family_id: str,
        *,
        threshold: float = 0.1,
    ) -> int:
        """Decay ``confidence`` on active memories in ``family_id`` whose
        confidence is below ``threshold``. Returns the count of touched
        rows. Called by the daily cron task."""
        count = 0
        for memory in self.context.repo.list_all_memories(family_id):
            if memory.status != "ACTIVE":
                continue
            if memory.confidence < threshold:
                self.context.update_memory(
                    family_id,
                    memory.id,
                    _system_user(),
                    {"confidence": max(0.0, memory.confidence * 0.5)},
                )
                count += 1
        return count

    def archive_expired_memories(
        self,
        family_id: str,
        *,
        now: int | None = None,
    ) -> int:
        """Mark every expired active memory as ARCHIVED. ``now`` is
        injected to avoid relying on the system clock at test time."""
        timestamp = int(datetime.now(tz=timezone.utc).timestamp()) if now is None else now
        count = 0
        for memory in self.context.repo.list_all_memories(family_id):
            if (
                memory.expires_at is not None
                and memory.expires_at <= timestamp
                and memory.status == "ACTIVE"
            ):
                self.context.update_memory(
                    family_id,
                    memory.id,
                    _system_user(),
                    {"status": "ARCHIVED"},
                )
                count += 1
        return count

    def detect_duplicates(self, family_id: str) -> list[list[FamilyMemoryRow]]:
        """Group memories with high token overlap. Two memories are
        duplicates if their token overlap ≥ 0.7 and they share the same
        subject_type and memory_type.
        """
        rows = list(self.context.repo.list_all_memories(family_id))
        groups: list[list[FamilyMemoryRow]] = []
        for index, candidate in enumerate(rows):
            tokens = set(_tokenize(candidate.content.casefold()))
            if not tokens:
                continue
            cluster: list[FamilyMemoryRow] = []
            for other in rows[index + 1:]:
                if other.memory_type != candidate.memory_type:
                    continue
                if other.subject_type != candidate.subject_type:
                    continue
                other_tokens = set(_tokenize(other.content.casefold()))
                if not other_tokens:
                    continue
                overlap = len(tokens & other_tokens) / max(
                    len(tokens), len(other_tokens)
                )
                if overlap >= 0.7:
                    cluster.append(other)
            if cluster:
                cluster.insert(0, candidate)
                groups.append(cluster)
        return groups

    # ---------------------------------------------------------------- helpers

    def _pending_candidate(self, family_id: str, candidate_id: str) -> MemoryCandidateRow:
        candidate = self.candidates.get(candidate_id)
        if candidate is None or candidate.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "memory candidate not found")
        if candidate.status not in {CANDIDATE_STATUS_PENDING}:
            raise OctopError(
                ErrorCode.CONFLICT,
                f"memory candidate is already {candidate.status.lower()}",
            )
        return candidate


def _tokenize(text: str) -> list[str]:
    """Crude tokenizer that splits on whitespace, ASCII punctuation, and
    any of the Chinese particles (和 / 与 / 及 / 或) that are present in
    the text. Each text is then re-split on every present particle, so
    "妈妈喜欢樱花和茶花" produces {"妈妈喜欢樱花", "茶花"}.
    """
    if not text:
        return []
    out: list[str] = []
    for piece in re.split(r"[\s,。!?；、，．\.!:;\"'()\[\]{}]+", text):
        if not piece:
            continue
        # Determine which particles this piece contains and split only on those.
        present = [particle for particle in ("和", "与", "及", "或") if particle in piece]
        if not present:
            out.append(piece.casefold())
            continue
        segments = [piece]
        for particle in present:
            new_segments: list[str] = []
            for segment in segments:
                new_segments.extend(
                    part for part in segment.split(particle) if part
                )
            segments = new_segments
        out.extend(segment.casefold() for segment in segments)
    return [token for token in out if len(token) >= 1]


def _system_user() -> User:
    return User(
        id=0,
        username="system",
        role="admin",
        display_name="System",
    )


__all__ = [
    "CandidateSpec",
    "MemoryLifecycleManager",
    "is_sensitive",
    "content_hash",
]