"""Device-backed transactions: park, resume, escalate (Stage 6).

The behaviour under test is what happens when a household tablet is
asleep. Nothing here may conclude "it worked" from silence, and nothing
may conclude "it failed" from a device that simply took six hours to
wake up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.device_transactions import resume_device_transaction
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import (
    FamilyTransactionManager,
    TransactionStatus,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


class ParkedCommandHandler:
    """A ``device.command`` handler that behaves like the real one.

    Declaring ``awaits_device_result`` is what makes the transaction
    manager park instead of verifying inline, so these tests exercise
    the real code path rather than a mock of it.
    """

    action = "device.command"
    risk = "HIGH"
    awaits_device_result = True

    def __init__(self, verify_result: dict[str, Any] | None = None) -> None:
        self.verify_result = dict(verify_result or {})
        self.verified_with: list[tuple[str, str]] = []
        self.execute_count = 0

    def validate(self, context: Any, payload: dict[str, Any]) -> None:
        return None

    def preview(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {"device_id": payload.get("device_id")}

    def execute(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        self.execute_count += 1
        result: dict[str, Any] = {"command_id": payload["command_id"]}
        if "expires_at" in payload:
            result["expires_at"] = payload["expires_at"]
        return result

    def verify(self, context: Any, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.verified_with.append((entity_id, str(payload.get("status", ""))))
        return dict(self.verify_result)

    def compensate(self, context: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"compensated": False, "reason": "command_already_dispatched"}


class _NoCommandIdHandler(ParkedCommandHandler):
    """Declares it awaits a device but never names the command."""

    def execute(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "QUEUED"}


class _ExplodingVerifyHandler(ParkedCommandHandler):
    def verify(self, context: Any, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("state service down")


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Device Family", timezone="Asia/Shanghai", locale="zh"
    )
    transactions = FamilyTransactionManager(
        families,
        FamilyContextManager(families, services.family_context_repo),
        FamilyTaskManager(families, services.family_task_repo),
        services.family_transaction_repo,
        UserRepo(pool),
    )
    device_repo = services.family_device_repo
    device = device_repo.create(
        family_id=family.id,
        name="Hall tablet",
        device_type="TABLET",
        platform="android",
        capabilities=["reboot"],
    )
    return {
        "pool": pool,
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
        "device": device,
        "transactions": transactions,
        "device_repo": device_repo,
    }


def _queue_command(env: Any, **overrides: Any) -> Any:
    """A dispatched command, as an approved device run would leave it."""
    return env["device_repo"].enqueue_command(
        env["family"].id,
        env["device"].id,
        capability="reboot",
        payload={"now": True},
        requested_by=env["owner"].id,
        expires_at=2**31 - 1,
        initial_status="DISPATCHED",
        **overrides,
    )


def _park(env: Any, handler: Any, **payload_overrides: Any) -> tuple[Any, Any]:
    """Register ``handler``, queue a command, and run the transaction.

    Returns ``(command, transaction_row)`` with the transaction already
    parked. Driving the real ``plan`` path keeps the test honest about
    what an approved command actually does.
    """
    env["transactions"].registry.register("device.command", handler)
    command = _queue_command(env)
    payload = {
        "device_id": env["device"].id,
        "command_id": command.id,
        **payload_overrides,
    }
    transaction, approval = env["transactions"].plan(
        env["family"].id, env["owner"], action="device.command", payload=payload
    )
    if approval is not None:
        # Every device command is REQUIRE_CONFIRMATION, so the real path
        # is plan → approve → execute. Skipping the approval would test
        # a path the product never takes.
        assert approval.id is not None
        transaction = env["transactions"].approve(
            env["family"].id, approval.id, env["owner"]
        )
    row = env["services"].family_transaction_repo.get_transaction(transaction.id)
    assert row is not None
    return command, row


# ------------------------------------------------------------------- parking


def test_an_approved_device_command_parks_instead_of_completing(env) -> None:  # noqa: ANN001
    handler = ParkedCommandHandler({"verified": True})
    _command, row = _park(env, handler)

    assert row.status == TransactionStatus.AWAITING_DEVICE.value
    assert row.device_command_id is not None
    assert row.device_deadline_at is not None
    # verify() must not have run yet — that is the entire point.
    assert handler.verified_with == []
    assert handler.execute_count == 1


def test_a_parked_transaction_is_found_by_its_command_id(env) -> None:  # noqa: ANN001
    command, row = _park(env, ParkedCommandHandler({"verified": True}))
    found = env["services"].family_transaction_repo.get_by_device_command(command.id)
    assert found is not None
    assert found.id == row.id


def test_a_handler_that_awaits_a_device_must_name_the_command(env) -> None:  # noqa: ANN001
    """Without a command id nothing could ever resume the row."""
    _command, row = _park(env, _NoCommandIdHandler())
    assert row.status == TransactionStatus.FAILED.value
    assert "command_id" in (row.error or "")


def test_a_non_device_action_is_unaffected_by_the_park(env) -> None:  # noqa: ANN001
    """Only handlers that opt in park; everything else verifies inline."""
    from homemind.infra.family.transaction_actions.tasks import TaskCreateHandler

    env["transactions"].registry.register(
        "task.create",
        TaskCreateHandler(env["families"], env["transactions"].tasks),
    )
    transaction, approval = env["transactions"].plan(
        env["family"].id,
        env["owner"],
        action="task.create",
        payload={"title": "Buy milk"},
    )
    if approval is not None:
        transaction = env["transactions"].approve(
            env["family"].id, approval.id, env["owner"]
        )
    row = env["services"].family_transaction_repo.get_transaction(transaction.id)
    assert row is not None
    assert row.status == TransactionStatus.COMPLETED.value


# ------------------------------------------------------------------- resuming


def test_a_device_report_resumes_and_completes_the_transaction(env) -> None:  # noqa: ANN001
    handler = ParkedCommandHandler({"verified": True, "state": "on"})
    command, row = _park(env, handler)

    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    resume(command.id, "SUCCEEDED", {"state": "on"})

    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    assert updated is not None
    assert updated.status == TransactionStatus.COMPLETED.value
    assert json.loads(updated.verification_json or "{}")["state"] == "on"
    # The handler was consulted, not bypassed.
    assert handler.verified_with and handler.verified_with[0][0] == command.id


def test_a_report_the_handler_disagrees_with_goes_to_review(env) -> None:  # noqa: ANN001
    handler = ParkedCommandHandler({"verified": False, "expected": "on", "state": "off"})
    command, row = _park(env, handler)

    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    resume(command.id, "SUCCEEDED", {"state": "off"})

    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    # The device said it ran; the handler says the world disagrees.
    # That is a human's problem, not a success.
    assert updated is not None
    assert updated.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value


def test_a_device_reporting_failure_is_not_a_success(env) -> None:  # noqa: ANN001
    handler = ParkedCommandHandler({"verified": False, "reason": "device_reported_failure"})
    command, row = _park(env, handler)

    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    resume(command.id, "FAILED", {"error": "disk full"})

    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    assert updated is not None
    assert updated.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value


def test_a_verify_that_raises_is_never_read_as_success(env) -> None:  # noqa: ANN001
    command, row = _park(env, _ExplodingVerifyHandler())

    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    resume(command.id, "SUCCEEDED", {})

    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    assert updated is not None
    assert updated.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value
    assert "RuntimeError" in (updated.verification_json or "")


def test_resuming_twice_is_a_no_op(env) -> None:  # noqa: ANN001
    command, _row = _park(env, ParkedCommandHandler({"verified": True}))
    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    resume(command.id, "SUCCEEDED", {})
    assert resume(command.id, "SUCCEEDED", {}) is None


def test_a_command_with_no_transaction_resumes_to_nothing(env) -> None:  # noqa: ANN001
    """A device command queued directly by the API has no transaction."""
    resume = resume_device_transaction(env["transactions"], env["device_repo"])
    assert resume("never-issued", "SUCCEEDED", {}) is None


# ---------------------------------------------------------------- escalation


def test_a_silent_device_is_escalated_not_assumed(env) -> None:  # noqa: ANN001
    _command, row = _park(
        env, ParkedCommandHandler({"verified": True}), expires_at=1000
    )

    # Before the deadline, nothing happens.
    assert env["transactions"].expire_overdue_device_awaits(now=900) == 0
    assert (
        env["services"].family_transaction_repo.get_transaction(row.id).status
        == TransactionStatus.AWAITING_DEVICE.value
    )

    # After it, a human is asked to look. Not success, not failure.
    assert env["transactions"].expire_overdue_device_awaits(now=2000) == 1
    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    assert updated.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value
    assert "did not report" in (updated.error or "")


def test_escalation_does_not_touch_a_healthy_await(env) -> None:  # noqa: ANN001
    _command, row = _park(
        env, ParkedCommandHandler({"verified": True}), expires_at=10**9
    )
    assert env["transactions"].expire_overdue_device_awaits(now=2000) == 0
    updated = env["services"].family_transaction_repo.get_transaction(row.id)
    assert updated.status == TransactionStatus.AWAITING_DEVICE.value


def test_escalating_twice_is_a_no_op(env) -> None:  # noqa: ANN001
    _command, _row = _park(env, ParkedCommandHandler({"verified": True}), expires_at=1000)
    assert env["transactions"].expire_overdue_device_awaits(now=2000) == 1
    assert env["transactions"].expire_overdue_device_awaits(now=2000) == 0


# ------------------------------------------------------------------ runtime


def test_the_runtime_callback_is_optional(env) -> None:  # noqa: ANN001
    """The device runtime works with no transaction layer at all."""
    manager = DeviceRuntimeManager(env["families"], env["device_repo"])
    assert manager.on_command_result is None


def test_a_resume_failure_never_loses_the_device_report(env) -> None:  # noqa: ANN001
    class _ExplodingRepo:
        def get_by_device_command(self, command_id: str) -> Any:
            raise RuntimeError("db down")

    class _Transactions:
        repo = _ExplodingRepo()
        registry: Any = None

    resume = resume_device_transaction(_Transactions(), env["device_repo"])
    # Must not raise: a device that reported correctly gets a successful
    # response even when our bookkeeping is broken.
    resume("cmd-1", "SUCCEEDED", {})