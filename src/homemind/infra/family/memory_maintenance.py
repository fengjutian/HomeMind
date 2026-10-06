"""Daily memory maintenance runner.

The runner keeps the memory table healthy without requiring an
external scheduler:

* ``memory_decay`` lowers ``confidence`` on long-idle, low-importance
  memories so a stale fact cannot outvote a fresh one.
* ``memory_expiration`` archives any active memory whose
  ``expires_at`` has passed; reviewers can still restore it via the
  ``/memories/{id}/restore`` endpoint.
* ``memory_deduplication`` flags candidate clusters for the manager
  to merge; it never deletes rows on its own.

The runner is idempotent — running it twice in a row yields the same
result. The first sweep runs immediately at startup so freshly bound
databases do not need to wait 24 h for the first pass.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.memory_candidates import (
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.db.pool import DatabasePool

logger = logging.getLogger(__name__)


class MemoryMaintenanceRunner:
    """Run decay / expiration / dedup once on startup then daily."""

    def __init__(
        self,
        *,
        db: DatabasePool,
        family_repo: Any,
        context_repo: FamilyContextRepo,
        candidate_repo: MemoryCandidateRepo,
        evidence_repo: MemoryEvidenceRepo,
        family_manager: FamilyManager | None = None,
        context_manager: FamilyContextManager | None = None,
        lifecycle: MemoryLifecycleManager | None = None,
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
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        # First sweep runs on the event loop without blocking startup.
        self._task = asyncio.create_task(self._loop(), name="homemind-memory-maintenance")

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
            logger.exception("MemoryMaintenanceRunner: initial sweep crashed")
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            if self._stop_event.is_set():
                break
            try:
                self.run_once()
            except Exception:
                logger.exception("MemoryMaintenanceRunner: periodic sweep crashed")

    def run_once(self) -> dict[str, int]:
        """Run one full sweep across every family. Returns counters."""
        totals = {"decayed": 0, "expired": 0, "duplicate_groups": 0, "families": 0}
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
                    "MemoryMaintenanceRunner: family sweep crashed family_id=%s",
                    family_id,
                )
        if totals["decayed"]:
            _hm_inc("memory_decay_total", totals["decayed"])
        if totals["expired"]:
            _hm_inc("memory_expiration_total", totals["expired"])
        if totals["duplicate_groups"]:
            _hm_inc("memory_dedup_groups_total", totals["duplicate_groups"])
        return totals

    def _sweep_family(self, family_id: str, totals: dict[str, int]) -> None:
        totals["families"] += 1
        totals["decayed"] += self._lifecycle.decay_memories(family_id)
        totals["expired"] += self._lifecycle.archive_expired_memories(family_id)
        groups = self._lifecycle.detect_duplicates(family_id)
        totals["duplicate_groups"] += len(groups)


__all__ = ["MemoryMaintenanceRunner"]
