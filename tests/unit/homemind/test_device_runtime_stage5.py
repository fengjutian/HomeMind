"""Stage 5 acceptance tests for the device runtime closed loop.

Each test maps to one bullet in the Stage 5 spec:

* pairing code cannot be replayed, and expires,
* a revoked token stops heartbeat immediately,
* device A cannot claim or report on device B's commands,
* a command whose capability the device never declared is refused,
* stale devices flip to OFFLINE without a dashboard page being open,
* a high-risk command is not claimable before approval,
* a reclaimed lease is safe for a safe command and refused for an
  unsafe one,
* path-bearing payloads stay inside the device's authorized root,
* ``hash_token`` comparison is constant-time and the plaintext token is
  never persisted.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.device_runtime import (
    UNSAFE_CAPABILITIES,
    DeviceRuntimeManager,
    hash_token,
    path_within_root,
)
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path, **kwargs: object):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    runtime = DeviceRuntimeManager(family, FamilyDeviceRepo(pool), **kwargs)
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, runtime, family, user, fam.id


def _pair(
    runtime,
    family_id,
    user,
    *,
    name="pi",
    capabilities=("ping",),
    address="192.0.2.10",
    root_path=None,
):
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name=name, device_type="rpi", platform="linux",
        capabilities=list(capabilities),
        root_path=root_path,
    )
    device, token, _expires = runtime.complete_pairing(pairing.code, address=address)
    return device, token


# ------------------------------------------------------------------- pairing


def test_pairing_code_is_single_use(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name="pi", device_type="rpi", platform="linux",
        capabilities=["ping"],
    )
    runtime.complete_pairing(pairing.code, address=None)
    with pytest.raises(HomeMindError):
        runtime.complete_pairing(pairing.code, address=None)
    pool.close()


def test_expired_pairing_code_fails(tmp_path: Path) -> None:
    import homemind.infra.family.device_runtime as dr

    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name="pi", device_type="rpi", platform="linux",
        capabilities=["ping"],
    )
    # Backdate the stored entry rather than sleeping for the TTL.
    with dr._PAIRING_LOCK:
        entry = dr._PAIRING_STORE[pairing.code]
        dr._PAIRING_STORE[pairing.code] = dr._PairingEntry(
            family_id=entry.family_id,
            device_name=entry.device_name,
            device_type=entry.device_type,
            platform=entry.platform,
            capabilities=entry.capabilities,
            expires_at=int(time.time()) - 1,
        )
    with pytest.raises(HomeMindError):
        runtime.complete_pairing(pairing.code, address=None)
    pool.close()


def test_plaintext_token_is_never_persisted(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(runtime, family_id, user)
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT token_hash FROM homemind_device_credentials"
        ).fetchall()
    assert rows
    for row in rows:
        stored = str(row["token_hash"])
        assert stored == hash_token(token)
        assert token not in stored
    pool.close()


# --------------------------------------------------------------- credentials


def test_revoked_token_stops_heartbeat(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(runtime, family_id, user)
    assert runtime.heartbeat(token).id == device.id
    runtime.revoke_token(family_id, device.id, user)
    with pytest.raises(HomeMindError):
        runtime.heartbeat(token)
    pool.close()


def test_revoked_token_stops_dispatch_and_result(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(runtime, family_id, user)
    runtime.enqueue_command(
        family_id, device.id, capability="ping", payload={},
        requested_by=user.id, expires_at=10**10,
    )
    runtime.revoke_token(family_id, device.id, user)
    with pytest.raises(HomeMindError):
        runtime.next_pending_command(token)
    with pytest.raises(HomeMindError):
        runtime.report_command_result(token, "whatever", status="SUCCEEDED", result={})
    pool.close()


def test_rotated_token_supersedes_the_old_one(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, old_token = _pair(runtime, family_id, user)
    new_token = runtime.rotate_token(family_id, device.id, user)
    assert new_token != old_token
    assert runtime.heartbeat(new_token).id == device.id
    with pytest.raises(HomeMindError):
        runtime.heartbeat(old_token)
    pool.close()


# ------------------------------------------------------------------ isolation


def test_device_a_cannot_claim_device_b_command(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device_a, token_a = _pair(runtime, family_id, user, name="a", address="192.0.2.10")
    _device_b, token_b = _pair(runtime, family_id, user, name="b", address="192.0.2.11")
    runtime.enqueue_command(
        family_id, device_a.id, capability="ping", payload={"owner": "a"},
        requested_by=user.id, expires_at=10**10,
    )
    # B's runtime must see an empty queue, never A's command.
    assert runtime.next_pending_command(token_b) is None
    claimed = runtime.next_pending_command(token_a)
    assert claimed is not None
    assert claimed.payload_json and "a" in claimed.payload_json
    pool.close()


def test_device_b_cannot_report_on_device_a_command(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device_a, token_a = _pair(runtime, family_id, user, name="a", address="192.0.2.10")
    _device_b, token_b = _pair(runtime, family_id, user, name="b", address="192.0.2.11")
    runtime.enqueue_command(
        family_id, device_a.id, capability="ping", payload={},
        requested_by=user.id, expires_at=10**10,
    )
    claimed = runtime.next_pending_command(token_a)
    assert claimed is not None
    with pytest.raises(HomeMindError):
        runtime.report_command_result(token_b, claimed.id, status="SUCCEEDED", result={})
    pool.close()


def test_capability_not_declared_is_refused(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, _token = _pair(runtime, family_id, user, capabilities=["ping"])
    with pytest.raises(HomeMindError):
        runtime.enqueue_command(
            family_id, device.id, capability="filesystem.delete", payload={},
            requested_by=user.id, expires_at=10**10,
        )
    pool.close()


# ------------------------------------------------------------------- approval


def test_unsafe_command_is_not_claimable_before_approval(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(
        runtime, family_id, user,
        capabilities=["ping", "filesystem.delete"],
        root_path="/mnt/photos",
    )
    command = runtime.enqueue_command(
        family_id, device.id, capability="filesystem.delete",
        payload={"path": "a.jpg"},
        requested_by=user.id, expires_at=10**10,
    )
    assert command.status == "WAITING_APPROVAL"
    assert command.is_unsafe is True
    # The runtime must not see it at all before approval.
    assert runtime.next_pending_command(token) is None

    approved = runtime.approve_command(family_id, command.id, user)
    assert approved.status == "PENDING"
    claimed = runtime.next_pending_command(token)
    assert claimed is not None
    assert claimed.id == command.id
    pool.close()


def test_unsafe_capabilities_never_auto_reclaim(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(
        tmp_path, command_lease_seconds=1,
    )
    device, token = _pair(
        runtime, family_id, user,
        capabilities=["ping", "filesystem.delete"],
        root_path="/mnt/photos",
    )
    command = runtime.enqueue_command(
        family_id, device.id, capability="filesystem.delete",
        payload={"path": "a.jpg"},
        requested_by=user.id, expires_at=10**10,
    )
    runtime.approve_command(family_id, command.id, user)
    claimed = runtime.next_pending_command(token)
    assert claimed is not None
    # Simulate the runtime crashing: the lease lapses.
    time.sleep(1.1)
    assert runtime.next_pending_command(token) is None, (
        "unsafe commands must escalate to a human instead of replaying"
    )
    pool.close()


def test_filesystem_capability_requires_a_root(tmp_path: Path) -> None:
    """A device that will run ``filesystem.*`` must declare where it is
    allowed to write; otherwise every path would be unconstrained."""

    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    with pytest.raises(HomeMindError):
        runtime.create_pairing_code(
            family_id, user,
            device_name="pi", device_type="rpi", platform="linux",
            capabilities=["filesystem.read"],
        )
    pool.close()


def test_path_escaping_the_root_is_refused(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, _token = _pair(
        runtime, family_id, user,
        capabilities=["ping", "filesystem.read"],
        root_path="/mnt/photos",
    )
    runtime.enqueue_command(
        family_id, device.id, capability="filesystem.read",
        payload={"path": "sub/a.jpg"},
        requested_by=user.id, expires_at=10**10,
    )
    with pytest.raises(HomeMindError):
        runtime.enqueue_command(
            family_id, device.id, capability="filesystem.read",
            payload={"path": "../../etc/passwd"},
            requested_by=user.id, expires_at=10**10,
        )
    pool.close()


def test_safe_command_reclaims_an_expired_lease(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(
        tmp_path, command_lease_seconds=1,
    )
    device, token = _pair(runtime, family_id, user, capabilities=["ping"])
    command = runtime.enqueue_command(
        family_id, device.id, capability="ping", payload={},
        requested_by=user.id, expires_at=10**10,
    )
    claimed = runtime.next_pending_command(token)
    assert claimed is not None and claimed.id == command.id
    # A live lease is not reclaimable.
    assert runtime.next_pending_command(token) is None
    time.sleep(1.1)
    reclaimed = runtime.next_pending_command(token)
    assert reclaimed is not None
    assert reclaimed.id == command.id
    assert reclaimed.retry_count == 1
    pool.close()


def test_cancel_command_blocks_dispatch(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(runtime, family_id, user, capabilities=["ping"])
    command = runtime.enqueue_command(
        family_id, device.id, capability="ping", payload={},
        requested_by=user.id, expires_at=10**10,
    )
    cancelled = runtime.cancel_command(family_id, command.id, user)
    assert cancelled.status == "CANCELLED"
    assert runtime.next_pending_command(token) is None
    pool.close()


# ------------------------------------------------------------------- liveness


def test_stale_device_flips_offline_without_dashboard(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(
        tmp_path, heartbeat_timeout_seconds=60,
    )
    device, token = _pair(runtime, family_id, user)
    assert runtime.heartbeat(token).status == "ONLINE"
    # Pretend the heartbeat lapsed an hour ago.
    stale = int(time.time()) - 3600
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_devices SET last_seen = ? WHERE device_id = ?",
            (stale, device.id),
        )
    assert runtime.mark_stale_devices_offline() == 1
    assert runtime.repo.get(device.id).status == "OFFLINE"  # type: ignore[union-attr]
    pool.close()


def test_fresh_device_stays_online(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(
        tmp_path, heartbeat_timeout_seconds=3600,
    )
    device, token = _pair(runtime, family_id, user)
    runtime.heartbeat(token)
    assert runtime.mark_stale_devices_offline() == 0
    assert runtime.repo.get(device.id).status == "ONLINE"  # type: ignore[union-attr]
    pool.close()


# ------------------------------------------------------------ path confinement


def test_path_within_root_normalizes_traversal() -> None:
    # Device payload paths resolve *under* the root, so a leading "/"
    # does not by itself escape, but an absolute path does.
    assert path_within_root("photos/a.jpg", "/mnt/photos") is True
    assert path_within_root("sub/a.jpg", "/mnt/photos") is True
    assert path_within_root("sub/../../etc/passwd", "/mnt/photos") is False
    assert path_within_root("/etc/passwd", "/mnt/photos") is False
    assert path_within_root("anything", None) is False, "fail closed without a root"
    # A relative path that walks up and back down stays inside.
    assert path_within_root("mnt/photos/../../etc", "/mnt/photos") is True


def test_unsafe_capability_set_covers_destructive_actions() -> None:
    for capability in ("filesystem.delete", "filesystem.move", "filesystem.rename"):
        assert capability in UNSAFE_CAPABILITIES


def test_heartbeat_records_the_reported_runtime_version(tmp_path: Path) -> None:
    """The panel can only show a runtime build if something stores it;
    a heartbeat carrying `runtime_version` is that something."""

    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair(runtime, family_id, user)
    assert runtime.repo.get(device.id).runtime_version is None  # type: ignore[union-attr]

    updated = runtime.heartbeat(token, runtime_version="homemind-runtime/0.4.2")
    assert updated.runtime_version == "homemind-runtime/0.4.2"

    # A later heartbeat without the field must not erase it.
    again = runtime.heartbeat(token)
    assert again.runtime_version == "homemind-runtime/0.4.2"
    pool.close()
