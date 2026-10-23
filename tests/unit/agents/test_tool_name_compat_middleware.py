"""Provider-safe aliases for model-facing tool names."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from octop.infra.agents.middleware.tool_name_compat import ToolNameCompatibilityMiddleware


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda value: value,
        name=name,
        description="demo",
    )


def _request(tools: list[Any]) -> ModelRequest[Any]:
    return ModelRequest(model=MagicMock(), messages=[], tools=tools)


def test_aliases_invalid_base_tool_and_restores_model_tool_call() -> None:
    middleware = ToolNameCompatibilityMiddleware()
    original = _tool("novel.audit")

    def _handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        assert request.tools[0].name == "novel_audit"
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "novel_audit", "args": {"value": "x"}, "id": "call-1"}],
                )
            ]
        )

    response = middleware.wrap_model_call(_request([original]), _handler)

    assert original.name == "novel.audit"
    assert response.result[0].tool_calls[0]["name"] == "novel.audit"


def test_aliases_openai_function_schema() -> None:
    middleware = ToolNameCompatibilityMiddleware()
    schema = {"type": "function", "function": {"name": "小说.校阅", "parameters": {}}}

    def _handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        assert request.tools[0]["function"]["name"].isascii()
        assert "." not in request.tools[0]["function"]["name"]
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request([schema]), _handler)

    assert schema["function"]["name"] == "小说.校阅"


@pytest.mark.asyncio
async def test_async_path_restores_original_name() -> None:
    middleware = ToolNameCompatibilityMiddleware()

    async def _handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        alias = request.tools[0].name
        return ModelResponse(
            result=[AIMessage(content="", tool_calls=[{"name": alias, "args": {}, "id": "c"}])]
        )

    response = await middleware.awrap_model_call(_request([_tool("查重.小说")]), _handler)

    assert response.result[0].tool_calls[0]["name"] == "查重.小说"
