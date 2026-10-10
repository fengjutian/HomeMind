from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import SystemMessage

from homemind.infra.agents.action_checklist_middleware import ActionChecklistMiddleware


class _Request:
    def __init__(self, content: Any = "base", *, with_ask_tool: bool = True) -> None:
        self.system_message = SystemMessage(content=content)
        self.tools = [SimpleNamespace(name="ask_user_question")] if with_ask_tool else []

    def override(self, **updates: Any) -> _Request:
        out = _Request()
        out.system_message = updates.get("system_message", self.system_message)
        out.tools = updates.get("tools", self.tools)
        return out


def test_appends_multi_select_guidance_to_system_message() -> None:
    middleware = ActionChecklistMiddleware()
    request = _Request()

    result = middleware.wrap_model_call(request, lambda value: value)

    assert result.system_message.content.startswith("base\n\n")
    assert "ask_user_question" in result.system_message.content
    assert "multi_select: true" in result.system_message.content
    assert "Never perform unchecked operations" in result.system_message.content
    assert request.system_message.content == "base"


def test_preserves_structured_system_content() -> None:
    middleware = ActionChecklistMiddleware()
    request = _Request([{"type": "text", "text": "base"}])

    result = middleware.wrap_model_call(request, lambda value: value)

    assert result is request


def test_does_not_add_guidance_when_interaction_tool_is_unavailable() -> None:
    middleware = ActionChecklistMiddleware()
    request = _Request(with_ask_tool=False)

    result = middleware.wrap_model_call(request, lambda value: value)

    assert result is request


def test_async_hook_applies_same_guidance() -> None:
    middleware = ActionChecklistMiddleware()

    async def handler(request: Any) -> Any:
        return request

    result = asyncio.run(middleware.awrap_model_call(_Request(""), handler))

    assert result.system_message.content.startswith("<action_checklists>")
