"""Bridge token refresh policy (plan phase 14).

Phase 0 found three defects in how a peer token is kept fresh:

* **No single-flight.** Every reconnect attempt calls ``_ensure_peer_token``
  independently. When a peer restarts and several links notice at once, each
  one logs in again — a login storm against a user's own instance, and the
  peer's rate limiter is the one that falls over.
* **A fixed 60-second threshold.** Every instance on the planet refreshes at
  the same offset from start-up, so reconnects cluster.
* **Failures are undifferentiated.** "Peer unreachable", "credentials
  revoked" and "peer is down for maintenance" all became the same
  ``BRIDGE_AUTH_FAILED``, so the operator could not tell a retry from a
  re-login from a shutdown.

This module is the policy: when to refresh, how to classify a failure, and how
to make concurrent callers share one login.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)

#: Refresh this far ahead of expiry so a token cannot expire mid-handshake.
REFRESH_SAFETY_MARGIN_SECONDS = 300

__all__ = [
    "REFRESH_SAFETY_MARGIN_SECONDS",
    "RefreshDecision",
    "RefreshFailure",
    "TokenRefresher",
    "classify_login_failure",
    "refresh_delay_seconds",
    "should_refresh",
]


class RefreshFailure(StrEnum):
    """Why a token refresh/login did not succeed.

    The distinction drives what happens next, so it is made once, here, rather
    than by string-matching an exception message at the call site.
    """

    #: The peer could not be reached. Retrying later is reasonable.
    TRANSIENT = "TRANSIENT"
    #: The peer answered 401: the credential was revoked or changed.
    #: Retrying the same password will never work.
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    #: The peer answered 4xx/5xx in some other way (upgrade required, bad
    #: request, server error). Worth one bounded retry, then give up.
    PROTOCOL_OR_SERVER = "PROTOCOL_OR_SERVER"
    #: No stored password, or password fallback is administratively disabled.
    NO_CREDENTIAL = "NO_CREDENTIAL"

    @property
    def retryable(self) -> bool:
        return self in (RefreshFailure.TRANSIENT, RefreshFailure.PROTOCOL_OR_SERVER)


def classify_login_failure(status: int | None, *, detail: str = "") -> RefreshFailure:
    """Map a login outcome onto a failure kind.

    ``status is None`` means the request never completed (DNS, refused, timed
    out) — the only case where "try again later" is honest.
    """
    if status is None:
        return RefreshFailure.TRANSIENT
    if status in (401, 403):
        return RefreshFailure.CREDENTIAL_REVOKED
    if 400 <= status < 500:
        return RefreshFailure.PROTOCOL_OR_SERVER
    return RefreshFailure.TRANSIENT


def should_refresh(
    *,
    expires_at: int | None,
    now: int,
    safety_margin_seconds: int = REFRESH_SAFETY_MARGIN_SECONDS,
) -> bool:
    """Whether the stored token is close enough to expiry to replace.

    A token with no recorded expiry is **kept**. Some peers issue
    non-expiring tokens; treating "no expiry recorded" as "expired" would log in
    again on every reconnect, which is a login storm against the user's own
    instance. The peer is the authority on validity — when it does reject the
    token, the caller drops the cached value and re-authenticates.
    """
    if expires_at is None:
        return False
    return expires_at - now <= safety_margin_seconds


def refresh_delay_seconds(
    *, expires_at: int | None, now: int, jitter_fraction: float = 0.1
) -> float:
    """How long to wait before refreshing, with jitter to de-cluster peers.

    Returns 0 when the token is already inside the safety margin.
    """
    if expires_at is None:
        return 0.0
    remaining = float(expires_at - now)
    if remaining <= 0:
        return 0.0
    base = max(0.0, remaining - REFRESH_SAFETY_MARGIN_SECONDS)
    if base <= 0 or jitter_fraction <= 0:
        return base
    # Full-width jitter around ``base`` keeps the expected delay unchanged
    # while spreading a fleet of instances out over the window.
    return max(0.0, base * (1.0 + random.uniform(-jitter_fraction, jitter_fraction)))


@dataclass
class _Flight:
    task: asyncio.Task[str] | None = None
    result: str | None = None


class TokenRefresher:
    """Single-flight token acquisition, keyed by connection.

    Concurrent callers for the same connection share one login. Callers for
    *different* connections do not block each other: two healthy links to the
    same peer must not serialise behind one slow login.
    """

    def __init__(
        self,
        login: Callable[[], Awaitable[str]],
        *,
        jitter_fraction: float = 0.1,
    ) -> None:
        self._login = login
        self._jitter = jitter_fraction
        self._flights: dict[str, _Flight] = {}
        self._results: dict[str, str] = {}
        self.logins = 0

    def cached(self, connection_id: str) -> str | None:
        return self._results.get(connection_id)

    def seed(self, connection_id: str, token: str) -> None:
        """Adopt a token obtained elsewhere without triggering a login."""
        self._results[connection_id] = token

    def drop(self, connection_id: str) -> None:
        """Forget a token — on logout, revoke, or connection deletion."""
        self._results.pop(connection_id, None)
        flight = self._flights.pop(connection_id, None)
        if flight is not None and flight.task is not None and not flight.task.done():
            flight.task.cancel()

    async def acquire(self, connection_id: str) -> str:
        """Return a usable token, logging in at most once per burst."""
        cached = self._results.get(connection_id)
        if cached is not None:
            return cached

        flight = self._flights.get(connection_id)
        if flight is not None and flight.task is not None and not flight.task.done():
            # Someone else is already logging in for this connection.
            return await asyncio.shield(flight.task)

        async def _do() -> str:
            self.logins += 1
            return await self._login()

        task: asyncio.Task[str] = asyncio.ensure_future(_do())
        self._flights[connection_id] = _Flight(task=task)
        try:
            token = await asyncio.shield(task)
        except Exception:
            # A failed login must not be cached; the next attempt retries.
            self._flights.pop(connection_id, None)
            raise
        self._results[connection_id] = token
        self._flights.pop(connection_id, None)
        return token
