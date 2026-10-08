"""Domain acceptance for asynchronous device asset transfers.

Three groups:

* the pure ``Range`` parser, which decides what a device may ask for;
* manifest / credential / state-machine rules on
  :class:`AssetTransferManager`;
* the streaming byte source, which must stay flat in memory and must
  refuse a resource whose version moved.

The parser is tested on its own because it is the one place where an
off-by-one turns into a corrupt multi-gigabyte file that no later check
would catch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_transfers import AssetTransferRepo
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.asset_transfers import (
    AssetTransferManager,
    RangeNotSatisfiable,
    compute_asset_etag,
    compute_file_sha256,
    hash_transfer_token,
    iter_file_range,
    open_verified_file,
    parse_byte_range,
    sanitize_failure_detail,
)
from homemind.infra.family.device_runtime import DeviceRuntimeManager, hash_token, mint_token
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User

SIZE = 1000


# ----------------------------------------------------------- range parsing


def test_closed_range() -> None:
    assert parse_byte_range("bytes=0-99", SIZE) is not None
    parsed = parse_byte_range("bytes=0-99", SIZE)
    assert (parsed.start, parsed.length, parsed.end) == (0, 100, 99)


def test_open_ended_range() -> None:
    parsed = parse_byte_range("bytes=100-", SIZE)
    assert (parsed.start, parsed.length, parsed.end) == (100, 900, 999)


def test_suffix_range() -> None:
    parsed = parse_byte_range("bytes=-100", SIZE)
    assert (parsed.start, parsed.length, parsed.end) == (900, 100, 999)


def test_suffix_longer_than_file_returns_the_whole_file() -> None:
    parsed = parse_byte_range("bytes=-5000", SIZE)
    assert (parsed.start, parsed.length) == (0, SIZE)


def test_single_first_byte_and_last_byte() -> None:
    first = parse_byte_range("bytes=0-0", SIZE)
    assert (first.start, first.length) == (0, 1)
    last = parse_byte_range("bytes=999-999", SIZE)
    assert (last.start, last.length, last.end) == (999, 1, 999)


def test_whole_file_range() -> None:
    parsed = parse_byte_range(f"bytes=0-{SIZE - 1}", SIZE)
    assert (parsed.start, parsed.length) == (0, SIZE)


def test_closed_range_past_eof_is_clamped_not_rejected() -> None:
    """RFC 9110 clamps an over-long closed range to the resource end."""
    parsed = parse_byte_range("bytes=900-99999", SIZE)
    assert (parsed.start, parsed.length, parsed.end) == (900, 100, 999)


def test_absent_header_means_the_whole_file() -> None:
    assert parse_byte_range(None, SIZE) is None


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "bytes=",
        "items=0-99",
        "bytes=abc-def",
        "bytes=--1",
        "bytes=-",
        "bytes=-0",
        "bytes=-abc",
        "bytes=5-1",
        "bytes=1000-",
        "bytes=1000-1001",
        "bytes=0-99,200-299",
        "bytes=0-99, 200-299",
    ],
)
def test_refused_forms(header: str) -> None:
    """Everything the first version refuses, including multi-range."""
    with pytest.raises(RangeNotSatisfiable):
        parse_byte_range(header, SIZE)


def test_empty_file_refuses_every_range() -> None:
    for header in ("bytes=0-", "bytes=0-0", "bytes=-1", "bytes=1-"):
        with pytest.raises(RangeNotSatisfiable):
            parse_byte_range(header, 0)


def test_start_equal_to_size_is_refused() -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_byte_range("bytes=1000-1000", SIZE)


def test_huge_integers_do_not_overflow_or_allocate() -> None:
    """Python ints are arbitrary precision; the risk is an allocation, so
    the resolved length is checked against the real file size."""
    parsed = parse_byte_range("bytes=0-99999999999999999999999999", SIZE)
    assert parsed is not None and parsed.length == SIZE
    # A huge *start* is refused outright rather than clamped to something.
    with pytest.raises(RangeNotSatisfiable):
        parse_byte_range("bytes=99999999999999999999-", SIZE)


def test_case_and_whitespace_tolerance() -> None:
    assert parse_byte_range("  BYTES=0-9  ", SIZE) is not None


# -------------------------------------------------------------- etag / hash


def test_etag_is_quoted_opaque_and_version_sensitive() -> None:
    base = compute_asset_etag("asset-1", 1000, 5, "a" * 64)
    assert base.startswith('"') and base.endswith('"')
    assert base == compute_asset_etag("asset-1", 1000, 5, "a" * 64)
    assert base != compute_asset_etag("asset-2", 1000, 5, "a" * 64)
    assert base != compute_asset_etag("asset-1", 1001, 5, "a" * 64)
    assert base != compute_asset_etag("asset-1", 1000, 6, "a" * 64)
    assert base != compute_asset_etag("asset-1", 1000, 5, "b" * 64)


def test_etag_never_leaks_the_local_path() -> None:
    tag = compute_asset_etag("asset-1", 1000, 5, "a" * 64)
    assert "/" not in tag and ":" not in tag


def test_sha256_of_a_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    payload = tmp_path / "blob.bin"
    payload.write_bytes(b"homemind" * 1000)
    assert compute_file_sha256(payload) == hashlib.sha256(b"homemind" * 1000).hexdigest()


def test_failure_detail_is_stripped_of_secrets(tmp_path: Path) -> None:
    detail = (
        f"GET https://example.test/a?token=abcdef0123456789abcdef0123456789 "
        f"failed for {tmp_path / 'secret' / 'file.bin'} "
        "key=ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210"
    )
    cleaned = sanitize_failure_detail(detail)
    assert cleaned is not None
    assert "abcdef0123456789" not in cleaned
    assert "ZYXWVUTSRQPONMLKJIHGFEDCBA" not in cleaned
    assert str(tmp_path) not in cleaned
    assert len(cleaned) <= 200


def test_failure_detail_of_none_is_none() -> None:
    assert sanitize_failure_detail(None) is None
    assert sanitize_failure_detail("") is None


# ----------------------------------------------------------------- env


class _Env:
    """A family, two devices, and a real file on disk."""

    def __init__(self, tmp_path: Path, *, body: bytes = b"0123456789" * 100) -> None:
        self.tmp_path = tmp_path
        self.pool = SqlitePool(tmp_path / "octop.db")
        run_migrations(self.pool)
        run_homemind_migrations(self.pool)
        with self.pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
                "(2, 'mama', 'x', 'user', 0, 'zh', 1)",
            )
        self.user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
        self.other = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
        family_manager = FamilyManager(FamilyRepo(self.pool))
        self.family = family_manager.create_family(
            self.user,
            name="Happy",
            timezone="Asia/Shanghai",
            locale="zh",
        )
        self.other_family = family_manager.create_family(
            self.other,
            name="Other",
            timezone="Asia/Shanghai",
            locale="zh",
        )
        self.device_repo = FamilyDeviceRepo(self.pool)
        self.asset_repo = FamilyAssetRepo(self.pool)
        self.transfer_repo = AssetTransferRepo(self.pool)
        self.runtime = DeviceRuntimeManager(family_manager, self.device_repo)
        self.manager = AssetTransferManager(
            device_repo=self.device_repo,
            asset_repo=self.asset_repo,
            transfer_repo=self.transfer_repo,
            device_runtime=self.runtime,
            chunk_size_bytes=16,
            max_concurrency=4,
            token_ttl_seconds=600,
            idle_ttl_seconds=86_400,
        )
        self.body = body
        self.payload = tmp_path / "video.mp4"
        self.payload.write_bytes(body)
        self.asset = self._index(self.family.id, self.payload, "FAMILY")

    def _index(self, family_id: str, path: Path, visibility: str):
        stat = path.stat()
        return self.asset_repo.upsert_asset(
            family_id=family_id,
            source_id=None,
            space_id=None,
            asset_type="VIDEO",
            name=path.name,
            uri=path.as_uri(),
            mime_type="video/mp4",
            size_bytes=stat.st_size,
            content_hash=compute_file_sha256(path),
            captured_at=None,
            metadata_json="{}",
            created_by=1,
            visibility=visibility,
        )

    def pair(self, name: str = "tv", family_id: str | None = None):
        family = FamilyManager(self.device_repo and FamilyRepo(self.pool))
        target = family_id or self.family.id
        self.runtime.family = family
        code = self.runtime.create_pairing_code(
            target,
            self.user,
            device_name=name,
            device_type="tv",
            platform="android",
            capabilities=["asset.download"],
        )
        device, token, _expires = self.runtime.complete_pairing(code.code, address=None)
        return device, token

    def close(self) -> None:
        self.pool.close()


@pytest.fixture
def env(tmp_path: Path):
    environment = _Env(tmp_path)
    yield environment
    environment.close()


def run(coro):  # noqa: ANN001, ANN201 - tiny sync bridge for async tests
    return asyncio.run(coro)


# --------------------------------------------------------- control plane


def test_create_returns_manifest_with_a_one_time_credential(env: _Env) -> None:
    device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k1"))

    assert handle.created is True
    assert handle.transfer.status == "ACTIVE"
    assert handle.transfer.size_bytes == len(env.body)
    assert handle.transfer.sha256 == compute_file_sha256(env.payload)
    assert handle.transfer.etag.startswith('"')
    assert handle.chunk_size == 16
    assert handle.max_concurrency == 4
    # The plaintext credential is never persisted.
    assert handle.token
    assert (
        env.transfer_repo.resolve_active_token(
            hash_transfer_token(handle.token),
            now=env.manager._now_ts(),
        )
        is not None
    )


def test_same_request_key_is_idempotent(env: _Env) -> None:
    _device, token = env.pair()
    first = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    second = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    assert second.created is False
    assert second.transfer.id == first.transfer.id
    # A replay rotates the credential: the first one is already stale.
    assert second.token != first.token


def test_two_devices_may_share_a_request_key(env: _Env) -> None:
    _d1, token_a = env.pair("tv-a")
    _d2, token_b = env.pair("tv-b")
    first = run(env.manager.create_transfer(token_a, asset_id=env.asset.id, request_key="k"))
    second = run(env.manager.create_transfer(token_b, asset_id=env.asset.id, request_key="k"))

    assert first.transfer.id != second.transfer.id
    assert first.transfer.device_id != second.transfer.device_id


def test_cross_family_asset_is_refused_without_leaking_existence(env: _Env) -> None:
    _device, token = env.pair()
    stranger = env.asset_repo.upsert_asset(
        family_id=env.other_family.id,
        source_id=None,
        space_id=None,
        asset_type="VIDEO",
        name="secret.mp4",
        uri=(env.tmp_path / "video.mp4").as_uri(),
        mime_type="video/mp4",
        size_bytes=len(env.body),
        content_hash=compute_file_sha256(env.payload),
        captured_at=None,
        metadata_json="{}",
        created_by=2,
        visibility="FAMILY",
    )

    with pytest.raises(HomeMindError) as caught:
        run(env.manager.create_transfer(token, asset_id=stranger.id, request_key="k"))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND

    missing = HomeMindError(HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND)
    assert caught.value.code is missing.code, "absent and foreign look identical"


def test_private_asset_is_not_device_readable(env: _Env) -> None:
    _device, token = env.pair()
    private = env._index(env.family.id, env.tmp_path / "video.mp4", "PRIVATE")

    with pytest.raises(HomeMindError) as caught:
        run(env.manager.create_transfer(token, asset_id=private.id, request_key="k"))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_FORBIDDEN


def test_revoked_device_cannot_create_refresh_or_read(env: _Env) -> None:
    device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    env.device_repo.revoke_all_credentials_for_device(device.id)

    with pytest.raises(HomeMindError):
        run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k2"))
    with pytest.raises(HomeMindError):
        run(env.manager.refresh_token(token, handle.transfer.id))
    with pytest.raises(HomeMindError):
        run(env.manager.get_transfer(token, handle.transfer.id))
    with pytest.raises(HomeMindError):
        env.manager.authorize_content(handle.token)


def test_device_cannot_read_another_devices_transfer(env: _Env) -> None:
    _d1, token_a = env.pair("tv-a")
    _d2, token_b = env.pair("tv-b")
    first = run(env.manager.create_transfer(token_a, asset_id=env.asset.id, request_key="k"))

    with pytest.raises(HomeMindError) as caught:
        run(env.manager.get_transfer(token_b, first.transfer.id))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND


def test_progress_is_monotonic_and_clamped(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    total = handle.transfer.size_bytes

    assert (
        run(
            env.manager.report_progress(token, handle.transfer.id, bytes_downloaded=total // 2)
        ).bytes_reported
        == total // 2
    )
    assert (
        run(
            env.manager.report_progress(token, handle.transfer.id, bytes_downloaded=1)
        ).bytes_reported
        == total // 2
    )
    assert (
        run(
            env.manager.report_progress(token, handle.transfer.id, bytes_downloaded=total * 10)
        ).bytes_reported
        == total
    )


def test_complete_requires_the_manifest_digest(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    with pytest.raises(HomeMindError) as caught:
        run(
            env.manager.complete(
                token,
                handle.transfer.id,
                size_bytes=handle.transfer.size_bytes,
                sha256="b" * 64,
            )
        )
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_HASH_MISMATCH
    assert env.transfer_repo.get(handle.transfer.id).status == "FAILED"

    # A failed task cannot then be "completed" by a retry.
    with pytest.raises(HomeMindError):
        run(
            env.manager.complete(
                token,
                handle.transfer.id,
                size_bytes=handle.transfer.size_bytes,
                sha256=handle.transfer.sha256,
            )
        )


def test_complete_is_idempotent_and_revokes_credentials(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    now = env.manager._now_ts()

    first = run(
        env.manager.complete(
            token,
            handle.transfer.id,
            size_bytes=handle.transfer.size_bytes,
            sha256=handle.transfer.sha256,
        )
    )
    assert first.status == "COMPLETED"
    assert (
        env.transfer_repo.resolve_active_token(
            hash_transfer_token(handle.token),
            now=now,
        )
        is None
    )

    second = run(
        env.manager.complete(
            token,
            handle.transfer.id,
            size_bytes=handle.transfer.size_bytes,
            sha256=handle.transfer.sha256,
        )
    )
    assert second.status == "COMPLETED", "a replay must not change the outcome"


def test_terminal_operations_are_idempotent(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    failed = run(
        env.manager.fail(
            token,
            handle.transfer.id,
            code="DISK_FULL",
            detail="no space",
        )
    )
    assert failed.status == "FAILED"

    again = run(
        env.manager.fail(token, handle.transfer.id, code="OTHER", detail="later"),
    )
    assert again.status == "FAILED"
    assert again.failure_code == "DISK_FULL"
    assert env.manager.cancel(env.family.id, handle.transfer.id).status == "FAILED"


def test_refresh_rotates_and_old_credential_stops_working(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    refreshed = run(env.manager.refresh_token(token, handle.transfer.id))

    assert refreshed.token != handle.token
    with pytest.raises(HomeMindError):
        env.manager.authorize_content(handle.token)
    assert env.manager.authorize_content(refreshed.token).transfer.id == handle.transfer.id


def test_refresh_is_refused_for_a_terminal_transfer(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    run(env.manager.fail(token, handle.transfer.id, code="X", detail=None))

    with pytest.raises(HomeMindError) as caught:
        run(env.manager.refresh_token(token, handle.transfer.id))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_TERMINAL


def test_changed_asset_refuses_resume(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    # Rewrite the file: same asset row, different bytes.
    env.payload.write_bytes(b"different" * 200)
    stat = env.payload.stat()
    env.asset_repo.upsert_asset(
        family_id=env.family.id,
        source_id=None,
        space_id=None,
        asset_type="VIDEO",
        name=env.payload.name,
        uri=env.payload.as_uri(),
        mime_type="video/mp4",
        size_bytes=stat.st_size,
        content_hash=compute_file_sha256(env.payload),
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )

    with pytest.raises(HomeMindError) as caught:
        run(env.manager.refresh_token(token, handle.transfer.id))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED
    assert env.transfer_repo.get(handle.transfer.id).status == "FAILED"


def test_create_rate_limit_returns_limit_exceeded(env: _Env) -> None:
    _device, token = env.pair()
    env.manager.create_rate_limit = 2
    for index in range(2):
        run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key=f"k{index}"))
    with pytest.raises(HomeMindError) as caught:
        run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k3"))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED


def test_active_transfer_cap_is_enforced(env: _Env) -> None:
    _device, token = env.pair()
    env.manager.max_active_per_device = 2
    for index in range(2):
        run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key=f"k{index}"))
    with pytest.raises(HomeMindError) as caught:
        run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k3"))
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED


def test_expiry_sweep_expires_and_revokes(env: _Env) -> None:
    _device, token = env.pair()
    env.manager.idle_ttl_seconds = 0
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    expired = env.manager.expire_stale()

    assert [row.id for row in expired] == [handle.transfer.id]
    assert env.transfer_repo.get(handle.transfer.id).status == "EXPIRED"
    with pytest.raises(HomeMindError):
        env.manager.authorize_content(handle.token)


def test_asset_deletion_cancels_live_transfers(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    cancelled = env.manager.cancel_for_asset(env.asset.id)

    assert [row.id for row in cancelled] == [handle.transfer.id]
    with pytest.raises(HomeMindError):
        env.manager.authorize_content(handle.token)


def test_device_removal_cancels_live_transfers(env: _Env) -> None:
    device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    cancelled = env.manager.cancel_for_device(device.id)

    assert [row.id for row in cancelled] == [handle.transfer.id]
    assert env.transfer_repo.get(handle.transfer.id).status == "CANCELLED"


# ------------------------------------------------------------ data plane


def test_authorize_content_returns_geometry(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    whole = env.manager.authorize_content(handle.token)
    assert whole.size_bytes == len(env.body)
    assert whole.range is None
    assert whole.response_length == len(env.body)
    assert whole.mime_type == "video/mp4"
    env.manager.release_content(whole)

    ranged = env.manager.authorize_content(handle.token, range_header="bytes=0-9")
    assert ranged.range is not None
    assert (ranged.range.start, ranged.range.length) == (0, 10)
    assert ranged.response_length == 10
    env.manager.release_content(ranged)


def test_if_match_mismatch_is_refused(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content(handle.token, if_match='"stale"')
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED

    good = env.manager.authorize_content(
        handle.token,
        if_match=handle.transfer.etag,
        range_header="bytes=0-9",
    )
    env.manager.release_content(good)


def test_forged_credential_is_refused(env: _Env) -> None:
    _device, token = env.pair()
    run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content("forged-token")
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_TOKEN_INVALID


def test_device_credential_cannot_read_the_data_plane(env: _Env) -> None:
    """The long-lived device token is not a substitute for a transfer token."""
    _device, device_token = env.pair()
    run(env.manager.create_transfer(device_token, asset_id=env.asset.id, request_key="k"))
    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content(device_token)
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_TOKEN_INVALID


def test_concurrent_range_requests_are_capped(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    env.manager.max_concurrent_ranges = 2

    first = env.manager.authorize_content(handle.token, range_header="bytes=0-9")
    second = env.manager.authorize_content(handle.token, range_header="bytes=10-19")
    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content(handle.token, range_header="bytes=20-29")
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED

    env.manager.release_content(first)
    # The slot is genuinely reusable once released.
    third = env.manager.authorize_content(handle.token, range_header="bytes=20-29")
    env.manager.release_content(second)
    env.manager.release_content(third)


def test_released_range_does_not_leak_the_slot(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    for _attempt in range(50):
        resolved = env.manager.authorize_content(handle.token, range_header="bytes=0-9")
        env.manager.release_content(resolved)
    # 50 sequential reads must not accumulate into a full slot table.
    assert env.manager.authorize_content(handle.token, range_header="bytes=0-9")


def test_changed_file_is_refused_mid_download(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))

    env.payload.write_bytes(b"x" * (len(env.body) + 10))

    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content(handle.token, range_header="bytes=0-9")
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED


def test_missing_file_is_refused(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    env.payload.unlink()

    with pytest.raises(HomeMindError) as caught:
        env.manager.authorize_content(handle.token)
    assert caught.value.code is HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE


def test_streaming_assembles_the_whole_file(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    resolved = env.manager.authorize_content(handle.token)

    async def drain() -> bytes:
        return b"".join([chunk async for chunk in env.manager.stream_bytes(resolved)])

    assert run(drain()) == env.body
    env.manager.release_content(resolved)


def test_streaming_honours_the_resolved_range(env: _Env) -> None:
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    resolved = env.manager.authorize_content(handle.token, range_header="bytes=10-19")

    async def drain() -> bytes:
        return b"".join([chunk async for chunk in env.manager.stream_bytes(resolved)])

    assert run(drain()) == env.body[10:20]
    env.manager.release_content(resolved)


def test_concurrent_ranges_reassemble_into_the_original_file(env: _Env) -> None:
    """Four ranges written at their own offsets must rebuild the file --
    the property the device depends on for parallel download."""
    _device, token = env.pair()
    run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    # A fresh credential per worker, exactly like a real device doing the
    # four-way download the manifest asks for.
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k2"))
    block = 256

    async def fetch(index: int) -> tuple[int, bytes]:
        start = index * block
        end = min(start + block, len(env.body)) - 1
        resolved = env.manager.authorize_content(
            handle.token,
            range_header=f"bytes={start}-{end}",
        )
        try:
            data = b"".join([chunk async for chunk in env.manager.stream_bytes(resolved)])
        finally:
            env.manager.release_content(resolved)
        return start, data

    async def run_all() -> list[tuple[int, bytes]]:
        return list(await asyncio.gather(*(fetch(i) for i in range(4))))

    assembled = bytearray(len(env.body))
    for start, data in run(run_all()):
        assembled[start : start + len(data)] = data
    assert bytes(assembled) == env.body


def test_range_iteration_is_bounded_by_the_requested_length(tmp_path: Path) -> None:
    """A file that grows under us must not inflate a range response."""
    payload = tmp_path / "grow.bin"
    payload.write_bytes(b"a" * 100)
    with payload.open("rb", buffering=0) as handle:
        assert b"".join(iter_file_range(handle, 0, 10, 4)) == b"a" * 10
        assert b"".join(iter_file_range(handle, 95, 50, 8)) == b"a" * 5
        assert b"".join(iter_file_range(handle, 100, 10, 8)) == b""


def test_client_disconnect_closes_the_file_handle(env: _Env) -> None:
    """Abandoning a multi-gigabyte read must not strand a descriptor.

    The generator's ``finally`` is what releases both the handle and the
    range-admission slot, so the test abandons the iteration mid-stream
    rather than draining it.
    """
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    resolved = env.manager.authorize_content(handle.token, range_header="bytes=0-99")

    async def abandon() -> None:
        stream = env.manager.stream_bytes(resolved)
        first = await stream.__anext__()
        assert len(first) > 0
        # Closing the async generator runs its ``finally``.
        await stream.aclose()

    run(abandon())
    env.manager.release_content(resolved)

    # The slot came back, so a later request is not locked out.
    follow_up = env.manager.authorize_content(handle.token, range_header="bytes=0-9")
    env.manager.release_content(follow_up)


def test_streaming_chunks_stay_bounded_regardless_of_file_size(tmp_path: Path) -> None:
    """Memory must not scale with the file.

    A 4 MiB asset served through a 64 KiB read block must arrive as many
    small chunks -- if a future change buffered the whole body, the first
    chunk would be the entire file and this would catch it.
    """
    payload = tmp_path / "big.bin"
    payload.write_bytes(b"z" * (4 * 1024 * 1024))
    handle = open_verified_file(payload, payload.stat().st_size)
    try:
        chunks = list(iter_file_range(handle, 0, payload.stat().st_size, 64 * 1024))
    finally:
        handle.close()
    assert len(chunks) == 64
    assert max(len(chunk) for chunk in chunks) <= 64 * 1024


def test_credential_hash_matches_the_device_scheme(env: _Env) -> None:
    """One hashing convention across both credential kinds keeps the
    'database stores only a hash' rule true for each."""
    assert hash_transfer_token("abc") == hash_token("abc")


def test_paired_device_token_is_the_only_identity_source(env: _Env) -> None:
    device, token = env.pair()
    assert device.id
    assert env.manager._authenticate(token).id == device.id
    assert mint_token() != token


# ------------------------------------------------------------------ metrics


def test_transfer_counters_are_low_cardinality_and_move(env: _Env) -> None:
    """Counters are how an operator notices a stuck download, so the
    lifecycle has to actually increment them."""
    from homemind.infra.metrics import METRICS

    before = METRICS.snapshot()
    _device, token = env.pair()
    handle = run(env.manager.create_transfer(token, asset_id=env.asset.id, request_key="k"))
    resolved = env.manager.authorize_content(handle.token, range_header="bytes=0-9")
    env.manager.release_content(resolved)
    run(
        env.manager.complete(
            token,
            handle.transfer.id,
            size_bytes=handle.transfer.size_bytes,
            sha256=handle.transfer.sha256,
        )
    )
    after = METRICS.snapshot()

    assert after["asset_transfer_created_total"] == before["asset_transfer_created_total"] + 1
    assert after["asset_transfer_completed_total"] > before["asset_transfer_completed_total"]
    # Labels stay low-cardinality: nothing resembling an id or a path is a key.
    assert all("01" not in key for key in after)
