"""Heuristic extractor that turns one chat turn into zero-or-more
``CandidateSpec`` instances.

The extractor is intentionally rule-based and offline so it cannot
break the chat pipeline (no LLM call, no network). It only analyses
what the *user* said; assistant replies are ignored so the model
never gets to write its own opinions back as family facts. Sensitive
categories stay PENDING per ``is_sensitive`` and low-confidence
candidates are dropped before they ever touch the lifecycle manager.

The interface is stable enough that a future embedding-based
extractor can drop in without changing the post-turn orchestrator.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from homemind.infra.db.repos.family_context import FamilyMemoryRow
from homemind.infra.family.memory_lifecycle import CandidateSpec, is_sensitive
from homemind.infra.family.resolvers.models import ResolutionCandidate
from homemind.infra.family.resolvers.relationship import RelationshipResolver

logger = logging.getLogger(__name__)


# Confidence floor below which we silently drop the candidate. Below
# this we cannot tell whether the user meant it as a joke or a real
# fact.
_MIN_CONFIDENCE = 0.5

# Maximum candidates per turn so a runaway chat cannot flood the
# review queue.
_MAX_CANDIDATES_PER_TURN = 5

# Patterns that signal a new fact about the *user* or someone they
# mention. Keep this small and conservative — the lifecycle manager is
# the gate, not the extractor.
_FACT_PATTERNS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    # 用户/提及对象 + 喜好
    (
        re.compile(
            r"(?P<subject>我|我妈|我爸爸|我外婆|我爷爷|外婆|爷爷|妈妈|爸爸)"
            r"(?:[^\n。?！!]{0,8})?(?:喜欢|爱吃|爱喝|爱吃|想)"
            r"(?P<object>[^。?！!；\n]{1,40})",
        ),
        "PREFERENCE",
        0.7,
    ),
    # 过敏
    (
        re.compile(
            r"(?P<subject>我|我妈|我爸爸|外婆|爷爷|妈妈|爸爸)"
            r"(?:[^\n。?！!]{0,8})?对(?P<object>[^。?！!；\n]{1,40})过敏",
        ),
        "ALLERGY",
        0.9,
    ),
    # 重要日期
    (
        re.compile(
            r"(?P<subject>我妈|我爸爸|外婆|爷爷|妈妈|爸爸)"
            r"(?:的)?生日(?:是(?P<object>\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}))?",
        ),
        "BIRTHDAY",
        0.85,
    ),
)

# Subject alias → resolution hint. The extractor does not resolve the
# alias to a member id; the lifecycle manager's
# ``MemoryLifecycleManager`` only stores ``subject_type`` +
# ``subject_id`` once a family manager has confirmed it. When the
# extractor cannot resolve, it leaves ``subject_id`` empty and the
# reviewer fixes it during approval.
_SUBJECT_TYPE_FOR_ALIAS: dict[str, str] = {
    "我": "MEMBER",
    "妈妈": "MEMBER",
    "我妈": "MEMBER",
    "爸爸": "MEMBER",
    "我爸爸": "MEMBER",
    "外婆": "MEMBER",
    "我外婆": "MEMBER",
    "爷爷": "MEMBER",
    "我爷爷": "MEMBER",
}


@dataclass(frozen=True)
class ExtractedCandidate:
    """One candidate fact produced by the extractor."""

    subject_type: str
    subject_id: str | None
    memory_type: str
    content: str
    importance: float
    confidence: float
    visibility: str = "FAMILY"


@dataclass(frozen=True)
class ExtractionResult:
    """All candidates produced for a single turn."""

    candidates: tuple[ExtractedCandidate, ...]
    skipped_sensitive: int = 0
    skipped_low_confidence: int = 0

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


def extract_candidates(
    user_message: str,
    *,
    members: Iterable[FamilyMemoryRow] = (),
    relationship_resolver: RelationshipResolver | None = None,
    current_member_id: str | None = None,
) -> ExtractionResult:
    """Scan a single user message for candidate facts.

    ``members`` and ``relationship_resolver`` are used purely to
    resolve pronouns like "妈妈" to ``subject_id`` so the review UI
    can pre-fill a member chip. They never affect *whether* a
    candidate is produced.
    """

    text = user_message.strip()
    if not text:
        return ExtractionResult(candidates=())

    # Whole-message sensitive scan: if the user's text contains a
    # sensitive category, we do not extract anything from it. The
    # lifecycle manager's PENDING bucket is for facts the extractor
    # found and labelled sensitive — raw sensitive chat text never
    # even reaches the candidate table.
    if is_sensitive(text):
        return ExtractionResult(candidates=(), skipped_sensitive=1)

    alias_to_member_id: dict[str, str] = {}
    for member in members:
        # Build a lookup keyed on display_name (casefolded). The
        # extractor only ever uses this for hints — the canonical
        # resolution lives in the lifecycle manager.
        if member.display_name:
            alias_to_member_id[member.display_name.casefold()] = member.id

    # Optional: resolve via family relationships so "我妈妈" /
    # "妈妈" both map to the same member when the user has set up the
    # PARENT / MOTHER_OF edge. The resolver is allowed to be None
    # (eval / unit tests).
    if relationship_resolver is not None and current_member_id:
        for edge in relationship_resolver.find("母亲", current_member_id=current_member_id):
            alias_to_member_id.setdefault("妈妈", edge.candidate.entity_id)
            alias_to_member_id.setdefault("我妈", edge.candidate.entity_id)
        for edge in relationship_resolver.find("父亲", current_member_id=current_member_id):
            alias_to_member_id.setdefault("爸爸", edge.candidate.entity_id)
            alias_to_member_id.setdefault("我爸爸", edge.candidate.entity_id)

    candidates: list[ExtractedCandidate] = []
    skipped_sensitive = 0
    skipped_low_confidence = 0

    for pattern, memory_type, base_confidence in _FACT_PATTERNS:
        for match in pattern.finditer(text):
            subject_alias = match.group("subject")
            object_value = match.group("object")
            if not object_value:
                # Patterns like the birthday one may produce no object
                # — the user said "妈妈的生日" without a date. We
                # still record a candidate but mark it as needing
                # follow-up so the reviewer knows the value is empty.
                content = f"{subject_alias}的{memory_type.lower()}信息待补充"
                confidence = base_confidence * 0.5
            else:
                content = f"{subject_alias}{match.group(0)[len(subject_alias):]}".strip()
                # Tighten the content to just the fact so the UI
                # shows a clean sentence instead of the full chat
                # line.
                content = f"{subject_alias}：{object_value.strip()}"
                confidence = base_confidence

            if confidence < _MIN_CONFIDENCE:
                skipped_low_confidence += 1
                continue
            if is_sensitive(content):
                skipped_sensitive += 1
                continue

            subject_type = _SUBJECT_TYPE_FOR_ALIAS.get(subject_alias, "FAMILY")
            subject_id = alias_to_member_id.get(subject_alias.casefold())
            candidates.append(
                ExtractedCandidate(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    memory_type=memory_type,
                    content=content,
                    importance=min(1.0, 0.5 + confidence * 0.3),
                    confidence=confidence,
                )
            )
            if len(candidates) >= _MAX_CANDIDATES_PER_TURN:
                break

    if not candidates:
        return ExtractionResult(
            candidates=(),
            skipped_sensitive=skipped_sensitive,
            skipped_low_confidence=skipped_low_confidence,
        )

    # Drop near-duplicate candidates within the same turn so the
    # reviewer is not asked to approve the same fact twice.
    seen_keys: set[tuple[str, str, str]] = set()
    deduped: list[ExtractedCandidate] = []
    for candidate in candidates:
        key = (candidate.subject_id or "", candidate.memory_type, candidate.content)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(candidate)

    return ExtractionResult(
        candidates=tuple(deduped),
        skipped_sensitive=skipped_sensitive,
        skipped_low_confidence=skipped_low_confidence,
    )


def to_candidate_spec(
    family_id: str,
    extracted: ExtractedCandidate,
    *,
    source_type: str = "USER",
    source_id: str | None = None,
) -> CandidateSpec:
    """Convert an extractor result into a lifecycle ``CandidateSpec``."""
    return CandidateSpec(
        family_id=family_id,
        subject_type=extracted.subject_type,
        subject_id=extracted.subject_id,
        content=extracted.content,
        memory_type=extracted.memory_type,
        importance=extracted.importance,
        confidence=extracted.confidence,
        visibility=extracted.visibility,
        source_type=source_type,
        source_id=source_id,
    )


__all__ = [
    "ExtractedCandidate",
    "ExtractionResult",
    "extract_candidates",
    "to_candidate_spec",
]
