"""Upload cleanup sweeper and Bridge forwarding (plan phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.upload_sessions import UploadSessionRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.metrics import METRICS
from octop.infra.uploads.blobstore import UploadBlobStore
from octop.infra.uploads.protocol import UploadLimits, UploadPurpose, UploadStatus
from octop.infra.uploads.service import UploadSessionService
from octop.infra.utils.paths import PathLayout

CHUNK = 1024


@pytest.fixture
def pool(tmp_path: Path) -> SqlitePool:
    p = SqlitePool(tmp_path / "octop.db")
    run_migrations(p)
    return p


@pytest.fixture
def layout(tmp_path: Path) -> PathLayout:
    return PathLayout(root=tmp_path / "home")


@pytest.fixture
def owner_id(pool: SqlitePool) -> int:
    return UserRepo(pool).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def service(pool: SqlitePool, layout: PathLayout) -> UploadSessionService:
    return UploadSessionService(
        UploadSessionRepo(pool), layout, limits=UploadLimits(min_chunk_bytes=1024)
    )


def _open(service: UploadSessionService, owner: int, uid: str, total: int = 2 * CHUNK):
    return service.create(
        upload_id=uid,
        owner_user_id=owner,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename=f"{uid}.bin",
        total_bytes=total,
        chunk_size=CHUNK,
        expires_at=2_000_000_000,
    )


def _fill_staging(layout: PathLayout, uid: str, part: int, size: int) -> None:
    staging = layout.ensure_upload_staging_dir(uid)
    (staging / f"{part:06d}.part").write_bytes(b"x" * size)


async def test_sweep_removes_staging_of_terminal_sessions(
    service: UploadSessionService, layout: PathLayout, owner_id: int
) -> None:
    """A completed session that crashed before its rmtree leaves disk behind."""
    uid = "01HQ0000000000000000000001"
    _open(service, owner_id, uid)
    _fill_staging(layout, uid, 1, 2048)
    assert layout.upload_staging_dir(uid).exists()

    # Simulate the crash window: terminal row, staging still on disk.
    service._repo.set_status(uid, status=UploadStatus.COMPLETED.value)  # noqa: SLF001

    report = await service.sweep_orphans()
    assert report["dirs"] == 1
    assert report["bytes"] == 2048
    assert not layout.upload_staging_dir(uid).exists()


async def test_sweep_keeps_staging_of_live_sessions(
    service: UploadSessionService, layout: PathLayout, owner_id: int
) -> None:
    uid = "01HQ0000000000000000000002"
    _open(service, owner_id, uid)
    _fill_staging(layout, uid, 1, 1024)

    await service.sweep_orphans()
    assert layout.upload_staging_dir(uid).exists(), "an OPEN session must survive the sweep"


async def test_sweep_removes_orphan_dirs_with_no_row(
    service: UploadSessionService, layout: PathLayout
) -> None:
    """A crash before the row was written leaves a directory nobody can reach."""
    orphan = layout.ensure_upload_staging_dir("01HQ0000000000000000000099")
    (orphan / "000001.part").write_bytes(b"y" * 512)

    report = await service.sweep_orphans()
    assert report["dirs"] == 1
    assert not orphan.exists()


async def test_sweep_reclaims_orphaned_blobs(
    service: UploadSessionService, layout: PathLayout, owner_id: int
) -> None:
    """A promoted blob whose row vanished must not pin disk forever."""
    uid = "01HQ0000000000000000000003"
    blobs = UploadBlobStore(layout)
    blobs.blob_dir(uid).mkdir(parents=True, exist_ok=True)
    blobs.blob_path(uid).write_bytes(b"z" * 4096)
    # No session row for this upload id at all.
    assert service._repo.get(uid) is None  # noqa: SLF001

    report = await service.sweep_orphans()
    assert report["bytes"] == 4096
    assert not blobs.blob_dir(uid).exists()


async def test_sweep_keeps_blobs_for_completed_sessions(
    service: UploadSessionService, layout: PathLayout, owner_id: int
) -> None:
    uid = "01HQ0000000000000000000004"
    _open(service, owner_id, uid)
    blobs = UploadBlobStore(layout)
    blobs.blob_dir(uid).mkdir(parents=True, exist_ok=True)
    blobs.blob_path(uid).write_bytes(b"z" * 1024)
    service._repo.set_status(uid, status=UploadStatus.COMPLETED.value)  # noqa: SLF001

    await service.sweep_orphans()
    assert blobs.blob_path(uid).exists(), "a completed upload's blob is the user's file"


async def test_cleanup_expired_moves_stale_sessions_and_counts_bytes(
    service: UploadSessionService, layout: PathLayout, owner_id: int
) -> None:
    uid = "01HQ0000000000000000000005"
    service.create(
        upload_id=uid,
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="stale.bin",
        total_bytes=CHUNK,
        chunk_size=CHUNK,
        expires_at=100,
    )
    _fill_staging(layout, uid, 1, 1024)

    before_sessions = METRICS.snapshot()["upload_cleanup_sessions_total"]
    before_bytes = METRICS.snapshot()["upload_cleanup_bytes_total"]
    assert await service.cleanup_expired(now=1_000) == 1

    assert service._repo.get(uid).status == UploadStatus.EXPIRED.value  # noqa: SLF001
    assert not layout.upload_staging_dir(uid).exists()
    after = METRICS.snapshot()
    assert after["upload_cleanup_sessions_total"] == before_sessions + 1
    assert after["upload_cleanup_bytes_total"] == before_bytes + 1024


def test_metrics_snapshot_exposes_every_upload_counter() -> None:
    snap = METRICS.snapshot()
    for key in (
        "upload_sessions_active",
        "upload_sessions_total",
        "upload_bytes_received_total",
        "upload_parts_total",
        "upload_failures_total",
        "upload_checksum_failures_total",
        "upload_resumes_total",
        "upload_cleanup_bytes_total",
        "upload_cleanup_sessions_total",
    ):
        assert key in snap, key
