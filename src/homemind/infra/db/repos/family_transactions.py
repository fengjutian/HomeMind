"""Persistence for family transactions, approvals, and audit records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyTransactionRow:
    id: str
    family_id: str
    requested_by: int
    action: str
    payload_json: str
    status: str
    result_json: str | None
    error: str | None
    created_at: int
    updated_at: int
    idempotency_key: str | None = None
    preview_json: str | None = None
    verification_json: str | None = None
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    approved_at: int | None = None
    executed_at: int | None = None
    verified_at: int | None = None
    cancelled_at: int | None = None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTransactionRow:
        def _int(value: Any) -> int | None:
            return int(value) if value is not None else None

        return cls(
            id=str(row["transaction_id"]),
            family_id=str(row["family_id"]),
            requested_by=int(row["requested_by"]),
            action=str(row["action"]),
            payload_json=str(row["payload_json"]),
            status=str(row["status"]),
            result_json=row["result_json"],
            error=row["error"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            idempotency_key=row["idempotency_key"],
            preview_json=row["preview_json"],
            verification_json=row["verification_json"],
            attempt_count=int(row["attempt_count"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=_int(row["lease_expires_at"]),
            approved_at=_int(row["approved_at"]),
            executed_at=_int(row["executed_at"]),
            verified_at=_int(row["verified_at"]),
            cancelled_at=_int(row["cancelled_at"]),
        )


@dataclass(frozen=True)
class FamilyApprovalRow:
    id: str
    transaction_id: str
    family_id: str
    status: str
    requested_by: int
    decided_by: int | None
    reason: str | None
    created_at: int
    decided_at: int | None
    approval_expires_at: int | None = None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyApprovalRow:
        expires = row.get("approval_expires_at") if hasattr(row, "get") else row["approval_expires_at"]  # type: ignore[index]
        expires_int: int | None = None
        if expires is not None:
            try:
                expires_int = int(expires)
            except (TypeError, ValueError):
                expires_int = None
        return cls(
            id=str(row["approval_id"]), transaction_id=str(row["transaction_id"]),
            family_id=str(row["family_id"]), status=str(row["status"]),
            requested_by=int(row["requested_by"]),
            decided_by=int(row["decided_by"]) if row["decided_by"] is not None else None,
            reason=row["reason"], created_at=int(row["created_at"]),
            decided_at=int(row["decided_at"]) if row["decided_at"] is not None else None,
            approval_expires_at=expires_int,
        )


@dataclass(frozen=True)
class FamilyAuditRow:
    id: str
    family_id: str
    user_id: int
    transaction_id: str | None
    action: str
    target: str | None
    result: str
    approval: str | None
    detail_json: str
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyAuditRow:
        return cls(
            id=str(row["audit_id"]), family_id=str(row["family_id"]),
            user_id=int(row["user_id"]), transaction_id=row["transaction_id"],
            action=str(row["action"]), target=row["target"], result=str(row["result"]),
            approval=row["approval"], detail_json=str(row["detail_json"]),
            created_at=int(row["created_at"]),
        )


class FamilyTransactionRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create_transaction(
        self,
        family_id: str,
        requested_by: int,
        action: str,
        payload_json: str,
        *,
        idempotency_key: str | None = None,
        preview_json: str = "{}",
    ) -> FamilyTransactionRow:
        # Idempotency: if (family_id, idempotency_key) already maps to a
        # transaction, return the existing one instead of inserting a new
        # row. SQLite/PG both enforce the partial unique index.
        if idempotency_key:
            existing = self.find_by_idempotency_key(family_id, idempotency_key)
            if existing is not None:
                return existing
        transaction_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_transactions(transaction_id, family_id, "
                "requested_by, action, payload_json, status, created_at, updated_at, "
                "idempotency_key, preview_json) "
                "VALUES (?, ?, ?, ?, ?, 'PLANNED', ?, ?, ?, ?)",
                (
                    transaction_id,
                    family_id,
                    requested_by,
                    action,
                    payload_json,
                    timestamp,
                    timestamp,
                    idempotency_key,
                    preview_json,
                ),
            )
        return self.get_transaction(transaction_id)  # type: ignore[return-value]

    def find_by_idempotency_key(
        self, family_id: str, idempotency_key: str,
    ) -> FamilyTransactionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_transactions "
                "WHERE family_id = ? AND idempotency_key = ?",
                (family_id, idempotency_key),
            ).fetchone()

    def get_approval_for_transaction(self, transaction_id: str) -> FamilyApprovalRow | None:
        """Return the (still-pending) approval for ``transaction_id``.

        Used by ``plan``'s idempotency path so a re-plan that lands
        on ``WAITING_APPROVAL`` returns the same approval row instead
        of minting a second one.
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_approvals WHERE transaction_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (transaction_id,),
            ).fetchone()
        return FamilyTransactionRow.from_row(row) if row else None

    def get_transaction(self, transaction_id: str) -> FamilyTransactionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return FamilyTransactionRow.from_row(row) if row else None

    def set_transaction(
        self, transaction_id: str, status: str, *, result_json: str | None = None,
        error: str | None = None,
    ) -> FamilyTransactionRow:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_transactions SET status = ?, result_json = ?, "
                "error = ?, updated_at = ? WHERE transaction_id = ?",
                (status, result_json, error, now_ts(), transaction_id),
            )
        return self.get_transaction(transaction_id)  # type: ignore[return-value]

    def transition(
        self,
        transaction_id: str,
        *,
        from_status: str | tuple[str, ...],
        to_status: str,
        result_json: str | None = None,
        verification_json: str | None = None,
        error: str | None = None,
        lease_owner: str | None = None,
        lease_expires_at: int | None = None,
        approved_at: int | None = None,
        executed_at: int | None = None,
        verified_at: int | None = None,
        cancelled_at: int | None = None,
        increment_attempt: bool = False,
    ) -> FamilyTransactionRow | None:
        """Atomic compare-and-swap status transition.

        Returns the updated row, or ``None`` if no row matched the
        ``from_status`` guard (i.e. another worker already moved the
        transaction forward).
        """
        from_clause = (
            f"status = '{from_status}'"
            if isinstance(from_status, str)
            else "status IN (" + ", ".join(f"'{s}'" for s in from_status) + ")"
        )
        sets = [
            "status = ?",
            "updated_at = ?",
        ]
        params: list[object] = [to_status, now_ts()]
        if result_json is not None:
            sets.append("result_json = ?"); params.append(result_json)
        if verification_json is not None:
            sets.append("verification_json = ?"); params.append(verification_json)
        if error is not None:
            sets.append("error = ?"); params.append(error)
        if lease_owner is not None:
            sets.append("lease_owner = ?"); params.append(lease_owner)
        if lease_expires_at is not None:
            sets.append("lease_expires_at = ?"); params.append(lease_expires_at)
        if approved_at is not None:
            sets.append("approved_at = ?"); params.append(approved_at)
        if executed_at is not None:
            sets.append("executed_at = ?"); params.append(executed_at)
        if verified_at is not None:
            sets.append("verified_at = ?"); params.append(verified_at)
        if cancelled_at is not None:
            sets.append("cancelled_at = ?"); params.append(cancelled_at)
        if increment_attempt:
            sets.append("attempt_count = attempt_count + 1")
        params.append(transaction_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE homemind_family_transactions SET {', '.join(sets)} "
                f"WHERE transaction_id = ? AND {from_clause}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_transaction(transaction_id)

    def list_transactions(
        self,
        family_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[FamilyTransactionRow]:
        clauses = ["family_id = ?"]
        params: list[object] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_transactions WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyTransactionRow)

    def list_running_with_expired_lease(
        self, *, now: int, statuses: tuple[str, ...] = ("EXECUTING", "VERIFYING"),
    ) -> list[FamilyTransactionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_transactions WHERE "
                "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ") "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (now,),
            ).fetchall()
        return map_rows(rows, FamilyTransactionRow)

    def acquire_lease(
        self,
        transaction_id: str,
        *,
        from_status: str,
        owner: str,
        ttl_seconds: int,
    ) -> FamilyTransactionRow | None:
        """Take a lease on a transaction from ``from_status``."""
        return self.transition(
            transaction_id,
            from_status=from_status,
            to_status="EXECUTING",
            lease_owner=owner,
            lease_expires_at=now_ts() + ttl_seconds,
            increment_attempt=True,
        )

    def release_lease(
        self, transaction_id: str, *, to_status: str,
    ) -> FamilyTransactionRow | None:
        return self.transition(
            transaction_id,
            from_status="EXECUTING",
            to_status=to_status,
            lease_owner=None,
            lease_expires_at=None,
        )

    def create_approval(
        self,
        transaction: FamilyTransactionRow,
        *,
        approval_expires_at: int | None = None,
    ) -> FamilyApprovalRow:
        approval_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_approvals(approval_id, transaction_id, family_id, "
                "requested_by, created_at, approval_expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    transaction.id,
                    transaction.family_id,
                    transaction.requested_by,
                    timestamp,
                    approval_expires_at,
                ),
            )
        return self.get_approval(approval_id)  # type: ignore[return-value]

    def expire_pending_approvals(self, *, now: int | None = None) -> int:
        """Mark every ``PENDING`` approval whose ``approval_expires_at``
        has passed as ``EXPIRED`` and surface the underlying
        transaction as ``CANCELLED`` so the dashboard stops showing
        the pending badge.

        Returns the number of approvals transitioned.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_approvals SET status = 'EXPIRED', decided_at = ?, "
                "reason = 'expired' "
                "WHERE status = 'PENDING' AND approval_expires_at IS NOT NULL "
                "  AND approval_expires_at <= ?",
                (timestamp, timestamp),
            )
            transitioned = cursor.rowcount or 0
            if transitioned:
                # Cancel the parent transaction so its lifecycle stops
                # progressing. ``acquire_lease`` refuses to flip a
                # ``CANCELLED`` row to ``EXECUTING``, so a worker that
                # raced the sweep cannot resume it.
                conn.execute(
                    "UPDATE homemind_family_transactions SET status = 'CANCELLED', "
                    "cancelled_at = ? WHERE transaction_id IN ("
                    "  SELECT transaction_id FROM homemind_family_approvals "
                    "  WHERE status = 'EXPIRED' AND decided_at = ?"
                    ") AND status IN ('WAITING_APPROVAL', 'PLANNED')",
                    (timestamp, timestamp),
                )
        return int(transitioned)

    def get_approval(self, approval_id: str) -> FamilyApprovalRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return FamilyApprovalRow.from_row(row) if row else None

    def list_approvals(self, family_id: str, status: str | None) -> list[FamilyApprovalRow]:
        sql, params = "SELECT * FROM homemind_family_approvals WHERE family_id = ?", [family_id]
        if status is not None:
            sql += " AND status = ?"; params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, FamilyApprovalRow)

    def decide_approval(
        self, approval_id: str, status: str, decided_by: int, reason: str | None
    ) -> FamilyApprovalRow:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_approvals SET status = ?, decided_by = ?, reason = ?, "
                "decided_at = ? WHERE approval_id = ? AND status = 'PENDING'",
                (status, decided_by, reason, now_ts(), approval_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("family approval is already decided")
        return self.get_approval(approval_id)  # type: ignore[return-value]

    def add_audit(
        self, family_id: str, user_id: int, action: str, result: str, *,
        transaction_id: str | None, target: str | None = None,
        approval: str | None = None, detail_json: str = "{}",
    ) -> FamilyAuditRow:
        audit_id = new_ulid()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_audit_log(audit_id, family_id, user_id, "
                "transaction_id, action, target, result, approval, detail_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (audit_id, family_id, user_id, transaction_id, action, target, result,
                 approval, detail_json, now_ts()),
            )
        return self.get_audit(audit_id)  # type: ignore[return-value]

    def get_audit(self, audit_id: str) -> FamilyAuditRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_audit_log WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        return FamilyAuditRow.from_row(row) if row else None

    def list_audit(self, family_id: str) -> list[FamilyAuditRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_audit_log WHERE family_id = ? "
                "ORDER BY created_at DESC, id DESC", (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyAuditRow)
