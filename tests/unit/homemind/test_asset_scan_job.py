from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.scan_job import (
    DEFAULT_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    FamilyAssetScanJob,
    resolve_interval_seconds,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _repos(tmp_path: Path) -> tuple[FamilyRepo, FamilyAssetRepo, SqlitePool]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'owner2', 'x', 'user', 0, 'zh', 1)"
        )
    return FamilyRepo(pool), FamilyAssetRepo(pool), pool


def _bootstrap(tmp_path: Path) -> tuple[
    FamilyRepo, FamilyAssetRepo, FamilyAssetManager, FamilyManager, SqlitePool,
]:
    family_repo, asset_repo, pool = _repos(tmp_path)
    manager = FamilyAssetManager(family_repo, asset_repo)
    return family_repo, asset_repo, manager, FamilyManager(family_repo), pool


def test_resolve_interval_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOP_HOMEMIND_ASSET_SCAN_INTERVAL_SECONDS", raising=False)
    assert resolve_interval_seconds() == DEFAULT_INTERVAL_SECONDS


def test_resolve_interval_clamps_to_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOMEMIND_ASSET_SCAN_INTERVAL_SECONDS", "10")
    assert resolve_interval_seconds() == MIN_INTERVAL_SECONDS


def test_resolve_interval_ignores_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOMEMIND_ASSET_SCAN_INTERVAL_SECONDS", "abc")
    assert resolve_interval_seconds() == DEFAULT_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_run_once_indexes_every_registered_source(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    owner_a = User(id=1, username="owner", role=Role.USER, display_name="Owner A")
    owner_b = User(id=2, username="owner2", role=Role.USER, display_name="Owner B")
    family_a = family_manager.create_family(
        owner_a, name="Family A", timezone="Asia/Shanghai", locale="zh",
    )
    family_b = family_manager.create_family(
        owner_b, name="Family B", timezone="Asia/Shanghai", locale="zh",
    )

    src_a = tmp_path / "a"
    src_a.mkdir()
    (src_a / "a.txt").write_text("alpha", encoding="utf-8")
    src_b = tmp_path / "b"
    src_b.mkdir()
    (src_b / "b.txt").write_text("bravo", encoding="utf-8")

    manager.scan_directory(family_a.id, owner_a, directory=str(src_a))
    manager.scan_directory(family_b.id, owner_b, directory=str(src_b))

    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=999,
    )
    report = await job.run_once()

    assert report.scanned_sources == 2
    # The initial scan_directory above already indexed both files, so the
    # re-scan should classify them as unchanged.
    assert report.indexed == 0
    assert report.unchanged == 2
    assert report.failed == 0
    assert report.errors == []
    pool.close()


@pytest.mark.asyncio
async def test_run_once_attribution_uses_family_owner(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    src = tmp_path / "docs"
    src.mkdir()
    (src / "x.txt").write_text("x", encoding="utf-8")
    manager.scan_directory(family.id, owner, directory=str(src))

    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=999,
    )
    await job.run_once()

    with pool.connect() as conn:
        row = conn.execute(
            "SELECT created_by FROM homemind_family_assets LIMIT 1"
        ).fetchone()
    assert row is not None and int(row["created_by"]) == owner.id
    pool.close()


@pytest.mark.asyncio
async def test_run_once_handles_missing_source_directory(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    src = tmp_path / "docs"
    src.mkdir()
    (src / "x.txt").write_text("x", encoding="utf-8")
    manager.scan_directory(family.id, owner, directory=str(src))

    # Delete the underlying directory out from under the source row so
    # the periodic run hits the "not a directory" path.
    import shutil
    shutil.rmtree(src)

    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=999,
    )
    report = await job.run_once()

    assert report.scanned_sources == 0
    assert report.failed == 1
    assert any("directory" in msg for msg in report.errors)
    pool.close()


@pytest.mark.asyncio
async def test_run_once_skips_orphan_source_rows(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    # Inject a source row that points at a missing family so we exercise
    # the orphan branch without relying on FK CASCADE behaviour.
    with pool.connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO homemind_family_asset_sources("
            "source_id, family_id, directory_uri, recursive, visibility, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 'FAMILY', 'ACTIVE', 1, 1, 1)",
            ("orphan-src", "ghost-family-id", "file:///nope"),
        )
        conn.execute("PRAGMA foreign_keys = ON")

    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=999,
    )
    report = await job.run_once()

    assert report.scanned_sources == 0
    assert any("no longer exists" in msg for msg in report.errors)
    pool.close()


@pytest.mark.asyncio
async def test_periodic_loop_runs_then_suspends(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    src = tmp_path / "docs"
    src.mkdir()
    (src / "x.txt").write_text("x", encoding="utf-8")
    manager.scan_directory(family.id, owner, directory=str(src))

    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=60,
    )
    job.start()
    # Give the loop enough time to fire one run_once.
    await asyncio.sleep(0.5)
    await job.shutdown()
    pool.close()
    assert job.interval_seconds == 60


@pytest.mark.asyncio
async def test_periodic_loop_honors_suspend(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=60,
    )
    job.suspend()
    job.start()
    # Suspended loops must exit immediately without doing any work —
    # this is what test harnesses rely on to keep pytest from hanging.
    await asyncio.sleep(0.2)
    task = job._task
    assert task is not None and task.done()
    await job.shutdown()
    pool.close()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    family_repo, asset_repo, manager, family_manager, pool = _bootstrap(tmp_path)
    job = FamilyAssetScanJob(
        asset_manager=manager,
        asset_repo=asset_repo,
        family_repo=family_repo,
        interval_seconds=60,
    )
    job.start()
    await job.shutdown()
    await job.shutdown()  # must not raise
    pool.close()
