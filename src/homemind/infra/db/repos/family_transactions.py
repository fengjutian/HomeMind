"""Persistence for family transactions, approvals, and audit records."""

from __future__ import annotations

from dataclasses import dataclass

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

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTransactionRow:
        return cls(
            id=str(row["transaction_id"]), family_id=str(row["family_id"]),
            requested_by=int(row["requested_by"]), action=str(row["action"]),
            payload_json=str(row["payload_json"]), status=str(row["status"]),
            result_json=row["result_json"], error=row["error"],
            created_at=int(row["created_at"]), updated_at=int(row["updated_at"]),
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

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyApprovalRow:
        return cls(
            id=str(row["approval_id"]), transaction_id=str(row["transaction_id"]),
            family_id=str(row["family_id"]), status=str(row["status"]),
            requested_by=int(row["requested_by"]),
            decided_by=int(row["decided_by"]) if row["decided_by"] is not None else None,
            reason=row["reason"], created_at=int(row["created_at"]),
            decided_at=int(row["decided_at"]) if row["decided_at"] is not None else None,
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
        self, family_id: str, requested_by: int, action: str, payload_json: str
    ) -> FamilyTransactionRow:
        transaction_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_transactions(transaction_id, family_id, "
                "requested_by, action, payload_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'PLANNED', ?, ?)",
                (
                    transaction_id,
                    family_id,
                    requested_by,
                    action,
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_transaction(transaction_id)  # type: ignore[return-value]

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

    def create_approval(self, transaction: FamilyTransactionRow) -> FamilyApprovalRow:
        approval_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_approvals(approval_id, transaction_id, family_id, "
                "requested_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    approval_id,
                    transaction.id,
                    transaction.family_id,
                    transaction.requested_by,
                    timestamp,
                ),
            )
        return self.get_approval(approval_id)  # type: ignore[return-value]

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
