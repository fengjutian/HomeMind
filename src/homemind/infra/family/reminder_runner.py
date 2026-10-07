"""Background worker that delivers due family reminders (Stage 1).

One runner per process, started by ``HomeMindServer`` next to the asset
job runner. Three properties matter and are all enforced by the storage
layer rather than by this loop:

* **Exactly once.** Every delivery is a leased row with a unique
  ``dedupe_key``, so a restart or a second runner cannot produce a
  second notification for the same occurrence.
* **No lost work.** A ``CLAIMED`` reminder whose lease expired is
  returned to the queue on boot and again by the periodic sweep.
* **The bus is optional.** Delivery pushes a domain event and marks the
  row ``SENT``; a deployment without a gateway still drains the queue,
  it just does not push. Nothing here touches a dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable

from homemind.infra.family.events import FamilyEventBus
from homemind.infra.family.reminders import FamilyReminderManager, ReminderDispatch
from homemind.infra.metrics import inc as _hm_inc

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 120
# After this many failed attempts a reminder is retired rather than
# retried forever. A reminder whose target was deleted is the common
# case; a broken event bus is the other, and neither heals itself.
MAX_DELIVERY_ATTEMPTS = 5


def _worker_id() -> str:
    """Stable-ish worker identity for lease ownership."""
    return f"{socket.gethostname()}:{os.getpid()}"


class ReminderRunner:
    """Claim due reminders, publish them, and record the outcome."""

    def __init__(
        self,
        *,
        manager: FamilyReminderManager,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = MAX_DELIVERY_ATTEMPTS,
        worker_id: str | None = None,
        event_bus: Callable[[], FamilyEventBus | None] | None = None,
    ) -> None:
        self._manager = manager
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._worker_id = worker_id or _worker_id()
        self._event_bus_source = event_bus
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def start(self) -> None:
        if self._task is not None:
            return
        # A restart is a resume: hand back whatever the previous worker
        # was holding before looking for new work.
        recovered = self._manager.recover_stale_leases()
        if recovered:
            logger.info("ReminderRunner: recovered %d stale leases", recovered)
        self._task = asyncio.create_task(self._loop(), name="homemind-reminders")

    async def stop(self) -> None:
        """Stop the loop without abandoning a claimed row.

        A reminder already claimed stays ``CLAIMED`` with a lease that
        will expire; the next boot recovers it. Cancelling the task
        mid-delivery would leave the row leased with no way to know
        whether the notification went out.
        """
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad reminder must not kill the loop
                logger.exception("ReminderRunner: drain failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def drain_once(self) -> int:
        """Deliver every reminder that is due right now.

        Returns the number of reminders successfully delivered, which the
        tests assert against to prove the loop actually did work.
        """
        # Claiming is synchronous SQL, but the delivery path awaits the
        # event bus, so the whole sweep runs off the event loop.
        dispatches = await asyncio.to_thread(
            self._manager.claim_due,
            owner=self._worker_id,
            ttl_seconds=self._lease_seconds,
        )
        delivered = 0
        for dispatch in dispatches:
            if await self._deliver(dispatch):
                delivered += 1
        return delivered

    async def _deliver(self, dispatch: ReminderDispatch) -> bool:
        reminder = dispatch.reminder
        try:
            await self._publish(dispatch)
        except Exception as exc:  # noqa: BLE001 — a bus outage must be retried, not lost
            permanent = reminder.attempt_count >= self._max_attempts
            logger.warning(
                "ReminderRunner: reminder %s delivery failed (attempt %d): %s",
                reminder.id,
                reminder.attempt_count,
                exc,
            )
            await asyncio.to_thread(
                self._manager.mark_failed,
                reminder.id,
                error=str(exc),
                permanent=permanent,
            )
            _hm_inc("reminder_delivery_failed_total")
            return False
        await asyncio.to_thread(self._manager.mark_sent, reminder.id)
        _hm_inc("reminder_delivered_total")
        return True

    async def _publish(self, dispatch: ReminderDispatch) -> None:
        """Emit the reminder as a family event.

        The payload carries *keys and parameters*, never pre-rendered
        text: the dashboard resolves the title in the reader's locale, so
        one stored reminder can be shown in Chinese or English without
        storing two copies.
        """
        if self._event_bus_source is None:
            return
        bus = self._event_bus_source()
        if bus is None:
            return
        from homemind.infra.family.events import (  # noqa: PLC0415
            EVENT_REMINDER_DUE,
            emit_family_event,
        )

        reminder = dispatch.reminder
        await emit_family_event(
            bus,
            EVENT_REMINDER_DUE,
            dispatch.family_id,
            {
                "reminder_id": reminder.id,
                "target_type": reminder.target_type,
                "target_id": reminder.target_id,
                "target_title": dispatch.target_title,
                "occurrence_key": reminder.occurrence_key,
                "remind_at": reminder.remind_at,
                "channel": reminder.channel,
            },
            owner_member_id=dispatch.recipient_member_id,
        )


__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "MAX_DELIVERY_ATTEMPTS",
    "ReminderRunner",
]
