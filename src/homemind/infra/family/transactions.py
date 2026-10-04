"""Permission-gated family transaction, approval, execution, and audit flow."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import StrEnum
from typing import Any

from homemind.infra.db.repos.family_transactions import (
    FamilyApprovalRow,
    FamilyAuditRow,
    FamilyTransactionRepo,
    FamilyTransactionRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.manager import FamilyManager, PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class TransactionStatus(StrEnum):
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class FamilyTransactionManager:
    def __init__(
        self,
        family: FamilyManager,
        context: FamilyContextManager,
        tasks: FamilyTaskManager,
        repo: FamilyTransactionRepo,
        user_repo: UserRepo,
        filesystem: FamilyFilesystemManager | None = None,
    ) -> None:
        self.family = family
        self.context = context
        self.tasks = tasks
        self.repo = repo
        self.user_repo = user_repo
        self.filesystem = filesystem

    def plan(
        self, family_id: str, user: User, *, action: str, payload: dict[str, Any]
    ) -> tuple[FamilyTransactionRow, FamilyApprovalRow | None]:
        self.family.require_access(family_id, user)
        membership = self.family.repo.get_membership(family_id, user.id)
        if membership is None:
            raise OctopError(ErrorCode.FORBIDDEN, "family membership required")
        transaction = self.repo.create_transaction(
            family_id,
            user.id,
            action,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        effect = self.family.evaluate_permission(
            family_id,
            user,
            subject_member_id=str(membership["member_id"]),
            action=action,
        )
        if action in {"filesystem.move", "filesystem.rename", "filesystem.delete"} and (
            effect is PermissionEffect.ALLOW
        ):
            effect = PermissionEffect.REQUIRE_CONFIRMATION
        if effect is PermissionEffect.DENY:
            transaction = self.repo.set_transaction(transaction.id, TransactionStatus.DENIED)
            self._audit(transaction, user.id, "DENIED", approval="DENY")
            return transaction, None
        if effect is PermissionEffect.REQUIRE_CONFIRMATION:
            transaction = self.repo.set_transaction(
                transaction.id, TransactionStatus.WAITING_APPROVAL
            )
            approval = self.repo.create_approval(transaction)
            self._audit(transaction, user.id, "WAITING_APPROVAL", approval="PENDING")
            return transaction, approval
        return self._execute(transaction), None

    def approve(
        self, family_id: str, approval_id: str, user: User, reason: str | None = None
    ) -> FamilyTransactionRow:
        self.family.require_manager(family_id, user)
        approval = self._pending_approval(family_id, approval_id)
        transaction = self._transaction(family_id, approval.transaction_id)
        requester = self._user(transaction.requested_by)
        membership = self.family.repo.get_membership(family_id, requester.id)
        if membership is None or self.family.evaluate_permission(
            family_id,
            requester,
            subject_member_id=str(membership["member_id"]),
            action=transaction.action,
        ) is PermissionEffect.DENY:
            try:
                self.repo.decide_approval(
                    approval_id,
                    "REJECTED",
                    user.id,
                    "permission no longer allows action",
                )
            except ValueError as exc:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_CONFLICT,
                    "family approval is already decided",
                ) from exc
            transaction = self.repo.set_transaction(
                transaction.id, TransactionStatus.DENIED
            )
            self._audit(transaction, user.id, "DENIED", approval="REJECTED")
            return transaction
        try:
            self.repo.decide_approval(approval_id, "APPROVED", user.id, reason)
        except ValueError as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family approval is already decided",
            ) from exc
        self._audit(transaction, user.id, "APPROVED", approval="APPROVED")
        return self._execute(transaction)

    def reject(
        self, family_id: str, approval_id: str, user: User, reason: str | None = None
    ) -> FamilyTransactionRow:
        self.family.require_manager(family_id, user)
        approval = self._pending_approval(family_id, approval_id)
        try:
            self.repo.decide_approval(approval_id, "REJECTED", user.id, reason)
        except ValueError as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family approval is already decided",
            ) from exc
        transaction = self.repo.set_transaction(
            approval.transaction_id, TransactionStatus.REJECTED
        )
        self._audit(transaction, user.id, "REJECTED", approval="REJECTED")
        return transaction

    def get_transaction(
        self, family_id: str, transaction_id: str, user: User
    ) -> FamilyTransactionRow:
        self.family.require_access(family_id, user)
        return self._transaction(family_id, transaction_id)

    def list_approvals(
        self, family_id: str, user: User, status: str | None = "PENDING"
    ) -> list[FamilyApprovalRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_approvals(family_id, status)

    def list_audit(self, family_id: str, user: User) -> list[FamilyAuditRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_audit(family_id)

    def _execute(self, transaction: FamilyTransactionRow) -> FamilyTransactionRow:
        if transaction.status not in {"PLANNED", "WAITING_APPROVAL"}:
            return transaction
        transaction = self.repo.set_transaction(transaction.id, TransactionStatus.EXECUTING)
        requester = self._user(transaction.requested_by)
        payload = json.loads(transaction.payload_json)
        try:
            result: Any
            if transaction.action == "task.create":
                result = self.tasks.create(transaction.family_id, requester, **payload)
            elif transaction.action == "event.create":
                result = self.context.create_event(transaction.family_id, requester, **payload)
            elif transaction.action == "memory.create":
                result = self.context.create_memory(transaction.family_id, requester, **payload)
            elif transaction.action.startswith("filesystem.") and self.filesystem is not None:
                result = self.filesystem.execute(
                    transaction.family_id,
                    requester,
                    action=transaction.action,
                    transaction_id=transaction.id,
                    payload=payload,
                )
            else:
                raise ValueError(f"unsupported family transaction action: {transaction.action}")
            result_json = json.dumps(asdict(result), ensure_ascii=False, default=str)
            transaction = self.repo.set_transaction(
                transaction.id, TransactionStatus.COMPLETED, result_json=result_json
            )
            target = getattr(result, "id", None) or getattr(result, "destination_path", None)
            self._audit(transaction, requester.id, "SUCCESS", target=target)
            return transaction
        except Exception as exc:
            transaction = self.repo.set_transaction(
                transaction.id, TransactionStatus.FAILED, error=str(exc)
            )
            self._audit(transaction, requester.id, "FAILED", detail={"error": str(exc)})
            return transaction

    def _pending_approval(self, family_id: str, approval_id: str) -> FamilyApprovalRow:
        approval = self.repo.get_approval(approval_id)
        if approval is None or approval.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family approval not found")
        if approval.status != "PENDING":
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family approval is already decided",
            )
        return approval

    def _transaction(self, family_id: str, transaction_id: str) -> FamilyTransactionRow:
        transaction = self.repo.get_transaction(transaction_id)
        if transaction is None or transaction.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family transaction not found")
        return transaction

    def _user(self, user_id: int) -> User:
        row = self.user_repo.get(user_id)
        if row is None or row.disabled:
            raise ValueError("transaction requester not found")
        return User(
            id=row.id,
            username=row.username,
            role=row.role,
            display_name=row.display_name,
            locale=row.locale,
            permissions=row.permissions,
        )

    def _audit(
        self,
        transaction: FamilyTransactionRow,
        user_id: int,
        result: str,
        *,
        target: str | None = None,
        approval: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.repo.add_audit(
            transaction.family_id,
            user_id,
            transaction.action,
            result,
            transaction_id=transaction.id,
            target=target,
            approval=approval,
            detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
        )
