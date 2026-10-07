"""Family Transaction action handlers (Stage 3).

Each handler is exercised through its own contract rather than through
the transaction manager, because that is where the interesting
behaviour lives: ownership checks that fail closed, ``verify`` that
re-reads instead of trusting ``execute``, and compensation that restores
what the *row* said rather than what the payload asked for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.calendar import FamilyCalendarManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.lowrisk import (
    AlbumAssetHandler,
    AlbumCreateHandler,
    EventDeleteHandler,
    EventUpdateHandler,
    MemoryDeprecateHandler,
    MemoryUpdateHandler,
    TaskCancelHandler,
    TaskUpdateHandler,
)
from homemind.infra.family.transaction_actions.registry import build_default_action_registry
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _FakeTransaction:
    """Minimal stand-in for the row the manager hands a handler."""

    def __init__(self, transaction_id: str = "txn-1") -> None:
        self.id = transaction_id
        self.family_id = "unused"
        self.action = ""
        self.idempotency_key = transaction_id


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse"), (3, "outsider")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Action Family", timezone="Asia/Shanghai", locale="zh"
    )
    other = families.create_family(
        User(2, "spouse", Role.USER, "Spouse"),
        name="Neighbour",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    context = FamilyContextManager(families, services.family_context_repo)
    return {
        "services": services,
        "families": families,
        "family": family,
        "other": other,
        "owner": owner,
        "tasks": FamilyTaskManager(families, services.family_task_repo),
        "context": context,
        "calendars": FamilyCalendarManager(
            families, services.family_calendar_repo, server_timezone="Asia/Shanghai"
        ),
    }


def _ctx(env, family_key: str = "family", user_key: str = "owner") -> object:
    from homemind.infra.family.transaction_actions.base import ActionContext

    return ActionContext(
        family_id=env[family_key].id,
        transaction=_FakeTransaction(),
        requester=env[user_key],
        payload={},
    )


# --------------------------------------------------------------- task.*


def test_task_update_changes_and_verifies(env) -> None:  # noqa: ANN001
    handler = TaskUpdateHandler(env["tasks"])
    context = _ctx(env)
    task = env["tasks"].create(env["family"].id, env["owner"], title="Buy milk")

    payload = {"task_id": task.id, "title": "Buy oat milk", "status": "DONE"}
    handler.validate(context, payload)
    preview = handler.preview(context, payload)
    assert preview["before"]["title"] == "Buy milk"
    assert preview["after"]["title"] == "Buy oat milk"

    result = handler.execute(context, payload)
    assert result["before"]["title"] == "Buy milk"
    verdict = handler.verify(context, result)
    assert verdict["verified"] is True
    assert verdict["status"] == "DONE"


def test_task_update_needs_at_least_one_field(env) -> None:  # noqa: ANN001
    handler = TaskUpdateHandler(env["tasks"])
    task = env["tasks"].create(env["family"].id, env["owner"], title="Nothing")
    with pytest.raises(HomeMindError) as excinfo:
        handler.validate(_ctx(env), {"task_id": task.id})
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_task_update_refuses_another_familys_task(env) -> None:  # noqa: ANN001
    handler = TaskUpdateHandler(env["tasks"])
    # A task created in the *other* family, addressed through this one.
    foreign = env["tasks"].create(
        env["other"].id, User(2, "spouse", Role.USER, "Spouse"), title="Theirs"
    )
    with pytest.raises(HomeMindError) as excinfo:
        handler.validate(_ctx(env), {"task_id": foreign.id, "title": "Mine now"})
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_NOT_FOUND


def test_task_update_compensates_to_the_row_snapshot(env) -> None:  # noqa: ANN001
    handler = TaskUpdateHandler(env["tasks"])
    context = _ctx(env)
    task = env["tasks"].create(
        env["family"].id, env["owner"], title="Original", description="Keep me"
    )
    result = handler.execute(context, {"task_id": task.id, "title": "Changed", "description": "New"})
    handler.compensate(context, result)
    restored = env["tasks"].repo.get(task.id)
    assert restored.title == "Original"
    assert restored.description == "Keep me"


def test_task_cancel_then_compensate(env) -> None:  # noqa: ANN001
    handler = TaskCancelHandler(env["tasks"])
    context = _ctx(env)
    task = env["tasks"].create(env["family"].id, env["owner"], title="Abandon me")
    result = handler.execute(context, {"task_id": task.id})
    assert handler.verify(context, result)["verified"] is True
    handler.compensate(context, result)
    assert env["tasks"].repo.get(task.id).status == "TODO"


# -------------------------------------------------------------- event.*


def _event(env, **overrides):  # noqa: ANN001, ANN201
    payload = {
        "event_type": "FAMILY",
        "title": "School pickup",
        "start_at": 1893456000,
        "end_at": 1893459600,
        "description": "",
        **overrides,
    }
    return env["context"].create_event(
        env["family"].id, env["owner"], **payload
    )


def test_event_update_verifies_and_compensates(env) -> None:  # noqa: ANN001
    handler = EventUpdateHandler(env["context"])
    context = _ctx(env)
    event = _event(env)
    result = handler.execute(context, {"event_id": event.id, "title": "School pickup (late)"})
    assert handler.verify(context, result)["verified"] is True
    handler.compensate(context, result)
    assert _find_event(env, event.id).title == "School pickup"


def test_event_delete_verifies_absence(env) -> None:  # noqa: ANN001
    handler = EventDeleteHandler(env["context"])
    context = _ctx(env)
    event = _event(env)
    result = handler.execute(context, {"event_id": event.id})
    assert handler.verify(context, result)["verified"] is True


def test_event_delete_compensate_restores_the_content(env) -> None:  # noqa: ANN001
    handler = EventDeleteHandler(env["context"])
    context = _ctx(env)
    event = _event(env, title="Dentist")
    result = handler.execute(context, {"event_id": event.id})
    outcome = handler.compensate(context, result)
    assert outcome["compensated"] is True
    # The restored row has a new id; the content is what came back.
    assert outcome["original_event_id"] == event.id
    assert outcome["restored_event_id"] != event.id
    titles = {e.title for e in env["context"].list_events(env["family"].id, env["owner"])}
    assert "Dentist" in titles


def _find_event(env, event_id: str):  # noqa: ANN001, ANN201
    for event in env["context"].list_events(env["family"].id, env["owner"]):
        if event.id == event_id:
            return event
    raise AssertionError(f"event {event_id} not found")


# ------------------------------------------------------------- memory.*


def _memory(env, content: str = "Dad works nights"):  # noqa: ANN001, ANN201
    return env["context"].create_memory(
        env["family"].id,
        env["owner"],
        subject_type="FAMILY",
        subject_id=None,
        content=content,
        memory_type="FACT",
        importance=0.5,
        confidence=0.9,
        visibility="FAMILY",
        source_type="MANUAL",
    )


def test_memory_update_verifies(env) -> None:  # noqa: ANN001
    handler = MemoryUpdateHandler(env["context"])
    context = _ctx(env)
    memory = _memory(env)
    result = handler.execute(context, {"memory_id": memory.id, "content": "Dad works days"})
    assert handler.verify(context, result)["verified"] is True


def test_memory_deprecate_then_compensate(env) -> None:  # noqa: ANN001
    handler = MemoryDeprecateHandler(env["context"])
    context = _ctx(env)
    memory = _memory(env)
    result = handler.execute(context, {"memory_id": memory.id, "reason": "no longer true"})
    assert handler.verify(context, result)["verified"] is True
    handler.compensate(context, result)
    restored = [
        m
        for m in env["context"].search_memories(env["family"].id, env["owner"])
        if m.id == memory.id
    ]
    assert restored and restored[0].status == "ACTIVE"


# ------------------------------------------------------------- album.*


def test_album_create_then_compensate(env) -> None:  # noqa: ANN001
    handler = AlbumCreateHandler(env["services"].family_album_repo)
    context = _ctx(env)
    handler.validate(context, {"name": "Trip 2026"})
    result = handler.execute(context, {"name": "Trip 2026"})
    assert handler.verify(context, result)["verified"] is True
    album_id = str(result["album_id"])
    assert album_id in [a.id for a in env["services"].family_album_repo.list_albums(env["family"].id)]


def test_album_asset_add_and_remove_are_idempotent(env) -> None:  # noqa: ANN001
    repo = env["services"].family_album_repo
    add = AlbumAssetHandler(repo, add=True)
    remove = AlbumAssetHandler(repo, add=False)
    context = _ctx(env)
    album = repo.create_album(env["family"].id, "Holidays", "", created_by=1)
    asset = env["services"].family_asset_repo.upsert_asset(
        family_id=env["family"].id,
        source_id=None,
        space_id=None,
        asset_type="IMAGE",
        name="a.jpg",
        uri="file:///photos/a.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        content_hash="abc",
        captured_at=None,
        metadata_json="{}",
        visibility="FAMILY",
        created_by=1,
    )

    payload = {"album_id": album.id, "asset_id": asset.id}
    add.validate(context, payload)
    add.execute(context, payload)
    # Adding twice must not duplicate the membership.
    add.execute(context, payload)
    assert repo.list_asset_ids(album.id) == [asset.id]
    assert add.verify(context, {"album_id": album.id, "asset_id": asset.id})["verified"] is True

    remove.execute(context, payload)
    assert repo.list_asset_ids(album.id) == []
    # Removing something already absent is a no-op, not an error.
    remove.execute(context, payload)
    assert remove.verify(context, {"album_id": album.id, "asset_id": asset.id})["verified"] is True


def test_album_asset_refuses_another_familys_album(env) -> None:  # noqa: ANN001
    repo = env["services"].family_album_repo
    handler = AlbumAssetHandler(repo, add=True)
    foreign = repo.create_album(env["other"].id, "Theirs", "", created_by=2)
    with pytest.raises(HomeMindError) as excinfo:
        handler.validate(_ctx(env), {"album_id": foreign.id, "asset_id": "x"})
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_NOT_FOUND


# ------------------------------------------------------------- registry


def test_registry_exposes_the_low_and_high_risk_actions(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    registry = build_default_action_registry(
        family=families,
        context=FamilyContextManager(families, services.family_context_repo),
        tasks=FamilyTaskManager(families, services.family_task_repo),
        albums=services.family_album_repo,
        calendars=FamilyCalendarManager(families, services.family_calendar_repo),
    )
    actions = set(registry.actions())
    assert {
        "task.update",
        "task.cancel",
        "event.update",
        "event.delete",
        "memory.update",
        "memory.deprecate",
        "album.create",
        "album.add_asset",
        "album.remove_asset",
        "calendar.create_event",
        "calendar.update_event",
        "calendar.cancel_event",
    } <= actions
    for action in actions:
        assert registry.get(action) is not None


def test_family_and_member_management_stay_closed_to_agents(tmp_path: Path) -> None:
    """The plan forbids exposing family/member/permission actions.

    Guarding it with a test rather than a comment: an action name is
    just a string, and nothing else would stop one from being added.
    """
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    registry = build_default_action_registry(
        family=families,
        context=FamilyContextManager(families, services.family_context_repo),
        tasks=FamilyTaskManager(families, services.family_task_repo),
        albums=services.family_album_repo,
        calendars=FamilyCalendarManager(families, services.family_calendar_repo),
    )
    forbidden = {
        "family.create",
        "family.update",
        "family.delete",
        "member.create",
        "member.update",
        "member.delete",
        "permission.grant",
        "permission.revoke",
        "member.bind_user",
        "family.transfer_ownership",
    }
    assert forbidden & set(registry.actions()) == set()
