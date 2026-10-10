"""Teach interactive agents to offer selectable follow-up actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

_ASK_USER_TOOL_NAME = "ask_user_question"

ACTION_CHECKLIST_PROMPT = """<action_checklists>
When your response presents two or more independent follow-up operations that
the user has not already authorized, first give the useful analysis, then call
`ask_user_question` once so the user can choose which operations to continue.
Use one question with `multi_select: true`; make each option a concrete action,
not a vague topic. Put the recommended actions first and briefly describe each
one's effect. Do not use a checklist for ordinary answers, for required steps
inside an already-authorized task, or merely to ask whether you should continue.
Never perform unchecked operations. If the user already selected operations,
perform only those selections without presenting the same checklist again.
</action_checklists>"""


def _with_action_checklist_prompt(request: ModelRequest[Any]) -> ModelRequest[Any]:
    if not any(getattr(tool, "name", None) == _ASK_USER_TOOL_NAME for tool in request.tools):
        return request
    existing = getattr(request, "system_message", None)
    content = existing.content if isinstance(existing, SystemMessage) else ""
    if not isinstance(content, str):
        return request
    merged = (
        f"{content.rstrip()}\n\n{ACTION_CHECKLIST_PROMPT}" if content else ACTION_CHECKLIST_PROMPT
    )
    message = SystemMessage(content=merged)
    return request.override(system_message=message)


class ActionChecklistMiddleware(AgentMiddleware[Any, Any]):
    """Add guidance for the existing ``ask_user_question`` multi-select UI."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_with_action_checklist_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_with_action_checklist_prompt(request))


__all__ = ["ACTION_CHECKLIST_PROMPT", "ActionChecklistMiddleware"]
