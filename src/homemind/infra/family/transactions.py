"""Permission-gated family transaction, approval, execution, and audit flow.

Implements the Stage 5 state machine:

    PLANNED → WAITING_APPROVAL → APPROVED → EXECUTING → VERIFYING → COMPLETED
                                     ↘ EXECUTING → COMPENSATING → COMPENSATED
                                     ↘ FAILED
    WAITING_APPROVAL → REJECTED / CANCELLED
    PLANNED → DENIED

Recovery on boot finds any ``EXECUTING`` or ``VERIFYING`` row whose
``lease_expires_at`` has passed and either re-runs (idempotent handlers
make this safe) or marks it ``FAILED_REQUIRES_REVIEW``.
"""

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
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionDecision,
    PermissionEffect,
)
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.base import ActionContext
from homemind.infra.family.transaction_actions.registry import (
    FamilyActionRegistry,
    build_default_action_registry,
)
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class TransactionStatus(StrEnum):
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FAILED_REQUIRES_REVIEW = "FAILED_REQUIRES_REVIEW"
    DENIED = "DENIED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"


# Statuses from which ``retry`` is allowed.
RETRYABLE_FROM_STATUSES: frozenset[TransactionStatus] = frozenset(
    {
        TransactionStatus.FAILED,
        TransactionStatus.FAILED_REQUIRES_REVIEW,
    }
)


DEFAULT_LEASE_TTL_SECONDS = 60


class FamilyTransactionManager:
    def __init__(
        self,
        family: FamilyManager,
        context: FamilyContextManager,
        tasks: FamilyTaskManager,
        repo: FamilyTransactionRepo,
        user_repo: UserRepo,
        filesystem: FamilyFilesystemManager | None = None,
        *,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
        action_registry: FamilyActionRegistry | None = None,
        lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self.family = family
        self.context = context
        self.tasks = tasks
        self.repo = repo
        self.user_repo = user_repo
        self.filesystem = filesystem
        self.permissions = permission_evaluator or FamilyPermissionEvaluator(family.repo)
        self.registry = action_registry or build_default_action_registry(
            family=family,
            context=context,
            tasks=tasks,
            filesystem=filesystem,
        )
        self.lease_ttl_seconds = lease_ttl_seconds

    # --------------------------------------------------------------- planning

    def plan(
        self,
        family_id: str,
        user: User,
        *,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[FamilyTransactionRow, FamilyApprovalRow | None]:
        self.family.require_access(family_id, user)
        membership = self.family.repo.get_membership(family_id, user.id)
        if membership is None:
            raise OctopError(ErrorCode.FORBIDDEN, "family membership required")
        handler = self.registry.get(action)
        if handler is None:
            raise OctopError(
                ErrorCode.BAD_REQUEST,
                f"unsupported family transaction action: {action}",
            )
        transaction = self.repo.create_transaction(
            family_id,
            user.id,
            action,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            idempotency_key=idempotency_key,
        )
        # Idempotent re-plan: if the existing row is already in a
        # terminal or in-flight state, return it without re-executing.
        terminal_statuses = {
            TransactionStatus.COMPLETED.value,
            TransactionStatus.DENIED.value,
            TransactionStatus.REJECTED.value,
            TransactionStatus.CANCELLED.value,
            TransactionStatus.FAILED.value,
            TransactionStatus.FAILED_REQUIRES_REVIEW.value,
        }
        if transaction.status in terminal_statuses:
            _hm_inc("transaction_idempotent_replay_total")
            self._audit(transaction, user.id, "IDEMPOTENT_REPLAY")
            return transaction, None  # type: ignore[return-value]
        # Build a preview so the row carries useful context for audit
        # even if execution never runs.
        try:
            preview = handler.preview(
                ActionContext(
                    family_id=family_id,
                    transaction=transaction,
                    requester=user,
                    payload=payload,
                ),
                payload,
            )
        except Exception as exc:  # noqa: BLE001 — handler is on the hook
            preview = {"preview_error": str(exc)}
        transaction = self.repo.transition(
            transaction.id,
            from_status=TransactionStatus.PLANNED,
            to_status=TransactionStatus.PLANNED,
            result_json=json.dumps(preview, ensure_ascii=False, sort_keys=True, default=str),
        )
        decision = self.permissions.evaluate(
            family_id=family_id,
            user=user,
            action=action,
            space_id=(payload.get("space_id") if isinstance(payload, dict) else None),
        )
        effect = decision.effect
        if effect is PermissionEffect.DENY:
            transaction = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.PLANNED,
                to_status=TransactionStatus.DENIED,
            )
            self._audit(transaction, user.id, "DENIED", approval="DENY")
            return transaction, None  # type: ignore[return-value]
        if effect is PermissionEffect.REQUIRE_CONFIRMATION:
            transaction = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.PLANNED,
                to_status=TransactionStatus.WAITING_APPROVAL,
            )
            approval = self.repo.create_approval(transaction)  # type: ignore[arg-type]
            self._audit(transaction, user.id, "WAITING_APPROVAL", approval="PENDING")
            return transaction, approval  # type: ignore[return-value]
        # Auto-allow: transition to EXECUTING then drive through VERIFYING.
        _hm_inc("transaction_plan_total")
        return self._run_handler(transaction, user, payload), None

    def approve(
        self, family_id: str, approval_id: str, user: User, reason: str | None = None
    ) -> FamilyTransactionRow:
        self.family.require_manager(family_id, user)
        approval = self._pending_approval(family_id, approval_id)
        transaction = self._transaction(family_id, approval.transaction_id)
        requester = self._user(transaction.requested_by)
        decision = self.permissions.evaluate(
            family_id=family_id,
            user=requester,
            action=transaction.action,
        )
        if decision.effect is PermissionEffect.DENY:
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
            transaction = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.WAITING_APPROVAL,
                to_status=TransactionStatus.DENIED,
                approved_at=None,
            )
            self._audit(transaction, user.id, "DENIED", approval="REJECTED")
            return transaction  # type: ignore[return-value]
        # Atomic transition WAITING_APPROVAL → APPROVED. If another
        # approver already moved the row, the CAS returns ``None`` and we
        # surface a conflict.
        updated = self.repo.transition(
            transaction.id,
            from_status=TransactionStatus.WAITING_APPROVAL,
            to_status=TransactionStatus.APPROVED,
            approved_at=self._now_epoch(),
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family approval is already decided",
            )
        try:
            self.repo.decide_approval(approval_id, "APPROVED", user.id, reason)
        except ValueError as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family approval is already decided",
            ) from exc
        self._audit(updated, user.id, "APPROVED", approval="APPROVED")
        payload = json.loads(updated.payload_json)
        _hm_inc("transaction_approve_total")
        return self._run_handler(updated, requester, payload)

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
        transaction = self.repo.transition(
            approval.transaction_id,
            from_status=TransactionStatus.WAITING_APPROVAL,
            to_status=TransactionStatus.REJECTED,
        )
        if transaction is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction is not in WAITING_APPROVAL",
            )
        self._audit(transaction, user.id, "REJECTED", approval="REJECTED")
        _hm_inc("transaction_reject_total")
        return transaction

    def cancel(
        self, family_id: str, transaction_id: str, user: User
    ) -> FamilyTransactionRow:
        """Requester cancels their own pending transaction."""
        self.family.require_access(family_id, user)
        transaction = self._transaction(family_id, transaction_id)
        if transaction.requested_by != user.id:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "only the requester can cancel a family transaction",
            )
        if transaction.status not in {
            TransactionStatus.PLANNED,
            TransactionStatus.WAITING_APPROVAL,
            TransactionStatus.APPROVED,
        }:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                f"family transaction cannot be cancelled from {transaction.status.lower()}",
            )
        updated = self.repo.transition(
            transaction_id,
            from_status=transaction.status,
            to_status=TransactionStatus.CANCELLED,
            cancelled_at=self._now_epoch(),
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction state changed during cancel",
            )
        self._audit(updated, user.id, "CANCELLED")
        _hm_inc("transaction_cancel_total")
        return updated

    def retry(
        self, family_id: str, transaction_id: str, user: User
    ) -> FamilyTransactionRow:
        """Re-run a FAILED / FAILED_REQUIRES_REVIEW transaction."""
        self.family.require_manager(family_id, user)
        transaction = self._transaction(family_id, transaction_id)
        if TransactionStatus(transaction.status) not in RETRYABLE_FROM_STATUSES:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                f"family transaction is not retryable from {transaction.status.lower()}",
            )
        # Reset to PLANNED so ``plan``-style permission flow runs again,
        # then immediately execute because a manager has explicitly
        # approved this retry.
        reset = self.repo.transition(
            transaction_id,
            from_status=transaction.status,
            to_status=TransactionStatus.PLANNED,
        )
        if reset is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction state changed during retry",
            )
        requester = self._user(transaction.requested_by)
        payload = json.loads(transaction.payload_json)
        _hm_inc("transaction_retry_total")
        return self._run_handler(reset, requester, payload)

    def get_transaction(
        self, family_id: str, transaction_id: str, user: User
    ) -> FamilyTransactionRow:
        self.family.require_access(family_id, user)
        return self._transaction(family_id, transaction_id)

    def list_transactions(
        self,
        family_id: str,
        user: User,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[FamilyTransactionRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_transactions(family_id, status=status, limit=limit)

    def list_approvals(
        self, family_id: str, user: User, status: str | None = "PENDING"
    ) -> list[FamilyApprovalRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_approvals(family_id, status)

    def list_audit(self, family_id: str, user: User) -> list[FamilyAuditRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_audit(family_id)

    def _execute(self, transaction: FamilyTransactionRow) -> FamilyTransactionRow:
        """Backward-compatible entry point retained for any caller that
        hasn't migrated to the new state machine. Routes through the
        handler pipeline so behavior matches ``plan``."""
        return self._run_handler(
            transaction,
            self._user(transaction.requested_by),
            json.loads(transaction.payload_json),
        )

    def _run_handler(
        self,
        transaction: FamilyTransactionRow,
        requester: User,
        payload: dict[str, Any],
    ) -> FamilyTransactionRow:
        """Drive the EXECUTING → VERIFYING → COMPLETED state machine."""
        handler = self.registry.get(transaction.action)
        if handler is None:
            updated = self.repo.transition(
                transaction.id,
                from_status=(
                    TransactionStatus.PLANNED,
                    TransactionStatus.APPROVED,
                ),
                to_status=TransactionStatus.FAILED,
                error=f"unsupported transaction action: {transaction.action}",
            )
            self._audit(updated or transaction, requester.id, "FAILED")
            return updated or transaction

        context = ActionContext(
            family_id=transaction.family_id,
            transaction=transaction,
            requester=requester,
            payload=payload,
        )

        # Atomic PLANNED|APPROVED → EXECUTING with a fresh lease.
        executing = self.repo.acquire_lease(
            transaction.id,
            from_status=(
                TransactionStatus.PLANNED.value,
                TransactionStatus.APPROVED.value,
            ),
            owner=f"outbox:{transaction.id}",
            ttl_seconds=self.lease_ttl_seconds,
        )
        if executing is None:
            # Another worker beat us to it; the caller can ``retry`` or
            # rely on recovery to surface the row.
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction already in EXECUTING",
            )
        executed_at = self._now_epoch()
        try:
            result = handler.execute(context, payload)
        except Exception as exc:  # noqa: BLE001 — broad catch by design
            failed = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.EXECUTING.value,
                to_status=TransactionStatus.FAILED,
                error=str(exc),
                lease_owner=None,
                lease_expires_at=None,
                executed_at=executed_at,
            )
            self._audit(
                failed or transaction,
                requester.id,
                "FAILED",
                detail={"error": str(exc)},
            )
            return failed or transaction

        # Verify step: handlers report their own observations.
        verifying = self.repo.transition(
            transaction.id,
            from_status=TransactionStatus.EXECUTING.value,
            to_status=TransactionStatus.VERIFYING.value,
            lease_owner=None,
            lease_expires_at=None,
            executed_at=executed_at,
        )
        if verifying is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction lost its EXECUTING state",
            )
        try:
            verification = handler.verify(context, result)
        except Exception as exc:  # noqa: BLE001 — broad catch by design
            failed = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.VERIFYING.value,
                to_status=TransactionStatus.FAILED_REQUIRES_REVIEW,
                verification_json=json.dumps(
                    {"error": str(exc)}, ensure_ascii=False, sort_keys=True,
                ),
                verified_at=self._now_epoch(),
            )
            self._audit(
                failed or transaction,
                requester.id,
                "FAILED_REQUIRES_REVIEW",
                detail={"error": str(exc)},
            )
            return failed or transaction

        # If the handler reports ``verified=False``, mark for review so
        # a human can reconcile instead of marking it silently COMPLETED.
        verified_flag = bool(verification.get("verified", True)) if isinstance(
            verification, dict
        ) else True
        if not verified_flag:
            needs_review = self.repo.transition(
                transaction.id,
                from_status=TransactionStatus.VERIFYING.value,
                to_status=TransactionStatus.FAILED_REQUIRES_REVIEW,
                verification_json=json.dumps(
                    verification, ensure_ascii=False, sort_keys=True, default=str,
                ),
                verified_at=self._now_epoch(),
            )
            self._audit(needs_review or transaction, requester.id, "NEEDS_REVIEW")
            return needs_review or transaction

        completed = self.repo.transition(
            transaction.id,
            from_status=TransactionStatus.VERIFYING.value,
            to_status=TransactionStatus.COMPLETED,
            result_json=json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            verification_json=json.dumps(
                verification, ensure_ascii=False, sort_keys=True, default=str,
            ),
            verified_at=self._now_epoch(),
        )
        if completed is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family transaction lost its VERIFYING state",
            )
        target = (
            result.get("id")
            or result.get("task_id")
            or result.get("event_id")
            or result.get("memory_id")
            or result.get("destination_path")
        ) if isinstance(result, dict) else None
        self._audit(completed, requester.id, "SUCCESS", target=target)
        return completed

    def recover_running(self, *, now: int | None = None) -> list[FamilyTransactionRow]:
        """Boot-time recovery: scan EXECUTING/VERIFYING rows whose lease
        has expired and either retry or mark for review.

        Safe to call multiple times; the atomic ``acquire_lease`` guards
        against double-recovery.
        """
        timestamp = self._now_epoch() if now is None else now
        affected: list[FamilyTransactionRow] = []
        for transaction in self.repo.list_running_with_expired_lease(now=timestamp):
            self._audit(transaction, transaction.requested_by, "RECOVERY_ATTEMPTED")
            if transaction.status == TransactionStatus.EXECUTING.value:
                # Another worker abandoned mid-execute; try again.
                reset = self.repo.transition(
                    transaction.id,
                    from_status=TransactionStatus.EXECUTING.value,
                    to_status=TransactionStatus.PLANNED,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                if reset is None:
                    continue
                requester = self._user(transaction.requested_by)
                payload = json.loads(transaction.payload_json)
                affected.append(self._run_handler(reset, requester, payload))
            else:
                # VERIFYING with expired lease — the side effect ran but
                # we never confirmed it. Mark for manual review.
                flagged = self.repo.transition(
                    transaction.id,
                    from_status=TransactionStatus.VERIFYING.value,
                    to_status=TransactionStatus.FAILED_REQUIRES_REVIEW,
                    verification_json=json.dumps(
                        {"reason": "lease_expired_during_verify"},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    verified_at=self._now_epoch(),
                    lease_owner=None,
                    lease_expires_at=None,
                )
                if flagged is not None:
                    affected.append(flagged)
                    _hm_inc("transaction_failed_requires_review_total")
            _hm_inc("transaction_recover_total")
        return affected

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _now_epoch() -> int:
        return now_ts()

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
