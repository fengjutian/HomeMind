"""Run the memory extractor after each chat turn.

The orchestrator is intentionally small and exception-tolerant: a
chat reply must never fail because the extractor or the lifecycle
manager threw. All failures are logged (without the original user
text) and counted in the metrics bus so we can alert on regressions
without leaking sensitive content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homemind.infra.agents._request_helpers import (
    ResolvedTurnUser as _ResolvedTurnUser,
    extract_text as _extract_text,
)
from homemind.infra.agents.memory_candidate_extractor import (
    ExtractionResult,
    extract_candidates,
    to_candidate_spec,
)
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.resolvers.relationship import RelationshipResolver
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostTurnResult:
    """Outcome of one ``run_for_turn`` call."""

    candidates_created: int
    skipped_sensitive: int
    skipped_low_confidence: int


def run_for_turn(
    *,
    user_repo: UserRepo,
    family_manager: Any,
    context_repo: FamilyContextRepo,
    lifecycle: MemoryLifecycleManager,
    active_family_id: str,
    turn_user: _ResolvedTurnUser | None,
    messages: list[Any],
) -> PostTurnResult:
    """Inspect the latest user message and, if it expresses a fact,
    create a Candidate through the lifecycle manager.

    Parameters
    ----------
    user_repo:
        Repository to load the authenticated user. ``turn_user`` may
        be ``None`` when no principal is on the request (e.g. eval
        harness) — in that case the orchestrator is a no-op.
    family_manager:
        ``FamilyManager`` instance used to resolve subject members
        via family relationships.
    context_repo:
        ``FamilyContextRepo`` for memory + event lookups.
    lifecycle:
        ``MemoryLifecycleManager`` that owns the Candidate insert
        path. ``create_candidate`` is the only repo write we make.
    active_family_id:
        The family the current turn is scoped to. The orchestrator
        never picks a fallback family.
    messages:
        The full request message list (system + conversation). Only
        the most recent HumanMessage is analysed; the assistant
        reply is ignored so the model cannot write its own opinions
        as family facts.
    """

    if turn_user is None or active_family_id is None:
        return PostTurnResult(0, 0, 0)

    user_row = user_repo.get(turn_user.user_id)
    if user_row is None:
        return PostTurnResult(0, 0, 0)
    user = User(
        id=user_row.id,
        username=user_row.username,
        role=user_row.role,
        display_name=user_row.display_name,
        locale=user_row.locale,
    )

    user_text = _extract_latest_human_text(messages)
    if not user_text:
        return PostTurnResult(0, 0, 0)

    try:
        members = family_manager.repo.list_members(active_family_id)
        membership = family_manager.repo.get_membership(active_family_id, user.id)
        current_member_id = str(membership["member_id"]) if membership else None
        relationship_resolver = (
            RelationshipResolver(family_manager.repo) if current_member_id else None
        )
        result: ExtractionResult = extract_candidates(
            user_text,
            members=members,
            relationship_resolver=relationship_resolver,
            current_member_id=current_member_id,
        )
    except Exception:
        logger.exception(
            "MemoryPostTurn: extractor crashed user_id=%s family_id=%s",
            user.id,
            active_family_id,
        )
        _hm_inc("memory_post_turn_extractor_error_total")
        return PostTurnResult(0, 0, 0)

    if not result.has_candidates:
        if result.skipped_sensitive:
            _hm_inc("memory_post_turn_sensitive_skip_total", result.skipped_sensitive)
        if result.skipped_low_confidence:
            _hm_inc(
                "memory_post_turn_low_confidence_skip_total",
                result.skipped_low_confidence,
            )
        return PostTurnResult(0, result.skipped_sensitive, result.skipped_low_confidence)

    created = 0
    try:
        for extracted in result.candidates:
            spec = to_candidate_spec(
                active_family_id,
                extracted,
                source_type="USER",
                source_id=turn_user.thread_id or None,
            )
            lifecycle.create_candidate(spec, creator=user)
            created += 1
    except Exception:
        logger.exception(
            "MemoryPostTurn: lifecycle create_candidate crashed user_id=%s family_id=%s",
            user.id,
            active_family_id,
        )
        _hm_inc("memory_post_turn_lifecycle_error_total")
        # Return whatever we managed to create; do not raise.
        return PostTurnResult(created, result.skipped_sensitive, result.skipped_low_confidence)

    _hm_inc("memory_post_turn_candidates_created_total", created)
    return PostTurnResult(
        created,
        result.skipped_sensitive,
        result.skipped_low_confidence,
    )


def _extract_latest_human_text(messages: list[Any]) -> str:
    """Return the most recent HumanMessage text from ``messages``.

    Post-turn extraction runs *after* the assistant has replied, so
    the last message in the list is usually the assistant's reply;
    we must skip past that to the user's turn.
    """
    for message in reversed(messages):
        if not _is_human_message(message):
            continue
        text = _extract_text(message)
        if text:
            return text
    return ""


def _is_human_message(message: Any) -> bool:
    cls_name = type(message).__name__
    if cls_name == "HumanMessage":
        return True
    role = getattr(message, "role", None) or getattr(message, "type", None)
    return role == "human" or role == "user"


__all__ = ["PostTurnResult", "run_for_turn"]
