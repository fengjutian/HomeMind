"""UploadSessionRepo — persistence model for resumable upload (migration 021)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.upload_sessions import (
    PartWriteOutcome,
    UploadPartConflict,
    UploadSessionRepo,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.uploads.protocol import (
    TERMINAL_UPLOAD_STATUSES,
    UploadPurpose,
    UploadStatus,
    part_bounds,
)
from octop.infra.utils.paths import PathLayout

CHUNK = 1024
TOTAL = 3 * CHUNK


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> UploadSessionRepo:
    return UploadSessionRepo(db)


@pytest.fixture
def owner_id(db: SqlitePool) -> int:
    return UserRepo(db).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def other_id(db: SqlitePool) -> int:
    return UserRepo(db).create(username="mallory", password_hash="h", role="user")


def _session(
    repo: UploadSessionRepo,
    owner_id: int,
    *,
    upload_id: str = "01HQ0000000000000000000001",
    total_bytes: int = TOTAL,
    status: str = UploadStatus.OPEN.value,
    expires_at: int = 2_000_000_000,
):
    return repo.create(
        upload_id=upload_id,
        owner_user_id=owner_id,
        purpose=UploadPurpose.CHAT_ATTACHMENT.value,
        filename="holiday.mp4",
        mime_type="video/mp4",
        agent_id="ag1",
        total_bytes=total_bytes,
        chunk_size=CHUNK,
        expires_at=expires_at,
    )


def _add(repo: UploadSessionRepo, upload_id: str, part_number: int, *, digest: str = "d1") -> None:
    offset, size = part_bounds(part_number, total_bytes=TOTAL, chunk_size=CHUNK)
    repo.add_part(
        upload_id=upload_id,
        part_number=part_number,
        offset=offset,
        size=size,
        sha256=digest,
    )


def test_create_persists_the_protocol_fields(repo: UploadSessionRepo, owner_id: int) -> None:
    row = _session(repo, owner_id)
    assert row.pk > 0
    assert row.upload_id == "01HQ0000000000000000000001"
    assert row.owner_user_id == owner_id
    assert row.purpose == UploadPurpose.CHAT_ATTACHMENT.value
    assert row.filename == "holiday.mp4"
    assert row.mime_type == "video/mp4"
    assert row.agent_id == "ag1"
    assert row.family_id is None
    assert row.total_bytes == TOTAL
    assert row.chunk_size == CHUNK
    assert row.received_bytes == 0
    assert row.status == UploadStatus.OPEN.value
    assert row.completed_at is None
    assert row.final_resource_id is None


def test_get_for_owner_is_scoped(repo: UploadSessionRepo, owner_id: int, other_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    assert repo.get_for_owner(uid, owner_id) is not None
    # A different user must not even see that the session exists.
    assert repo.get_for_owner(uid, other_id) is None
    assert repo.get_for_owner("01HQ0000000000000000009999", owner_id) is None


def test_delete_cascades_to_parts(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    _add(repo, uid, 1)
    assert repo.list_parts(uid)
    assert repo.delete(uid) is True
    assert repo.get(uid) is None
    assert repo.list_parts(uid) == []


def test_add_part_tracks_received_bytes(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    _add(repo, uid, 1)
    _add(repo, uid, 2)
    assert repo.get(uid).received_bytes == 2 * CHUNK  # type: ignore[union-attr]
    _add(repo, uid, 3)  # short final part
    assert repo.get(uid).received_bytes == TOTAL  # type: ignore[union-attr]
    assert [p.part_number for p in repo.list_parts(uid)] == [1, 2, 3]


def test_add_part_is_idempotent_for_an_identical_resend(
    repo: UploadSessionRepo, owner_id: int
) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    offset, size = part_bounds(1, total_bytes=TOTAL, chunk_size=CHUNK)
    first = repo.add_part(upload_id=uid, part_number=1, offset=offset, size=size, sha256="same")
    second = repo.add_part(upload_id=uid, part_number=1, offset=offset, size=size, sha256="same")
    assert first is PartWriteOutcome.CREATED
    assert second is PartWriteOutcome.IDEMPOTENT
    # A resend must not double-count.
    assert repo.get(uid).received_bytes == CHUNK  # type: ignore[union-attr]


def test_add_part_rejects_a_tampered_resend(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    offset, size = part_bounds(1, total_bytes=TOTAL, chunk_size=CHUNK)
    repo.add_part(upload_id=uid, part_number=1, offset=offset, size=size, sha256="good")
    with pytest.raises(UploadPartConflict):
        repo.add_part(upload_id=uid, part_number=1, offset=offset, size=size, sha256="bad")
    assert repo.get_part(uid, 1).sha256 == "good"  # type: ignore[union-attr]


def test_add_part_rejects_an_overlapping_range(repo: UploadSessionRepo, owner_id: int) -> None:
    """A different part number may not claim bytes another part already holds."""
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    repo.add_part(upload_id=uid, part_number=1, offset=0, size=CHUNK, sha256="a")
    with pytest.raises(UploadPartConflict):
        repo.add_part(upload_id=uid, part_number=7, offset=CHUNK - 10, size=CHUNK, sha256="b")
    assert repo.get_part(uid, 7) is None


def test_claim_for_assembly_is_exclusive(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    assert repo.claim_for_assembly(uid) is True
    assert repo.get(uid).status == UploadStatus.ASSEMBLING.value  # type: ignore[union-attr]
    # A concurrent second completion must lose the race.
    assert repo.claim_for_assembly(uid) is False


def test_release_to_open_lets_the_client_resume(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    repo.claim_for_assembly(uid)
    repo.release_to_open(uid, last_error="disk full")
    row = repo.get(uid)
    assert row.status == UploadStatus.OPEN.value  # type: ignore[union-attr]
    assert row.last_error == "disk full"  # type: ignore[union-attr]
    assert repo.claim_for_assembly(uid) is True


def test_mark_completed_records_the_final_resource(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    repo.claim_for_assembly(uid)
    row = repo.mark_completed(uid, final_resource_id="inbound/1_h.mp4")
    assert row.status == UploadStatus.COMPLETED.value  # type: ignore[union-attr]
    assert row.final_resource_id == "inbound/1_h.mp4"  # type: ignore[union-attr]
    assert row.completed_at is not None  # type: ignore[union-attr]
    assert row.last_error is None  # type: ignore[union-attr]


def test_set_status_records_last_error_and_can_clear_it(
    repo: UploadSessionRepo, owner_id: int
) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    repo.set_status(uid, status=UploadStatus.FAILED.value, last_error="hash mismatch")
    assert repo.get(uid).last_error == "hash mismatch"  # type: ignore[union-attr]
    repo.set_status(uid, status=UploadStatus.OPEN.value, clear_error=True)
    row = repo.get(uid)
    assert row.status == UploadStatus.OPEN.value  # type: ignore[union-attr]
    assert row.last_error is None  # type: ignore[union-attr]


def test_count_active_for_owner_excludes_terminal_and_expired(
    repo: UploadSessionRepo, owner_id: int
) -> None:
    _session(repo, owner_id, upload_id="u-live", expires_at=5_000)
    _session(repo, owner_id, upload_id="u-expired", expires_at=100)
    _session(repo, owner_id, upload_id="u-done", expires_at=5_000)
    repo.set_status("u-done", status=UploadStatus.COMPLETED.value)

    assert repo.count_active_for_owner(owner_id, now=1_000) == 1


def test_count_active_is_scoped_per_user(
    repo: UploadSessionRepo, owner_id: int, other_id: int
) -> None:
    _session(repo, owner_id, upload_id="u-mine", expires_at=5_000)
    assert repo.count_active_for_owner(other_id, now=1_000) == 0


def test_list_expired_skips_terminal_sessions(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id, upload_id="u-expired", expires_at=100)
    _session(repo, owner_id, upload_id="u-live", expires_at=5_000)
    _session(repo, owner_id, upload_id="u-done", expires_at=100)
    repo.set_status("u-done", status=UploadStatus.ABORTED.value)

    expired = [row.upload_id for row in repo.list_expired(now=1_000)]
    assert expired == ["u-expired"]


def test_sum_received_bytes_backs_the_staging_quota(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id, upload_id="u-a")
    _session(repo, owner_id, upload_id="u-b")
    _add(repo, "u-a", 1)
    _add(repo, "u-b", 1, digest="other")
    assert repo.sum_received_bytes() == 2 * CHUNK
    repo.set_status("u-b", status=UploadStatus.COMPLETED.value)
    active = [
        UploadStatus.OPEN.value,
        UploadStatus.ASSEMBLING.value,
        UploadStatus.FAILED.value,
    ]
    assert repo.sum_received_bytes(statuses=active) == CHUNK


def test_received_ranges_round_trips(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id)
    uid = "01HQ0000000000000000000001"
    _add(repo, uid, 2)
    _add(repo, uid, 1)
    assert repo.received_ranges(uid) == [(0, CHUNK), (CHUNK, CHUNK)]


def test_list_for_owner_filters_by_status(repo: UploadSessionRepo, owner_id: int) -> None:
    _session(repo, owner_id, upload_id="u-open")
    _session(repo, owner_id, upload_id="u-aborted")
    repo.set_status("u-aborted", status=UploadStatus.ABORTED.value)
    open_rows = repo.list_for_owner(owner_id, statuses=[UploadStatus.OPEN.value])
    assert [row.upload_id for row in open_rows] == ["u-open"]
    assert len(repo.list_for_owner(owner_id)) == 2


def test_repo_status_literals_match_the_protocol() -> None:
    """The repo stores statuses as plain literals to stay SQL-only.

    That duplicates vocabulary, so pin it: if a status is added or renamed in
    the protocol, this fails instead of the two sides silently diverging.
    """
    from octop.infra.db.repos import upload_sessions as repo_mod

    assert UploadStatus.OPEN.value == repo_mod._STATUS_OPEN
    assert UploadStatus.ASSEMBLING.value == repo_mod._STATUS_ASSEMBLING
    assert UploadStatus.COMPLETED.value == repo_mod._STATUS_COMPLETED
    assert set(repo_mod._TERMINAL_STATUSES) == {s.value for s in TERMINAL_UPLOAD_STATUSES}


def test_staging_paths_stay_inside_the_staging_root(tmp_path: Path) -> None:
    layout = PathLayout(root=tmp_path)
    uid = "01HQ0000000000000000000001"
    created = layout.ensure_upload_staging_dir(uid)
    assert created.is_dir()
    assert created.parent == layout.uploads_staging_dir
    assert layout.upload_staging_dir(uid) == created
    assert created.parent.parent == layout.uploads_dir


@pytest.mark.parametrize(
    "bad",
    ["../escape", "..", "a/b", "a\\b", "/abs", "C:\\abs", "", "x" * 65, "a b"],
)
def test_staging_paths_reject_ids_that_could_escape(tmp_path: Path, bad: str) -> None:
    layout = PathLayout(root=tmp_path)
    with pytest.raises(ValueError):
        layout.upload_staging_dir(bad)
    with pytest.raises(ValueError):
        layout.ensure_upload_staging_dir(bad)
