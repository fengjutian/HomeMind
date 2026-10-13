from __future__ import annotations

from pathlib import Path

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.desktop_notifications import DesktopNotificationRepo
from octop.infra.db.repos.users import UserRepo


def test_desktop_notification_round_trip_is_user_scoped(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    alice_id = UserRepo(db).create(username="alice", password_hash="h", role="user")
    bob_id = UserRepo(db).create(username="bob", password_hash="h", role="user")
    repo = DesktopNotificationRepo(db)
    repo.insert(
        notification_id="notice-1",
        user_id=alice_id,
        agent_id="agent-1",
        thread_id="thread-1",
        agent_name="Helper",
        body="Reminder",
    )

    assert repo.list_pending(bob_id) == []
    assert repo.list_pending(alice_id)[0].to_frame() == {
        "type": "dashboard_push",
        "notification_id": "notice-1",
        "agent_id": "agent-1",
        "thread_id": "thread-1",
        "agent_name": "Helper",
        "text": "Reminder",
    }
    repo.acknowledge(bob_id, "notice-1")
    assert len(repo.list_pending(alice_id)) == 1
    repo.acknowledge(alice_id, "notice-1")
    assert repo.list_pending(alice_id) == []
