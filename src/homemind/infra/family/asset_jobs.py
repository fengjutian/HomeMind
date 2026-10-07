"""Persistent asset job manager (Stage 6).

Replaces the in-memory periodic scan loop with a database-backed queue so
work survives a restart, supports pause / resume / cancel / retry, and
reports per-item progress.

Design points that matter:

* **Batching.** Items are pulled in fixed-size batches and the cursor is
  persisted after every batch, so a restart resumes from the cursor
  instead of re-walking the whole source.
* **Isolation.** One bad photo marks its *item* failed and the batch
  continues. The job only fails when the failure ratio crosses
  ``max_failure_ratio``, so a single unreadable file cannot discard a
  10k-file scan.
* **Concurrency.** Vision / embedding fan-out is bounded by
  ``max_concurrency``; the heavy work is the reason this is a job queue
  rather than a request handler.
* **Memory.** Paths are never materialised in full — items are written to
  the DB in batches and read back a batch at a time, so a source with
  hundreds of thousands of files stays flat in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homemind.infra.db.repos.asset_jobs import (
    ITEM_STATUS_FAILED,
    ITEM_STATUS_SKIPPED,
    ITEM_STATUS_SUCCEEDED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PAUSED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_TYPES,
    AssetJobItemRow,
    AssetJobRepo,
    AssetJobRow,
)
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.asset_job_config import (
    AssetJobConfig,
    validate_config_for_job_type,
)
from homemind.infra.family.asset_job_handlers import SkipItem
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 200
DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_FAILURE_RATIO = 0.25
DEFAULT_MAX_ITEM_ATTEMPTS = 3

# One item handler receives the item row and returns ``None`` on success
# or raises to mark the item failed. Handlers must be idempotent: a
# retry re-invokes them with the same row.
ItemHandler = Callable[[AssetJobItemRow], None]


@dataclass(frozen=True)
class AssetJobSummary:
    """Aggregate view used by the API layer and the dashboard."""

    job_id: str
    family_id: str
    job_type: str
    status: str
    total_items: int
    processed_items: int
    succeeded_items: int
    skipped_items: int
    failed_items: int
    error_summary: str | None

    def progress_percent(self) -> float:
        """Completion percentage, including the zero-item case."""
        return _progress_percent(self)

    @classmethod
    def from_row(cls, row: AssetJobRow) -> AssetJobSummary:
        return cls(
            job_id=row.id,
            family_id=row.family_id,
            job_type=row.job_type,
            status=row.status,
            total_items=row.total_items,
            processed_items=row.processed_items,
            succeeded_items=row.succeeded_items,
            skipped_items=row.skipped_items,
            failed_items=row.failed_items,
            error_summary=row.error_summary,
        )


def _progress_percent(summary: AssetJobSummary) -> float:
    """Completion as a percentage.

    A job with zero items is 100%, not 0%: it is already finished, and
    reporting 0 would render an indefinite progress bar for a scan of an
    empty directory.
    """
    if summary.total_items <= 0:
        return 100.0 if summary.status == JOB_STATUS_COMPLETED else 0.0
    return round(summary.processed_items * 100.0 / summary.total_items, 2)


class AssetJobManager:
    """Create, run, and control persistent asset jobs."""

    def __init__(
        self,
        family: FamilyManager,
        repo: AssetJobRepo,
        *,
        asset_repo: FamilyAssetRepo | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_failure_ratio: float = DEFAULT_MAX_FAILURE_RATIO,
        max_item_attempts: int = DEFAULT_MAX_ITEM_ATTEMPTS,
    ) -> None:
        self._family = family
        self._repo = repo
        # Needed to turn an ``asset_id`` into a path at creation time. Kept
        # optional so a caller that only drives SCAN (and therefore already
        # owns its path stream) does not have to wire a second repo.
        self._asset_repo = asset_repo
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.max_concurrency = max(1, max_concurrency)
        self.max_failure_ratio = max_failure_ratio
        self.max_item_attempts = max_item_attempts

    # -------------------------------------------------------------- creation

    def create_job(
        self,
        family_id: str,
        user: User,
        *,
        job_type: str,
        paths: Iterable[str] = (),
        asset_ids: Iterable[str] | dict[str, str] | None = None,
        source_id: str | None = None,
        cursor: dict[str, Any] | None = None,
        config: AssetJobConfig | None = None,
    ) -> AssetJobRow:
        """Create a job and seed its items.

        Two input shapes, because the job types genuinely differ:

        * ``SCAN`` walks a registered source, so the caller streams
          ``paths``. The stream is consumed in batches, never accumulated,
          so a 100k-file directory stays flat in memory.
        * Every other type operates on assets that are already registered,
          so the caller passes ``asset_ids`` and the manager resolves each
          one to its stored path. That resolution is what makes "no
          unregistered client path" enforceable: an ``asset_id`` from
          another family fails here rather than at execution time.

        ``config`` is the non-sensitive job description a worker reads back
        after a restart. It is validated against ``job_type`` before the job
        is written, so a VISION job missing its model is a 400 at creation
        rather than a failure hours later in a worker thread.
        """
        # Manager-only, not merely "has access": queueing work writes rows
        # and (for SCAN) indexes files, so it belongs to the same trust
        # level as pause / resume / cancel / retry.
        self._family.require_manager(family_id, user)
        if job_type not in JOB_TYPES:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"unsupported job type {job_type!r}",
            )
        resolved_config = config or AssetJobConfig()
        validate_config_for_job_type(job_type, resolved_config)

        job = self._repo.create_job(
            family_id,
            job_type=job_type,
            requested_by=user.id,
            source_id=source_id,
            cursor_json=json.dumps(cursor or {}, ensure_ascii=False, sort_keys=True),
            config_json=resolved_config.to_json(),
        )
        total = 0
        if asset_ids is not None and not isinstance(asset_ids, dict):
            resolved = self._resolve_asset_ids(family_id, asset_ids)
            total = self._seed_pairs(job.id, resolved)
        else:
            lookup = asset_ids if isinstance(asset_ids, dict) else None
            batch: list[str] = []
            for path in paths:
                batch.append(path)
                if len(batch) >= self.batch_size:
                    total += self._repo.add_items(job.id, batch, lookup)
                    batch.clear()
            if batch:
                total += self._repo.add_items(job.id, batch, lookup)
        _hm_inc("asset_job_created_total")
        refreshed = self._repo.get_job(job.id) or job
        if total == 0:
            # "Nothing to do" is a result, not a job that never finishes.
            # Completing here keeps the dashboard from showing a spinner
            # for a scan of an empty directory or a reindex of a family
            # that has no assets yet.
            refreshed = (
                self._repo.set_status(
                    job.id,
                    to_status=JOB_STATUS_COMPLETED,
                    from_status=JOB_STATUS_PENDING,
                )
                or refreshed
            )
        logger.info(
            "AssetJobManager: created %s job %s with %d items",
            job_type,
            job.id,
            total,
        )
        return refreshed

    def _resolve_asset_ids(
        self,
        family_id: str,
        asset_ids: Iterable[str],
    ) -> list[tuple[str, str]]:
        """Map each ``asset_id`` to its stored path, rejecting foreign rows.

        Returns ``(path, asset_id)`` pairs ready for :meth:`_seed_pairs`.
        A missing or cross-family id is refused here: the alternative is a
        worker that opens a path it was never authorised for.
        """
        resolved: list[tuple[str, str]] = []
        if self._asset_repo is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "asset-scoped jobs require an asset repository",
            )
        for asset_id in asset_ids:
            asset = self._asset_repo.get(asset_id)
            if asset is None or asset.family_id != family_id:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    "asset does not belong to this family",
                )
            resolved.append((asset.uri, asset.id))
        return resolved

    def _seed_pairs(self, job_id: str, pairs: list[tuple[str, str]]) -> int:
        """Insert ``(path, asset_id)`` rows in batches."""
        total = 0
        for start in range(0, len(pairs), self.batch_size):
            chunk = pairs[start : start + self.batch_size]
            lookup = dict(chunk)
            total += self._repo.add_items(job_id, [path for path, _ in chunk], lookup)
        return total

    # --------------------------------------------------------------- queries

    def list_jobs(
        self,
        family_id: str,
        user: User,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AssetJobSummary]:
        self._family.require_access(family_id, user)
        return [
            AssetJobSummary.from_row(row)
            for row in self._repo.list_jobs(family_id, status=status, limit=limit)
        ]

    def get_job(
        self,
        family_id: str,
        job_id: str,
        user: User,
    ) -> AssetJobSummary:
        self._family.require_access(family_id, user)
        return AssetJobSummary.from_row(self._assert_job(family_id, job_id))

    def list_items(
        self,
        family_id: str,
        job_id: str,
        user: User,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[AssetJobItemRow]:
        self._family.require_access(family_id, user)
        self._assert_job(family_id, job_id)
        return self._repo.list_items(job_id, status=status, limit=limit)

    # -------------------------------------------------------------- controls

    def pause_job(
        self,
        family_id: str,
        job_id: str,
        user: User,
    ) -> AssetJobSummary:
        """Stop claiming new items.

        The running batch finishes; pausing is not a hard kill, so a
        partially-applied batch is never left half-written.
        """
        self._family.require_manager(family_id, user)
        self._assert_job(family_id, job_id)
        paused = self._repo.set_status(
            job_id,
            to_status=JOB_STATUS_PAUSED,
            from_status=(JOB_STATUS_PENDING, JOB_STATUS_RUNNING),
        )
        if paused is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "job cannot be paused in its state",
            )
        return AssetJobSummary.from_row(paused)

    def resume_job(
        self,
        family_id: str,
        job_id: str,
        user: User,
    ) -> AssetJobSummary:
        self._family.require_manager(family_id, user)
        self._assert_job(family_id, job_id)
        resumed = self._repo.set_status(
            job_id,
            to_status=JOB_STATUS_PENDING,
            from_status=JOB_STATUS_PAUSED,
        )
        if resumed is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "job is not paused",
            )
        return AssetJobSummary.from_row(resumed)

    def cancel_job(
        self,
        family_id: str,
        job_id: str,
        user: User,
    ) -> AssetJobSummary:
        self._family.require_manager(family_id, user)
        self._assert_job(family_id, job_id)
        cancelled = self._repo.set_status(
            job_id,
            to_status=JOB_STATUS_CANCELLED,
            from_status=(JOB_STATUS_PENDING, JOB_STATUS_PAUSED, JOB_STATUS_RUNNING),
        )
        if cancelled is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "job is already terminal",
            )
        _hm_inc("asset_job_cancelled_total")
        return AssetJobSummary.from_row(cancelled)

    def retry_job(
        self,
        family_id: str,
        job_id: str,
        user: User,
    ) -> AssetJobSummary:
        """Reset only the failed items and requeue the job.

        Succeeded and skipped items keep their terminal state so a retry
        does not redo a 10k-file scan just because one file failed.
        """
        self._family.require_manager(family_id, user)
        job = self._assert_job(family_id, job_id)
        if job.status == JOB_STATUS_RUNNING:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "cannot retry a running job",
            )
        reset = self._repo.reset_failed_items(job_id)
        requeued = self._repo.set_status(
            job_id,
            to_status=JOB_STATUS_PENDING,
            from_status=(
                JOB_STATUS_FAILED,
                JOB_STATUS_CANCELLED,
                JOB_STATUS_COMPLETED,
                JOB_STATUS_PAUSED,
            ),
        )
        if requeued is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "job cannot be retried in its state",
            )
        _hm_inc("asset_job_retried_total")
        logger.info(
            "AssetJobManager: retry %s reset %d failed items",
            job_id,
            reset,
        )
        return AssetJobSummary.from_row(requeued)

    # ---------------------------------------------------------------- running

    def run_job(
        self,
        job_id: str,
        handler: ItemHandler,
        *,
        owner: str | None = None,
        now: int | None = None,
    ) -> AssetJobSummary:
        """Claim ``job_id`` and drain it in batches.

        Synchronous by design: the caller owns the event loop (background
        runner or a request-scoped thread), and the DB lease is what
        provides mutual exclusion, not the process boundary.
        """
        lease_owner = owner or f"worker:{job_id}"
        job = self._repo.claim_job(
            job_id,
            owner=lease_owner,
            ttl_seconds=self.lease_seconds,
            now=now,
        )
        if job is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "job is already claimed elsewhere",
            )
        return self._drain(job, lease_owner, handler, now=now)

    def _drain(
        self,
        job: AssetJobRow,
        lease_owner: str,
        handler: ItemHandler,
        *,
        now: int | None = None,
    ) -> AssetJobSummary:
        succeeded = skipped = failed = processed = 0
        errors: list[str] = []
        while True:
            current = self._repo.get_job(job.id)
            if current is None:
                break
            if current.status in {JOB_STATUS_PAUSED, JOB_STATUS_CANCELLED}:
                logger.info(
                    "AssetJobManager: job %s moved to %s mid-run",
                    job.id,
                    current.status,
                )
                return AssetJobSummary.from_row(current)
            batch = self._repo.list_pending_items(job.id, limit=self.batch_size)
            if not batch:
                break
            for item in batch:
                outcome = self._run_item(job, lease_owner, item, handler)
                processed += 1
                if outcome == ITEM_STATUS_SUCCEEDED:
                    succeeded += 1
                elif outcome == ITEM_STATUS_SKIPPED:
                    skipped += 1
                else:
                    failed += 1
                    if len(errors) < 20 and item.error:
                        errors.append(f"{item.source_path}: {item.error}")
            self._repo.save_progress(
                job.id,
                owner=lease_owner,
                processed_delta=processed,
                succeeded_delta=succeeded,
                skipped_delta=skipped,
                failed_delta=failed,
                error_summary="; ".join(errors) if errors else None,
                now=now,
            )
            processed = succeeded = skipped = failed = 0
            self._maybe_fail(job.id, lease_owner, now=now)

        final = self._repo.get_job(job.id)
        if final is None:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "job vanished mid-run")
        if final.status == JOB_STATUS_RUNNING:
            finished = self._repo.set_status(
                job.id,
                to_status=JOB_STATUS_COMPLETED,
                from_status=JOB_STATUS_RUNNING,
            )
            final = finished or final
        _hm_inc("asset_job_completed_total")
        return AssetJobSummary.from_row(final)

    def _run_item(
        self,
        job: AssetJobRow,
        lease_owner: str,
        item: AssetJobItemRow,
        handler: ItemHandler,
    ) -> str:
        if item.attempt_count >= self.max_item_attempts:
            # Poison item: stop burning attempts and record the give-up.
            self._repo.mark_item(
                item.id,
                status=ITEM_STATUS_FAILED,
                error=f"exceeded {self.max_item_attempts} attempts",
                increment_attempt=False,
            )
            return ITEM_STATUS_FAILED
        try:
            handler(item)
        except SkipItem as exc:
            # "Nothing to do" is a terminal success, not a failure: a video
            # in a photo library has no thumbnail, and recording that as an
            # error would make every mixed library look broken.
            self._repo.mark_item(
                item.id,
                status=ITEM_STATUS_SKIPPED,
                error=str(exc)[:500],
            )
            return ITEM_STATUS_SKIPPED
        except Exception as exc:  # noqa: BLE001 — isolate one bad item
            logger.warning(
                "AssetJobManager: item %s failed: %s",
                item.id,
                exc,
            )
            self._repo.mark_item(item.id, status=ITEM_STATUS_FAILED, error=str(exc)[:500])
            return ITEM_STATUS_FAILED
        self._repo.mark_item(item.id, status=ITEM_STATUS_SUCCEEDED, error=None)
        return ITEM_STATUS_SUCCEEDED

    def _maybe_fail(self, job_id: str, lease_owner: str, *, now: int | None = None) -> None:
        """Fail the job once the failure ratio crosses the threshold."""
        current = self._repo.get_job(job_id)
        if current is None or current.total_items <= 0:
            return
        ratio = current.failed_items / current.total_items
        if ratio < self.max_failure_ratio:
            return
        self._repo.set_status(
            job_id,
            to_status=JOB_STATUS_FAILED,
            from_status=JOB_STATUS_RUNNING,
            error_summary=(f"failure ratio {ratio:.0%} exceeded {self.max_failure_ratio:.0%}"),
            now=now,
        )
        _hm_inc("asset_job_failed_total")

    async def run_until_empty(
        self,
        family_id: str | None,
        handler: ItemHandler,
        *,
        owner: str,
        lease_seconds: int | None = None,
        max_jobs: int = 25,
        now: int | None = None,
    ) -> list[AssetJobSummary]:
        """Worker loop: claim and drain runnable jobs until none remain.

        This is what makes a restart a resume — a job left ``RUNNING``
        with a lapsed lease becomes claimable again, and its pending
        items are still in the table.
        """
        results: list[AssetJobSummary] = []
        for _ in range(max_jobs):
            job = self._repo.claim_next_job(
                family_id,
                owner=owner,
                ttl_seconds=lease_seconds or self.lease_seconds,
                now=now,
            )
            if job is None:
                break
            summary = self._drain(job, owner, handler, now=now)
            results.append(summary)
            await asyncio.sleep(0)
        return results

    # -------------------------------------------------------------- recovery

    def recover_stale_jobs(self, *, now: int | None = None) -> list[str]:
        """Requeue jobs whose worker died mid-run.

        Returns the requeued job ids. Idempotent: a job that was already
        recovered no longer matches the stale predicate.
        """
        timestamp = now_ts_int(now)
        recovered: list[str] = []
        with self._repo._db.connect() as conn:  # noqa: SLF001 — same-module access
            rows = conn.execute(
                "SELECT job_id FROM homemind_asset_jobs WHERE status = 'RUNNING' "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (timestamp,),
            ).fetchall()
        for row in rows:
            job_id = str(row["job_id"])
            reset = self._repo.set_status(
                job_id,
                to_status=JOB_STATUS_PENDING,
                from_status=JOB_STATUS_RUNNING,
                clear_lease=True,
            )
            if reset is not None:
                recovered.append(job_id)
        if recovered:
            _hm_inc("asset_job_recovered_total", len(recovered))
            logger.info("AssetJobManager: recovered %d stale jobs", len(recovered))
        return recovered

    # --------------------------------------------------------------- helpers

    def _assert_job(self, family_id: str, job_id: str) -> AssetJobRow:
        job = self._repo.get_job(job_id)
        if job is None or job.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "asset job not found",
            )
        return job


def now_ts_int(now: int | None) -> int:
    return int(time.time()) if now is None else now


def run_bounded(
    items: list[Any],
    worker: Callable[[Any], None],
    *,
    max_concurrency: int,
) -> None:
    """Run ``worker`` over ``items`` with a bounded concurrency budget.

    Vision / embedding calls are network-bound, so a semaphore keeps the
    in-flight count predictable instead of fanning out over the whole
    batch.
    """
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _guarded(item: Any) -> None:
        async with semaphore:
            worker(item)

    async def _all() -> None:
        await asyncio.gather(*(_guarded(item) for item in items))

    with contextlib.suppress(RuntimeError):
        # No running loop: fall back to a synchronous drain so callers in
        # scripts and tests still get correct (if serial) behaviour.
        for item in items:
            worker(item)
        return
    asyncio.get_event_loop().run_until_complete(_all())


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_MAX_FAILURE_RATIO",
    "AssetJobManager",
    "AssetJobSummary",
    "ItemHandler",
    "run_bounded",
]
