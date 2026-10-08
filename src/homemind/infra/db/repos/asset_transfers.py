"""SQL access for asynchronous device asset transfers.

Two tables back the device download protocol:

* ``homemind_asset_transfers`` -- one row per (device, asset) download
  job, pinned to a single family and to the *resource version* the device
  may resume against.
* ``homemind_asset_transfer_tokens`` -- short-lived, hashed data-plane
  credentials. Plaintext never reaches this layer.

Everything here is SQL and row mapping. State-machine rules (which
transition is legal from where) live in
:mod:`homemind.infra.family.asset_transfers`; the repo only enforces what
is cheapest to enforce in the statement itself -- monotonic progress and
conditional terminal updates.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid


#: States a transfer can still download bytes in.
ACTIVE_STATUSES: tuple[str, ...] = ("PENDING", "ACTIVE")

#: States from which no transition is legal. A device that reports
#: complete twice must not drag the row back into an active state.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}
)


def _row_data(row: DbRow) -> dict[str, Any]:
    # ``sqlite3.Row`` iterates *values*, so column names come from
    # ``.keys()``; ``.get`` is unavailable on it too.
    return (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )


@dataclass(frozen=True)
class AssetTransferRow:
    id: str
    pk: int
    family_id: str
    asset_id: str
    device_id: str
    request_key: str
    status: str
    source_kind: str
    size_bytes: int
    sha256: str
    source_mtime_ns: int | None
    etag: str
    chunk_size: int
    bytes_reported: int
    last_progress_at: int | None
    expires_at: int
    completed_at: int | None
    failure_code: str | None
    failure_detail: str | None
    created_at: int
    updated_at: int

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @classmethod
    def from_row(cls, row: DbRow) -> AssetTransferRow:
        data = _row_data(row)
        return cls(
            id=str(data["transfer_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            asset_id=str(data["asset_id"]),
            device_id=str(data["device_id"]),
            request_key=str(data["request_key"]),
            status=str(data["status"]),
            source_kind=str(data["source_kind"]),
            size_bytes=int(data["size_bytes"]),
            sha256=str(data["sha256"]),
            source_mtime_ns=(
                int(data["source_mtime_ns"])
                if data["source_mtime_ns"] is not None
                else None
            ),
            etag=str(data["etag"]),
            chunk_size=int(data["chunk_size"]),
            bytes_reported=int(data["bytes_reported"]),
            last_progress_at=(
                int(data["last_progress_at"])
                if data["last_progress_at"] is not None
                else None
            ),
            expires_at=int(data["expires_at"]),
            completed_at=(
                int(data["completed_at"]) if data["completed_at"] is not None else None
            ),
            failure_code=data["failure_code"],
            failure_detail=data["failure_detail"],
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


@dataclass(frozen=True)
class AssetTransferTokenRow:
    id: str
    pk: int
    transfer_id: str
    token_hash: str
    expires_at: int
    revoked_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AssetTransferTokenRow:
        data = _row_data(row)
        return cls(
            id=str(data["token_id"]),
            pk=int(data["id"]),
            transfer_id=str(data["transfer_id"]),
            token_hash=str(data["token_hash"]),
            expires_at=int(data["expires_at"]),
            revoked_at=(
                int(data["revoked_at"]) if data["revoked_at"] is not None else None
            ),
            created_at=int(data["created_at"]),
        )


class AssetTransferRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ transfers

    def create(
        self,
        *,
        family_id: str,
        asset_id: str,
        device_id: str,
        request_key: str,
        size_bytes: int,
        sha256: str,
        source_mtime_ns: int | None,
        etag: str,
        chunk_size: int,
        expires_at: int,
        source_kind: str = "LOCAL_FILE",
        now: int | None = None,
    ) -> AssetTransferRow:
        """Insert a ``PENDING`` transfer, or return the existing one.

        The unique ``(device_id, request_key)`` pair is what makes a
        retried create idempotent. The pre-read covers the common case;
        the ``except`` covers the genuine race where two POSTs for the
        same key commit concurrently, which the unique index rejects.
        """
        existing = self.get_by_request_key(device_id, request_key)
        if existing is not None:
            return existing
        transfer_id = new_ulid()
        ts = now_ts() if now is None else now
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO homemind_asset_transfers(transfer_id, family_id, "
                    "asset_id, device_id, request_key, status, source_kind, "
                    "size_bytes, sha256, source_mtime_ns, etag, chunk_size, "
                    "bytes_reported, expires_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        transfer_id,
                        family_id,
                        asset_id,
                        device_id,
                        request_key,
                        source_kind,
                        size_bytes,
                        sha256,
                        source_mtime_ns,
                        etag,
                        chunk_size,
                        expires_at,
                        ts,
                        ts,
                    ),
                )
        except Exception:
            raced = self.get_by_request_key(device_id, request_key)
            if raced is not None:
                return raced
            raise
        row = self.get(transfer_id)
        if row is None:
            raise RuntimeError("asset transfer insert returned no row")
        return row

    def get(self, transfer_id: str) -> AssetTransferRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_asset_transfers WHERE transfer_id = ?",
                (transfer_id,),
            ).fetchone()
        return AssetTransferRow.from_row(row) if row else None

    def get_by_request_key(
        self, device_id: str, request_key: str,
    ) -> AssetTransferRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_asset_transfers "
                "WHERE device_id = ? AND request_key = ?",
                (device_id, request_key),
            ).fetchone()
        return AssetTransferRow.from_row(row) if row else None

    def list_for_device(
        self,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AssetTransferRow]:
        """Newest-first, tie-broken on the public ``transfer_id``.

        The tie-break column is the public ULID on purpose: the bare
        ``id`` column is the integer surrogate key, so keyset paging on
        ``id`` would order differently from what the API hands out.
        """
        sql = "SELECT * FROM homemind_asset_transfers WHERE device_id = ?"
        params: list[object] = [device_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, transfer_id DESC LIMIT ?"
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, AssetTransferRow)

    def count_active_for_device(self, device_id: str, *, now: int) -> int:
        """Non-terminal transfers still holding a claim on the device."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM homemind_asset_transfers "
                "WHERE device_id = ? AND status IN ('PENDING', 'ACTIVE') "
                "AND expires_at > ?",
                (device_id, now),
            ).fetchone()
        return int(_row_data(row)["n"] if row else 0)

    def activate(self, transfer_id: str, now: int) -> AssetTransferRow | None:
        """``PENDING`` → ``ACTIVE`` on the first manifest hand-off."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'ACTIVE', updated_at = ? "
                "WHERE transfer_id = ? AND status = 'PENDING'",
                (now, transfer_id),
            )
        return self.get(transfer_id)

    def advance_progress(
        self, transfer_id: str, bytes_reported: int, now: int,
    ) -> AssetTransferRow | None:
        """Record aggregate progress, monotonically.

        ``bytes_reported <= ?`` in the WHERE clause means a *smaller*
        report is a no-op (the stored maximum survives) while an equal
        one still refreshes ``last_progress_at`` -- an alive but slow
        device must not be expired out from under itself.
        """
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_asset_transfers SET bytes_reported = ?, "
                "last_progress_at = ?, updated_at = ? "
                "WHERE transfer_id = ? AND status IN ('PENDING', 'ACTIVE') "
                "AND bytes_reported <= ?",
                (bytes_reported, now, now, transfer_id, bytes_reported),
            )
        return self.get(transfer_id)

    def complete(self, transfer_id: str, now: int) -> AssetTransferRow | None:
        """Terminal, idempotent. A second call leaves the row alone."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'COMPLETED', "
                "completed_at = ?, bytes_reported = size_bytes, updated_at = ? "
                "WHERE transfer_id = ? AND status IN ('PENDING', 'ACTIVE')",
                (now, now, transfer_id),
            )
        return self.get(transfer_id)

    def fail(
        self, transfer_id: str, code: str, detail: str | None, now: int,
    ) -> AssetTransferRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'FAILED', "
                "failure_code = ?, failure_detail = ?, updated_at = ? "
                "WHERE transfer_id = ? AND status IN ('PENDING', 'ACTIVE')",
                (code, detail, now, transfer_id),
            )
        return self.get(transfer_id)

    def cancel(
        self, transfer_id: str, now: int, *, statuses: tuple[str, ...] = ACTIVE_STATUSES,
    ) -> int:
        if not statuses:
            return 0
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'CANCELLED', updated_at = ? "
                f"WHERE transfer_id = ? AND status IN ({', '.join('?' * len(statuses))})",
                (now, transfer_id, *statuses),
            )
        return int(cursor.rowcount or 0)

    def cancel_for_asset(self, asset_id: str, now: int) -> list[AssetTransferRow]:
        """Cancel every live transfer for a deleted index entry."""
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_transfers WHERE asset_id = ? "
                "AND status IN ('PENDING', 'ACTIVE')",
                (asset_id,),
            ).fetchall()
            conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'CANCELLED', updated_at = ? "
                "WHERE asset_id = ? AND status IN ('PENDING', 'ACTIVE')",
                (now, asset_id),
            )
        affected = map_rows(rows, AssetTransferRow)
        self.revoke_tokens_for_transfers([row.id for row in affected], now)
        return affected

    def cancel_for_device(self, device_id: str, now: int) -> list[AssetTransferRow]:
        """Cancel every live transfer for a revoked / removed device."""
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_transfers WHERE device_id = ? "
                "AND status IN ('PENDING', 'ACTIVE')",
                (device_id,),
            ).fetchall()
            conn.execute(
                "UPDATE homemind_asset_transfers SET status = 'CANCELLED', updated_at = ? "
                "WHERE device_id = ? AND status IN ('PENDING', 'ACTIVE')",
                (now, device_id),
            )
        affected = map_rows(rows, AssetTransferRow)
        self.revoke_tokens_for_transfers([row.id for row in affected], now)
        return affected

    def expire_before(self, now: int, *, limit: int = 200) -> list[AssetTransferRow]:
        """Move lapsed live transfers to ``EXPIRED`` and drop their tokens.

        SQLite has no portable ``UPDATE ... LIMIT``, so the ids are read
        first and the update is driven by that bounded set.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_transfers "
                "WHERE status IN ('PENDING', 'ACTIVE') AND expires_at <= ? "
                "ORDER BY expires_at LIMIT ?",
                (now, limit),
            ).fetchall()
        expired = map_rows(rows, AssetTransferRow)
        if not expired:
            return []
        ids = [row.id for row in expired]
        with self._db.transaction() as conn:
            for transfer_id in ids:
                conn.execute(
                    "UPDATE homemind_asset_transfers SET status = 'EXPIRED', "
                    "updated_at = ? WHERE transfer_id = ? "
                    "AND status IN ('PENDING', 'ACTIVE')",
                    (now, transfer_id),
                )
        self.revoke_tokens_for_transfers(ids, now)
        return expired

    # -------------------------------------------------------------- tokens

    def issue_token(
        self,
        transfer_id: str,
        token_hash: str,
        *,
        expires_at: int,
        now: int | None = None,
    ) -> AssetTransferTokenRow:
        token_id = new_ulid()
        ts = now_ts() if now is None else now
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_asset_transfer_tokens(token_id, transfer_id, "
                "token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (token_id, transfer_id, token_hash, expires_at, ts),
            )
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_asset_transfer_tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("asset transfer token insert returned no row")
        return AssetTransferTokenRow.from_row(row)

    def resolve_active_token(
        self, token_hash: str, *, now: int,
    ) -> AssetTransferTokenRow | None:
        """Look up a live data-plane credential for ``token_hash``.

        The SQL narrows to unexpired, unrevoked rows, but string equality
        in SQLite / PostgreSQL is *not* constant-time, so the match is
        settled with :func:`hmac.compare_digest` over the candidate set.
        Live tokens are bounded by active transfers per device, so the
        scan stays small -- and correctness beats a cached fast path here.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_asset_transfer_tokens "
                "WHERE revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
                (now,),
            ).fetchall()
        candidates = [AssetTransferTokenRow.from_row(row) for row in rows]
        matched: AssetTransferTokenRow | None = None
        for candidate in candidates:
            if hmac.compare_digest(candidate.token_hash, token_hash):
                matched = candidate
        return matched

    def revoke_tokens(self, transfer_id: str, now: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_asset_transfer_tokens SET revoked_at = ? "
                "WHERE transfer_id = ? AND revoked_at IS NULL",
                (now, transfer_id),
            )
        return int(cursor.rowcount or 0)

    def revoke_tokens_for_transfers(self, transfer_ids: list[str], now: int) -> int:
        if not transfer_ids:
            return 0
        total = 0
        for transfer_id in transfer_ids:
            total += self.revoke_tokens(transfer_id, now)
        return total


__all__ = [
    "ACTIVE_STATUSES",
    "AssetTransferRepo",
    "AssetTransferRow",
    "AssetTransferTokenRow",
    "TERMINAL_STATUSES",
]