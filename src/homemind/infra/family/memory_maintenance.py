"""Daily maintenance runner for HomeMind.

Two sweeps run here so neither depends on a browser being open:

* **Memory** — ``memory_decay`` lowers ``confidence`` on long-idle,
  low-importance memories; ``memory_expiration`` archives rows whose
  ``expires_at`` has passed; ``memory_deduplication`` flags candidate
  clusters for a manager to merge (it never deletes).
* **Devices** — any device whose ``last_seen`` fell behind the heartbeat
  timeout is flipped to ``OFFLINE`` so online state stays truthful even
  when nobody is looking at the dashboard.

Both sweeps are idempotent — running twice in a row yields the same
result — and the first pass runs immediately at startup so a freshly
bound database does not wait 24 h.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.db.repos.memory_candidates import (
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.device_runtime import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DeviceRuntimeManager,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.notifications import NotificationManager
from homemind.infra.family.transactions import FamilyTransactionManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.db.pool import DatabasePool

logger = logging.getLogger(__name__)


class MaintenanceRunner:
    """Run memory + device sweeps once on startup then daily."""

    def __init__(
        self,
        *,
        db: DatabasePool,
        family_repo: Any,
        context_repo: FamilyContextRepo,
        candidate_repo: MemoryCandidateRepo,
        evidence_repo: MemoryEvidenceRepo,
        device_repo: FamilyDeviceRepo | None = None,
        family_manager: FamilyManager | None = None,
        context_manager: FamilyContextManager | None = None,
        lifecycle: MemoryLifecycleManager | None = None,
        device_manager: DeviceRuntimeManager | None = None,
        notification_manager: NotificationManager | None = None,
        transaction_manager: FamilyTransactionManager | None = None,
        interval_seconds: float = 24 * 60 * 60,
    ) -> None:
        self._db = db
        self._family_repo = family_repo
        self._context_repo = context_repo
        self._candidate_repo = candidate_repo
        self._evidence_repo = evidence_repo
        self._family_manager = family_manager or FamilyManager(family_repo)
        self._context_manager = context_manager or FamilyContextManager(
            self._family_manager, context_repo,
        )
        self._lifecycle = lifecycle or MemoryLifecycleManager(
            self._family_manager,
            self._context_manager,
            candidate_repo,
            evidence_repo,
        )
        self._device_manager = (
            device_manager
            if device_manager is not None
            else (
                DeviceRuntimeManager(self._family_manager, device_repo)
                if device_repo is not None
                else None
            )
        )
        self._transaction_manager = transaction_manager
        # Optional so a deployment without the notification tables still
        # sweeps memories and devices.
        self._notifications = notification_manager
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        # First sweep runs on the event loop without blocking startup.
        self._task = asyncio.create_task(self._loop(), name="homemind-maintenance")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        try:
            self.run_once()
        except Exception:
            logger.exception("MaintenanceRunner: initial sweep crashed")
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            if self._stop_event.is_set():
                break
            try:
                self.run_once()
            except Exception:
                logger.exception("MaintenanceRunner: periodic sweep crashed")

    def run_once(self) -> dict[str, int]:
        """Run one full sweep. Returns counters for tests + metrics."""
        totals = {
            "decayed": 0,
            "expired": 0,
            "duplicate_groups": 0,
            "families": 0,
            "devices_offline": 0,
            "approvals_expired": 0,
        }
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT family_id FROM homemind_families"
            ).fetchall()
        family_ids = [str(row["family_id"]) for row in rows]
        for family_id in family_ids:
            try:
                self._sweep_family(family_id, totals)
            except Exception:
                logger.exception(
                    "MaintenanceRunner: family sweep crashed family_id=%s",
                    family_id,
                )
        if totals["decayed"]:
            _hm_inc("memory_decay_total", totals["decayed"])
        if totals["expired"]:
            _hm_inc("memory_expiration_total", totals["expired"])
        if totals["duplicate_groups"]:
            _hm_inc("memory_dedup_groups_total", totals["duplicate_groups"])
        if self._device_manager is not None:
            try:
                stale = self._device_manager.list_stale_online_devices()
                totals["devices_offline"] = self._device_manager.mark_stale_devices_offline()
                # Tell the family which device went dark, not just that
                # something did. A notification with no name is a nag;
                # a named one is actionable.
                for device in stale:
                    if self._notifications is None:
                        break
                    self._notifications.notify_device_offline(
                        device.family_id, device_id=device.id, name=device.name
                    )
            except Exception:
                logger.exception("MaintenanceRunner: device liveness sweep crashed")
        if self._notifications is not None:
            try:
                totals["notifications_purged"] = self._notifications.purge_expired_globally()
            except Exception:
                logger.exception("MaintenanceRunner: notification expiry sweep crashed")
        if self._transaction_manager is not None:
            # Stale approvals must not linger forever: an expired
            # approval is cancelled so the dashboard stops showing a
            # pending badge nobody can act on.
            try:
                totals["approvals_expired"] = (
                    self._transaction_manager.expire_pending_approvals()
                )
            except Exception:
                logger.exception("MaintenanceRunner: approval expiry sweep crashed")
        return totals

    def _sweep_family(self, family_id: str, totals: dict[str, int]) -> None:
        totals["families"] += 1
        totals["decayed"] += self._lifecycle.decay_memories(family_id)
        totals["expired"] += self._lifecycle.archive_expired_memories(family_id)
        groups = self._lifecycle.detect_duplicates(family_id)
        totals["duplicate_groups"] += len(groups)


# Backwards-compatible alias: the runner was originally memory-only.
MemoryMaintenanceRunner = MaintenanceRunner

__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "MaintenanceRunner",
    "MemoryMaintenanceRunner",
]
