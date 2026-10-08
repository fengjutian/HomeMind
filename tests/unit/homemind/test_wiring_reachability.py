"""Wiring tests: the event bus and approval expiry must be reached from
real write paths, not just from their own unit tests.

The spec's disqualifier list calls out "只有类或文件,没有接入运行时"
(a class exists with nothing calling it). These tests close that gap
for the two paths that were previously unwired:

* device command enqueue / approve / cancel push events,
* the daily sweep expires stale approvals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.events import (
    EVENT_APPROVAL_CREATED,
    EVENT_APPROVAL_DECIDED,
    FamilyEventBus,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_maintenance import MaintenanceRunner
from homemind.infra.family.permissions import PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


class _RecordingHub:
    def __init__(self) -> None:
        self.frames: dict[str, dict[str, Any]] = {}

    def open(self, user_id: int, connection_id: str) -> None:
        self.frames[connection_id] = {}

    async def push_to_user(self, user_id: int, frame: dict[str, Any]) -> None:
        self.frames[f"conn-{user_id}"] = frame


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    services = HomeMindServices.from_pool(pool)
    family_manager = FamilyManager(services.family_repo)
    family = family_manager.create_family(
        owner,
        name="Happy",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    hub = _RecordingHub()
    hub.open(1, "conn-1")
    return (
        pool,
        services,
        FamilyEventBus(services, hub=hub),
        hub,
        family_manager,
        owner,
        family.id,
    )


# ------------------------------------------------- device command events


def test_command_enqueue_emits_approval_created(tmp_path: Path) -> None:
    """An unsafe command must announce itself to the family's managers
    the moment it lands in WAITING_APPROVAL."""

    import asyncio

    from homemind.api.routers.devices import emit_command_event

    pool, services, bus, hub, family_manager, owner, family_id = _bootstrap(tmp_path)
    devices = DeviceRuntimeManager(family_manager, services.family_device_repo)
    pairing = devices.create_pairing_code(
        family_id,
        owner,
        device_name="pi",
        device_type="rpi",
        platform="linux",
        capabilities=["filesystem.delete"],
        root_path="/mnt/photos",
    )
    device, _token, _expires = devices.complete_pairing(
        pairing.code,
        address="192.0.2.10",
    )
    command = devices.enqueue_command(
        family_id,
        device.id,
        capability="filesystem.delete",
        payload={"path": "a.jpg"},
        requested_by=owner.id,
        expires_at=10**10,
        user=owner,
    )
    assert command.status == "WAITING_APPROVAL"

    class _Server:
        family_event_bus = bus

    asyncio.run(
        emit_command_event(
            _Server(),
            family_id,
            EVENT_APPROVAL_CREATED,  # type: ignore[arg-type]
            {"command_id": command.id, "is_unsafe": command.is_unsafe},
        )
    )
    frame = hub.frames["conn-1"]
    assert frame["type"] == EVENT_APPROVAL_CREATED
    assert frame["payload"]["command_id"] == command.id
    pool.close()


def test_approve_then_cancel_push_two_distinct_events(tmp_path: Path) -> None:
    import asyncio

    from homemind.api.routers.devices import emit_command_event

    pool, services, bus, hub, family_manager, owner, family_id = _bootstrap(tmp_path)
    devices = DeviceRuntimeManager(family_manager, services.family_device_repo)
    pairing = devices.create_pairing_code(
        family_id,
        owner,
        device_name="pi",
        device_type="rpi",
        platform="linux",
        capabilities=["filesystem.delete"],
        root_path="/mnt/photos",
    )
    device, _token, _expires = devices.complete_pairing(pairing.code, address=None)
    command = devices.enqueue_command(
        family_id,
        device.id,
        capability="filesystem.delete",
        payload={"path": "a.jpg"},
        requested_by=owner.id,
        expires_at=10**10,
        user=owner,
    )
    approved = devices.approve_command(family_id, command.id, owner)
    assert approved.status == "PENDING"

    class _Server:
        family_event_bus = bus

    asyncio.run(
        emit_command_event(
            _Server(),
            family_id,
            EVENT_APPROVAL_DECIDED,  # type: ignore[arg-type]
            {"command_id": approved.id, "decision": "APPROVED"},
        )
    )
    frame = hub.frames["conn-1"]
    assert frame["payload"]["decision"] == "APPROVED"

    # Cancelling a different command is a distinct event.
    other = devices.enqueue_command(
        family_id,
        device.id,
        capability="filesystem.delete",
        payload={"path": "b.jpg"},
        requested_by=owner.id,
        expires_at=10**10,
        user=owner,
    )
    cancelled = devices.cancel_command(family_id, other.id, owner)
    assert cancelled.status == "CANCELLED"
    asyncio.run(
        emit_command_event(
            _Server(),
            family_id,
            EVENT_APPROVAL_DECIDED,  # type: ignore[arg-type]
            {"command_id": cancelled.id, "decision": "CANCELLED"},
        )
    )
    assert hub.frames["conn-1"]["payload"]["decision"] == "CANCELLED"
    pool.close()


def test_missing_bus_does_not_break_the_write(tmp_path: Path) -> None:
    """A plain Octop deployment has no bus; the write must still work."""

    import asyncio

    from homemind.api.routers.devices import emit_command_event

    pool, services, _bus, _hub, family_manager, owner, family_id = _bootstrap(tmp_path)
    devices = DeviceRuntimeManager(family_manager, services.family_device_repo)
    pairing = devices.create_pairing_code(
        family_id,
        owner,
        device_name="pi",
        device_type="rpi",
        platform="linux",
        capabilities=["ping"],
    )
    device, _token, _expires = devices.complete_pairing(pairing.code, address=None)

    class _NoBus:
        family_event_bus = None

    asyncio.run(
        emit_command_event(
            _NoBus(),
            family_id,
            EVENT_APPROVAL_CREATED,
            {},  # type: ignore[arg-type]
        )
    )
    pool.close()


# ------------------------------------------------- approval expiry sweep


def test_daily_sweep_expires_stale_approvals(tmp_path: Path) -> None:
    """The maintenance runner must actually close stale approvals —
    ``expire_pending_approvals`` existing is not enough."""

    pool, services, _bus, _hub, family_manager, owner, family_id = _bootstrap(tmp_path)
    family_manager.create_permission(
        family_id,
        owner,
        subject_member_id=family_manager.repo.list_members(family_id)[0].id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    context_manager = FamilyContextManager(
        family_manager,
        services.family_context_repo,
    )
    transactions = FamilyTransactionManager(
        family_manager,
        context_manager,
        FamilyTaskManager(family_manager, FamilyTaskRepo(pool)),
        services.family_transaction_repo,
        UserRepo(pool),
        approval_ttl_seconds=60,
    )
    transaction, approval = transactions.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    assert approval is not None
    assert approval.status == "PENDING"

    runner = MaintenanceRunner(
        db=pool,
        family_repo=services.family_repo,
        context_repo=services.family_context_repo,
        candidate_repo=services.memory_candidate_repo,
        evidence_repo=services.memory_evidence_repo,
        family_manager=family_manager,
        context_manager=context_manager,
        transaction_manager=transactions,
    )
    # Nothing is stale yet.
    assert runner.run_once()["approvals_expired"] == 0

    # Backdate the approval past its window.
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_approvals SET approval_expires_at = 1 WHERE approval_id = ?",
            (approval.id,),
        )
    totals = runner.run_once()
    assert totals["approvals_expired"] == 1

    refreshed = services.family_transaction_repo.get_approval(approval.id)
    assert refreshed is not None
    assert refreshed.status == "EXPIRED"
    assert services.family_transaction_repo.get_transaction(transaction.id).status == "CANCELLED"  # type: ignore[union-attr]

    # A second sweep is a no-op.
    assert runner.run_once()["approvals_expired"] == 0
    pool.close()


def test_daily_sweep_expires_stale_asset_transfers(tmp_path: Path) -> None:
    """The maintenance runner must actually expire idle downloads.

    ``AssetTransferRepo.expire_before`` existing is not enough: without a
    caller, a transfer whose device went quiet keeps a live download
    credential forever.
    """

    from pathlib import Path as _Path

    from homemind.infra.db.repos.asset_transfers import AssetTransferRepo
    from homemind.infra.db.repos.family_assets import FamilyAssetRepo

    pool, services, _bus, _hub, family_manager, owner, family_id = _bootstrap(tmp_path)
    payload = _Path(tmp_path) / "sweep-me.mp4"
    payload.write_bytes(b"x" * 32)
    asset = FamilyAssetRepo(pool).upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="VIDEO",
        name=payload.name,
        uri=payload.as_uri(),
        mime_type="video/mp4",
        size_bytes=32,
        content_hash="a" * 64,
        captured_at=None,
        metadata_json="{}",
        created_by=owner.id,
        visibility="FAMILY",
    )
    device = services.family_device_repo.create(
        family_id,
        name="tv",
        device_type="tv",
        platform="android",
        capabilities=["asset.download"],
    )
    transfers = AssetTransferRepo(pool)
    transfer = transfers.create(
        family_id=family_id,
        asset_id=asset.id,
        device_id=device.id,
        request_key="sweep",
        size_bytes=32,
        sha256="a" * 64,
        source_mtime_ns=None,
        etag='"v1"',
        chunk_size=16,
        expires_at=1,  # already lapsed
    )
    transfers.issue_token(transfer.id, "hash-sweep", expires_at=9_999_999_999)

    context_manager = FamilyContextManager(
        family_manager,
        services.family_context_repo,
    )
    runner = MaintenanceRunner(
        db=pool,
        family_repo=services.family_repo,
        context_repo=services.family_context_repo,
        candidate_repo=services.memory_candidate_repo,
        evidence_repo=services.memory_evidence_repo,
        family_manager=family_manager,
        context_manager=context_manager,
        transfer_repo=transfers,
    )

    assert runner.run_once()["asset_transfers_expired"] == 1
    assert transfers.get(transfer.id).status == "EXPIRED"  # type: ignore[union-attr]
    assert transfers.resolve_active_token("hash-sweep", now=100) is None

    # A second sweep must not re-report the same row.
    assert runner.run_once()["asset_transfers_expired"] == 0
    pool.close()
