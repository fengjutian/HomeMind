"""Bridge wire protocol — version and capability negotiation (plan phase 13).

Phase 0 found the handshake compared a single integer with ``!=`` and closed
the socket on any difference. That makes every minor release a breaking
change: a peer that is merely a patch behind can no longer be reached, and the
operator sees one indistinguishable "protocol mismatch" for two very different
situations.

This module replaces that with the rule the plan asks for:

* **Major mismatch → refuse, explicitly.** A different major means the frames
  themselves are shaped differently; talking anyway is how a tunnel starts
  forwarding fields it does not understand.
* **Minor mismatch → negotiate down.** A peer that is behind simply does not
  get offered capabilities it has never heard of.
* **Legacy peers still connect.** Before this change the version was a bare
  integer. An old peer sends ``1``, not ``"1.0"``, and it must keep working —
  refusing it would break every already-deployed link on upgrade day.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

#: Bumping MINOR is backwards compatible; bumping MAJOR is not.
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0

#: Legacy spelling: peers before version negotiation sent a bare integer.
LEGACY_PROTOCOL_VERSION = 1

_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?$")


class ProtocolIncompatible(Exception):
    """Major versions differ — the peers cannot speak the same protocol."""

    def __init__(self, ours: str, theirs: str) -> None:
        super().__init__(
            f"bridge protocol {ours} cannot talk to {theirs}; "
            "both instances must run a matching major version"
        )
        self.ours = ours
        self.theirs = theirs


class Capability(StrEnum):
    """Optional features a peer may support. A peer omits what it lacks."""

    #: Streaming HTTP responses rather than a single base64 frame.
    STREAMED_BODY = "streamed_body"
    #: Frame compression (zstd) when both ends agree.
    COMPRESSION = "compression"
    #: Tunnel request cancellation.
    CANCEL = "cancel"
    #: Resumable upload sessions proxied through the tunnel.
    UPLOAD_SESSIONS = "upload_sessions"
    #: Structured diagnostic payloads (request/turn ids, timings).
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True)
class ProtocolVersion:
    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    @classmethod
    def parse(cls, raw: object) -> ProtocolVersion | None:
        """Accept both ``"1.0"`` and the legacy bare integer ``1``."""
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            # Legacy peers sent an int. Treat it as the major version it was,
            # with no minor, so an old peer still negotiates.
            return cls(major=raw, minor=0) if raw > 0 else None
        if not isinstance(raw, str):
            return None
        match = _VERSION_RE.match(raw.strip())
        if match is None:
            return None
        return cls(major=int(match.group(1)), minor=int(match.group(2) or 0))

    def is_compatible_with(self, other: ProtocolVersion) -> bool:
        return self.major == other.major

    def is_older_than(self, other: ProtocolVersion) -> bool:
        return (self.major, self.minor) < (other.major, other.minor)


LOCAL_VERSION = ProtocolVersion(PROTOCOL_MAJOR, PROTOCOL_MINOR)
LOCAL_VERSION_STRING = str(LOCAL_VERSION)

#: What this build can do. The negotiated set is the intersection with the peer.
LOCAL_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.STREAMED_BODY,
        Capability.CANCEL,
        Capability.UPLOAD_SESSIONS,
        Capability.DIAGNOSTICS,
    }
)

#: Largest frame this build will accept on a tunnel socket. Advertised in hello
#: so a peer never sends something the other end will only drop.
MAX_FRAME_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Hello:
    """The negotiated outcome of a handshake."""

    peer_version: ProtocolVersion | None
    peer_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    peer_instance_id: str = ""
    peer_max_frame_bytes: int = 0
    legacy: bool = False

    @property
    def compatible(self) -> bool:
        return self.peer_version is not None


def build_hello(
    *,
    connection_id: str,
    role: str,
    base_url: str,
    username: str,
    display_name: str,
    instance_id: str,
    octop_version: str,
) -> dict[str, object]:
    """The hello frame this build sends.

    ``protocol_version`` is now a ``"major.minor"`` string. Legacy peers
    compared it with ``!=`` against an int, so a string would break them —
    see :func:`negotiate` for how the transition is handled on their side.
    """
    return {
        "type": "hello",
        "protocol_version": LOCAL_VERSION_STRING,
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "capabilities": sorted(c.value for c in LOCAL_CAPABILITIES),
        "max_frame_bytes": MAX_FRAME_BYTES,
        "instance_id": instance_id,
        "octop_version": octop_version,
        "connection_id": connection_id,
        "role": role,
        "advertise_base_url": base_url,
        "advertise_username": username,
        "display_name": display_name,
    }


def negotiate(hello: dict[str, object]) -> Hello:
    """Read a peer's hello and decide what the two ends can do together.

    Raises :class:`ProtocolIncompatible` only for a genuine major mismatch.
    A peer that omits or mangles the version is treated as legacy rather than
    refused: an unparseable value is not evidence of a newer major, and
    refusing would turn a cosmetic change into an outage.
    """
    raw_version = hello.get("protocol_version")
    peer_version = ProtocolVersion.parse(raw_version)
    legacy = peer_version is None or isinstance(raw_version, int)

    if peer_version is not None and not peer_version.is_compatible_with(LOCAL_VERSION):
        raise ProtocolIncompatible(LOCAL_VERSION_STRING, str(peer_version))

    if peer_version is None:
        # No usable version at all. A pre-negotiation peer still speaks the
        # framing it always did, so accept it with no optional features.
        peer_version = ProtocolVersion(major=LEGACY_PROTOCOL_VERSION, minor=0)

    capabilities = _parse_capabilities(hello.get("capabilities"))
    # Only what both ends have. A legacy peer claims nothing.
    negotiated = capabilities & LOCAL_CAPABILITIES if capabilities else frozenset()

    return Hello(
        peer_version=peer_version,
        peer_capabilities=negotiated,
        peer_instance_id=str(hello.get("instance_id") or ""),
        peer_max_frame_bytes=_as_positive_int(hello.get("max_frame_bytes")),
        legacy=legacy,
    )


def _parse_capabilities(raw: object) -> frozenset[Capability]:
    if isinstance(raw, str):
        # A comma-separated list is what an operator would hand-edit into a
        # config; accept it rather than failing the whole handshake.
        raw = [part for part in raw.split(",") if part]
    if not isinstance(raw, (list, tuple, set)):
        return frozenset()
    out: set[Capability] = set()
    for item in raw:
        try:
            out.add(Capability(str(item).strip()))
        except ValueError:
            # An unknown capability is not an error: it is simply a feature
            # this build has never heard of.
            continue
    return frozenset(out)


def _as_positive_int(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def effective_max_frame(peers: Iterable[int] = ()) -> int:
    """Smallest frame any live peer accepts; our own limit when none are known."""
    limits = [n for n in peers if n > 0]
    return min([*limits, MAX_FRAME_BYTES])


__all__ = [
    "LOCAL_CAPABILITIES",
    "LOCAL_VERSION",
    "LOCAL_VERSION_STRING",
    "MAX_FRAME_BYTES",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "Capability",
    "Hello",
    "ProtocolIncompatible",
    "ProtocolVersion",
    "build_hello",
    "effective_max_frame",
    "negotiate",
]
