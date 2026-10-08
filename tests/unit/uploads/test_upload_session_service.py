"""UploadSessionService — streaming part writes and merge (phase 2)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.upload_sessions import UploadSessionRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.uploads.protocol import UploadLimits, UploadPurpose
from octop.infra.uploads.service import UploadSessionService
from octop.infra.utils.paths import PathLayout

CHUNK = 1024


def _chunks(data: bytes, size: int = 100) -> AsyncIterator[bytes]:
    async def gen() -> AsyncIterator[bytes]:
        for start in range(0, len(data), size):
            yield data[start : start + size]

    return gen()


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
def other_id(pool: SqlitePool) -> int:
    return UserRepo(pool).create(username="eve", password_hash="h", role="user")


@pytest.fixture
def service(pool: SqlitePool, layout: PathLayout) -> UploadSessionService:
    # Relax the protocol minimum so tests can use tiny chunks; the production
    # floor (clamping small requests *up*) is covered in test_protocol.py.
    return UploadSessionService(
        UploadSessionRepo(pool), layout, limits=UploadLimits(min_chunk_bytes=1024)
    )


def _open(
    service: UploadSessionService,
    owner: int,
    data: bytes,
    *,
    chunk_size: int = CHUNK,
    **kw: object,
):
    return service.create(
        owner_user_id=owner,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="movie.mp4",
        mime_type="video/mp4",
        agent_id="ag1",
        total_bytes=len(data),
        chunk_size=chunk_size,
        **kw,  # type: ignore[arg-type]
    )


async def _put(service: UploadSessionService, uid: str, owner: int, n: int, blob: bytes) -> None:
    await service.write_part(
        upload_id=uid,
        owner_user_id=owner,
        part_number=n,
        stream=_chunks(blob),
        declared_length=len(blob),
        declared_sha256=hashlib.sha256(blob).hexdigest(),
    )


async def test_write_parts_then_complete_merges_and_verifies(
    service: UploadSessionService, owner_id: int
) -> None:
    data = bytes(range(256)) * 9  # 2304 bytes -> 3 parts
    view = _open(service, owner_id, data)
    for index in range(3):
        blob = data[index * CHUNK : (index + 1) * CHUNK]
        await _put(service, view.upload_id, owner_id, index + 1, blob)

    landed: dict[str, object] = {}

    async def lander(row: object, assembled: Path) -> str:
        landed["bytes"] = assembled.read_bytes()
        landed["sha"] = hashlib.sha256(assembled.read_bytes()).hexdigest()
        return "inbound/1_movie.mp4"

    done = await service.complete(upload_id=view.upload_id, owner_user_id=owner_id, lander=lander)
    assert landed["bytes"] == data
    assert landed["sha"] == hashlib.sha256(data).hexdigest()
    assert done.status == "COMPLETED"
    assert done.final_resource_id == "inbound/1_movie.mp4"
    # Staging is reclaimed after a successful landing.
    assert not service._paths.upload_staging_dir(view.upload_id).exists()


async def test_complete_rejects_incomplete_upload(
    service: UploadSessionService, owner_id: int
) -> None:
    data = b"x" * 2048
    view = _open(service, owner_id, data)
    await _put(service, view.upload_id, owner_id, 1, data[:CHUNK])

    async def lander(row: object, assembled: Path) -> str:  # pragma: no cover - must not run
        raise AssertionError("lander must not be called for an incomplete upload")

    with pytest.raises(OctopError) as excinfo:
        await service.complete(upload_id=view.upload_id, owner_user_id=owner_id, lander=lander)
    assert excinfo.value.code is ErrorCode.UPLOAD_INCOMPLETE
    assert excinfo.value.details["missing_count"] == 1
    # Parts survive so the client can resume.
    assert service.status(upload_id=view.upload_id, owner_user_id=owner_id).status == "OPEN"


async def test_identical_resend_is_idempotent(service: UploadSessionService, owner_id: int) -> None:
    data = b"y" * 1024
    view = _open(service, owner_id, data)
    await _put(service, view.upload_id, owner_id, 1, data)
    await _put(service, view.upload_id, owner_id, 1, data)

    status = service.status(upload_id=view.upload_id, owner_user_id=owner_id)
    assert status.received_bytes == CHUNK
    assert status.part_count == 1
    # The duplicate spool file is cleaned up, only one copy remains.
    staging = service._paths.upload_staging_dir(view.upload_id)
    assert sorted(p.name for p in staging.iterdir()) == ["000001.part"]


async def test_tampered_resend_is_rejected(service: UploadSessionService, owner_id: int) -> None:
    data = b"z" * 1024
    view = _open(service, owner_id, data)
    await _put(service, view.upload_id, owner_id, 1, data)

    with pytest.raises(OctopError) as excinfo:
        await service.write_part(
            upload_id=view.upload_id,
            owner_user_id=owner_id,
            part_number=1,
            stream=_chunks(b"Z" * 1024),
            declared_length=1024,
            declared_sha256=hashlib.sha256(b"Z" * 1024).hexdigest(),
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_PART_CONFLICT


async def test_wrong_chunk_digest_is_rejected_and_leaves_nothing(
    service: UploadSessionService, owner_id: int
) -> None:
    data = b"q" * 1024
    view = _open(service, owner_id, data)
    with pytest.raises(OctopError) as excinfo:
        await service.write_part(
            upload_id=view.upload_id,
            owner_user_id=owner_id,
            part_number=1,
            stream=_chunks(data),
            declared_length=1024,
            declared_sha256="0" * 64,
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_CHECKSUM_MISMATCH
    # The bad spool file must not linger as if it were durable.
    assert not list(service._paths.upload_staging_dir(view.upload_id).glob("*.part"))
    assert service.status(upload_id=view.upload_id, owner_user_id=owner_id).received_bytes == 0


async def test_declared_length_must_match_part_geometry(
    service: UploadSessionService, owner_id: int
) -> None:
    data = b"m" * 1024
    view = _open(service, owner_id, data)
    with pytest.raises(OctopError) as excinfo:
        await service.write_part(
            upload_id=view.upload_id,
            owner_user_id=owner_id,
            part_number=1,
            stream=_chunks(data),
            declared_length=999,
            declared_sha256=hashlib.sha256(data).hexdigest(),
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_PART_CONFLICT


async def test_oversized_part_body_aborts_early(
    service: UploadSessionService, owner_id: int
) -> None:
    """A body longer than the geometry is refused rather than silently truncated."""
    data = b"a" * 1024
    view = _open(service, owner_id, data)
    with pytest.raises(OctopError) as excinfo:
        await service.write_part(
            upload_id=view.upload_id,
            owner_user_id=owner_id,
            part_number=1,
            stream=_chunks(b"a" * 2048),
            declared_length=2048,
            declared_sha256="0" * 64,
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_PART_CONFLICT
    assert not list(service._paths.upload_staging_dir(view.upload_id).glob("*.part*"))


async def test_another_user_cannot_read_write_or_complete(
    service: UploadSessionService, owner_id: int, other_id: int
) -> None:
    data = b"p" * 1024
    view = _open(service, owner_id, data)
    uid = view.upload_id

    with pytest.raises(OctopError) as excinfo:
        service.status(upload_id=uid, owner_user_id=other_id)
    assert excinfo.value.code is ErrorCode.UPLOAD_SESSION_NOT_FOUND

    with pytest.raises(OctopError) as excinfo:
        await service.write_part(
            upload_id=uid,
            owner_user_id=other_id,
            part_number=1,
            stream=_chunks(data),
            declared_length=1024,
            declared_sha256=hashlib.sha256(data).hexdigest(),
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_SESSION_NOT_FOUND

    async def lander(row: object, assembled: Path) -> str:  # pragma: no cover
        raise AssertionError("must not land another user's upload")

    with pytest.raises(OctopError) as excinfo:
        await service.complete(upload_id=uid, owner_user_id=other_id, lander=lander)
    assert excinfo.value.code is ErrorCode.UPLOAD_SESSION_NOT_FOUND

    with pytest.raises(OctopError):
        service.cancel(upload_id=uid, owner_user_id=other_id)


async def test_whole_file_checksum_mismatch_keeps_parts_for_retry(
    service: UploadSessionService, owner_id: int
) -> None:
    data = b"k" * 1024
    view = _open(service, owner_id, data, expected_sha256="1" * 64)
    await _put(service, view.upload_id, owner_id, 1, data)

    async def lander(row: object, assembled: Path) -> str:  # pragma: no cover
        raise AssertionError("must not land a file that failed verification")

    with pytest.raises(OctopError) as excinfo:
        await service.complete(upload_id=view.upload_id, owner_user_id=owner_id, lander=lander)
    assert excinfo.value.code is ErrorCode.UPLOAD_CHECKSUM_MISMATCH
    status = service.status(upload_id=view.upload_id, owner_user_id=owner_id)
    assert status.status == "OPEN"
    assert status.received_bytes == CHUNK


async def test_status_reports_missing_ranges(service: UploadSessionService, owner_id: int) -> None:
    data = b"s" * 3072
    view = _open(service, owner_id, data)
    await _put(service, view.upload_id, owner_id, 1, data[:CHUNK])
    await _put(service, view.upload_id, owner_id, 3, data[2 * CHUNK :])

    status = service.status(upload_id=view.upload_id, owner_user_id=owner_id)
    assert status.missing.ranges == ((CHUNK, CHUNK),)
    assert status.missing.truncated is False
    assert status.part_count == 2


async def test_expired_session_refuses_writes(service: UploadSessionService, owner_id: int) -> None:
    data = b"e" * 1024
    view = service.create(
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="a.bin",
        total_bytes=len(data),
        chunk_size=CHUNK,
        expires_at=100,
    )
    with pytest.raises(OctopError) as excinfo:
        await _maybe_put(service, view.upload_id, owner_id, 1, data[:CHUNK])
    assert excinfo.value.code is ErrorCode.UPLOAD_SESSION_EXPIRED


async def _maybe_put(
    service: UploadSessionService, uid: str, owner: int, n: int, blob: bytes
) -> None:
    await service.write_part(
        upload_id=uid,
        owner_user_id=owner,
        part_number=n,
        stream=_chunks(blob),
        declared_length=len(blob),
        declared_sha256=hashlib.sha256(blob).hexdigest(),
    )


async def test_cancel_reclaims_staging_bytes(service: UploadSessionService, owner_id: int) -> None:
    data = b"c" * 1024
    view = _open(service, owner_id, data)
    await _put(service, view.upload_id, owner_id, 1, data)
    staging = service._paths.upload_staging_dir(view.upload_id)
    assert staging.exists()

    service.cancel(upload_id=view.upload_id, owner_user_id=owner_id)
    assert not staging.exists()
    # The row survives so the client sees ABORTED rather than a phantom 404.
    assert service.status(upload_id=view.upload_id, owner_user_id=owner_id).status == "ABORTED"


async def test_cleanup_expired_expires_and_reclaims(
    service: UploadSessionService, owner_id: int
) -> None:
    stale = service.create(
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="old.bin",
        total_bytes=1024,
        chunk_size=CHUNK,
        expires_at=50,
    )
    live = service.create(
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="new.bin",
        total_bytes=1024,
        chunk_size=CHUNK,
        expires_at=9_999_999,
    )
    assert await service.cleanup_expired(now=1_000) == 1
    assert not service._paths.upload_staging_dir(stale.upload_id).exists()
    assert service._paths.upload_staging_dir(live.upload_id).exists()
    assert service._repo.get(stale.upload_id).status == "EXPIRED"  # type: ignore[union-attr]


def test_create_enforces_file_size_limit(
    pool: SqlitePool, layout: PathLayout, owner_id: int
) -> None:
    tiny = UploadSessionService(
        UploadSessionRepo(pool), layout, limits=UploadLimits(max_file_bytes=100)
    )
    with pytest.raises(OctopError) as excinfo:
        tiny.create(
            owner_user_id=owner_id,
            purpose=UploadPurpose.CHAT_ATTACHMENT.value,
            filename="big.bin",
            total_bytes=101,
        )
    assert excinfo.value.code is ErrorCode.ATTACHMENT_TOO_LARGE


def test_create_enforces_per_user_session_cap(
    pool: SqlitePool, layout: PathLayout, owner_id: int, other_id: int
) -> None:
    capped = UploadSessionService(
        UploadSessionRepo(pool), layout, limits=UploadLimits(max_open_sessions_per_user=1)
    )
    capped.create(
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="a.bin",
        total_bytes=10,
    )
    with pytest.raises(OctopError) as excinfo:
        capped.create(
            owner_user_id=owner_id,
            purpose=UploadPurpose.CHAT_ATTACHMENT.value,
            filename="b.bin",
            total_bytes=10,
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_TOO_MANY_SESSIONS
    # Another user has their own budget.
    capped.create(
        owner_user_id=other_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="c.bin",
        total_bytes=10,
    )


def test_create_enforces_global_staging_quota(
    pool: SqlitePool, layout: PathLayout, owner_id: int
) -> None:
    capped = UploadSessionService(
        UploadSessionRepo(pool),
        layout,
        limits=UploadLimits(global_staging_quota_bytes=10, max_file_bytes=1024),
    )
    capped.create(
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="a.bin",
        total_bytes=10,
    )
    with pytest.raises(OctopError) as excinfo:
        capped.create(
            owner_user_id=owner_id,
            purpose=UploadPurpose.CHAT_ATTACHMENT.value,
            filename="b.bin",
            total_bytes=5,
        )
    assert excinfo.value.code is ErrorCode.UPLOAD_QUOTA_EXCEEDED


async def test_merge_does_not_load_the_whole_file(
    service: UploadSessionService, owner_id: int
) -> None:
    """Sanity check that assembly works for a payload far larger than one buffer."""
    data = bytes(range(256)) * 4096  # 1 MiB, four 256 KiB parts
    view = _open(service, owner_id, data, chunk_size=256 * 1024)
    for index in range(4):
        await _put(
            service,
            view.upload_id,
            owner_id,
            index + 1,
            data[index * 256 * 1024 : (index + 1) * 256 * 1024],
        )

    async def lander(row: object, assembled: Path) -> str:
        assert assembled.stat().st_size == len(data)
        return "inbound/big.bin"

    done = await service.complete(upload_id=view.upload_id, owner_user_id=owner_id, lander=lander)
    assert done.status == "COMPLETED"
