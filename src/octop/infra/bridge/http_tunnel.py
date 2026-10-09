"""Execute or decode Bridge HTTP tunnel frames against the local ASGI app."""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from octop.infra.bridge.audit import TunnelAudit
from octop.infra.bridge.tunnel_policy import is_tunnel_path_allowed
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)

# Hop-by-hop headers (RFC 9110 7.6.1) never cross a proxy boundary: they
# describe *this* hop's connection, and replaying them onto another connection
# is how request-smuggling and desync bugs start.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Headers whose value is a credential, or an identity claim about the far side.
# A tunneled request runs as the connection owner, so forwarding any of these
# would let the initiator assert an identity the peer never verified - or
# smuggle a cookie the owner never granted to this peer.
_IDENTITY_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "cookie2",
        "set-cookie",
        "set-cookie2",
        "www-authenticate",
        "x-api-key",
        "x-auth-token",
    }
)

# Proxy metadata describes the hop the value was observed on. Forwarding it
# lets a caller forge X-Forwarded-For and make the peer log an address that
# never existed, which is exactly what an audit trail must not allow.
_FORWARDED_HEADERS = frozenset({"forwarded", "via", "x-real-ip", "x-client-ip"})
_FORWARDED_PREFIX = "x-forwarded-"

# Only these request headers are forwarded. Everything else - including any
# header a future peer version invents - is dropped by default. A denylist is
# the wrong shape here: a header nobody thought of would be forwarded anyway.
_ALLOWED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "content-type",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "range",
        "user-agent",
        "x-chunk-sha256",
        "x-octop-agent-id",
    }
)

# Response headers worth returning. set-cookie is deliberately absent: a peer
# must not be able to plant a session cookie in the owner's browser.
_ALLOWED_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
    }
)


def _is_forwarded_metadata(name: str) -> bool:
    return name in _FORWARDED_HEADERS or name.startswith(_FORWARDED_PREFIX)


def sanitize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep only headers on the allow-list, lower-cased."""
    out: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _HOP_BY_HOP or lowered in _IDENTITY_HEADERS:
            continue
        if _is_forwarded_metadata(lowered):
            continue
        if lowered in _ALLOWED_REQUEST_HEADERS:
            out[lowered] = value
    return out


def sanitize_response_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep only response headers on the allow-list, lower-cased."""
    out: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _HOP_BY_HOP or lowered in _IDENTITY_HEADERS:
            continue
        if _is_forwarded_metadata(lowered):
            continue
        if lowered in _ALLOWED_RESPONSE_HEADERS:
            out[lowered] = value
    return out


def decode_body_b64(payload: dict[str, Any]) -> bytes:
    raw = payload.get("body_b64")
    if not raw:
        return b""
    return base64.b64decode(str(raw))


def encode_tunnel_response(
    *,
    status: int,
    headers: dict[str, str],
    body: bytes,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": status,
        "headers": headers,
        "done": True,
    }
    if body:
        out["body_b64"] = base64.b64encode(body).decode("ascii")
    return out


async def execute_local_http(
    *,
    app: Any,
    method: str,
    path: str,
    query: str = "",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    access_token: str,
    audit: TunnelAudit | None = None,
) -> dict[str, Any]:
    """Run ``method path`` against the local FastAPI app as ``access_token``."""
    if not is_tunnel_path_allowed(method, path):
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
            "This action is not available through the remote bridge. Manage it on the peer Octop.",
        )
    if audit is None:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
            "tunnel request is missing its audit block",
        )
    clean_headers = {
        name: value
        for name, value in sanitize_request_headers(headers or {}).items()
        if value is not None
    }
    # The owner's token is injected here and nowhere else, so an initiator can
    # never assert a different identity.
    clean_headers["authorization"] = f"Bearer {access_token}"
    url = path if path.startswith("/") else f"/{path}"
    if query:
        url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://bridge.local",
        timeout=120.0,
    ) as client:
        resp = await client.request(
            method.upper(),
            url,
            headers=clean_headers,
            content=body or None,
        )
    out_headers = sanitize_response_headers(dict(resp.headers))
    payload = encode_tunnel_response(
        status=resp.status_code, headers=out_headers, body=resp.content
    )
    # The hub sized its budget before sending; returning a silently truncated
    # body would leave the caller unable to tell that it was truncated.
    encoded = payload.get("body_b64")
    if isinstance(encoded, str) and len(encoded) * 3 // 4 > audit.max_response_bytes:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
            f"tunnel response exceeded its {audit.max_response_bytes} byte budget",
            details={"max_response_bytes": audit.max_response_bytes},
        )
    payload["request_id"] = audit.request_id
    return payload


def rewrite_agent_path(path: str, *, remote_agent_id: str, bridge_agent_id: str) -> str:
    """Replace a bridge agent id segment with the remote agent id (or reverse)."""
    return path.replace(bridge_agent_id, remote_agent_id)


def build_query_string(params: list[tuple[str, str]]) -> str:
    return urlencode(params)
