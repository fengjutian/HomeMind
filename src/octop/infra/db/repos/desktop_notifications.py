"""Pending desktop notification persistence."""

from __future__ import annotations

import time
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow


@dataclass(frozen=True)
class DesktopNotificationRow:
    notification_id: str
    user_id: int
    agent_id: str
    thread_id: str
    agent_name: str
    body: str
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> DesktopNotificationRow:
        return cls(
            notification_id=str(row["notification_id"]),
            user_id=int(row["user_id"]),
            agent_id=str(row["agent_id"]),
            thread_id=str(row["thread_id"]),
            agent_name=str(row["agent_name"]),
            body=str(row["body"]),
            created_at=int(row["created_at"]),
        )

    def to_frame(self) -> dict[str, object]:
        return {
            "type": "dashboard_push",
            "notification_id": self.notification_id,
            "agent_id": self.agent_id,
            "thread_id": self.thread_id,
            "agent_name": self.agent_name,
            "text": self.body,
        }


class DesktopNotificationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def insert(
        self,
        *,
        notification_id: str,
        user_id: int,
        agent_id: str,
        thread_id: str,
        agent_name: str,
        body: str,
    ) -> None:
        now = int(time.time())
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM desktop_notifications WHERE created_at < ?",
                (now - 30 * 86400,),
            )
            conn.execute(
                "INSERT INTO desktop_notifications("
                "notification_id, user_id, agent_id, thread_id, agent_name, body, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    notification_id,
                    user_id,
                    agent_id,
                    thread_id,
                    agent_name,
                    body,
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM desktop_notifications WHERE user_id = ? AND id NOT IN ("
                "SELECT id FROM desktop_notifications WHERE user_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1000)",
                (user_id, user_id),
            )

    def list_pending(self, user_id: int, *, limit: int = 100) -> list[DesktopNotificationRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT notification_id, user_id, agent_id, thread_id, agent_name, "
                "body, created_at FROM desktop_notifications WHERE user_id = ? "
                "ORDER BY created_at, id LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [DesktopNotificationRow.from_row(row) for row in rows]

    def acknowledge(self, user_id: int, notification_id: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "DELETE FROM desktop_notifications WHERE user_id = ? AND notification_id = ?",
                (user_id, notification_id),
            )
