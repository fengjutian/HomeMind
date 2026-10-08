"""Repo-level acceptance for the asset transfer tables.

These tests pin the SQL guarantees the domain layer leans on: idempotent
creation, monotonic progress, terminal states that cannot be reopened,
and token revocation that actually removes a credential. A regression
here shows up as a state machine that "sometimes" lets a dead transfer
serve bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_transfers import AssetTransferRepo
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User

SHA = "a" * 64


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        self.pool = SqlitePool(tmp_path / "octop.db")
        run_migrations(self.pool)
        run_homemind_migrations(self.pool)
        with self.pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)",
            )
        user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
        family_manager = FamilyManager(FamilyRepo(self.pool))
        self.family = family_manager.create_family(
            user,
            name="Happy",
            timezone="Asia/Shanghai",
            locale="zh",
        )
        self.user = user
        self.device_repo = FamilyDeviceRepo(self.pool)
        self.transfers = AssetTransferRepo(self.pool)
        self.payload = tmp_path / "video.mp4"
        self.payload.write_bytes(b"x" * 4096)
        self.asset_repo = FamilyAssetRepo(self.pool)
        self.asset = self.asset_repo.upsert_asset(
            family_id=self.family.id,
            source_id=None,
            space_id=None,
            asset_type="VIDEO",
            name="video.mp4",
            uri=self.payload.as_uri(),
            mime_type="video/mp4",
            size_bytes=4096,
            content_hash=SHA,
            captured_at=None,
            metadata_json="{}",
            created_by=1,
            visibility="FAMILY",
        )

    def device(self, name: str = "living-tv"):
        row = self.device_repo.create(
            self.family.id,
            name=name,
            device_type="tv",
            platform="android",
            capabilities=["asset.download"],
        )
        self.device_repo.issue_credential(row.id, row.family_id, f"hash-{name}")
        return row

    def close(self) -> None:
        self.pool.close()


@pytest.fixture
def env(tmp_path: Path):
    environment = _Env(tmp_path)
    yield environment
    environment.close()


def _create(env: _Env, device_id: str, *, request_key: str = "req-1", size: int = 4096):
    return env.transfers.create(
        family_id=env.family.id,
        asset_id=env.asset.id,
        device_id=device_id,
        request_key=request_key,
        size_bytes=size,
        sha256=SHA,
        source_mtime_ns=1_700_000_000_000_000_000,
        etag='"v1"',
        chunk_size=1024,
        expires_at=2_000,
        now=1000,
    )


# ------------------------------------------------------------ row mapping


def test_created_row_round_trips_every_version_field(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)

    assert row.id and row.pk > 0
    assert row.status == "PENDING"
    assert row.source_kind == "LOCAL_FILE"
    assert row.size_bytes == 4096
    assert row.sha256 == SHA
    assert row.source_mtime_ns == 1_700_000_000_000_000_000
    assert row.etag == '"v1"'
    assert row.chunk_size == 1024
    assert row.bytes_reported == 0
    assert row.expires_at == 2000
    assert row.created_at == 1000 and row.updated_at == 1000
    assert not row.is_terminal and row.is_active


# -------------------------------------------------------------- idempotency


def test_same_request_key_returns_the_same_transfer(env: _Env) -> None:
    device = env.device()
    first = _create(env, device.id)
    second = _create(env, device.id)

    assert second.id == first.id
    assert len(env.transfers.list_for_device(device.id)) == 1


def test_request_key_is_scoped_per_device(env: _Env) -> None:
    """Two devices may legitimately pick the same key -- an ULID or a
    timestamp is not globally unique in practice."""
    first_device = env.device("tv-a")
    second_device = env.device("tv-b")

    first = _create(env, first_device.id)
    second = _create(env, second_device.id)

    assert first.id != second.id
    assert first.device_id == first_device.id
    assert second.device_id == second_device.id


def test_listing_orders_newest_first_on_the_public_id(env: _Env) -> None:
    device = env.device()
    older = _create(env, device.id, request_key="a")
    newer = env.transfers.create(
        family_id=env.family.id,
        asset_id=env.asset.id,
        device_id=device.id,
        request_key="b",
        size_bytes=4096,
        sha256=SHA,
        source_mtime_ns=None,
        etag='"v1"',
        chunk_size=1024,
        expires_at=2000,
        now=1500,
    )
    rows = env.transfers.list_for_device(device.id)
    assert [row.id for row in rows] == [newer.id, older.id]
    assert env.transfers.list_for_device(device.id, status="PENDING")


# ---------------------------------------------------------------- lifecycle


def test_activate_moves_pending_to_active_once(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    activated = env.transfers.activate(row.id, 1100)
    assert activated is not None and activated.status == "ACTIVE"
    assert env.transfers.activate(row.id, 1200).status == "ACTIVE"  # idempotent


def test_progress_is_monotonic(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.activate(row.id, 1100)

    assert env.transfers.advance_progress(row.id, 2048, 1200).bytes_reported == 2048
    # A stale report (a slow worker finishing after a fast one) must not
    # walk the number backwards.
    stale = env.transfers.advance_progress(row.id, 512, 1300)
    assert stale is not None and stale.bytes_reported == 2048
    # An equal report still counts as liveness, so the idle TTL does not
    # expire a slow-but-alive download.
    same = env.transfers.advance_progress(row.id, 2048, 1400)
    assert same is not None and same.last_progress_at == 1400


def test_progress_is_refused_on_a_terminal_transfer(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.activate(row.id, 1100)
    env.transfers.complete(row.id, 1200)

    after = env.transfers.advance_progress(row.id, 4096, 1300)
    assert after is not None
    assert after.status == "COMPLETED"
    assert after.bytes_reported == 4096  # set by complete(), not by progress


def test_complete_is_idempotent_and_does_not_reopen(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.activate(row.id, 1100)

    first = env.transfers.complete(row.id, 1200)
    assert first is not None and first.status == "COMPLETED"
    assert first.completed_at == 1200

    second = env.transfers.complete(row.id, 1300)
    assert second is not None
    assert second.status == "COMPLETED"
    assert second.completed_at == 1200, "a replay must not move completed_at"


def test_fail_is_terminal(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    failed = env.transfers.fail(row.id, "DISK_FULL", "no space", 1200)
    assert failed is not None
    assert (failed.status, failed.failure_code, failed.failure_detail) == (
        "FAILED",
        "DISK_FULL",
        "no space",
    )
    replay = env.transfers.fail(row.id, "OTHER", "later", 1300)
    assert replay is not None and replay.failure_code == "DISK_FULL"


# -------------------------------------------------------------- cancellation


def test_cancel_for_asset_revokes_every_credential(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.issue_token(row.id, "hash-a", expires_at=1500, now=1000)

    cancelled = env.transfers.cancel_for_asset(env.asset.id, 1200)

    assert [item.id for item in cancelled] == [row.id]
    assert env.transfers.resolve_active_token("hash-a", now=1100) is None
    assert env.transfers.get(row.id).status == "CANCELLED"


def test_cancel_for_device_revokes_every_credential(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.issue_token(row.id, "hash-b", expires_at=1500, now=1000)

    cancelled = env.transfers.cancel_for_device(device.id, 1200)

    assert [item.id for item in cancelled] == [row.id]
    assert env.transfers.resolve_active_token("hash-b", now=1100) is None


def test_expire_before_moves_lapsed_tasks_and_drops_tokens(env: _Env) -> None:
    device = env.device()
    row = env.transfers.create(
        family_id=env.family.id,
        asset_id=env.asset.id,
        device_id=device.id,
        request_key="old",
        size_bytes=4096,
        sha256=SHA,
        source_mtime_ns=None,
        etag='"v1"',
        chunk_size=1024,
        expires_at=900,
        now=800,
    )
    env.transfers.activate(row.id, 850)
    env.transfers.issue_token(row.id, "hash-c", expires_at=1_000_000, now=850)

    expired = env.transfers.expire_before(1000)

    assert [item.id for item in expired] == [row.id]
    assert env.transfers.get(row.id).status == "EXPIRED"
    assert env.transfers.resolve_active_token("hash-c", now=1100) is None
    assert env.transfers.expire_before(2000) == [], "expired rows must not repeat"


# ------------------------------------------------------------------- tokens


def test_token_resolves_until_revoked(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.activate(row.id, 1100)
    env.transfers.issue_token(row.id, "hash-live", expires_at=1300, now=1100)

    resolved = env.transfers.resolve_active_token("hash-live", now=1200)
    assert resolved is not None and resolved.transfer_id == row.id

    env.transfers.revoke_tokens(row.id, 1250)
    assert env.transfers.resolve_active_token("hash-live", now=1255) is None


def test_expired_token_does_not_resolve(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.issue_token(row.id, "hash-old", expires_at=1200, now=1100)

    assert env.transfers.resolve_active_token("hash-old", now=1199) is not None
    assert env.transfers.resolve_active_token("hash-old", now=1201) is None
    assert env.transfers.resolve_active_token("hash-nope", now=1150) is None


def test_refresh_revokes_the_previous_credential(env: _Env) -> None:
    device = env.device()
    row = _create(env, device.id)
    env.transfers.activate(row.id, 1100)
    env.transfers.issue_token(row.id, "hash-old", expires_at=1400, now=1100)

    assert env.transfers.revoke_tokens(row.id, 1150) == 1
    env.transfers.issue_token(row.id, "hash-new", expires_at=1500, now=1150)

    assert env.transfers.resolve_active_token("hash-old", now=1200) is None
    assert env.transfers.resolve_active_token("hash-new", now=1200) is not None


# ------------------------------------------------------------------ limits


def test_active_count_ignores_terminal_and_expired_rows(env: _Env) -> None:
    device = env.device()
    live = _create(env, device.id, request_key="live")
    done = _create(env, device.id, request_key="done")
    lapsed = env.transfers.create(
        family_id=env.family.id,
        asset_id=env.asset.id,
        device_id=device.id,
        request_key="lapsed",
        size_bytes=4096,
        sha256=SHA,
        source_mtime_ns=None,
        etag='"v1"',
        chunk_size=1024,
        expires_at=1000,
        now=1000,
    )
    env.transfers.complete(done.id, 1100)

    assert {row.id for row in env.transfers.list_for_device(device.id)} == {
        live.id,
        done.id,
        lapsed.id,
    }
    # Before the lapse: 'live' and 'lapsed' both hold a claim.
    assert env.transfers.count_active_for_device(device.id, now=900) == 2
    # After it: only 'live' -- 'done' is terminal, 'lapsed' has expired.
    assert env.transfers.count_active_for_device(device.id, now=1050) == 1
