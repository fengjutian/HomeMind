"""Background worker that drains persistent asset jobs (Stage 6).

Started by ``HomeMindServer`` alongside the maintenance runner. On boot it
recovers jobs whose previous worker died mid-run, then polls for new
work. Because every claim is a database lease, running more than one
worker process is safe: the losers simply get no job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable
from typing import Any

from homemind.infra.db.repos.asset_jobs import (
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from homemind.infra.family.asset_job_handlers import build_handler
from homemind.infra.family.asset_jobs import AssetJobManager, ItemHandler
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.thumbnails import ThumbnailService
from homemind.infra.metrics import inc as _hm_inc

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 5.0


def _worker_id() -> str:
    """Stable-ish worker identity for lease ownership."""
    return f"{socket.gethostname()}:{os.getpid()}"


class AssetJobRunner:
    """Poll the job queue and drain whatever it claims."""

    def __init__(
        self,
        *,
        manager: AssetJobManager,
        asset_manager: FamilyAssetManager,
        family_manager: FamilyManager,
        asset_repo: Any,
        thumbnail_service: ThumbnailService | None = None,
        search_indexer: Any | None = None,
        photo_intelligence: Any | None = None,
        face_manager: Any | None = None,
        provider_repo: Any | None = None,
        user_factory: Callable[[int], Any] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        worker_id: str | None = None,
        event_bus: Callable[[], Any] | None = None,
    ) -> None:
        self._manager = manager
        self._asset_manager = asset_manager
        self._family_manager = family_manager
        self._asset_repo = asset_repo
        self._thumbnail_service = thumbnail_service
        self._search_indexer = search_indexer
        self._photo_intelligence = photo_intelligence
        self._face_manager = face_manager
        self._provider_repo = provider_repo
        # A job runs as a user for permission purposes; a worker has no
        # session, so the runner asks for one by id.
        self._user_factory = user_factory
        self._poll_interval = poll_interval_seconds
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
        # A restart is a resume: requeue whatever the previous worker
        # abandoned before looking for new work.
        recovered = self._manager.recover_stale_jobs()
        if recovered:
            logger.info("AssetJobRunner: recovered %d stale jobs", len(recovered))
        self._task = asyncio.create_task(self._loop(), name="homemind-asset-jobs")

    async def stop(self) -> None:
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
            except Exception:  # noqa: BLE001 — a bad job must not kill the loop
                logger.exception("AssetJobRunner: drain failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval,
                )

    async def drain_once(self) -> int:
        """Claim and run one job. Returns 1 when work was done."""
        job = self._manager._repo.claim_next_job(  # noqa: SLF001 — same-package access
            None,
            owner=self._worker_id,
            ttl_seconds=self._manager.lease_seconds,
        )
        if job is None:
            return 0

        family = self._family_manager.repo.get_family(job.family_id)
        if family is None:
            # The family was deleted while the job was queued; retire the
            # job rather than leaving it RUNNING with no owner.
            self._manager._repo.set_status(  # noqa: SLF001 — same-package access
                job.id,
                to_status="CANCELLED",
                from_status="RUNNING",
                error_summary="family no longer exists",
            )
            logger.warning(
                "AssetJobRunner: job %s references a missing family",
                job.id,
            )
            return 1

        try:
            handler: ItemHandler = build_handler(  # type: ignore[assignment]
                job,
                asset_manager=self._asset_manager,
                asset_repo=self._asset_repo,
                thumbnail_service=self._thumbnail_service,
                search_indexer=self._search_indexer,
                photo_intelligence=self._photo_intelligence,
                face_manager=self._face_manager,
                provider_repo=self._provider_repo,
                family_manager=self._family_manager,
                user=self._resolve_user(int(family.owner_user_id)),
                family_id=job.family_id,
                created_by_user_id=int(family.owner_user_id),
            )
        except NotImplementedError:
            logger.warning(
                "AssetJobRunner: no handler for job type %s",
                job.job_type,
            )
            self._manager._repo.set_status(  # noqa: SLF001 — same-package access
                job.id,
                to_status=JOB_STATUS_FAILED,
                from_status=JOB_STATUS_RUNNING,
                error_summary=f"no handler for job type {job.job_type}",
            )
            _hm_inc("asset_job_failed_total")
            return 1
        except Exception:
            # A job whose *config* is unusable (deleted provider, unknown
            # version) must fail on its own without killing the worker loop.
            logger.exception(
                "AssetJobRunner: job %s (%s) could not be prepared",
                job.id,
                job.job_type,
            )
            self._manager._repo.set_status(  # noqa: SLF001 — same-package access
                job.id,
                to_status=JOB_STATUS_FAILED,
                from_status=JOB_STATUS_RUNNING,
                error_summary="job configuration could not be loaded",
            )
            _hm_inc("asset_job_failed_total")
            return 1

        # Item work is blocking (filesystem + hashing + provider I/O),
        # so it runs in an executor rather than blocking the event loop.
        await asyncio.to_thread(self._manager.run_job, job.id, handler, owner=self._worker_id)
        await self._emit_terminal_event(job.id, job.family_id, job.job_type)
        return 1

    def _resolve_user(self, user_id: int) -> Any:
        """Load the acting user, or ``None`` when no factory is wired.

        Only the AI job types need one — they call into services that take a
        ``User`` for permission checks. A deployment without a user factory
        still drains SCAN / METADATA / THUMBNAIL / REINDEX.
        """
        if self._user_factory is None:
            return None
        try:
            return self._user_factory(user_id)
        except Exception:  # noqa: BLE001 — a bad lookup must not kill the loop
            logger.exception("AssetJobRunner: could not load user %s", user_id)
            return None

    async def _emit_terminal_event(
        self,
        job_id: str,
        family_id: str,
        job_type: str,
    ) -> None:
        """Tell the family's dashboards how the job ended.

        The bus is optional, so a deployment without a gateway still
        drains jobs — it just does not push.
        """

        if self._event_bus_source is None:
            return
        bus = self._event_bus_source()
        if bus is None:
            return
        from homemind.infra.db.repos.asset_jobs import (  # noqa: PLC0415
            JOB_STATUS_CANCELLED,
            JOB_STATUS_COMPLETED,
            JOB_STATUS_FAILED,
        )
        from homemind.infra.family.events import (  # noqa: PLC0415
            EVENT_JOB_COMPLETED,
            EVENT_JOB_FAILED,
            EVENT_JOB_PROGRESS,
            emit_family_event,
        )

        current = self._manager._repo.get_job(job_id)  # noqa: SLF001
        if current is None:
            return
        status = current.status
        if status == JOB_STATUS_COMPLETED:
            event_type = EVENT_JOB_COMPLETED
        elif status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            event_type = EVENT_JOB_FAILED
        else:
            event_type = EVENT_JOB_PROGRESS
        await emit_family_event(
            bus,
            event_type,
            family_id,
            {
                "job_id": job_id,
                "job_type": job_type,
                "status": status,
                "total_items": current.total_items,
                "processed_items": current.processed_items,
                "failed_items": current.failed_items,
            },
        )


__all__ = ["DEFAULT_POLL_INTERVAL_SECONDS", "AssetJobRunner"]
