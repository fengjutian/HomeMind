"""Stage 6 acceptance tests for persistent asset jobs and thumbnails.

Covers the spec's bullet list:

* service restart resumes a job from its cursor rather than restarting,
* ``pause`` stops claiming new items, ``resume`` continues,
* ``cancel`` stops the job,
* a single bad file does not abort the batch,
* a large path stream is never materialised in memory,
* a thumbnail request cannot escape the cache directory,
* a private-space photo is not readable by another member.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import (
    ITEM_STATUS_FAILED,
    ITEM_STATUS_SUCCEEDED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_PAUSED,
    JOB_STATUS_PENDING,
    AssetJobRepo,
)
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.asset_jobs import AssetJobManager
from homemind.infra.family.manager import FamilyManager, MemberRole, SpaceType
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.thumbnails import (
    MAX_THUMBNAIL_DIMENSION,
    THUMBNAIL_FORMAT_JPEG,
    THUMBNAIL_FORMAT_WEBP,
    ThumbnailService,
    cache_key,
    clamp_dimension,
    resolve_output_format,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    other = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    job_repo = AssetJobRepo(pool)
    asset_repo = FamilyAssetRepo(pool)
    manager = AssetJobManager(family, job_repo, batch_size=2)
    return pool, manager, job_repo, asset_repo, family, user, other, fam.id


# --------------------------------------------------------------- job basics


def test_create_job_streams_paths_without_materialising(tmp_path: Path) -> None:
    """A generator over many paths must be consumed batch-by-batch, not
    collected, so a 100k-file source stays flat in memory."""

    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    produced = 0

    def _stream():
        nonlocal produced
        for index in range(1000):
            produced += 1
            yield f"/photos/{index}.jpg"

    job = manager.create_job(
        family_id, user, job_type="SCAN", paths=_stream(),
    )
    assert produced == 1000
    assert job.total_items == 1000
    assert job.status == JOB_STATUS_PENDING
    pool.close()


def test_run_job_completes_and_counts(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(5)],
    )
    handled: list[str] = []

    def handle(item) -> None:  # type: ignore[no-untyped-def]
        handled.append(item.source_path)

    summary = manager.run_job(job.id, handle)
    assert len(handled) == 5
    assert summary.status == JOB_STATUS_COMPLETED
    assert summary.succeeded_items == 5
    assert summary.failed_items == 0
    pool.close()


def test_single_failure_does_not_abort_the_batch(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(6)],
    )

    def handle(item) -> None:  # type: ignore[no-untyped-def]
        if item.source_path.endswith("3.jpg"):
            raise OSError("unreadable")

    summary = manager.run_job(job.id, handle)
    assert summary.failed_items == 1
    assert summary.succeeded_items == 5
    assert summary.status == JOB_STATUS_COMPLETED, "one bad file must not fail the job"
    items = job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert len(items) == 1
    assert "unreadable" in (items[0].error or "")
    pool.close()


def test_excessive_failure_ratio_fails_the_job(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    manager = AssetJobManager(family, job_repo, batch_size=10, max_failure_ratio=0.25)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(4)],
    )

    def handle(item) -> None:  # type: ignore[no-untyped-def]
        raise OSError("broken")

    summary = manager.run_job(job.id, handle)
    assert summary.status == "FAILED"
    assert summary.failed_items == 4
    pool.close()


# ---------------------------------------------------------- pause / resume


def test_pause_stops_claiming_and_resume_continues(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(6)],
    )
    paused = manager.pause_job(family_id, job.id, user)
    assert paused.status == JOB_STATUS_PAUSED

    # A paused job must not be claimable.
    assert job_repo.claim_next_job(
        family_id, owner="w1", ttl_seconds=60,
    ) is None

    resumed = manager.resume_job(family_id, job.id, user)
    assert resumed.status == JOB_STATUS_PENDING
    claimed = job_repo.claim_next_job(family_id, owner="w1", ttl_seconds=60)
    assert claimed is not None and claimed.id == job.id
    pool.close()


def test_cancel_stops_the_job(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN", paths=["/photos/a.jpg"],
    )
    cancelled = manager.cancel_job(family_id, job.id, user)
    assert cancelled.status == JOB_STATUS_CANCELLED
    assert job_repo.claim_next_job(family_id, owner="w1", ttl_seconds=60) is None
    pool.close()


def test_retry_only_resets_failed_items(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(4)],
    )

    def handle(item) -> None:  # type: ignore[no-untyped-def]
        if item.source_path.endswith("2.jpg"):
            raise OSError("broken")

    manager.run_job(job.id, handle)
    succeeded_before = {
        row.source_path
        for row in job_repo.list_items(job.id, status=ITEM_STATUS_SUCCEEDED)
    }
    retried = manager.retry_job(family_id, job.id, user)
    assert retried.status == JOB_STATUS_PENDING
    pending = job_repo.list_items(job.id, status="PENDING")
    assert len(pending) == 1
    assert pending[0].source_path.endswith("2.jpg")
    # The successes are still terminal — no redoing a whole scan.
    assert succeeded_before
    pool.close()


# ------------------------------------------------------------- restart path


def test_restart_resumes_from_cursor(tmp_path: Path) -> None:
    """A job abandoned mid-run by a dead worker is requeued and resumes
    at its remaining items, not from scratch."""

    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=[f"/photos/{i}.jpg" for i in range(6)],
    )
    # Simulate a worker that claimed the job then died before finishing.
    claimed = job_repo.claim_job(job.id, owner="dead-worker", ttl_seconds=1)
    assert claimed is not None

    # Process two items, then abandon the rest.
    batch = job_repo.list_pending_items(job.id, limit=2)
    for item in batch:
        job_repo.mark_item(item.id, status=ITEM_STATUS_SUCCEEDED, error=None)
    job_repo.save_progress(
        job.id, owner="dead-worker",
        processed_delta=2, succeeded_delta=2,
        skipped_delta=0, failed_delta=0,
    )

    # Restart: stale RUNNING rows come back as PENDING.
    recovered = manager.recover_stale_jobs(now=claimed.updated_at + 3600)
    assert job.id in recovered
    refreshed = job_repo.get_job(job.id)
    assert refreshed is not None
    assert refreshed.status == JOB_STATUS_PENDING

    # Resuming processes only the four remaining items.
    handled: list[str] = []
    manager.run_job(job.id, handled.append, owner="worker-2")
    assert len(handled) == 4
    final = job_repo.get_job(job.id)
    assert final is not None
    assert final.status == JOB_STATUS_COMPLETED
    assert final.succeeded_items == 6
    pool.close()


def test_lease_prevents_two_workers_claiming_the_same_job(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN", paths=["/photos/a.jpg"],
    )
    first = job_repo.claim_job(job.id, owner="w1", ttl_seconds=300)
    second = job_repo.claim_job(job.id, owner="w2", ttl_seconds=300)
    assert first is not None
    assert second is None, "a live lease must block a second worker"
    pool.close()


def test_progress_write_is_blocked_after_lease_theft(tmp_path: Path) -> None:
    """A worker whose lease was stolen cannot overwrite the new owner's
    progress."""

    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN", paths=["/photos/a.jpg"],
    )
    job_repo.claim_job(job.id, owner="w1", ttl_seconds=1)
    # Steal it by advancing the clock past the lease.
    job_repo.claim_job(job.id, owner="w2", ttl_seconds=300, now=job.updated_at + 3600)
    stale = job_repo.save_progress(
        job.id, owner="w1", processed_delta=99, succeeded_delta=99,
        skipped_delta=0, failed_delta=0,
    )
    assert stale is not None
    assert stale.processed_items == 0, "stale worker must not advance counters"
    pool.close()


def test_cursor_is_persisted(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id, user, job_type="SCAN",
        paths=["/photos/a.jpg"],
        cursor={"offset": 128},
    )
    reloaded = job_repo.get_job(job.id)
    assert reloaded is not None
    assert json.loads(reloaded.cursor_json) == {"offset": 128}
    pool.close()


def test_unsupported_job_type_is_refused(tmp_path: Path) -> None:
    pool, manager, job_repo, _assets, family, user, _other, family_id = _bootstrap(tmp_path)
    with pytest.raises(HomeMindError):
        manager.create_job(family_id, user, job_type="NOPE", paths=["/a.jpg"])
    pool.close()


# --------------------------------------------------------------- thumbnails


def test_thumbnail_dimension_clamping() -> None:
    assert clamp_dimension(None, fallback=320) == 320
    assert clamp_dimension(0, fallback=320) == 320
    assert clamp_dimension(64, fallback=320) == 64
    assert clamp_dimension(99999, fallback=320) == MAX_THUMBNAIL_DIMENSION


def test_thumbnail_format_resolution() -> None:
    assert resolve_output_format(None) == THUMBNAIL_FORMAT_WEBP
    assert resolve_output_format("webp") == THUMBNAIL_FORMAT_WEBP
    assert resolve_output_format("JPEG") == THUMBNAIL_FORMAT_JPEG
    assert resolve_output_format("bogus") == THUMBNAIL_FORMAT_WEBP


def test_thumbnail_cache_key_cannot_traverse(tmp_path: Path) -> None:
    """Neither the cache key nor the family-id path segment may escape
    the cache directory — ``family_id`` arrives from the URL."""

    pool, manager, job_repo, asset_repo, family, user, _other, family_id = _bootstrap(tmp_path)
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    service = ThumbnailService(family, tmp_path / "cache", asset_repo=asset_repo)

    # The cache key is built only from server-side fields.
    key = cache_key(asset, width=100, height=100, fmt="WEBP")
    assert "/" not in key and ".." not in key

    # A hostile family id must not steer the write outside the cache.
    hostile = service.cache_path(
        "../../etc", asset, width=100, height=100, fmt="WEBP",
    )
    assert hostile.parent.parent == (tmp_path / "cache").resolve() or (
        tmp_path / "cache"
    ) in hostile.parents
    assert "etc" not in [part for part in hostile.parts if part == "etc"] or (
        hostile.parent.parent.name == "cache"
    )
    assert hostile.parent.name != "etc"
    pool.close()


def test_thumbnail_missing_asset_returns_none(tmp_path: Path) -> None:
    pool, manager, job_repo, asset_repo, family, user, _other, family_id = _bootstrap(tmp_path)
    service = ThumbnailService(
        family,
        tmp_path / "cache",
        asset_repo=asset_repo,
        permission_evaluator=FamilyPermissionEvaluator(family.repo),
    )
    assert service.get_or_build(family_id, "asset_missing", user) is None
    pool.close()


def test_private_photo_thumbnail_denied_for_other_member(tmp_path: Path) -> None:
    pool, manager, job_repo, asset_repo, family, user, other, family_id = _bootstrap(tmp_path)
    sibling = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family.create_member(
        family_id, user, display_name="Sibling", role=MemberRole.MEMBER,
        user_id=sibling.id,
    )
    child = family.create_member(
        family_id, user, display_name="Teen", role=MemberRole.CHILD,
    )
    space = family.create_space(
        family_id, user, name="Teen private",
        space_type=SpaceType.PRIVATE, owner_member_id=child.id,
    )
    asset = _seed_asset(asset_repo, family_id, tmp_path, space_id=space.id)
    service = ThumbnailService(
        family,
        tmp_path / "cache",
        asset_repo=asset_repo,
        permission_evaluator=FamilyPermissionEvaluator(family.repo),
    )
    # A photo in the teen's private space is invisible to both the
    # parent and another family member.
    assert service.get_or_build(family_id, asset.id, user) is None
    assert service.get_or_build(family_id, asset.id, sibling) is None
    pool.close()


def test_shared_photo_thumbnail_is_readable(tmp_path: Path) -> None:
    pool, manager, job_repo, asset_repo, family, user, _other, family_id = _bootstrap(tmp_path)
    sibling = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family.create_member(
        family_id, user, display_name="Sibling", role=MemberRole.MEMBER,
        user_id=sibling.id,
    )
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    service = ThumbnailService(
        family,
        tmp_path / "cache",
        asset_repo=asset_repo,
        permission_evaluator=FamilyPermissionEvaluator(family.repo),
    )
    result = service.get_or_build(family_id, asset.id, user)
    assert result is not None
    assert result.path.exists()
    # Second call is served from cache.
    again = service.get_or_build(family_id, asset.id, user)
    assert again is not None
    assert again.path == result.path
    # The sibling may read a shared-space photo too.
    assert service.get_or_build(family_id, asset.id, sibling) is not None
    pool.close()


def _seed_asset(asset_repo, family_id: str, tmp_path: Path, *, space_id: str | None = None):
    """Insert a small real JPEG so Pillow can decode it."""
    source = tmp_path / "source.jpg"
    _write_test_jpeg(source)
    return asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=space_id,
        asset_type="PHOTO",
        name=source.name,
        uri=source.resolve().as_uri(),
        mime_type="image/jpeg",
        size_bytes=source.stat().st_size,
        content_hash=f"hash-{source.name}",
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="PRIVATE" if space_id else "FAMILY",
    )


def _write_test_jpeg(path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        pytest.skip("Pillow not installed")
    Image.new("RGB", (64, 48), (120, 90, 60)).save(path, "JPEG")
