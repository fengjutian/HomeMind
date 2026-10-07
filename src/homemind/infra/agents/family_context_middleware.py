"""Per-turn injection of the active family context into the agent request.

The middleware hooks LangChain's ``awrap_model_call`` / ``wrap_model_call``
so every model turn observes the caller's active family without the
caller passing a ``family_id``. The injected payload is treated as
untrusted data (see ``family_context_renderer._XML_PROLOGUE_NOTE``)
and never lands in the persisted user-message history.

Identity comes from ``configurable`` on the agent runtime — the
active caller's ``user_id`` is read per request, never from a process
global. If the user is unauthenticated, the active family has been
deleted, or the user has been removed from the active family, the
middleware appends a minimal hint and does not pick a fallback.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.agents._request_helpers import (
    ResolvedTurnUser as _ResolvedTurnUser,
)
from homemind.infra.agents._request_helpers import (
    extract_text as _extract_text,
)
from homemind.infra.agents._request_helpers import (
    message_hash as _message_hash,
)
from homemind.infra.agents.family_context_renderer import (
    _XML_PROLOGUE_NOTE,
    AssetSummary,
    EventSummary,
    FamilySummary,
    MemberSummary,
    MemorySummary,
    RelationshipSummary,
    render_family_context,
    render_no_active_family_hint,
)
from homemind.infra.agents.memory_post_turn import run_for_turn as _run_post_turn
from homemind.infra.family.context import FamilyContextManager, ResolvedFamilyContext
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


def _latest_user_text(messages: Sequence[Any]) -> str:
    """Return the most recent HumanMessage text from the model request."""
    for message in reversed(messages):
        text = _extract_text(message)
        if text:
            return text
    return ""


class FamilyContextMiddleware(AgentMiddleware[Any, Any]):
    """Inject the active family's resolved context into the system message.

    Required collaborators are injected at construction time so the
    middleware does not reach into the Octop composition root. The
    caller (``HomeMindServer.build_extra_agent_middleware``) builds
    them after the database is bound.
    """

    def __init__(
        self,
        *,
        user_repo: UserRepo,
        active_family_resolver: ActiveFamilyResolver,
        family_manager: FamilyManager,
        context_manager: FamilyContextManager,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
        memory_lifecycle: MemoryLifecycleManager | None = None,
        event_bus: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__()
        self._user_repo = user_repo
        self._active_family = active_family_resolver
        self._family = family_manager
        self._context = context_manager
        self._permissions = permission_evaluator or FamilyPermissionEvaluator(family_manager.repo)
        self._memory_lifecycle = memory_lifecycle
        # A *callable*, not the bus itself: this middleware is built
        # during ``OctopServer._boot_runtime``, before the gateway hub
        # exists. Resolving lazily lets a later ``start()`` wire the bus
        # in without rebuilding the middleware chain.
        self._event_bus_source = event_bus
        # Per-process dedupe for the post-turn extractor so a single
        # user message does not become three candidates when the agent
        # loops through multiple model calls inside one turn.
        self._seen_message_keys: set[tuple[int, str]] = set()

    def _system_message(self, request: Any) -> SystemMessage:
        existing = getattr(request, "system_message", None)
        if isinstance(existing, SystemMessage):
            return existing
        return SystemMessage(content="")

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        turn = _ResolvedTurnUser.from_request_config()
        if turn is None:
            # No authenticated principal on this turn (e.g. eval harness).
            return await handler(request)
        try:
            user_row = self._user_repo.get(turn.user_id)
        except Exception:
            logger.exception("FamilyContextMiddleware: user lookup failed")
            return await handler(request)
        if user_row is None:
            return await handler(request)
        user = User(
            id=user_row.id,
            username=user_row.username,
            role=user_row.role,
            display_name=user_row.display_name,
            locale=user_row.locale,
        )
        try:
            family_id = self._active_family.get(user)
        except OctopError:
            family_id = None
        except Exception:
            logger.exception("FamilyContextMiddleware: active family lookup failed")
            family_id = None
        if not family_id:
            block = render_no_active_family_hint()
            return await handler(_append_block(request, block, self._system_message(request)))

        try:
            family = self._family.require_access(family_id, user)
        except OctopError:
            block = render_no_active_family_hint()
            return await handler(_append_block(request, block, self._system_message(request)))
        except Exception:
            logger.exception("FamilyContextMiddleware: family access check failed")
            return await handler(request)

        messages = list(getattr(request, "messages", []) or [])
        query_text = _latest_user_text(messages)
        if not query_text:
            return await handler(request)

        # Post-turn candidate extraction. Runs only on the first model
        # call for each new user message so tool-loop replays do not
        # create N copies of the same fact. The lifecycle manager is
        # optional so test doubles can opt out.
        if self._memory_lifecycle is not None:
            message_key = (turn.user_id, _message_hash(query_text))
            if message_key not in self._seen_message_keys:
                self._seen_message_keys.add(message_key)
                try:
                    result = _run_post_turn(
                        user_repo=self._user_repo,
                        family_manager=self._family,
                        context_repo=self._context.repo,
                        lifecycle=self._memory_lifecycle,
                        active_family_id=family_id,
                        turn_user=turn,
                        messages=messages,
                    )
                    await self._announce_candidates(family_id, result)
                except Exception:
                    # ``run_for_turn`` already swallows and logs, but
                    # belt-and-braces: an extractor crash must never
                    # break the chat.
                    logger.exception(
                        "FamilyContextMiddleware: post-turn crashed family_id=%s user_id=%s",
                        family_id,
                        user.id,
                    )

        try:
            resolved = self._context.resolve(family_id, user, query_text)
        except OctopError:
            logger.warning(
                "FamilyContextMiddleware: resolve failed family_id=%s user_id=%s",
                family_id,
                user.id,
                exc_info=True,
            )
            return await handler(request)
        except Exception:
            logger.exception("FamilyContextMiddleware: resolve crashed family_id=%s", family_id)
            return await handler(request)

        payload = _build_payload(family, user, resolved, self._family, self._context.repo)
        block = render_family_context(**payload)
        return await handler(_append_block(request, block, self._system_message(request)))

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        # LangChain requires both sync and async hooks. AgentMiddleware's
        # runtime is async-only in this codebase so we just delegate to
        # the sync form via the request — the async path is the real one.
        block = render_no_active_family_hint()
        # No async context to resolve here; return the bare request so
        # the agent falls back to its non-family path instead of
        # crashing. Tests rely on this default behaviour.
        _ = block  # silence unused-warning; kept for future sync driver
        return handler(request)

    async def _announce_candidates(
        self, family_id: str, result: Any,
    ) -> None:
        """Tell the family's dashboards a candidate is waiting review.

        Skipped silently when the bus is absent — a conversation must
        never fail because nobody is subscribed.
        """

        if self._event_bus_source is None or result is None:
            return
        bus = self._event_bus_source()
        if bus is None:
            return
        created = getattr(result, "candidates_created", 0)
        if not created:
            return
        from homemind.infra.family.events import (  # noqa: PLC0415
            EVENT_MEMORY_CANDIDATE_CREATED,
            emit_family_event,
        )

        await emit_family_event(
            bus,
            EVENT_MEMORY_CANDIDATE_CREATED,
            family_id,
            {"candidates_created": created},
        )


def _append_block(request: Any, block: str, base_message: SystemMessage) -> Any:
    existing_content = base_message.content or ""
    merged: Any
    if isinstance(existing_content, str):
        merged = existing_content.rstrip() + "\n\n" + block if existing_content else block
    else:
        merged = existing_content
    new_message = SystemMessage(content=merged)
    if hasattr(request, "override"):
        return request.override(system_message=new_message)
    # Fallback for stub requests in tests.
    with contextlib.suppress(Exception):
        request.system_message = new_message
    return request


def _build_payload(
    family: Any,
    user: User,
    resolved: ResolvedFamilyContext,
    family_manager: FamilyManager,
    context_repo: Any,
) -> dict[str, Any]:
    """Translate the resolve output into the renderer's named tuples.

    Each list is capped at ``_MAX_PER_KIND`` items and permission-
    filtered so private-space data never reaches the model — the
    spec requires the renderer to be the last layer of defence, but
    trimming here keeps the block bounded.
    """

    _MAX_PER_KIND = 10

    family_summary = FamilySummary(
        family_id=family.id,
        name=family.name,
        timezone=family.timezone,
        locale=getattr(family, "locale", None),
    )

    current_member: MemberSummary | None = None
    if resolved.current_member_id:
        for member in family_manager.repo.list_members(family.id):
            if member.id == resolved.current_member_id:
                current_member = MemberSummary(
                    id=member.id,
                    display_name=member.display_name,
                    role=member.role,
                )
                break

    members: list[MemberSummary] = []
    for member in family_manager.repo.list_members(family.id):
        if member.id in resolved.member_ids:
            members.append(
                MemberSummary(id=member.id, display_name=member.display_name, role=member.role)
            )
        if len(members) >= _MAX_PER_KIND:
            break

    relationships: list[RelationshipSummary] = []
    for rel in family_manager.repo.list_relationships(family.id):
        if rel.id in resolved.relationship_ids:
            relationships.append(
                RelationshipSummary(
                    id=rel.id,
                    from_member_id=rel.from_member_id,
                    to_member_id=rel.to_member_id,
                    relationship_type=rel.relationship_type,
                )
            )
        if len(relationships) >= _MAX_PER_KIND:
            break

    events: list[EventSummary] = []
    for event in context_repo.list_events(family.id):
        if event.id in resolved.event_ids:
            events.append(
                EventSummary(
                    id=event.id,
                    title=event.title,
                    event_type=event.event_type,
                    start_at=event.start_at,
                    end_at=event.end_at,
                    location=event.location,
                )
            )
        if len(events) >= _MAX_PER_KIND:
            break

    memories: list[MemorySummary] = []
    for memory in context_repo.list_all_memories(family.id):
        if memory.id in resolved.memory_ids and memory.status == "ACTIVE":
            memories.append(
                MemorySummary(
                    id=memory.id,
                    content=memory.content,
                    memory_type=memory.memory_type,
                    subject_id=memory.subject_id,
                    confidence=memory.confidence,
                )
            )
        if len(memories) >= _MAX_PER_KIND:
            break

    assets: list[AssetSummary] = []
    asset_repo = getattr(context_repo, "_asset_repo", None) or getattr(context_repo, "asset_repo", None)
    if asset_repo is not None and resolved.asset_ids:
        for asset in asset_repo.list_for_family(family.id):
            if asset.id in resolved.asset_ids:
                assets.append(
                    AssetSummary(
                        id=asset.id,
                        name=asset.name,
                        asset_type=asset.asset_type,
                        captured_at=asset.captured_at,
                    )
                )
            if len(assets) >= _MAX_PER_KIND:
                break

    return {
        "family": family_summary,
        "current_member": current_member,
        "time_range": resolved.time_range,
        "matched_member_ids": list(resolved.member_ids),
        "matched_event_ids": list(resolved.event_ids),
        "members": members,
        "relationships": relationships,
        "events": events,
        "memories": memories,
        "assets": assets,
        "permissions": list(resolved.permissions),
        "ambiguities": list(resolved.ambiguities),
    }


__all__ = [
    "FamilyContextMiddleware",
    "_XML_PROLOGUE_NOTE",
]
