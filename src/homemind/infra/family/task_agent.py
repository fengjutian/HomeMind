"""Agent execution for scheduled family tasks (Stage 4).

Wiring only — the scheduler owns the lease, the retry policy and the
recurrence; this module owns the conversation. Three rules decide how a
task is handed to an agent:

* **A task gets its own thread.** Two runs of the same recurring task
  must not inherit each other's context, and the task id is embedded in
  the thread id so the trajectory can be found later without a separate
  index.
* **The agent proposes; a transaction disposes.** If the agent's answer
  names a transaction, the task parks in ``WAITING_APPROVAL`` rather
  than claiming the work is done. The scheduler already understands
  that state, and this is where it is produced.
* **A timeout is not a failure.** Cancelling a running agent does not
  undo whatever side effects it already triggered. A timeout therefore
  reports "may have acted" and is escalated, never silently retried
  into a second partial execution.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homemind.infra.db.repos.family_tasks import (
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_WAITING_APPROVAL,
    FamilyTaskRow,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.task_scheduler import TaskExecutionResult

logger = logging.getLogger(__name__)

#: A scheduled task is not an interactive chat. Long enough for a real
#: plan, short enough that a wedged agent does not hold a lease for an
#: hour — the scheduler's lease is shorter than this on purpose, so a
#: timeout is noticed rather than waited out.
DEFAULT_AGENT_TIMEOUT_SECONDS = 600

#: The prefix that makes a task's threads findable in the trajectory
#: archive without a separate mapping table.
THREAD_ID_PREFIX = "homemind-task"


def build_task_thread_id(task_id: str, attempt: int) -> str:
    """A stable, searchable thread id for one attempt of one task."""
    return f"{THREAD_ID_PREFIX}-{task_id}-{attempt}"


@dataclass(frozen=True)
class AgentRunResult:
    """What came back from one agent invocation."""

    status: str
    summary: str = ""
    transaction_id: str | None = None


class FamilyTaskAgentExecutor:
    """Run one scheduled task through an Octop agent."""

    def __init__(
        self,
        family: FamilyManager,
        agent_manager: Any,
        *,
        family_context: Callable[[FamilyTaskRow], str] | None = None,
        timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    ) -> None:
        self.family = family
        self.agent_manager = agent_manager
        # Optional so a deployment without the context renderer still
        # runs tasks; the prompt just carries less family background.
        self._family_context = family_context
        self._timeout = timeout_seconds

    async def __call__(self, task: FamilyTaskRow) -> TaskExecutionResult:
        agent_id = task.agent_id
        if not agent_id:
            # Not an error: a DEVICE task has no agent and should have
            # been executed by something else. Saying so beats claiming
            # success.
            return TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                error="task has no agent assigned",
            )

        request = self._build_request(task)
        try:
            response = await asyncio.wait_for(
                self.agent_manager.call(agent_id, request),
                timeout=self._timeout,
            )
        except TimeoutError:
            # The agent may have triggered side effects before we
            # cancelled it. Retrying blind could double them, so this
            # is reported as needing review rather than as a failure
            # that invites another attempt.
            logger.warning(
                "TaskExecutor: task %s timed out after %ss; the agent may "
                "have acted before cancellation",
                task.id,
                self._timeout,
            )
            return TaskExecutionResult(
                status=TASK_STATUS_WAITING_APPROVAL,
                summary=f"agent timed out after {self._timeout}s",
                error="agent_timeout_possible_partial_effects",
            )
        except Exception as exc:  # noqa: BLE001 — a failing agent is an outcome
            logger.exception("TaskExecutor: task %s raised", task.id)
            return TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )

        outcome = self._interpret(response)
        if outcome.transaction_id is not None:
            # The agent hit something it may not do alone. Park the task
            # rather than marking it finished.
            return TaskExecutionResult(
                status=TASK_STATUS_WAITING_APPROVAL,
                summary=outcome.summary,
                transaction_id=outcome.transaction_id,
            )
        return TaskExecutionResult(
            status=TASK_STATUS_DONE if outcome.status == TASK_STATUS_DONE else TASK_STATUS_FAILED,
            summary=outcome.summary,
            error=None if outcome.status == TASK_STATUS_DONE else outcome.summary,
        )

    def _build_request(self, task: FamilyTaskRow) -> dict[str, Any]:
        thread_id = build_task_thread_id(task.id, task.attempt_count)
        parts = [
            f"You are running a scheduled household task (id: {task.id}).",
            f"Title: {task.title}",
        ]
        if task.description:
            parts.append(f"Details: {task.description}")
        parts.extend(self._context_parts(task))
        parts.append(
            "Reply with a short summary of what you did. If part of this "
            "needs human approval, say so plainly: that part will be "
            "raised as an approval request rather than done by you."
        )
        return {
            "thread_id": thread_id,
            "input": {"role": "user", "content": "\n\n".join(parts)},
        }

    def _context_parts(self, task: FamilyTaskRow) -> list[str]:
        """Family background for the prompt, or nothing.

        A failure here is swallowed on purpose: the context is extra
        detail, and losing it must not stop the family from having
        their task done. The task runs with less background rather than
        not at all.
        """
        if self._family_context is None:
            return []
        try:
            context = self._family_context(task)
        except Exception:  # noqa: BLE001 — context is a nicety, not a gate
            logger.warning(
                "TaskExecutor: family context unavailable for task %s", task.id
            )
            return []
        return [f"Family context:\n{context}"] if context else []

    def _interpret(self, response: Any) -> AgentRunResult:
        """Read the agent's answer for a transaction reference.

        Agents cannot grant themselves permission, so a transaction id
        in the answer is a *request* for approval, not an act already
        performed. Anything unparseable is a failed run, not a silent
        success.
        """
        if not isinstance(response, dict):
            return AgentRunResult(status=TASK_STATUS_FAILED, summary=str(response)[:500])
        text = _extract_text(response)
        if not text.strip():
            return AgentRunResult(status=TASK_STATUS_FAILED, summary="agent returned no content")
        transaction_id = _find_transaction_id(text)
        summary = text.strip()[:2000]
        return AgentRunResult(
            status=TASK_STATUS_DONE,
            summary=summary,
            transaction_id=transaction_id,
        )


def _extract_text(response: dict[str, Any]) -> str:
    """Pull the assistant text out of a harness response.

    The shape varies with the graph, so each known container is tried
    rather than assuming one.
    """
    for key in ("result", "output", "content", "text", "message"):
        value = response.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("content") or value.get("text")
            if isinstance(nested, str):
                return nested
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict) and isinstance(first.get("content"), str):
                return str(first["content"])
    messages = response.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return str(message["content"])
    return ""


def _find_transaction_id(text: str) -> str | None:
    """Spot a transaction id in the agent's own words.

    Deliberately narrow: a ULID-shaped token after an explicit marker.
    Scanning for any 26-character run would fire on an unrelated
    identifier and park the task for no reason.
    """
    marker = "transaction_id"
    lowered = text.lower()
    index = lowered.find(marker)
    if index < 0:
        return None
    tail = text[index + len(marker) :]
    for token in tail.replace(":", " ").replace(",", " ").split():
        cleaned = token.strip("`'\"(){}[].")
        if len(cleaned) == 26 and cleaned[:1].isalnum() and cleaned.isalnum():
            return cleaned.upper()
    return None


__all__ = [
    "DEFAULT_AGENT_TIMEOUT_SECONDS",
    "THREAD_ID_PREFIX",
    "AgentRunResult",
    "FamilyTaskAgentExecutor",
    "build_task_thread_id",
]
