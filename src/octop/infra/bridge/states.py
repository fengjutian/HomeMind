"""Bridge connection states (plan phase 13).

Phase 0 found four free-text values in ``bridge_connections.status`` with no
enum and no constraint. The plan needs eight. The reason the extra four matter
is not tidiness: ``error`` cannot tell an operator whether retrying will help.

* ``DEGRADED`` — up, but something is wrong. Retrying may help.
* ``REAUTH_REQUIRED`` — the credential was revoked. Retrying *never* helps;
  a human must sign in again.
* ``INCOMPATIBLE`` — protocol major mismatch. Retrying never helps; one side
  must be upgraded.
* ``DISABLED`` — the operator turned it off. Retrying never helps.

Collapsing all four into ``error`` means the dashboard tells an operator to
"check the logs" for a condition that only re-authentication or an upgrade
can resolve.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = [
    "LEGACY_STATUS_FOR_STATE",
    "BridgeState",
    "coerce_state",
    "is_recoverable",
    "persist_state",
]


class BridgeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    INCOMPATIBLE = "INCOMPATIBLE"
    DISABLED = "DISABLED"

    @property
    def retry_may_help(self) -> bool:
        """Whether a reconnect attempt is worth making on its own."""
        return self in _RECOVERABLE

    @property
    def needs_human(self) -> bool:
        return self in _NEEDS_HUMAN

    @property
    def is_live(self) -> bool:
        """A socket is carrying traffic in this state."""
        return self in {BridgeState.ONLINE, BridgeState.DEGRADED}


_RECOVERABLE: frozenset[BridgeState] = frozenset(
    {
        BridgeState.DISCONNECTED,
        BridgeState.CONNECTING,
        BridgeState.AUTHENTICATING,
        BridgeState.DEGRADED,
    }
)

#: States a retry cannot fix on its own.
_NEEDS_HUMAN: frozenset[BridgeState] = frozenset(
    {
        BridgeState.REAUTH_REQUIRED,
        BridgeState.INCOMPATIBLE,
        BridgeState.DISABLED,
    }
)

#: The single mapping to the legacy ``status`` column. Defined once, here:
#: two independent maps would drift, and the older code that still reads
#: ``status`` would quietly disagree with the new one.
LEGACY_STATUS_FOR_STATE: dict[str, str] = {
    BridgeState.DISCONNECTED.value: "disconnected",
    BridgeState.CONNECTING.value: "connecting",
    BridgeState.AUTHENTICATING.value: "connecting",
    BridgeState.ONLINE.value: "connected",
    BridgeState.DEGRADED.value: "error",
    BridgeState.REAUTH_REQUIRED.value: "error",
    BridgeState.INCOMPATIBLE.value: "error",
    BridgeState.DISABLED.value: "disconnected",
}

#: Reverse map used when reading rows written before the migration.
_LEGACY_TO_STATE: dict[str, BridgeState] = {
    "disconnected": BridgeState.DISCONNECTED,
    "connecting": BridgeState.CONNECTING,
    "connected": BridgeState.ONLINE,
    "error": BridgeState.DEGRADED,
}


def coerce_state(raw: object) -> BridgeState:
    """Read a state from the DB without ever raising.

    A row may hold a legacy value, a value from a newer build, or nothing at
    all. All three mean "we do not know", and none of them should take down a
    dashboard render.
    """
    if isinstance(raw, BridgeState):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        # Try the new vocabulary first, then the legacy one — both without
        # caring about case, so a hand-edited row cannot read differently.
        try:
            return BridgeState(text)
        except ValueError:
            pass
        try:
            return BridgeState(text.upper())
        except ValueError:
            return _LEGACY_TO_STATE.get(text.lower(), BridgeState.DISCONNECTED)
    return BridgeState.DISCONNECTED


def is_recoverable(state: BridgeState) -> bool:
    return state.retry_may_help


def persist_state(
    repo: Any,
    connection_id: str,
    state: BridgeState,
    *,
    detail: str | None = None,
    clear_detail: bool = False,
    peer_protocol: str | None = None,
    peer_instance_id: str | None = None,
) -> Any:
    """Write ``state`` through the repo, supplying the legacy value.

    The repo stays SQL-only, so the new→legacy mapping is applied here, in the
    domain, and passed in. One definition, one caller.
    """
    return repo.set_state(
        connection_id,
        state.value,
        legacy_status=LEGACY_STATUS_FOR_STATE[state.value],
        detail=detail,
        clear_detail=clear_detail,
        peer_protocol=peer_protocol,
        peer_instance_id=peer_instance_id,
    )
