"""Append-only agent workspace change audit records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows


@dataclass(frozen=True)
class WorkspaceChangeRow:
    change_id: str
    ts: int
    actor_type: str
    actor_id: str | None
    user_id: int | None
    agent_id: str
    thread_id: str | None
    turn_id: str | None
    tool_call_id: str | None
    source: str
    operation: str
    path: str
    destination: str | None
    before_exists: bool
    after_exists: bool
    before_size: int | None
    after_size: int | None
    before_hash: str | None
    after_hash: str | None
    diff_kind: str | None
    diff_text: str | None
    status: str
    error_code: str | None
    error_message: str | None
    request_id: str | None
    correlation_id: str | None
    metadata_json: str
    integrity_hash: str
    schema_version: int

    @classmethod
    def from_row(cls, row: DbRow) -> WorkspaceChangeRow:
        return cls(
            change_id=str(row["change_id"]),
            ts=int(row["ts"]),
            actor_type=str(row["actor_type"]),
            actor_id=str(row["actor_id"]) if row["actor_id"] is not None else None,
            user_id=int(row["user_id"]) if row["user_id"] is not None else None,
            agent_id=str(row["agent_id"]),
            thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
            turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
            tool_call_id=(str(row["tool_call_id"]) if row["tool_call_id"] is not None else None),
            source=str(row["source"]),
            operation=str(row["operation"]),
            path=str(row["path"]),
            destination=(str(row["destination"]) if row["destination"] is not None else None),
            before_exists=bool(row["before_exists"]),
            after_exists=bool(row["after_exists"]),
            before_size=int(row["before_size"]) if row["before_size"] is not None else None,
            after_size=int(row["after_size"]) if row["after_size"] is not None else None,
            before_hash=str(row["before_hash"]) if row["before_hash"] is not None else None,
            after_hash=str(row["after_hash"]) if row["after_hash"] is not None else None,
            diff_kind=str(row["diff_kind"]) if row["diff_kind"] is not None else None,
            diff_text=str(row["diff_text"]) if row["diff_text"] is not None else None,
            status=str(row["status"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_message=(str(row["error_message"]) if row["error_message"] is not None else None),
            request_id=str(row["request_id"]) if row["request_id"] is not None else None,
            correlation_id=(
                str(row["correlation_id"]) if row["correlation_id"] is not None else None
            ),
            metadata_json=str(row["metadata_json"]),
            integrity_hash=str(row["integrity_hash"]),
            schema_version=int(row["schema_version"]),
        )


class WorkspaceChangeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def insert(self, values: dict[str, Any]) -> None:
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        with self._db.transaction() as conn:
            conn.execute(
                f"INSERT INTO workspace_change_log ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({placeholders})",
                tuple(values[column] for column in columns),
            )

    def get(self, change_id: str) -> WorkspaceChangeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_change_log WHERE change_id = ?", (change_id,)
            ).fetchone()
        return WorkspaceChangeRow.from_row(row) if row is not None else None

    def list_for_agent(
        self,
        agent_id: str,
        *,
        thread_id: str | None = None,
        path: str | None = None,
        source: str | None = None,
        operation: str | None = None,
        limit: int = 100,
        before_ts: int | None = None,
    ) -> list[WorkspaceChangeRow]:
        clauses = ["agent_id = ?"]
        params: list[object] = [agent_id]
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if path is not None:
            clauses.append("path = ?")
            params.append(path)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if operation is not None:
            clauses.append("operation = ?")
            params.append(operation)
        if before_ts is not None:
            clauses.append("ts < ?")
            params.append(before_ts)
        params.append(max(1, min(limit, 500)))
        sql = (
            "SELECT * FROM workspace_change_log WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ts DESC, id DESC LIMIT ?"
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return map_rows(rows, WorkspaceChangeRow)
