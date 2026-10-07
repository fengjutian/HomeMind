"""Agent execution for scheduled tasks (Stage 4).

What matters here is not that the agent gets called, but what the
scheduler is told afterwards: a task that needs approval parks, a task
whose agent timed out does not claim a clean failure, and a task with no
agent says so rather than reporting success.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_tasks import (
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_WAITING_APPROVAL,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.task_agent import (
    FamilyTaskAgentExecutor,
    build_task_thread_id,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _StubAgentManager:
    """Records calls and replays a canned answer."""

    def __init__(self, answer: Any = None, *, delay: float = 0.0, raise_exc: Exception | None = None):
        self.answer = answer if answer is not None else {"result": "Sorted the hall."}
        self.delay = delay
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, agent_id: str, request: dict[str, Any]) -> Any:
        self.calls.append((agent_id, request))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.answer


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
            "created_at) VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Agent Family", timezone="Asia/Shanghai", locale="zh"
    )
    return {
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
    }


def _task(env: Any, **overrides: Any) -> Any:
    task = env["services"].family_task_repo.create(
        env["family"].id,
        title=overrides.pop("title", "Sort the hall"),
        description=overrides.pop("description", "Put things away"),
        assigned_member_id=None,
        due_at=None,
        created_by=env["owner"].id,
    )
    env["services"].family_task_repo.update(
        task.id,
        task_type=overrides.pop("task_type", "AGENT"),
        agent_id=overrides.pop("agent_id", "agent-home"),
        schedule_at=overrides.pop("schedule_at", 1000),
        **overrides,
    )
    return env["services"].family_task_repo.get(task.id)


# --------------------------------------------------------------- thread ids


def test_each_attempt_gets_its_own_thread() -> None:
    first = build_task_thread_id("01ABC", 1)
    second = build_task_thread_id("01ABC", 2)
    assert first != second
    # The task id is recoverable from the thread alone, which is what
    # makes the trajectory findable without a mapping table.
    assert "01ABC" in first
    assert first.startswith("homemind-task-")


# ------------------------------------------------------------------ outcomes


@pytest.mark.asyncio
async def test_a_successful_run_completes_the_task(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager({"result": "Sorted the hall into three piles."})
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_DONE
    assert "three piles" in outcome.summary
    assert outcome.transaction_id is None


@pytest.mark.asyncio
async def test_the_prompt_carries_the_task_and_the_context(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager()
    executor = FamilyTaskAgentExecutor(
        env["families"],
        agents,
        family_context=lambda task: "Family context: two adults, one child.",
    )
    await executor(_task(env, title="Renew the insurance"))

    agent_id, request = agents.calls[0]
    assert agent_id == "agent-home"
    text = request["input"]["content"]
    assert "Renew the insurance" in text
    assert "two adults" in text
    assert request["thread_id"].startswith("homemind-task-")


@pytest.mark.asyncio
async def test_a_missing_family_context_does_not_fail_the_run(env) -> None:  # noqa: ANN001
    """Context is a nicety; losing it must not lose the task."""

    def boom(task: Any) -> str:
        raise RuntimeError("context service down")

    agents = _StubAgentManager()
    executor = FamilyTaskAgentExecutor(env["families"], agents, family_context=boom)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_DONE


@pytest.mark.asyncio
async def test_a_task_with_no_agent_says_so(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager()
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env, task_type="DEVICE", agent_id=None))
    assert outcome.status == TASK_STATUS_FAILED
    assert "no agent" in (outcome.error or "")
    assert agents.calls == []


@pytest.mark.asyncio
async def test_an_agent_exception_is_a_failure_not_a_crash(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager(raise_exc=RuntimeError("model unavailable"))
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_FAILED
    assert "model unavailable" in (outcome.error or "")


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_a_success(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager({"result": ""})
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_FAILED


@pytest.mark.asyncio
async def test_a_timeout_parks_rather_than_retrying(env) -> None:  # noqa: ANN001
    """A cancelled agent may already have acted; retrying could double it."""
    agents = _StubAgentManager(delay=0.5)
    executor = FamilyTaskAgentExecutor(
        env["families"], agents, timeout_seconds=0.05
    )
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_WAITING_APPROVAL
    assert "timed out" in outcome.summary
    assert "possible_partial_effects" in (outcome.error or "")


# ---------------------------------------------------------------- transactions


@pytest.mark.asyncio
async def test_an_agent_that_needs_approval_parks_the_task(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager(
        {
            "result": (
                "I need to move 40 files into the archive. "
                "transaction_id: 01ARZ3NDEKTSV4RRFFQ69G5FAV"
            )
        }
    )
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_WAITING_APPROVAL
    assert outcome.transaction_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"


@pytest.mark.asyncio
async def test_a_bare_ulid_in_prose_is_not_an_approval_request(env) -> None:  # noqa: ANN001
    """Otherwise any 26-character token would park the task for nothing."""
    agents = _StubAgentManager(
        {"result": "The invoice reference 01ARZ3NDEKTSV4RRFFQ69G5FAV was archived."}
    )
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.transaction_id is None
    assert outcome.status == TASK_STATUS_DONE


@pytest.mark.asyncio
async def test_a_malformed_transaction_id_is_ignored(env) -> None:  # noqa: ANN001
    agents = _StubAgentManager({"result": "transaction_id: not-a-real-id"})
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.transaction_id is None


# ----------------------------------------------------------------- responses


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"result": "done"},
        {"output": "done"},
        {"content": "done"},
        {"text": "done"},
        {"result": {"content": "done"}},
        {"messages": [{"role": "assistant", "content": "done"}]},
    ],
)
async def test_every_response_shape_is_read(env, response: dict[str, Any]) -> None:  # noqa: ANN001
    """The harness's container varies; a task must not fail on shape."""
    agents = _StubAgentManager(response)
    executor = FamilyTaskAgentExecutor(env["families"], agents)
    outcome = await executor(_task(env))
    assert outcome.status == TASK_STATUS_DONE
    assert "done" in outcome.summary
