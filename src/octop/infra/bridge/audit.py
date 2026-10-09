"""Per-request audit context for tunneled HTTP (plan phase 15).

A tunneled request is the one place where two independent instances act as one
logical caller: the hub holds the browser's identity, the peer executes it. If
the peer only sees "somebody with a valid token", the audit trail on that
instance records an action with no accountable actor, and a support question
("who deleted this file?") has no answer that survives the hop.

This module makes the hop explicit. Every ``tunnel.request`` carries:

* ``request_id`` — correlates the two instances' logs and the audit row.
* ``owner_user_id`` — the user whose token is being used, *asserted by the hub*
  and verified against the executing identity by the peer.
* ``deadline_at`` — a wall-clock bound. A peer that is slow must not hold a
  tunnel slot past the point where the hub has given up.
* ``max_response_bytes`` — a ceiling on what may come back, so one request
  cannot buffer an unbounded response on either side.

Everything here is validation and bookkeeping; it performs no I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

#: Default wall-clock bound for a single tunneled request. The hub abandons a
#: request past this, so a peer that never answers costs one slot, not forever.
DEFAULT_TUNNEL_DEADLINE_SECONDS = 120

#: Default ceiling on a tunneled response body. Generous enough for a document
#: or a media preview, small enough that a runaway endpoint cannot exhaust the
#: hub's memory through the base64 frame it arrives in.
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: Frames are base64 on the wire, so the encoded body is ~4/3 of the raw one.
#: Refuse a request whose *declared* response could not fit a frame.
_MAX_FRAME_BYTES = 32 * 1024 * 1024

#: An owner id that is not a plausible user id is a bug or a forgery, not a
#: value we should pass through to a peer.
_MAX_OWNER_USER_ID = 2**63 - 1


@dataclass(frozen=True)
class TunnelAudit:
    """The metadata that accompanies one tunneled request."""

    request_id: str
    owner_user_id: int
    deadline_at: int
    max_response_bytes: int
    remote_agent_id: str = ""
    hub_instance_id: str = ""

    def as_frame_fields(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "owner_user_id": self.owner_user_id,
            "deadline_at": self.deadline_at,
            "max_response_bytes": self.max_response_bytes,
            "hub_instance_id": self.hub_instance_id,
        }

    def describe(self) -> str:
        """One line, safe for a log: no token, no query, no body."""
        return (
            f"request_id={self.request_id} owner_user_id={self.owner_user_id} "
            f"remote_agent_id={self.remote_agent_id or '-'} "
            f"max_response_bytes={self.max_response_bytes}"
        )


def new_audit(
    *,
    owner_user_id: int,
    now: int,
    remote_agent_id: str = "",
    hub_instance_id: str = "",
    deadline_seconds: int = DEFAULT_TUNNEL_DEADLINE_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> TunnelAudit:
    """Build the audit block for a request the hub is about to send."""
    return TunnelAudit(
        request_id=new_ulid(),
        owner_user_id=owner_user_id,
        deadline_at=now + max(1, deadline_seconds),
        max_response_bytes=min(max(1, max_response_bytes), _MAX_FRAME_BYTES),
        remote_agent_id=remote_agent_id,
        hub_instance_id=hub_instance_id,
    )


def audit_from_frame(frame: dict[str, Any]) -> TunnelAudit:
    """Read the audit block the hub attached, rejecting a malformed one.

    A peer must not have to guess whether a missing field means "old hub" or
    "forged hub": the difference decides whether the request gets an identity
    attached to it at all, so an implausible value is refused rather than
    silently defaulted.
    """
    request_id = str(frame.get("request_id") or "").strip()
    if not request_id:
        request_id = str(frame.get("id") or "").strip()
    if not request_id:
        raise OctopError(ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, "tunnel request has no id")

    raw_owner = frame.get("owner_user_id")
    if not isinstance(raw_owner, int) or isinstance(raw_owner, bool):
        raise OctopError(ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, "tunnel request has no owner_user_id")
    if raw_owner <= 0 or raw_owner > _MAX_OWNER_USER_ID:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, "tunnel request owner_user_id is out of range"
        )

    deadline = frame.get("deadline_at")
    if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0:
        raise OctopError(ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, "tunnel request has no deadline_at")

    raw_max = frame.get("max_response_bytes")
    if not isinstance(raw_max, int) or isinstance(raw_max, bool) or raw_max <= 0:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, "tunnel request has no max_response_bytes"
        )
    if raw_max > _MAX_FRAME_BYTES:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
            "tunnel request max_response_bytes exceeds the frame limit",
        )

    return TunnelAudit(
        request_id=request_id,
        owner_user_id=raw_owner,
        deadline_at=deadline,
        max_response_bytes=raw_max,
        remote_agent_id=str(frame.get("remote_agent_id") or ""),
        hub_instance_id=str(frame.get("hub_instance_id") or ""),
    )


def deadline_remaining(deadline_at: int, *, now: int) -> int:
    """Seconds left before the hub gives up. Never negative."""
    return max(0, deadline_at - now)


def enforce_response_budget(body: bytes, *, max_response_bytes: int) -> bytes:
    """Refuse a response larger than the caller agreed to accept.

    The hub sized its budget before sending, so exceeding it means the peer
    ignored the contract — returning a truncated body would be worse, because the
    caller could not tell it was truncated.
    """
    if len(body) > max_response_bytes:
        raise OctopError(
            ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
            f"tunnel response exceeded its {max_response_bytes} byte budget",
            details={"size": len(body), "max_response_bytes": max_response_bytes},
        )
    return body


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TUNNEL_DEADLINE_SECONDS",
    "TunnelAudit",
    "audit_from_frame",
    "deadline_remaining",
    "enforce_response_budget",
    "new_audit",
]
