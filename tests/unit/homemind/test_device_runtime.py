"""Stage 6 tests: device pairing, credential rotation, command dispatch."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.family.device_runtime import (
    DeviceRuntimeManager,
    hash_token,
)
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path) -> tuple[
    SqlitePool, DeviceRuntimeManager, FamilyManager, User, str
]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'runtime', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    runtime = DeviceRuntimeManager(family, FamilyDeviceRepo(pool))
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, runtime, family, user, fam.id


def _pair_device(runtime, family_id, user):
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name="kitchen-pi",
        device_type="rpi",
        platform="linux",
        capabilities=["ping", "fs.read"],
    )
    device, token, _expires_at = runtime.complete_pairing(
        pairing.code, address="192.0.2.10",
    )
    return device, token


# ------------------------------------------------------------------ pairing


def test_pairing_exchanges_code_for_token(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name="kitchen-pi",
        device_type="rpi",
        platform="linux",
        capabilities=["ping", "fs.read"],
    )
    device, token, _expires_at = runtime.complete_pairing(
        pairing.code, address="192.0.2.10",
    )
    assert device.name == "kitchen-pi"
    assert device.status == "ONLINE"
    assert token
    # Second use of the same code must fail.
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.complete_pairing(pairing.code, address=None)


def test_pairing_code_expires(tmp_path: Path) -> None:
    from homemind.infra.family import device_runtime
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    pairing = runtime.create_pairing_code(
        family_id, user,
        device_name="kitchen-pi",
        device_type="rpi",
        platform="linux",
        capabilities=["ping"],
    )
    with device_runtime._PAIRING_LOCK:  # type: ignore[attr-defined]
        device_runtime._PAIRING_STORE[pairing.code].expires_at = 1  # type: ignore[attr-defined]
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.complete_pairing(pairing.code, address=None)


# -------------------------------------------------------------- heartbeat


def test_heartbeat_uses_token_hash(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair_device(runtime, family_id, user)
    updated = runtime.heartbeat(token, address="192.0.2.10")
    assert updated.id == device.id
    assert updated.last_seen is not None


def test_heartbeat_rejects_unknown_token(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.heartbeat("definitely-not-real", address=None)


# -------------------------------------------------------------- rotation


def test_rotate_token_revokes_previous(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, first_token = _pair_device(runtime, family_id, user)
    new_token = runtime.rotate_token(family_id, device.id, user)
    assert new_token != first_token
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.heartbeat(first_token, address=None)
    refreshed = runtime.heartbeat(new_token, address="192.0.2.10")
    assert refreshed.id == device.id


def test_revoke_token_disables_all_credentials(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair_device(runtime, family_id, user)
    revoked = runtime.revoke_token(family_id, device.id, user)
    assert revoked == 1
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.heartbeat(token, address=None)


# -------------------------------------------------------------- commands


def test_enqueue_command_requires_declared_capability(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, _token = _pair_device(runtime, family_id, user)
    command = runtime.enqueue_command(
        family_id, device.id,
        capability="ping",
        payload={"hello": "world"},
        requested_by=user.id,
        expires_at=10**10,
    )
    assert command.status == "PENDING"
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        runtime.enqueue_command(
            family_id, device.id,
            capability="fs.delete",
            payload={},
            requested_by=user.id,
            expires_at=10**10,
        )


def test_runtime_claim_and_complete_command(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair_device(runtime, family_id, user)
    command = runtime.enqueue_command(
        family_id, device.id,
        capability="ping",
        payload={},
        requested_by=user.id,
        expires_at=10**10,
    )
    # Dispatch is credential-gated: the token resolves the device, so a
    # runtime cannot be pointed at another device's queue.
    next_command = runtime.next_pending_command(token)
    assert next_command is not None
    assert next_command.id == command.id
    assert next_command.status == "DISPATCHED"
    assert next_command.lease_owner == f"device:{device.id}"
    assert next_command.lease_expires_at is not None
    finished = runtime.report_command_result(
        token,
        next_command.id,
        status="SUCCEEDED",
        result={"pong": True},
    )
    assert finished is not None
    assert finished.status == "SUCCEEDED"
    refreshed = runtime.heartbeat(token, address=None)
    assert refreshed.last_seen is not None


def test_runtime_result_is_idempotent(tmp_path: Path) -> None:
    """A retrying runtime resubmitting a terminal outcome must not
    error out — it cannot tell "lost" from "already done"."""

    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair_device(runtime, family_id, user)
    runtime.enqueue_command(
        family_id, device.id,
        capability="ping",
        payload={},
        requested_by=user.id,
        expires_at=10**10,
    )
    claimed = runtime.next_pending_command(token)
    assert claimed is not None
    first = runtime.report_command_result(
        token, claimed.id, status="SUCCEEDED", result={"pong": True},
    )
    second = runtime.report_command_result(
        token, claimed.id, status="SUCCEEDED", result={"pong": True},
    )
    assert first is not None and first.status == "SUCCEEDED"
    assert second is not None and second.status == "SUCCEEDED"


def test_expired_command_is_marked(tmp_path: Path) -> None:
    pool, runtime, family, user, family_id = _bootstrap(tmp_path)
    device, token = _pair_device(runtime, family_id, user)
    runtime.enqueue_command(
        family_id, device.id,
        capability="ping",
        payload={},
        requested_by=user.id,
        expires_at=1,  # already expired
    )
    runtime.next_pending_command(token)  # triggers expiry side-effect
    recent = runtime.list_commands(family_id, device.id, user)
    assert recent
    statuses = {cmd.status for cmd in recent}
    assert "EXPIRED" in statuses


# ------------------------------------------------------------------ helpers


def test_hash_token_is_stable() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")