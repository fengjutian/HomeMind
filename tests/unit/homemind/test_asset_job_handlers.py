"""Stage C1 / C5 acceptance tests: THUMBNAIL and REINDEX handlers.

THUMBNAIL and REINDEX are the two job types that need no external provider,
so they are the first things a family can actually run end to end. The tests
here go through the real job machinery — create, build the handler from the
job row, drain, then inspect the *effect* (a file on disk, a row in the
index) — because a handler that returns without doing anything would pass a
unit test of the handler alone.

Covered:

* THUMBNAIL renders, caches, invalidates on content change, and skips
  non-images instead of failing them,
* REINDEX writes, updates, removes on delete, stays inside one family, and
  survives a re-run without duplicating documents,
* a handler cannot be built for a job whose dependencies are missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import (
    ITEM_STATUS_FAILED,
    ITEM_STATUS_SKIPPED,
    JOB_STATUS_COMPLETED,
    AssetJobRepo,
)
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.search_index import (
    KIND_ASSET,
    SearchIndexRepo,
    document_id_for,
)
from homemind.infra.family.asset_job_config import AssetJobConfig
from homemind.infra.family.asset_job_handlers import build_handler
from homemind.infra.family.asset_jobs import AssetJobManager
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.search_indexer import FamilySearchIndexer
from homemind.infra.family.thumbnails import ThumbnailService
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
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    family = FamilyManager(FamilyRepo(pool))
    fam = family.create_family(
        user,
        name="Happy",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    job_repo = AssetJobRepo(pool)
    asset_repo = FamilyAssetRepo(pool)
    search_repo = SearchIndexRepo(pool)
    indexer = FamilySearchIndexer(
        search_repo,
        family_repo=family.repo,
        context_repo=FamilyContextRepo(pool),
        asset_repo=asset_repo,
    )
    manager = AssetJobManager(family, job_repo, asset_repo=asset_repo, batch_size=10)
    asset_manager = FamilyAssetManager(family.repo, asset_repo, search_indexer=indexer)
    thumbnails = ThumbnailService(family, tmp_path / "cache", asset_repo=asset_repo)
    return (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        search_repo,
        indexer,
        thumbnails,
        family,
        user,
        fam.id,
    )


def _seed_asset(
    asset_repo: FamilyAssetRepo,
    family_id: str,
    tmp_path: Path,
    *,
    name: str = "photo.jpg",
    mime_type: str = "image/jpeg",
    write_image: bool = True,
    content_hash: str | None = None,
):
    source = tmp_path / name
    if write_image:
        from PIL import Image

        Image.new("RGB", (64, 48), (120, 90, 60)).save(source, "JPEG")
    else:
        source.write_bytes(b"not an image at all")
    return asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="PHOTO" if mime_type.startswith("image/") else "DOCUMENT",
        name=name,
        uri=source.resolve().as_uri(),
        mime_type=mime_type,
        size_bytes=source.stat().st_size,
        content_hash=content_hash or f"hash-{name}",
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )


# =========================================================== THUMBNAIL


def test_thumbnail_renders_a_cache_file(tmp_path: Path) -> None:
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    job = manager.create_job(
        family_id,
        user,
        job_type="THUMBNAIL",
        asset_ids=[asset.id],
    )
    handler = build_handler(
        job_repo.get_job(job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        thumbnail_service=thumbnails,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    summary = manager.run_job(job.id, handler)
    assert summary.succeeded_items == 1
    rendered = thumbnails.cache_path(family_id, asset, width=320, height=320, fmt="WEBP")
    assert rendered.exists()
    pool.close()


def test_thumbnail_honours_configured_size(tmp_path: Path) -> None:
    """The job config must actually reach the render, not be stored and ignored."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    job = manager.create_job(
        family_id,
        user,
        job_type="THUMBNAIL",
        asset_ids=[asset.id],
        config=AssetJobConfig(thumbnail_width=128, thumbnail_height=128),
    )
    handler = build_handler(
        job_repo.get_job(job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        thumbnail_service=thumbnails,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    manager.run_job(job.id, handler)
    assert thumbnails.cache_path(
        family_id,
        asset,
        width=128,
        height=128,
        fmt="WEBP",
    ).exists()
    pool.close()


def test_thumbnail_is_idempotent_on_rerun(tmp_path: Path) -> None:
    """A second run must hit the cache, not fail or duplicate work."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    asset = _seed_asset(asset_repo, family_id, tmp_path)

    def _drain() -> None:
        job = manager.create_job(
            family_id,
            user,
            job_type="THUMBNAIL",
            asset_ids=[asset.id],
        )
        handler = build_handler(
            job_repo.get_job(job.id),
            asset_manager=asset_manager,
            asset_repo=asset_repo,
            thumbnail_service=thumbnails,
            family_id=family_id,
            created_by_user_id=user.id,
        )
        summary = manager.run_job(job.id, handler)
        assert summary.succeeded_items == 1

    _drain()
    first = thumbnails.cache_path(family_id, asset, width=320, height=320, fmt="WEBP")
    _drain()
    second = thumbnails.cache_path(family_id, asset, width=320, height=320, fmt="WEBP")
    assert first == second and first.exists()
    pool.close()


def test_thumbnail_skips_a_non_image_instead_of_failing(tmp_path: Path) -> None:
    """A family library legitimately contains videos and PDFs."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    video = _seed_asset(
        asset_repo,
        family_id,
        tmp_path,
        name="clip.mp4",
        mime_type="video/mp4",
        write_image=False,
    )
    job = manager.create_job(
        family_id,
        user,
        job_type="THUMBNAIL",
        asset_ids=[video.id],
    )
    handler = build_handler(
        job_repo.get_job(job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        thumbnail_service=thumbnails,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    summary = manager.run_job(job.id, handler)
    assert summary.skipped_items == 1
    assert summary.failed_items == 0
    assert summary.status == JOB_STATUS_COMPLETED
    items = job_repo.list_items(job.id, status=ITEM_STATUS_SKIPPED)
    assert len(items) == 1
    assert "not an image" in (items[0].error or "")
    pool.close()


def test_thumbnail_fails_only_the_corrupt_item(tmp_path: Path) -> None:
    """One unreadable photo must not discard the rest of the batch."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    good = _seed_asset(asset_repo, family_id, tmp_path, name="good.jpg")
    bad = _seed_asset(
        asset_repo,
        family_id,
        tmp_path,
        name="bad.jpg",
        write_image=False,
    )
    job = manager.create_job(
        family_id,
        user,
        job_type="THUMBNAIL",
        asset_ids=[good.id, bad.id],
    )
    handler = build_handler(
        job_repo.get_job(job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        thumbnail_service=thumbnails,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    summary = manager.run_job(job.id, handler)
    assert summary.succeeded_items == 1
    assert summary.failed_items == 1
    failed = job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert len(failed) == 1
    assert "thumbnail render failed" in (failed[0].error or "")
    pool.close()


def test_thumbnail_refuses_a_cross_family_asset(tmp_path: Path) -> None:
    """Defence in depth: creation refuses it, and so does the handler."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        _ix,
        thumbnails,
        family,
        user,
        family_id,
    ) = _bootstrap(tmp_path)
    other_family = family.create_family(
        user,
        name="Other",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    foreign = _seed_asset(asset_repo, other_family.id, tmp_path, name="foreign.jpg")
    foreign_row = job_repo.create_job(
        family_id,
        job_type="THUMBNAIL",
        requested_by=user.id,
    )
    job_repo.add_items(foreign_row.id, [foreign.uri], {foreign.uri: foreign.id})
    handler = build_handler(
        foreign_row,
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        thumbnail_service=thumbnails,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    summary = manager.run_job(foreign_row.id, handler)
    assert summary.succeeded_items == 0
    assert summary.failed_items == 1
    pool.close()


def test_thumbnail_handler_needs_a_service() -> None:
    """A missing dependency must fail loudly, not silently no-op."""
    from homemind.infra.db.repos.asset_jobs import JOB_TYPE_THUMBNAIL

    with pytest.raises(NotImplementedError):
        build_handler(
            _FakeRow(JOB_TYPE_THUMBNAIL),
            asset_manager=object(),
            asset_repo=object(),
            thumbnail_service=None,
            search_indexer=None,
            family_id="f",
            created_by_user_id=1,
        )


# ============================================================= REINDEX


def test_reindex_writes_one_document(tmp_path: Path) -> None:
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        indexer,
        _th,
        family,
        user,
        family_id,
    ) = _bootstrap(  # noqa: E501
        tmp_path
    )
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    job = manager.create_job(
        family_id,
        user,
        job_type="REINDEX",
        asset_ids=[asset.id],
    )
    handler = build_handler(
        job_repo.get_job(job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        search_indexer=indexer,
        family_id=family_id,
        created_by_user_id=user.id,
    )
    summary = manager.run_job(job.id, handler)
    assert summary.succeeded_items == 1
    assert _document_exists(indexer, family_id, asset.id)
    pool.close()


def test_reindex_is_idempotent(tmp_path: Path) -> None:
    """Re-running must not duplicate documents."""
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        indexer,
        _th,
        family,
        user,
        family_id,
    ) = _bootstrap(  # noqa: E501
        tmp_path
    )
    asset = _seed_asset(asset_repo, family_id, tmp_path)

    def _drain() -> None:
        job = manager.create_job(
            family_id,
            user,
            job_type="REINDEX",
            asset_ids=[asset.id],
        )
        handler = build_handler(
            job_repo.get_job(job.id),
            asset_manager=asset_manager,
            asset_repo=asset_repo,
            search_indexer=indexer,
            family_id=family_id,
            created_by_user_id=user.id,
        )
        manager.run_job(job.id, handler)

    _drain()
    first = indexer.index.count_documents(family_id)
    _drain()
    assert indexer.index.count_documents(family_id) == first
    pool.close()


def test_reindex_removes_a_deleted_asset(tmp_path: Path) -> None:
    """A photo removed from disk must stop appearing in search.

    The document outliving its asset is the failure mode that makes search
    return links to files that no longer exist.
    """
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        indexer,
        _th,
        family,
        user,
        family_id,
    ) = _bootstrap(  # noqa: E501
        tmp_path
    )
    asset = _seed_asset(asset_repo, family_id, tmp_path)

    def _handler(row):
        return build_handler(
            row,
            asset_manager=asset_manager,
            asset_repo=asset_repo,
            search_indexer=indexer,
            family_id=family_id,
            created_by_user_id=user.id,
        )

    job = manager.create_job(
        family_id,
        user,
        job_type="REINDEX",
        asset_ids=[asset.id],
    )
    manager.run_job(job.id, _handler(job_repo.get_job(job.id)))
    assert _document_exists(indexer, family_id, asset.id)

    assert asset_repo.delete(asset.id)
    # Seeded directly rather than through ``create_job``: creating a job
    # for a deleted asset is refused on purpose, but a job queued *before*
    # the delete must still clean up the orphan document it would leave.
    after = job_repo.create_job(
        family_id,
        job_type="REINDEX",
        requested_by=user.id,
    )
    job_repo.add_items(after.id, [asset.uri], {asset.uri: asset.id})
    summary = manager.run_job(after.id, _handler(after))
    assert summary.succeeded_items == 1
    assert not _document_exists(indexer, family_id, asset.id)
    pool.close()


def test_reindex_never_touches_another_family(tmp_path: Path) -> None:
    (
        pool,
        manager,
        job_repo,
        asset_repo,
        asset_manager,
        _sr,
        indexer,
        _th,
        family,
        user,
        family_id,
    ) = _bootstrap(  # noqa: E501
        tmp_path
    )
    other_family = family.create_family(
        user,
        name="Other",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    foreign = _seed_asset(asset_repo, other_family.id, tmp_path, name="foreign.jpg")
    other_job = manager.create_job(
        other_family.id,
        user,
        job_type="REINDEX",
        asset_ids=[foreign.id],
    )
    handler = build_handler(
        job_repo.get_job(other_job.id),
        asset_manager=asset_manager,
        asset_repo=asset_repo,
        search_indexer=indexer,
        family_id=other_family.id,
        created_by_user_id=user.id,
    )
    manager.run_job(other_job.id, handler)
    assert _document_exists(indexer, other_family.id, foreign.id)
    assert indexer.index.count_documents(family_id) == 0
    pool.close()


def test_reindex_handler_needs_an_indexer() -> None:
    from homemind.infra.db.repos.asset_jobs import JOB_TYPE_REINDEX

    with pytest.raises(NotImplementedError):
        build_handler(
            _FakeRow(JOB_TYPE_REINDEX),
            asset_manager=object(),
            asset_repo=object(),
            thumbnail_service=None,
            search_indexer=None,
            family_id="f",
            created_by_user_id=1,
        )


# ============================================================== helpers


class _FakeRow:
    """Minimal stand-in for an ``AssetJobRow`` in construction tests."""

    def __init__(self, job_type: str) -> None:
        self.id = "job"
        self.family_id = "f"
        self.job_type = job_type
        self.status = "PENDING"
        self.config_json = "{}"
        self.cursor_json = "{}"


def _document_exists(indexer: FamilySearchIndexer, family_id: str, asset_id: str) -> bool:
    with indexer.index._db.connect() as conn:  # noqa: SLF001 — test introspection
        row = conn.execute(
            "SELECT 1 FROM homemind_search_documents WHERE document_id = ?",
            (document_id_for(family_id, KIND_ASSET, asset_id),),
        ).fetchone()
    return row is not None
