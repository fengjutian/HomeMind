"""SQL access for HomeMind family tasks."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyTaskRow:
    id: str
    pk: int
    family_id: str
    title: str
    description: str
    status: str
    assigned_member_id: str | None
    due_at: int | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTaskRow:
        return cls(
            id=str(row["task_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            title=str(row["title"]),
            description=str(row["description"]),
            status=str(row["status"]),
            assigned_member_id=row["assigned_member_id"],
            due_at=int(row["due_at"]) if row["due_at"] is not None else None,
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class FamilyTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        family_id: str,
        *,
        title: str,
        description: str,
        assigned_member_id: str | None,
        due_at: int | None,
        created_by: int,
    ) -> FamilyTaskRow:
        task_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_tasks(task_id, family_id, title, description, "
                "assigned_member_id, due_at, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    family_id,
                    title,
                    description,
                    assigned_member_id,
                    due_at,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        row = self.get(task_id)
        assert row is not None
        return row

    def get(self, task_id: str) -> FamilyTaskRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return FamilyTaskRow.from_row(row) if row else None

    def list(self, family_id: str, *, status: str | None = None) -> list[FamilyTaskRow]:
        sql = "SELECT * FROM homemind_family_tasks WHERE family_id = ?"
        params: list[object] = [family_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY due_at IS NULL, due_at, created_at"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, FamilyTaskRow)
