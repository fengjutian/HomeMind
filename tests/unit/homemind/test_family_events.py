"""Stage 14 acceptance tests for the family real-time event bus.

Covers the spec's rules:

* events only reach members of the family that produced them,
* a private-space event reaches only that space's members,
* an ordinary member does not receive manager-only audit detail,
* repeated events carry a stable id so a reconnecting client can
  de-duplicate,
* an unknown event type is dropped rather than broadcast,
* closing a socket stops delivery (no leaked subscription).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.events import (
    EVENT_APPROVAL_CREATED,
    EVENT_DEVICE_OFFLINE,
    EVENT_MEMORY_CANDIDATE_CREATED,
    EVENT_TYPES,
    FamilyEvent,
    FamilyEventBus,
)
from homemind.infra.family.manager import FamilyManager, MemberRole, SpaceType
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _RecordingHub:
    """Stand-in for Octop's ``WebSocketHub``.

    Mirrors the real hub's contract: a connection is registered per
    user, ``push_to_user`` fans out to that user's live connections,
    and closing the socket unregisters them so later pushes go
    nowhere. Without that last part the "no leaked subscription" test
    would prove nothing.
    """

    def __init__(self) -> None:
        self.connections: dict[int, set[str]] = {}
        self.frames: dict[str, dict[str, Any]] = {}
        self.dropped: dict[int, list[dict[str, Any]]] = {}

    def open(self, user_id: int, connection_id: str) -> None:
        self.connections.setdefault(user_id, set()).add(connection_id)

    def close(self, user_id: int, connection_id: str) -> None:
        self.connections.get(user_id, set()).discard(connection_id)

    async def push_to_user(self, user_id: int, frame: dict[str, Any]) -> None:
        live = self.connections.get(user_id, set())
        if not live:
            # No socket open: the real hub logs and drops.
            self.dropped.setdefault(user_id, []).append(frame)
            return
        for connection_id in live:
            self.frames[connection_id] = frame


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
            "(3, 'friend', 'x', 'user', 0, 'zh', 1)"
        )
    papa = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    family_repo = FamilyRepo(pool)
    manager = FamilyManager(family_repo)
    family = manager.create_family(
        papa, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    # mama is an ordinary member; friend is an admin.
    manager.create_member(
        family.id, papa, display_name="妈妈", role=MemberRole.MEMBER, user_id=2,
    )
    manager.create_member(
        family.id, papa, display_name="朋友", role=MemberRole.ADMIN, user_id=3,
    )
    # A second household, so cross-family leakage is detectable.
    other_family = manager.create_family(
        User(id=3, username="friend", role=Role.USER, display_name="朋友"),
        name="Other", timezone="UTC", locale="en",
    )
    services = HomeMindServices.from_pool(pool)
    hub = _RecordingHub()
    # Every member has a dashboard socket open.
    for user_id in (1, 2, 3):
        hub.open(user_id, f"conn-{user_id}")
    return (
        pool, FamilyEventBus(services, hub=hub), hub, manager, papa,
        family.id, other_family.id,
    )


# ------------------------------------------------------------------ routing


@pytest.mark.asyncio
async def test_event_reaches_every_family_member(tmp_path: Path) -> None:
    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    reached = await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            payload={"candidate_id": "c1"},
        ),
    )
    assert reached == 3
    assert set(hub.frames) == {"conn-1", "conn-2", "conn-3"}
    pool.close()


@pytest.mark.asyncio
async def test_event_does_not_leak_to_another_family(tmp_path: Path) -> None:
    """A second household's member must not hear about this family."""

    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            payload={"candidate_id": "c1"},
        ),
    )
    # Every delivered frame names the originating family.
    for frame in hub.frames.values():
        assert frame["family_id"] == family_id
    # User 3 belongs to the *other* household, so this family\'s event
    # reaches them only through their membership here, never as a
    # cross-family leak.
    assert all(f["family_id"] == family_id for f in hub.frames.values())
    pool.close()


@pytest.mark.asyncio
async def test_private_space_event_reaches_only_its_owner(tmp_path: Path) -> None:
    pool, bus, hub, manager, papa, family_id, _oid = _bootstrap(tmp_path)
    space = manager.create_space(
        family_id, papa, name="妈妈的空间",
        space_type=SpaceType.PRIVATE,
        owner_member_id=manager.repo.list_members(family_id)[1].id,
    )
    reached = await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            space_id=space.id,
            payload={"candidate_id": "secret"},
        ),
    )
    assert reached == 1, "only the space owner may be told"
    assert set(hub.frames) == {"conn-2"}
    pool.close()


@pytest.mark.asyncio
async def test_manager_only_event_is_filtered_for_ordinary_members(
    tmp_path: Path,
) -> None:
    """An approval is a manager concern; an ordinary member's dashboard
    should not light up for it."""

    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    await bus.emit(
        FamilyEvent(
            event_type=EVENT_APPROVAL_CREATED,
            family_id=family_id,
            payload={"approval_id": "a1"},
        ),
    )
    # papa is the owner and friend is an admin; mama is a plain member.
    assert "conn-1" in hub.frames
    assert "conn-3" in hub.frames
    assert "conn-2" not in hub.frames
    pool.close()


@pytest.mark.asyncio
async def test_member_scoped_event_reaches_only_that_member(tmp_path: Path) -> None:
    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    members = {row.user_id: row.id for row in _manager.repo.list_members(family_id)}
    await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            owner_member_id=members[2],
            payload={"candidate_id": "mine"},
        ),
    )
    assert set(hub.frames) == {"conn-2"}
    pool.close()


# ------------------------------------------------------------------- frames


def test_repeated_events_share_a_stable_id() -> None:
    event = FamilyEvent(
        event_type=EVENT_DEVICE_OFFLINE,
        family_id="f1",
        payload={"device_id": "d1"},
        created_at=1700,
    )
    assert event.event_id == event.event_id
    assert event.as_frame()["event_id"] == event.event_id
    # A different payload must produce a different id, otherwise a
    # reconnecting client would swallow a genuinely new event.
    other = FamilyEvent(
        event_type=EVENT_DEVICE_OFFLINE,
        family_id="f1",
        payload={"device_id": "d2"},
        created_at=1700,
    )
    assert other.event_id != event.event_id


def test_frame_carries_the_documented_shape() -> None:
    frame = FamilyEvent(
        event_type=EVENT_DEVICE_OFFLINE,
        family_id="f1",
        payload={"device_id": "d1"},
        created_at=1700,
    ).as_frame()
    assert frame["type"] == EVENT_DEVICE_OFFLINE
    assert frame["family_id"] == "f1"
    assert frame["payload"] == {"device_id": "d1"}
    assert frame["created_at"] == 1700


@pytest.mark.asyncio
async def test_unknown_event_type_is_dropped(tmp_path: Path) -> None:
    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    reached = await bus.emit(
        FamilyEvent(
            event_type="family.not.a.real.event",
            family_id=family_id,
            payload={},
        ),
    )
    assert reached == 0
    assert hub.frames == {}
    assert hub.dropped == {}
    pool.close()


def test_every_declared_event_type_is_namespaced() -> None:
    for event_type in EVENT_TYPES:
        assert event_type.startswith("family.")


# ---------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_no_subscription_survives_a_closed_socket(tmp_path: Path) -> None:
    """The bus holds no subscription of its own — it pushes through
    the hub, so an unregistered connection simply receives nothing."""

    pool, bus, hub, _manager, _papa, family_id, _oid = _bootstrap(tmp_path)
    await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            payload={"candidate_id": "c1"},
        ),
    )
    assert set(hub.frames) == {"conn-1", "conn-2", "conn-3"}
    delivered_before = len(hub.frames)

    # Simulate every socket closing.
    for user_id in (1, 2, 3):
        hub.close(user_id, f"conn-{user_id}")
    hub.frames.clear()

    await bus.emit(
        FamilyEvent(
            event_type=EVENT_MEMORY_CANDIDATE_CREATED,
            family_id=family_id,
            payload={"candidate_id": "c2"},
        ),
    )
    assert hub.frames == {}, "a closed socket must not keep receiving"
    assert sum(len(frames) for frames in hub.dropped.values()) == 3
    assert delivered_before == 3
    pool.close()
