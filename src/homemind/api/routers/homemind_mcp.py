"""JSON-RPC transport for the HomeMind MCP server (Stage 10).

Octop's ``/internal/mcp`` speaks to *external* connector servers, which
is the opposite direction. This router is the surface an MCP **client**
talks to in order to use HomeMind's family tools.

The transport is deliberately thin: it authenticates the caller, then
hands every request to :class:`~homemind.tools.mcp_server.HomeMindMcpServer`,
which owns identity binding, family scoping, transaction routing, and
audit. Adding a method here that reaches the repo directly would
defeat the point of that split.

Identity comes from the dashboard JWT, never from a JSON-RPC field —
the spec's hard rule that ``user_id`` must not be a tool argument.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.tools.mcp_server import HomeMindMcpServer, McpPrincipal
from octop.api.deps import current_user, get_server
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "homemind-family", "version": "0.1.0"}

# JSON-RPC error codes. -32602 is "invalid params", -32601 is
# "method not found", -32000 is the implementation-defined band this
# server uses for its own refusals (privacy gate, family scope).
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_TOOL_ERROR = -32000


def _server(server: OctopServer) -> HomeMindMcpServer:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    family_manager = FamilyManager(services.family_repo)
    return HomeMindMcpServer(
        services,
        active_family_resolver=ActiveFamilyResolver(
            SettingsRepo(server.services.db), family_manager,
        ),
    )


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@router.post("/mcp")
async def homemind_mcp(
    request: Request,
    server: Server,
    user: CurrentUser,
) -> JSONResponse:
    """Single JSON-RPC entry point (streamable HTTP shape).

    Handles ``initialize``, ``tools/list`` and ``tools/call``. Batch
    arrays are not accepted — a batch would have to run several tools
    under one identity check, which is exactly the ambiguity the
    per-request principal rules out.
    """

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content=_error(None, RPC_INVALID_REQUEST, "body must be a JSON object"),
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content=_error(None, RPC_INVALID_REQUEST, "body must be a JSON object"),
        )

    request_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content=_error(request_id, RPC_INVALID_PARAMS, "params must be an object"),
        )

    if method == "initialize":
        return JSONResponse(
            content=_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        )

    if method in {"notifications/initialized", "initialized"}:
        # Notifications carry no id and expect no response.
        return JSONResponse(status_code=202, content={})

    if method == "tools/list":
        return JSONResponse(
            content=_result(request_id, {"tools": _server(server).list_tools()}),
        )

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return JSONResponse(
                content=_error(
                    request_id, RPC_INVALID_PARAMS, "arguments must be an object",
                ),
            )
        outcome = _server(server).call_tool(
            name, arguments, _principal(user, request),
        )
        payload = outcome.as_payload()
        if payload.get("ok"):
            return JSONResponse(
                content=_result(request_id, {
                    "content": [{"type": "text", "text": _dump(payload["data"])}],
                    "isError": False,
                }),
            )
        return JSONResponse(
            content=_result(request_id, {
                "content": [{"type": "text", "text": _dump(payload["error"])}],
                "isError": True,
            }),
        )

    if method == "ping":
        return JSONResponse(content=_result(request_id, {}))

    return JSONResponse(
        content=_error(request_id, RPC_METHOD_NOT_FOUND, f"unknown method {method!r}"),
    )


def _principal(user: User, request: Request) -> McpPrincipal:
    """Bind the authenticated caller into an MCP principal.

    ``client_id`` / ``session_id`` come from headers, which the client
    controls — they are audit labels, never authorisation. The
    authoritative identity is the user resolved from the JWT above.
    """
    return McpPrincipal(
        user_id=user.id,
        client_id=request.headers.get("x-mcp-client") or "dashboard",
        session_id=request.headers.get("x-mcp-session") or "-",
    )


def _dump(value: Any) -> str:
    import json  # noqa: PLC0415

    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_INFO",
    "router",
]
