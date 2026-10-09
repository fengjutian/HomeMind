"""Path policy for Bridge HTTP tunnel requests against the local ASGI app.

Keep the allowlist aligned with ``dashboard/src/utils/remoteExpert.ts``
``SURFACES``: chat / history / tasks / workspace / memory / mbti / subagents
are fully tunneled; tools / plugins / channels are limited writes (the
Personalization UI still PATCHes tool-settings and may POST reload);
skill packages, global ACP, connector admin, and knowledge-base admin stay
peer-only in the UI even when a matching path would otherwise match.
Management / auth / bridge control planes stay local-only.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

# Only agent-scoped product surfaces may be executed on behalf of the connection
# owner. Management / auth / bridge control planes stay local-only.
_AGENT_COLLECTION = re.compile(r"^/api/agents/?$")
_AGENT_RESOURCE = re.compile(
    r"^/api/agents/"
    r"(?:bridge:[^/]+|[^/]+)"
    r"(?:/(?:threads|history|upload|avatar|icon|chat|messages|files|workspace"
    r"|attachments|media|turns|memory|skills|tools|tool-settings|mbti|persona"
    r"|channels|cron|config|state|status|welcome|members|subagents|reload|acp"
    r")(?:/.*)?)?$"
)

# Composer read-only surfaces for remote chat (models + knowledge pickers).
# Write / document / admin provider routes stay denied.
_COMPOSER_READONLY = re.compile(
    r"^/api/(?:"
    r"providers/resolved|"
    r"providers/active-model|"
    r"knowledge-bases|"
    r"knowledge-bases/capability"
    r")$"
)

# Chat dock browser viewer (peer harness). Install / record-replay stay denied.
_BROWSER_VIEWER_GET = re.compile(r"^/api/browser/(?:env-status|harness-sessions)$")
_BROWSER_HANDOFF = re.compile(r"^/api/browser/sessions/[^/]+/handoff$")

# Experts surfaces that are not under /api/agents/{id} but still agent-scoped
# on the peer (header or /plugins/agents/{id} path).
_PLUGIN_AGENT = re.compile(r"^/api/plugins/agents/(?:bridge:[^/]+|[^/]+)(?:/tools)?$")
_MBTI = re.compile(r"^/api/mbti(?:/.*)?$")
_SUBAGENT_CATALOG = re.compile(r"^/api/subagent-catalog(?:/.*)?$")
_ACP_GLOBAL = re.compile(r"^/api/acp(?:/[^/]+)?$")
_CRON_SETTINGS = re.compile(r"^/api/cron/settings$")
_CONNECTOR_INSTANCES_LIST = re.compile(r"^/api/connector-instances$")

# Resumable upload sessions. The state machine lives on the instance that will
# hold the file, so the whole session (create / status / part / complete /
# cancel) is forwarded rather than proxied part-by-part from the hub.
_UPLOAD_SESSIONS = re.compile(r"^/api/uploads/sessions(?:/[^/]+(?:/(?:parts/[^/]+|complete))?)?$")
_UPLOAD_BLOBS = re.compile(r"^/api/uploads/blobs/[^/]+$")


def _matches(method: str, path: str) -> bool:
    """The allow-list itself, applied to one concrete path form."""
    verb = (method or "GET").upper()
    raw = (path or "").split("?", 1)[0].strip() or "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    # Normalize trailing slash except root.
    if len(raw) > 1:
        raw = raw.rstrip("/")

    if _AGENT_COLLECTION.fullmatch(raw):
        return verb == "GET"

    if _COMPOSER_READONLY.fullmatch(raw):
        return verb == "GET"

    if _BROWSER_VIEWER_GET.fullmatch(raw):
        return verb == "GET"

    if _BROWSER_HANDOFF.fullmatch(raw):
        return verb == "POST"

    if _AGENT_RESOURCE.fullmatch(raw):
        return verb in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}

    if _PLUGIN_AGENT.fullmatch(raw):
        return verb in {"GET", "PATCH"}

    if _MBTI.fullmatch(raw):
        return verb in {"GET", "POST"}

    if _SUBAGENT_CATALOG.fullmatch(raw):
        return verb == "GET"

    if _ACP_GLOBAL.fullmatch(raw):
        return verb in {"GET", "PUT", "DELETE"}

    if _CRON_SETTINGS.fullmatch(raw):
        return verb == "GET"

    if _CONNECTOR_INSTANCES_LIST.fullmatch(raw):
        return verb == "GET"

    if _UPLOAD_SESSIONS.fullmatch(raw):
        return verb in {"GET", "POST", "PUT", "DELETE"}

    if _UPLOAD_BLOBS.fullmatch(raw):
        return verb == "GET"

    return False


def normalize_tunnel_path(path: str) -> str | None:
    """Canonical form of a tunnel path, or ``None`` if it cannot be trusted.

    Only paths that are *already* canonical are accepted. A path that needs
    ``.``, ``..``, ``//``, percent-decoding or a backslash to be understood is
    refused outright rather than resolved, because the component that finally
    routes it may resolve differently than we did — and which one wins would
    decide whether the allow-list was honoured at all.
    """
    raw = (path or "").split("?", 1)[0].strip()
    if not raw.startswith("/"):
        return None
    if "\\" in raw:
        return None

    decoded = unquote(raw)
    if decoded != raw:
        # Either an escape sequence, or a double-encoded one. Both mean the
        # literal path and the routed path can differ.
        return None

    trimmed = decoded.rstrip("/") or "/"
    if trimmed == "/":
        return None
    segments = trimmed.split("/")[1:]
    if not segments:
        return None
    for segment in segments:
        if segment in ("", ".", ".."):
            return None
    return "/" + "/".join(segments)


def is_tunnel_path_allowed(method: str, path: str) -> bool:
    """Allow-list check applied to the canonical form of the path.

    The raw form must already *be* canonical, so there is only ever one path
    the allow-list has to reason about.
    """
    normalized = normalize_tunnel_path(path)
    if normalized is None:
        return False
    return _matches(method, normalized)
