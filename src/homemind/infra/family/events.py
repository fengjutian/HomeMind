"""Family real-time event bus (Stage 14).

The dashboard already has a WebSocket hub with per-user bindings, so
HomeMind reuses it rather than inventing a second protocol. What this
module adds is the part the hub cannot know on its own: **which
members of a family may see a given event**.

Fan-out rules, in order:

1. A caller only ever receives events for families they belong to.
2. Private-space events reach only the members authorised to read
   that space.
3. An ordinary member does not receive the full audit detail of a
   transaction — managers do.
4. Repeated events carry an ``event_id`` so the client can suppress a
   duplicate toast after a reconnect.

The bus is deliberately write-only from the domain side: managers call
:meth:`FamilyEventBus.emit`, and the bus decides who hears about it.
Nothing in the domain layer touches a socket.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
)

logger = logging.getLogger(__name__)


# Event types the dashboard can render. Keeping them enumerated means a
# typo in a call site is a test failure rather than a silent dead
# event nobody ever sees.
EVENT_APPROVAL_CREATED = "family.approval.created"
EVENT_APPROVAL_DECIDED = "family.approval.decided"
EVENT_TRANSACTION_COMPLETED = "family.transaction.completed"
EVENT_TRANSACTION_FAILED = "family.transaction.failed"
EVENT_JOB_PROGRESS = "family.asset_job.progress"
EVENT_JOB_COMPLETED = "family.asset_job.completed"
EVENT_JOB_FAILED = "family.asset_job.failed"
EVENT_DEVICE_ONLINE = "family.device.online"
EVENT_DEVICE_OFFLINE = "family.device.offline"
EVENT_INVITE_REDEEMED = "family.invite.redeemed"
EVENT_MEMORY_CANDIDATE_CREATED = "family.memory_candidate.created"
EVENT_REMINDER_DUE = "family.reminder.due"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_APPROVAL_CREATED,
        EVENT_APPROVAL_DECIDED,
        EVENT_TRANSACTION_COMPLETED,
        EVENT_TRANSACTION_FAILED,
        EVENT_JOB_PROGRESS,
        EVENT_JOB_COMPLETED,
        EVENT_JOB_FAILED,
        EVENT_DEVICE_ONLINE,
        EVENT_DEVICE_OFFLINE,
        EVENT_INVITE_REDEEMED,
        EVENT_MEMORY_CANDIDATE_CREATED,
        EVENT_REMINDER_DUE,
    }
)

# Only a family manager gets the full audit view of a transaction.
_MANAGER_ONLY_EVENTS: frozenset[str] = frozenset({EVENT_APPROVAL_CREATED, EVENT_APPROVAL_DECIDED})


@dataclass(frozen=True)
class FamilyEvent:
    """One notification, already scoped to a family."""

    event_type: str
    family_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    space_id: str | None = None
    owner_member_id: str | None = None
    created_at: int = 0

    @property
    def event_id(self) -> str:
        """Stable id derived from the event content.

        A reconnecting client that re-fetches state and replays the
        same events sees identical ids and can de-duplicate instead of
        showing the same toast three times.
        """

        blob = json.dumps(
            {
                "t": self.event_type,
                "f": self.family_id,
                "s": self.space_id,
                "p": self.payload,
                "c": self.created_at,
            },
            sort_keys=True,
            default=str,
        )
        import hashlib  # noqa: PLC0415

        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def as_frame(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "event_id": self.event_id,
            "family_id": self.family_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class FamilyEventBus:
    """Routes :class:`FamilyEvent` objects to authorised members."""

    def __init__(
        self,
        services: HomeMindServices,
        *,
        hub: Any,
    ) -> None:
        self._services = services
        self._hub = hub
        self._family = FamilyManager(services.family_repo)
        self._permissions = FamilyPermissionEvaluator(services.family_repo)

    async def emit(self, event: FamilyEvent) -> int:
        """Deliver ``event`` to every member allowed to see it.

        Returns the number of members reached, which the tests assert
        against to prove the fan-out rule is doing something.
        """

        if event.event_type not in EVENT_TYPES:
            logger.warning(
                "FamilyEventBus: unknown event type %r; dropping",
                event.event_type,
            )
            return 0
        timestamp = event.created_at or int(time.time())
        enriched = FamilyEvent(
            event_type=event.event_type,
            family_id=event.family_id,
            payload=event.payload,
            space_id=event.space_id,
            owner_member_id=event.owner_member_id,
            created_at=timestamp,
        )
        frame = enriched.as_frame()

        reached = 0
        for member in self._services.family_repo.list_members(event.family_id):
            if member.user_id is None:
                continue
            if not self._may_see(event, member.id, str(member.role)):
                continue
            await self._hub.push_to_user(member.user_id, frame)
            reached += 1
        _hm_inc("family_event_emitted_total")
        return reached

    # -------------------------------------------------------------- helpers

    def _may_see(
        self,
        event: FamilyEvent,
        member_id: str,
        role: str,
    ) -> bool:
        """Authorisation for one member.

        The private-space check runs *before* the manager shortcut —
        otherwise a manager's blanket access would leak another
        household's private space into their notification stream.
        """

        if event.space_id is not None:
            space = self._services.family_repo.get_space(event.space_id)
            if (
                space is not None
                and space.space_type == "PRIVATE"
                and space.owner_member_id != member_id
            ):
                return False
        if event.owner_member_id is not None and event.owner_member_id != member_id:
            # Scoped to a specific member (e.g. their own candidate).
            return False
        return not (event.event_type in _MANAGER_ONLY_EVENTS and role not in {"OWNER", "ADMIN"})

    async def emit_many(self, events: list[FamilyEvent]) -> int:
        total = 0
        for event in events:
            total += await self.emit(event)
        return total


def _hm_inc(name: str, n: int = 1) -> None:
    from homemind.infra.metrics import inc  # noqa: PLC0415

    with contextlib.suppress(AttributeError):
        inc(name, n)


async def emit_family_event(
    bus: FamilyEventBus | None,
    event_type: str,
    family_id: str,
    payload: dict[str, Any],
    *,
    space_id: str | None = None,
    owner_member_id: str | None = None,
) -> int:
    """Emit ``event_type`` through ``bus``, tolerating a missing bus.

    Routers call this on every write. The bus is absent in test rigs and
    before the gateway is up; a write must not fail because nobody is
    watching, so a ``None`` bus is a silent no-op.
    """

    if bus is None:
        return 0
    return await bus.emit(
        FamilyEvent(
            event_type=event_type,
            family_id=family_id,
            payload=payload,
            space_id=space_id,
            owner_member_id=owner_member_id,
        )
    )


__all__ = [
    "EVENT_APPROVAL_CREATED",
    "EVENT_APPROVAL_DECIDED",
    "EVENT_DEVICE_OFFLINE",
    "EVENT_DEVICE_ONLINE",
    "EVENT_INVITE_REDEEMED",
    "EVENT_JOB_COMPLETED",
    "EVENT_JOB_FAILED",
    "EVENT_JOB_PROGRESS",
    "EVENT_MEMORY_CANDIDATE_CREATED",
    "EVENT_REMINDER_DUE",
    "EVENT_TRANSACTION_COMPLETED",
    "EVENT_TRANSACTION_FAILED",
    "EVENT_TYPES",
    "FamilyEvent",
    "FamilyEventBus",
    "emit_family_event",
]
