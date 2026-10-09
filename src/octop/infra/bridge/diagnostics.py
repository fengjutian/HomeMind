"""Bridge diagnostics summary (plan phase 16).

An operator staring at a link that keeps dropping wants one thing: enough to
describe the problem without opening six files. This builds that summary and
is the single place that decides *what may leave the process*.

Nothing here may contain a password, a token, an attachment body, or a full
query string — the summary is designed to be pasted into an issue, so anything
secret or user-identifying in it would end up somewhere it cannot be retracted
from. ``redact_for_log`` already truncates a credential; the query is reduced to
its key names, because values are where the secrets and paths live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from octop.infra.bridge.protocol import LOCAL_VERSION_STRING, Capability
from octop.infra.bridge.states import coerce_state
from octop.infra.db.repos.bridge_connections import BridgeConnectionRow
from octop.infra.metrics import METRICS

__all__ = ["BridgeDiagnostic", "connection_diagnostic", "redact_query", "summarize"]

#: Query keys whose values are known to carry identifiers rather than settings.
_SENSITIVE_QUERY_HINTS = ("token", "key", "secret", "password", "sig", "auth")


def redact_query(query: str) -> str:
    """Reduce a query string to its key names.

    The *shape* is almost always what an operator needs ("was it a signed
    URL, was it a range request") and the values are exactly what must not be
    copied out of the process.
    """
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return "<unparseable>"
    keys: list[str] = []
    for key, _value in pairs:
        lowered = key.lower()
        if any(hint in lowered for hint in _SENSITIVE_QUERY_HINTS):
            keys.append(f"{key}=<redacted>")
        else:
            keys.append(key)
    return ",".join(keys)


def path_only(url_or_path: str) -> str:
    """Strip scheme, host and query from a URL or path."""
    parts = urlsplit(url_or_path)
    return parts.path or "/"


@dataclass
class BridgeDiagnostic:
    """One connection's share of the diagnostic summary."""

    connection_id: str
    display_name: str
    peer_base_url: str
    state: str
    retry_may_help: bool
    peer_protocol: str | None
    peer_instance_id: str | None
    local_protocol: str = LOCAL_VERSION_STRING
    capabilities: list[str] = field(default_factory=list)
    connected_since: int | None = None
    online_seconds: int | None = None
    reconnect_attempts: int = 0
    last_error: str | None = None
    last_seen_at: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "display_name": self.display_name,
            "peer_base_url": self.peer_base_url,
            "state": self.state,
            "retry_may_help": self.retry_may_help,
            "peer_protocol": self.peer_protocol,
            "peer_instance_id": self.peer_instance_id,
            "local_protocol": self.local_protocol,
            "capabilities": self.capabilities,
            "connected_since": self.connected_since,
            "online_seconds": self.online_seconds,
            "reconnect_attempts": self.reconnect_attempts,
            "last_error": self.last_error,
            "last_seen_at": self.last_seen_at,
        }


def connection_diagnostic(
    row: BridgeConnectionRow,
    *,
    negotiated: Any = None,
    now: int = 0,
) -> BridgeDiagnostic:
    """Summarise one connection row for the diagnostics panel."""
    state = coerce_state(row.state)
    caps: list[str] = []
    if negotiated is not None:
        caps = sorted(c.value for c in getattr(negotiated, "peer_capabilities", ()))
    online = None
    if row.connected_since and state.is_live:
        online = max(0, now - row.connected_since)
    return BridgeDiagnostic(
        connection_id=row.connection_id,
        display_name=row.display_name,
        peer_base_url=row.peer_base_url,
        state=state.value,
        retry_may_help=state.retry_may_help,
        peer_protocol=row.peer_protocol,
        peer_instance_id=row.peer_instance_id,
        capabilities=caps,
        connected_since=row.connected_since,
        online_seconds=online,
        reconnect_attempts=row.reconnect_attempts,
        last_error=row.last_error,
        last_seen_at=row.last_seen_at,
    )


def summarize(
    diagnostics: list[BridgeDiagnostic],
    *,
    local_instance_id: str = "",
    local_capabilities: frozenset[Capability] | tuple[Capability, ...] = frozenset(),
) -> dict[str, Any]:
    """The payload behind "copy diagnostics".

    Counters come from ``METRICS`` so the summary reflects the process that is
    actually running, not a re-read of the database.
    """
    online = [d for d in diagnostics if coerce_state(d.state).is_live]
    needs_human = [d for d in diagnostics if not d.retry_may_help]
    return {
        "instance_id": local_instance_id,
        "local_protocol": LOCAL_VERSION_STRING,
        "local_capabilities": sorted(c.value for c in local_capabilities),
        "connections_total": len(diagnostics),
        "connections_online": len(online),
        "connections_needing_human": [d.connection_id for d in needs_human],
        "connections": [d.as_dict() for d in diagnostics],
        "metrics": {k: v for k, v in METRICS.snapshot().items() if k.startswith("bridge_")},
    }
