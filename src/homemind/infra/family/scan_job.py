"""Stage 7: periodic family asset re-scan job.

The job fans out across every asset source registered for every
family and re-runs the scanner. It does NOT call
:meth:`FamilyAssetManager.scan_source` because that requires a manager
user; instead it goes straight through ``scan_source_internal`` so the
periodic run is attributed to the family owner (not to whichever
admin happened to be online).

V0.1 trade-off: the interval is hard-coded to ``DEFAULT_INTERVAL_SECONDS``
(1 hour). A config knob is left for V0.2; for now ops can change
``OCTOP_HOMEMIND_ASSET_SCAN_INTERVAL_SECONDS`` if they need to.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homemind.infra.errors import HomeMindError

if TYPE_CHECKING:
    from homemind.infra.db.repos.family_assets import FamilyAssetRepo
    from homemind.infra.db.repos.families import FamilyRepo
    from homemind.infra.family.assets import FamilyAssetManager

logger = logging.getLogger(__name__)


DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 60
_ENV_INTERVAL_KEY = "OCTOP_HOMEMIND_ASSET_SCAN_INTERVAL_SECONDS"


def resolve_interval_seconds() -> int:
    """Resolve the re-scan interval from env or fall back to default."""
    raw = os.environ.get(_ENV_INTERVAL_KEY)
    if raw is None or not raw.strip():
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


@dataclass(frozen=True)
class AssetScanReport:
    """Summary of one periodic run, returned from :meth:`run_once`."""

    started_at: int
    finished_at: int
    scanned_sources: int = 0
    indexed: int = 0
    unchanged: int = 0
    missing: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class FamilyAssetScanJob:
    """Background job that re-scans every asset source on an interval."""

    def __init__(
        self,
        *,
        asset_manager: "FamilyAssetManager",
        asset_repo: "FamilyAssetRepo",
        family_repo: "FamilyRepo",
        interval_seconds: int | None = None,
    ) -> None:
        self._manager = asset_manager
        self._asset_repo = asset_repo
        self._family_repo = family_repo
        self._interval = interval_seconds or resolve_interval_seconds()
        self._task: asyncio.Task[None] | None = None
        self._suspended = False
        self._stopping = False

    @property
    def interval_seconds(self) -> int:
        return self._interval

    def suspend(self) -> None:
        """Stop scheduling new runs.

        Mirrors :meth:`ProactiveCareScheduler.suspend` so the test
        harness can prevent the loop from outliving the process.
        """
        self._suspended = True

    def resume(self) -> None:
        self._suspended = False

    def start(self) -> None:
        """Kick off the periodic loop.

        Idempotent — a second ``start`` while the task is already
        running is a no-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="family_asset_scan")

    async def shutdown(self) -> None:
        """Cancel the periodic loop and wait for it to drain."""
        self._stopping = True
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def run_once(self) -> AssetScanReport:
        """Run one full sweep across every source. Safe to call directly."""
        started_at = int(time.time())
        scanned_sources = 0
        indexed = 0
        unchanged = 0
        missing = 0
        failed = 0
        errors: list[str] = []
        sources = self._asset_repo.list_all_sources()
        for source in sources:
            family = self._family_repo.get_family(source.family_id)
            if family is None:
                errors.append(
                    f"{source.id}: family {source.family_id!r} no longer exists",
                )
                continue
            try:
                result = self._manager.scan_source_internal(
                    source.family_id,
                    source.id,
                    created_by_user_id=int(family.owner_user_id),
                )
            except HomeMindError as exc:
                failed += 1
                errors.append(f"{source.id}: {exc.message}")
                continue
            except (OSError, ValueError) as exc:
                failed += 1
                errors.append(f"{source.id}: {exc}")
                continue
            scanned_sources += 1
            indexed += result.indexed
            unchanged += result.unchanged
            missing += result.missing
            failed += result.failed
            errors.extend(f"{source.id}: {msg}" for msg in result.errors)
        report = AssetScanReport(
            started_at=started_at,
            finished_at=int(time.time()),
            scanned_sources=scanned_sources,
            indexed=indexed,
            unchanged=unchanged,
            missing=missing,
            failed=failed,
            errors=errors,
        )
        logger.info(
            "FamilyAssetScanJob: scanned=%d indexed=%d unchanged=%d missing=%d failed=%d",
            report.scanned_sources,
            report.indexed,
            report.unchanged,
            report.missing,
            report.failed,
        )
        return report

    async def _loop(self) -> None:
        try:
            while not self._stopping and not self._suspended:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("FamilyAssetScanJob: run_once crashed")
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass


__all__ = [
    "AssetScanReport",
    "DEFAULT_INTERVAL_SECONDS",
    "FamilyAssetScanJob",
    "MIN_INTERVAL_SECONDS",
    "resolve_interval_seconds",
]
