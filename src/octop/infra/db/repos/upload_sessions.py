"""Upload session and part rows — server-managed resumable upload."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

try:
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - optional PostgreSQL driver
    pg_errors = None  # type: ignore[assignment]

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, sql_in_placeholders

# Storage vocabulary as plain literals: this module is SQL-only and may not
# import sibling domain packages (``infra/db/repos/`` boundary in AGENTS.md).
# ``test_upload_session_repo.py::test_repo_status_literals_match_the_protocol``
# fails if these ever drift from ``octop.infra.uploads.protocol.UploadStatus``.
_STATUS_OPEN = "OPEN"
_STATUS_ASSEMBLING = "ASSEMBLING"
_STATUS_COMPLETED = "COMPLETED"
_TERMINAL_STATUSES = (_STATUS_COMPLETED, "ABORTED", "EXPIRED")


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    return pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation)


class PartWriteOutcome(StrEnum):
    """Result of registering one part."""

    CREATED = "CREATED"
    #: Same part number re-sent with the identical hash — already durable.
    IDEMPOTENT = "IDEMPOTENT"


class UploadPartConflict(Exception):
    """Part rejected: hash mismatch, or a byte range that overlaps a sibling."""


@dataclass(frozen=True)
class UploadSessionRow:
    pk: int
    upload_id: str
    owner_user_id: int
    agent_id: str | None
    family_id: str | None
    purpose: str
    filename: str
    relative_target: str | None
    mime_type: str | None
    total_bytes: int
    chunk_size: int
    expected_sha256: str | None
    received_bytes: int
    status: str
    expires_at: int
    created_at: int
    updated_at: int
    completed_at: int | None
    final_resource_id: str | None
    last_error: str | None

    @classmethod
    def from_row(cls, r: DbRow) -> UploadSessionRow:
        def opt(name: str) -> str | None:
            raw = r[name]
            if raw is None:
                return None
            text = str(raw).strip()
            return text or None

        return cls(
            pk=int(r["id"]),
            upload_id=str(r["upload_id"]),
            owner_user_id=int(r["owner_user_id"]),
            agent_id=opt("agent_id"),
            family_id=opt("family_id"),
            purpose=str(r["purpose"]),
            filename=str(r["filename"] or ""),
            relative_target=opt("relative_target"),
            mime_type=opt("mime_type"),
            total_bytes=int(r["total_bytes"]),
            chunk_size=int(r["chunk_size"]),
            expected_sha256=opt("expected_sha256"),
            received_bytes=int(r["received_bytes"] or 0),
            status=str(r["status"] or _STATUS_OPEN),
            expires_at=int(r["expires_at"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
            completed_at=(int(r["completed_at"]) if r["completed_at"] is not None else None),
            final_resource_id=opt("final_resource_id"),
            last_error=r["last_error"],
        )


@dataclass(frozen=True)
class UploadPartRow:
    upload_id: str
    part_number: int
    offset: int
    size: int
    sha256: str
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> UploadPartRow:
        return cls(
            upload_id=str(r["upload_id"]),
            part_number=int(r["part_number"]),
            offset=int(r["byte_offset"]),
            size=int(r["size"]),
            sha256=str(r["sha256"]),
            created_at=int(r["created_at"]),
        )


class UploadSessionRepo:
    """CRUD for upload sessions plus the part registry.

    ``received_bytes`` is always recomputed from ``SUM(size)`` after a part
    insert instead of being incremented, so a crash between the two can never
    leave a drifted counter.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------------ sessions

    def create(
        self,
        *,
        upload_id: str,
        owner_user_id: int,
        purpose: str,
        filename: str,
        total_bytes: int,
        chunk_size: int,
        expires_at: int,
        agent_id: str | None = None,
        family_id: str | None = None,
        relative_target: str | None = None,
        mime_type: str | None = None,
        expected_sha256: str | None = None,
        status: str = _STATUS_OPEN,
    ) -> UploadSessionRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO upload_sessions("
                "upload_id, owner_user_id, agent_id, family_id, purpose, filename, "
                "relative_target, mime_type, total_bytes, chunk_size, expected_sha256, "
                "received_bytes, status, expires_at, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    upload_id,
                    owner_user_id,
                    agent_id,
                    family_id,
                    purpose,
                    filename,
                    relative_target,
                    mime_type,
                    total_bytes,
                    chunk_size,
                    expected_sha256,
                    status,
                    expires_at,
                    ts,
                    ts,
                ),
            )
        row = self.get(upload_id)
        assert row is not None  # noqa: S101
        return row

    def get(self, upload_id: str) -> UploadSessionRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM upload_sessions WHERE upload_id = ?",
                (upload_id,),
            ).fetchone()
        return UploadSessionRow.from_row(r) if r else None

    def get_for_owner(self, upload_id: str, owner_user_id: int) -> UploadSessionRow | None:
        """Ownership-scoped read; sessions are never readable across users."""
        row = self.get(upload_id)
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def list_for_owner(
        self,
        owner_user_id: int,
        *,
        statuses: list[str] | None = None,
    ) -> list[UploadSessionRow]:
        sql = "SELECT * FROM upload_sessions WHERE owner_user_id = ?"
        params: list[object] = [owner_user_id]
        if statuses:
            sql += f" AND status IN ({sql_in_placeholders(len(statuses))})"
            params.extend(statuses)
        sql += " ORDER BY id DESC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, UploadSessionRow)

    def count_active_for_owner(self, owner_user_id: int, *, now: int) -> int:
        """Unexpired, non-terminal sessions — the per-user concurrency limit."""
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM upload_sessions "
                "WHERE owner_user_id = ? AND status NOT IN (?, ?, ?) AND expires_at > ?",
                (owner_user_id, *_TERMINAL_STATUSES, now),
            ).fetchone()
        return int(r[0]) if r else 0

    def sum_received_bytes(self, *, statuses: list[str] | None = None) -> int:
        """Bytes actually on disk in staging — for metrics and reporting."""
        sql = "SELECT COALESCE(SUM(received_bytes), 0) FROM upload_sessions"
        params: list[object] = []
        if statuses:
            sql += f" WHERE status IN ({sql_in_placeholders(len(statuses))})"
            params.extend(statuses)
        with self._db.connect() as conn:
            r = conn.execute(sql, params).fetchone()
        return int(r[0]) if r else 0

    def sum_reserved_bytes(self, *, statuses: list[str] | None = None) -> int:
        """Declared ``total_bytes`` of live sessions.

        The quota must be reserved against *declared* size, not received bytes:
        otherwise opening many sessions without sending a part would each pass
        the gate and collectively exceed the staging quota.
        """
        sql = "SELECT COALESCE(SUM(total_bytes), 0) FROM upload_sessions"
        params: list[object] = []
        if statuses:
            sql += f" WHERE status IN ({sql_in_placeholders(len(statuses))})"
            params.extend(statuses)
        with self._db.connect() as conn:
            r = conn.execute(sql, params).fetchone()
        return int(r[0]) if r else 0

    def list_by_statuses(self, statuses: list[str]) -> list[UploadSessionRow]:
        """Every session in one of *statuses* — used by the orphan sweeper."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_sessions "
                f"WHERE status IN ({sql_in_placeholders(len(statuses))})",
                list(statuses),
            ).fetchall()
        return map_rows(rows, UploadSessionRow)

    def list_expired(self, *, now: int, limit: int = 100) -> list[UploadSessionRow]:
        """Non-terminal sessions past their TTL, oldest first."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_sessions "
                "WHERE expires_at <= ? AND status NOT IN (?, ?, ?) "
                "ORDER BY id ASC LIMIT ?",
                (now, *_TERMINAL_STATUSES, limit),
            ).fetchall()
        return map_rows(rows, UploadSessionRow)

    def set_status(
        self,
        upload_id: str,
        *,
        status: str,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> UploadSessionRow | None:
        """Move a session to *status*; ``last_error`` is only touched on request."""
        ts = now_ts()
        with self._db.transaction() as conn:
            if last_error is not None:
                conn.execute(
                    "UPDATE upload_sessions SET status = ?, last_error = ?, updated_at = ? "
                    "WHERE upload_id = ?",
                    (status, last_error, ts, upload_id),
                )
            elif clear_error:
                conn.execute(
                    "UPDATE upload_sessions SET status = ?, last_error = NULL, updated_at = ? "
                    "WHERE upload_id = ?",
                    (status, ts, upload_id),
                )
            else:
                conn.execute(
                    "UPDATE upload_sessions SET status = ?, updated_at = ? WHERE upload_id = ?",
                    (status, ts, upload_id),
                )
        return self.get(upload_id)

    def claim_for_assembly(self, upload_id: str) -> bool:
        """Atomically take ownership of completion; False if already claimed.

        Guards the plan's "exclusive claim" so two concurrent ``complete`` calls
        cannot both stream the merge.
        """
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE upload_sessions SET status = ?, updated_at = ? "
                "WHERE upload_id = ? AND status = ?",
                (_STATUS_ASSEMBLING, now_ts(), upload_id, _STATUS_OPEN),
            )
            return int(getattr(cur, "rowcount", 0) or 0) > 0

    def release_to_open(self, upload_id: str, *, last_error: str | None = None) -> None:
        """Give a failed assembly back to the client so it can resume."""
        self.set_status(upload_id, status=_STATUS_OPEN, last_error=last_error)

    def mark_completed(self, upload_id: str, *, final_resource_id: str) -> UploadSessionRow | None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE upload_sessions SET status = ?, final_resource_id = ?, "
                "completed_at = ?, last_error = NULL, updated_at = ? WHERE upload_id = ?",
                (
                    _STATUS_COMPLETED,
                    final_resource_id,
                    ts,
                    ts,
                    upload_id,
                ),
            )
        return self.get(upload_id)

    def delete(self, upload_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM upload_sessions WHERE upload_id = ?", (upload_id,))
            return int(getattr(cur, "rowcount", 0) or 0) > 0

    # --------------------------------------------------------------------- parts

    def add_part(
        self,
        *,
        upload_id: str,
        part_number: int,
        offset: int,
        size: int,
        sha256: str,
    ) -> PartWriteOutcome:
        """Register one durable part.

        Re-sending an identical part is idempotent; a different hash for the
        same part number, or a byte range overlapping a sibling, raises
        ``UploadPartConflict``.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM upload_parts WHERE upload_id = ? AND part_number = ?",
                (upload_id, part_number),
            ).fetchone()
            if existing is not None:
                prior = UploadPartRow.from_row(existing)
                if prior.sha256 == sha256 and prior.offset == offset and prior.size == size:
                    return PartWriteOutcome.IDEMPOTENT
                raise UploadPartConflict(
                    f"part {part_number} already registered with a different digest"
                )

            clash = conn.execute(
                "SELECT part_number FROM upload_parts "
                "WHERE upload_id = ? AND byte_offset < ? AND byte_offset + size > ? LIMIT 1",
                (upload_id, offset + size, offset),
            ).fetchone()
            if clash is not None:
                raise UploadPartConflict(
                    f"part {part_number} byte range overlaps part {int(clash[0])}"
                )

            try:
                conn.execute(
                    "INSERT INTO upload_parts("
                    "upload_id, part_number, byte_offset, size, sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (upload_id, part_number, offset, size, sha256, ts),
                )
            except Exception as exc:  # noqa: BLE001 - re-read to classify the race
                if not _is_unique_violation(exc):
                    raise
                prior_row = self.get_part(upload_id, part_number)
                if (
                    prior_row is not None
                    and prior_row.sha256 == sha256
                    and prior_row.offset == offset
                    and prior_row.size == size
                ):
                    return PartWriteOutcome.IDEMPOTENT
                raise UploadPartConflict(
                    f"part {part_number} already registered with a different digest"
                ) from exc

            conn.execute(
                "UPDATE upload_sessions SET received_bytes = "
                "(SELECT COALESCE(SUM(size), 0) FROM upload_parts WHERE upload_id = ?), "
                "updated_at = ? WHERE upload_id = ?",
                (upload_id, ts, upload_id),
            )
        return PartWriteOutcome.CREATED

    def get_part(self, upload_id: str, part_number: int) -> UploadPartRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM upload_parts WHERE upload_id = ? AND part_number = ?",
                (upload_id, part_number),
            ).fetchone()
        return UploadPartRow.from_row(r) if r else None

    def list_parts(self, upload_id: str) -> list[UploadPartRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_parts WHERE upload_id = ? ORDER BY part_number ASC",
                (upload_id,),
            ).fetchall()
        return map_rows(rows, UploadPartRow)

    def received_ranges(self, upload_id: str) -> list[tuple[int, int]]:
        """``(offset, size)`` pairs of registered parts, in part order."""
        return [(p.offset, p.size) for p in self.list_parts(upload_id)]
