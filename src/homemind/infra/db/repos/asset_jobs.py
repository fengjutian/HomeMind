"""Persistence for persistent asset jobs and their per-item rows (Stage 6).

A job owns an ordered list of items; workers claim the job with a lease,
process items in batches, and persist a cursor after every batch so a
restart resumes instead of restarting. Every mutation that changes job
state is a compare-and-swap so two workers cannot both advance the same
row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

# Job types. ``SCAN`` walks an asset source; the rest operate on assets
# that are already indexed.
JOB_TYPE_SCAN = "SCAN"
JOB_TYPE_METADATA = "METADATA"
JOB_TYPE_THUMBNAIL = "THUMBNAIL"
JOB_TYPE_VISION = "VISION"
JOB_TYPE_EMBEDDING = "EMBEDDING"
JOB_TYPE_FACE_MATCH = "FACE_MATCH"
JOB_TYPE_REINDEX = "REINDEX"

JOB_TYPES: frozenset[str] = frozenset(
    {
        JOB_TYPE_SCAN,
        JOB_TYPE_METADATA,
        JOB_TYPE_THUMBNAIL,
        JOB_TYPE_VISION,
        JOB_TYPE_EMBEDDING,
        JOB_TYPE_FACE_MATCH,
        JOB_TYPE_REINDEX,
    }
)

# Job lifecycle.
JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_PAUSED = "PAUSED"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUS_CANCELLED = "CANCELLED"

# Per-item lifecycle.
ITEM_STATUS_PENDING = "PENDING"
ITEM_STATUS_SUCCEEDED = "SUCCEEDED"
ITEM_STATUS_SKIPPED = "SKIPPED"
ITEM_STATUS_FAILED = "FAILED"


@dataclass(frozen=True)
class AssetJobRow:
    id: str
    pk: int
    family_id: str
    source_id: str | None
    job_type: str
    status: str
    cursor_json: str
    config_json: str
    total_items: int
    processed_items: int
    succeeded_items: int
    skipped_items: int
    failed_items: int
    error_summary: str | None
    requested_by: int
    created_at: int
    started_at: int | None
    updated_at: int
    finished_at: int | None
    lease_owner: str | None
    lease_expires_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> AssetJobRow:
        # ``sqlite3.Row`` iterates *values*, so column names must come
        # from ``.keys()``; ``.get`` is not available on it either.
        # ruff SIM118 wants ``for key in row`` here, which would yield
        # values and raise IndexError — keep ``.keys()``.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        return cls(
            id=str(data["job_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            source_id=data["source_id"],
            job_type=str(data["job_type"]),
            status=str(data["status"]),
            cursor_json=str(data["cursor_json"]),
            config_json=str(data["config_json"]),
            total_items=int(data["total_items"]),
            processed_items=int(data["processed_items"]),
            succeeded_items=int(data["succeeded_items"]),
            skipped_items=int(data["skipped_items"]),
            failed_items=int(data["failed_items"]),
            error_summary=data["error_summary"],
            requested_by=int(data["requested_by"]),
            created_at=int(data["created_at"]),
            started_at=_opt_int("started_at"),
            updated_at=int(data["updated_at"]),
            finished_at=_opt_int("finished_at"),
            lease_owner=data["lease_owner"],
            lease_expires_at=_opt_int("lease_expires_at"),
        )


@dataclass(frozen=True)
class AssetJobItemRow:
    id: str
    pk: int
    job_id: str
    asset_id: str | None
    source_path: str
    status: str
    attempt_count: int
    error: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AssetJobItemRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["item_id"]),
            pk=int(data["id"]),
            job_id=str(data["job_id"]),
            asset_id=data["asset_id"],
            source_path=str(data["source_path"]),
            status=str(data["status"]),
            attempt_count=int(data["attempt_count"]),
            error=data["error"],
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class AssetJobRepo:
    """SQL access for ``homemind_asset_jobs`` and its items."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------------ jobs

    def create_job(
        self,
        family_id: str,
        *,
        job_type: str,
        requested_by: int,
        source_id: str | None = None,
        cursor_json: str = "{}",
        config_json: str = "{}",
    ) -> AssetJobRow:
        job_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_asset_jobs(job_id, family_id, source_id, job_type, "
                "status, cursor_json, config_json, total_items, requested_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'PENDING', ?, ?, 0, ?, ?, ?)",
                (
                    job_id,
                    family_id,
                    source_id,
                    job_type,
                    cursor_json,
                    config_json,
                    requested_by,
                    ts,
                    ts,
                ),
            )
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> AssetJobRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_asset_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return AssetJobRow.from_row(row) if row else None

    def list_jobs(
        self,
        family_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AssetJobRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_jobs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, AssetJobRow)

    def add_items(
        self,
        job_id: str,
        paths: list[str],
        asset_ids: dict[str, str] | None = None,
    ) -> int:
        """Bulk-insert item rows. Batched so a 100k-file source never
        builds one enormous statement."""
        if not paths:
            return 0
        lookup = asset_ids or {}
        ts = now_ts()
        inserted = 0
        with self._db.transaction() as conn:
            for start in range(0, len(paths), _ITEM_INSERT_BATCH):
                chunk = paths[start : start + _ITEM_INSERT_BATCH]
                rows = [
                    (new_ulid(), job_id, lookup.get(path), path, ITEM_STATUS_PENDING, ts, ts)
                    for path in chunk
                ]
                conn.executemany(
                    "INSERT INTO homemind_asset_job_items(item_id, job_id, asset_id, "
                    "source_path, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                inserted += len(rows)
            conn.execute(
                "UPDATE homemind_asset_jobs SET total_items = "
                "(SELECT COUNT(*) FROM homemind_asset_job_items WHERE job_id = ?), "
                "updated_at = ? WHERE job_id = ?",
                (job_id, ts, job_id),
            )
        return inserted

    def claim_job(
        self,
        job_id: str,
        *,
        owner: str,
        ttl_seconds: int,
        now: int | None = None,
    ) -> AssetJobRow | None:
        """CAS ``PENDING|PAUSED-resumed → RUNNING`` and take a lease.

        Returns ``None`` when another worker already holds it, which is
        how the single-owner guarantee is enforced: the UPDATE guard makes
        exactly one writer observe the transition.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_asset_jobs SET status = 'RUNNING', "
                "started_at = COALESCE(started_at, ?), updated_at = ?, "
                "lease_owner = ?, lease_expires_at = ? "
                "WHERE job_id = ? AND status IN ('PENDING', 'RUNNING') "
                "  AND (lease_owner IS NULL OR lease_expires_at IS NULL "
                "       OR lease_expires_at <= ?)",
                (timestamp, timestamp, owner, timestamp + ttl_seconds, job_id, timestamp),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_job(job_id)

    def claim_next_job(
        self,
        family_id: str | None,
        *,
        owner: str,
        ttl_seconds: int,
        now: int | None = None,
    ) -> AssetJobRow | None:
        """Claim the oldest runnable job, optionally scoped to a family."""
        timestamp = now_ts() if now is None else now
        clauses = [
            "status = 'PENDING'",
            "(lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
        ]
        params: list[Any] = [timestamp]
        if family_id is not None:
            clauses.append("family_id = ?")
            params.append(family_id)
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT job_id FROM homemind_asset_jobs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, id ASC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return None
        data = {key: row[key] for key in row.keys()}  # noqa: SIM118
        return self.claim_job(str(data["job_id"]), owner=owner, ttl_seconds=ttl_seconds, now=now)

    def save_progress(
        self,
        job_id: str,
        *,
        owner: str,
        cursor_json: str | None = None,
        processed_delta: int = 0,
        succeeded_delta: int = 0,
        skipped_delta: int = 0,
        failed_delta: int = 0,
        error_summary: str | None = None,
        now: int | None = None,
    ) -> AssetJobRow | None:
        """Persist counters + cursor after a batch, guarded by the lease.

        The ``lease_owner = ?`` clause means a worker whose lease was
        stolen while it was working cannot overwrite the new owner's
        progress — its batch result is discarded, which is correct because
        the new owner re-runs those items.
        """
        timestamp = now_ts() if now is None else now
        sets = [
            "processed_items = processed_items + ?",
            "succeeded_items = succeeded_items + ?",
            "skipped_items = skipped_items + ?",
            "failed_items = failed_items + ?",
            "updated_at = ?",
        ]
        params: list[Any] = [
            processed_delta,
            succeeded_delta,
            skipped_delta,
            failed_delta,
            timestamp,
        ]
        if cursor_json is not None:
            sets.append("cursor_json = ?")
            params.append(cursor_json)
        if error_summary is not None:
            sets.append("error_summary = ?")
            params.append(error_summary)
        params.extend((job_id, owner))
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE homemind_asset_jobs SET {', '.join(sets)} "
                "WHERE job_id = ? AND lease_owner = ?",
                params,
            )
        return self.get_job(job_id)

    def renew_lease(
        self,
        job_id: str,
        *,
        owner: str,
        ttl_seconds: int,
        now: int | None = None,
    ) -> AssetJobRow | None:
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_asset_jobs SET lease_expires_at = ?, updated_at = ? "
                "WHERE job_id = ? AND lease_owner = ?",
                (timestamp + ttl_seconds, timestamp, job_id, owner),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_job(job_id)

    def set_status(
        self,
        job_id: str,
        *,
        to_status: str,
        from_status: str | tuple[str, ...],
        error_summary: str | None = None,
        clear_lease: bool = False,
        now: int | None = None,
    ) -> AssetJobRow | None:
        timestamp = now_ts() if now is None else now
        from_clause = (
            f"status = '{from_status}'"
            if isinstance(from_status, str)
            else "status IN (" + ", ".join(f"'{s}'" for s in from_status) + ")"
        )
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [to_status, timestamp]
        if to_status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            sets.append("finished_at = ?")
            params.append(timestamp)
        if clear_lease or to_status in {
            JOB_STATUS_COMPLETED,
            JOB_STATUS_FAILED,
            JOB_STATUS_CANCELLED,
        }:
            # A finished job no longer owns work, and a recovered job
            # must be claimable again — either way the lease goes.
            sets.append("lease_owner = NULL")
            sets.append("lease_expires_at = NULL")
        if error_summary is not None:
            sets.append("error_summary = ?")
            params.append(error_summary)
        params.append(job_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE homemind_asset_jobs SET {', '.join(sets)} "
                f"WHERE job_id = ? AND {from_clause}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_job(job_id)

    # ---------------------------------------------------------------- items

    def list_pending_items(
        self,
        job_id: str,
        *,
        limit: int = 100,
    ) -> list[AssetJobItemRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_job_items WHERE job_id = ? "
                "AND status = 'PENDING' ORDER BY id ASC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return map_rows(rows, AssetJobItemRow)

    def list_failed_items(
        self,
        job_id: str,
        *,
        limit: int = 100,
    ) -> list[AssetJobItemRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_job_items WHERE job_id = ? "
                "AND status = 'FAILED' ORDER BY id ASC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return map_rows(rows, AssetJobItemRow)

    def list_items(
        self,
        job_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[AssetJobItemRow]:
        clauses = ["job_id = ?"]
        params: list[Any] = [job_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_job_items WHERE "
                + " AND ".join(clauses)
                + " ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, AssetJobItemRow)

    def mark_item(
        self,
        item_id: str,
        *,
        status: str,
        error: str | None = None,
        increment_attempt: bool = True,
        now: int | None = None,
    ) -> AssetJobItemRow | None:
        timestamp = now_ts() if now is None else now
        sets = ["status = ?", "error = ?", "updated_at = ?"]
        params: list[Any] = [status, error, timestamp]
        if increment_attempt:
            sets.append("attempt_count = attempt_count + 1")
        params.append(item_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE homemind_asset_job_items SET {', '.join(sets)} WHERE item_id = ?",
                params,
            )
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_asset_job_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        return AssetJobItemRow.from_row(row) if row else None

    def reset_failed_items(self, job_id: str) -> int:
        """Move ``FAILED`` items back to ``PENDING`` for a targeted retry.

        Items that already succeeded keep their terminal state, so a retry
        only redoes what actually failed.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_asset_job_items SET status = 'PENDING', error = NULL, "
                "updated_at = ? WHERE job_id = ? AND status = 'FAILED'",
                (ts, job_id),
            )
        return int(cursor.rowcount or 0)


_ITEM_INSERT_BATCH = 500


__all__ = [
    "ITEM_STATUS_FAILED",
    "ITEM_STATUS_PENDING",
    "ITEM_STATUS_SKIPPED",
    "ITEM_STATUS_SUCCEEDED",
    "JOB_STATUS_CANCELLED",
    "JOB_STATUS_COMPLETED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_PAUSED",
    "JOB_STATUS_PENDING",
    "JOB_STATUS_RUNNING",
    "JOB_TYPES",
    "JOB_TYPE_EMBEDDING",
    "JOB_TYPE_FACE_MATCH",
    "JOB_TYPE_METADATA",
    "JOB_TYPE_REINDEX",
    "JOB_TYPE_SCAN",
    "JOB_TYPE_THUMBNAIL",
    "JOB_TYPE_VISION",
    "AssetJobItemRow",
    "AssetJobRepo",
    "AssetJobRow",
]
