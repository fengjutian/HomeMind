"""Make model-facing tool names portable across strict provider APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import BaseTool

from octop.infra.agents.plugins.plugin_tool_names import sanitize_plugin_tool_name


def _dict_tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(tool.get("name") or "")


def _renamed_dict_tool(tool: dict[str, Any], name: str) -> dict[str, Any]:
    cloned = deepcopy(tool)
    function = cloned.get("function")
    if isinstance(function, dict):
        function["name"] = name
    else:
        cloned["name"] = name
    return cloned


def _portable_request(
    request: ModelRequest[Any],
) -> tuple[ModelRequest[Any], dict[str, str]]:
    used: set[str] = set()
    tools_out: list[BaseTool | dict[str, Any]] = []
    original_by_portable: dict[str, str] = {}
    changed = False

    for tool in request.tools or []:
        original = tool.name if isinstance(tool, BaseTool) else _dict_tool_name(tool)
        if not original:
            tools_out.append(tool)
            continue
        portable = sanitize_plugin_tool_name(original, used=used)
        original_by_portable[portable] = original
        if portable == original:
            tools_out.append(tool)
            continue
        changed = True
        if isinstance(tool, BaseTool):
            tools_out.append(tool.model_copy(update={"name": portable}))
        else:
            tools_out.append(_renamed_dict_tool(tool, portable))

    if not changed:
        return request, {}
    return request.override(tools=tools_out), original_by_portable


def _restore_response(
    response: ModelResponse[Any], original_by_portable: dict[str, str]
) -> ModelResponse[Any]:
    if not original_by_portable:
        return response
    result: list[Any] = []
    changed = False
    for message in response.result:
        if not isinstance(message, AIMessage | AIMessageChunk):
            result.append(message)
            continue
        updates: dict[str, Any] = {}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            restored = [
                {
                    **call,
                    "name": original_by_portable.get(str(call.get("name") or ""), call.get("name")),
                }
                for call in tool_calls
            ]
            if restored != tool_calls:
                updates["tool_calls"] = restored
        chunks = getattr(message, "tool_call_chunks", None)
        if chunks:
            restored_chunks = [
                {
                    **chunk,
                    "name": original_by_portable.get(
                        str(chunk.get("name") or ""), chunk.get("name")
                    ),
                }
                for chunk in chunks
            ]
            if restored_chunks != chunks:
                updates["tool_call_chunks"] = restored_chunks
        if updates:
            changed = True
            result.append(message.model_copy(update=updates))
        else:
            result.append(message)
    if not changed:
        return response
    return ModelResponse(result=result, structured_response=response.structured_response)


class ToolNameCompatibilityMiddleware(AgentMiddleware[Any, Any]):
    """Round-trip illegal tool names through provider-safe aliases."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        portable, aliases = _portable_request(request)
        return _restore_response(handler(portable), aliases)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        portable, aliases = _portable_request(request)
        return _restore_response(await handler(portable), aliases)


__all__ = ["ToolNameCompatibilityMiddleware"]
