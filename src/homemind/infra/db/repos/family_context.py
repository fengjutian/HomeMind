"""SQL access for HomeMind events and memories."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, optional_updates
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyEventRow:
    id: str
    pk: int
    family_id: str
    event_type: str
    title: str
    start_at: int
    end_at: int
    location: str | None
    description: str
    metadata_json: str
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyEventRow:
        return cls(
            id=str(row["event_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            event_type=str(row["event_type"]),
            title=str(row["title"]),
            start_at=int(row["start_at"]),
            end_at=int(row["end_at"]),
            location=row["location"],
            description=str(row["description"]),
            metadata_json=str(row["metadata_json"]),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FamilyMemoryRow:
    id: str
    pk: int
    family_id: str
    subject_type: str
    subject_id: str | None
    content: str
    memory_type: str
    importance: float
    confidence: float
    visibility: str
    source_type: str
    source_id: str | None
    created_by: int
    created_at: int
    updated_at: int
    expires_at: int | None
    status: str

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyMemoryRow:
        return cls(
            id=str(row["memory_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            subject_type=str(row["subject_type"]),
            subject_id=row["subject_id"],
            content=str(row["content"]),
            memory_type=str(row["memory_type"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            visibility=str(row["visibility"]),
            source_type=str(row["source_type"]),
            source_id=row["source_id"],
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            status=str(row["status"]),
        )


class FamilyContextRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create_event(self, family_id: str, **values: object) -> FamilyEventRow:
        event_id, ts = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_events(event_id, family_id, event_type, title, "
                "start_at, end_at, location, description, metadata_json, created_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    family_id,
                    values["event_type"],
                    values["title"],
                    values["start_at"],
                    values["end_at"],
                    values.get("location"),
                    values.get("description", ""),
                    values.get("metadata_json", "{}"),
                    values["created_by"],
                    ts,
                    ts,
                ),
            )
        return self.get_event(event_id)  # type: ignore[return-value]

    def get_event(self, event_id: str) -> FamilyEventRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return FamilyEventRow.from_row(row) if row else None

    def list_events(
        self,
        family_id: str,
        *,
        start_at: int | None = None,
        end_at: int | None = None,
    ) -> list[FamilyEventRow]:
        clauses = ["family_id = ?", "status = 'ACTIVE'"]
        params: list[object] = [family_id]
        if start_at is not None:
            clauses.append("end_at >= ?")
            params.append(start_at)
        if end_at is not None:
            clauses.append("start_at <= ?")
            params.append(end_at)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY start_at DESC",
                params,
            ).fetchall()
        return map_rows(rows, FamilyEventRow)

    def update_event(self, event_id: str, **values: object) -> FamilyEventRow | None:
        allowed = {
            "event_type", "title", "start_at", "end_at", "location",
            "description", "metadata_json",
        }
        fields, params = optional_updates(
            [(key, value) for key, value in values.items() if key in allowed]
        )
        if fields:
            fields.append("updated_at = ?")
            params.extend((now_ts(), event_id))
            with self._db.transaction() as conn:
                conn.execute(
                    f"UPDATE homemind_family_events SET {', '.join(fields)} "
                    "WHERE event_id = ?",
                    params,
                )
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM homemind_family_events WHERE event_id = ?", (event_id,))

    def link_event_asset(self, event_id: str, asset_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_event_assets(event_id, asset_id) VALUES (?, ?) "
                "ON CONFLICT(event_id, asset_id) DO NOTHING",
                (event_id, asset_id),
            )

    def list_event_ids_for_asset(self, asset_id: str) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT event_id FROM homemind_family_event_assets WHERE asset_id = ? "
                "ORDER BY event_id",
                (asset_id,),
            ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def create_memory(self, family_id: str, **values: object) -> FamilyMemoryRow:
        memory_id, ts = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_memories(memory_id, family_id, subject_type, "
                "subject_id, content, memory_type, importance, confidence, visibility, "
                "source_type, source_id, "
                "created_by, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id, family_id, values["subject_type"],
                    values.get("subject_id"), values["content"], values["memory_type"],
                    values.get("importance", 0.5), values.get("confidence", 0.5),
                    values.get("visibility", "FAMILY"), values["source_type"],
                    values.get("source_id"), values["created_by"], ts, ts,
                    values.get("expires_at"),
                ),
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def get_memory(self, memory_id: str) -> FamilyMemoryRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return FamilyMemoryRow.from_row(row) if row else None

    def search_memories(
        self, family_id: str, *, query: str | None = None, now: int | None = None
    ) -> list[FamilyMemoryRow]:
        clauses = [
            "family_id = ?",
            "status = 'ACTIVE'",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        params: list[object] = [family_id, now if now is not None else now_ts()]
        if query:
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{query.lower()}%")
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_memories WHERE "
                + " AND ".join(clauses)
                + " ORDER BY importance DESC, updated_at DESC",
                params,
            ).fetchall()
        return map_rows(rows, FamilyMemoryRow)

    def search_memories_fts(
        self,
        family_id: str,
        *,
        query: str,
        limit: int = 20,
    ) -> list[FamilyMemoryRow]:
        """Tokenised full-text memory search.

        SQLite uses the FTS5 virtual table created in
        ``007_memory_fts5.sql``; PostgreSQL uses the GIN expression
        index on ``to_tsvector('simple', content)`` added in
        ``007_memory_fts5.pg.sql``. Both paths filter out archived
        and expired memories, scope to ``family_id``, and rank by the
        backend's native score (``bm25`` on SQLite, ``ts_rank`` on
        PostgreSQL). The repo returns ``FamilyMemoryRow`` in both
        dialects so callers do not branch on ``db.dialect``.

        Returns ``[]`` on any backend error so the manager can fall
        back to ``search_memories`` (LIKE-based).
        """
        now = now_ts()
        if self._db.dialect == "postgresql":
            sql = (
                "SELECT m.* FROM homemind_family_memories m "
                "WHERE m.family_id = ? "
                "  AND m.status = 'ACTIVE' "
                "  AND (m.expires_at IS NULL OR m.expires_at > ?) "
                "  AND to_tsvector('simple', coalesce(m.content, '')) "
                "      @@ websearch_to_tsquery('simple', ?) "
                "ORDER BY ts_rank("
                "  to_tsvector('simple', coalesce(m.content, '')),"
                "  websearch_to_tsquery('simple', ?)"
                ") DESC, m.updated_at DESC "
                "LIMIT ?"
            )
            params: tuple[object, ...] = (family_id, now, query, query, limit)
        else:
            sql = (
                "SELECT m.* FROM homemind_family_memories m "
                "JOIN homemind_family_memory_fts fts "
                "  ON fts.memory_id = m.memory_id "
                "WHERE fts.family_id = ? "
                "  AND m.status = 'ACTIVE' "
                "  AND (m.expires_at IS NULL OR m.expires_at > ?) "
                "  AND homemind_family_memory_fts MATCH ? "
                "ORDER BY fts.rank LIMIT ?"
            )
            params = (family_id, now, query, limit)
        try:
            with self._db.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001 — graceful degradation
            return []
        return map_rows(rows, FamilyMemoryRow)

    def list_all_memories(self, family_id: str) -> list[FamilyMemoryRow]:
        """Return *every* memory row for ``family_id`` regardless of
        status / expiry. Used by the memory lifecycle manager for
        maintenance operations (decay, archive, duplicate detection).
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_memories WHERE family_id = ? "
                "ORDER BY importance DESC, updated_at DESC",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyMemoryRow)

    def update_memory(self, memory_id: str, **values: object) -> FamilyMemoryRow | None:
        allowed = {
            "subject_type", "subject_id", "content", "memory_type", "importance",
            "confidence", "visibility", "source_type", "source_id", "expires_at", "status",
        }
        fields, params = optional_updates(
            [(key, value) for key, value in values.items() if key in allowed]
        )
        if fields:
            fields.append("updated_at = ?")
            params.extend((now_ts(), memory_id))
            with self._db.transaction() as conn:
                conn.execute(
                    f"UPDATE homemind_family_memories SET {', '.join(fields)} "
                    "WHERE memory_id = ?",
                    params,
                )
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM homemind_family_memories WHERE memory_id = ?", (memory_id,))
