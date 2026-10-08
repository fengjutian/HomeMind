"""Home Assistant WebSocket subscription (plan phase 6).

Home Assistant pushes every state change over a WebSocket at
``/api/websocket``. Polling catches up eventually, but a household wants the
light to reflect reality in about a second, and a poll loop large enough to
feel live is a poll loop that hammers a home server.

Three properties this module exists to guarantee:

* **Reconnect with bounded backoff.** A restarted Home Assistant, a flaky
  Wi-Fi link, or a sleeping laptop all look the same: the socket dies. The
  retry schedule is capped and jittered so a house full of clients does not
  resume in lockstep.
* **Full reconcile after every reconnect.** Events that fired while the
  socket was down were never seen, so a resumed connection must not trust its
  own memory — it re-reads the whole state before resuming incremental
  updates. Skipping this is how a "connected" integration silently serves
  stale data for hours.
* **Duplicate and replayed events are harmless.** A reconnect replays
  nothing, but a proxy may, and a duplicate update must not flip a device's
  recorded state back and forth.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Reconnect backoff schedule in seconds. Bounded on purpose: a long outage
#: should recover within a minute, not drift into minutes between attempts.
BACKOFF_SCHEDULE: tuple[float, ...] = (1, 2, 5, 10, 20, 30)
MAX_BACKOFF_SECONDS = BACKOFF_SCHEDULE[-1]

#: Home Assistant's own handshake/command timeout.
HA_COMMAND_TIMEOUT_SECONDS = 10


class SubscriptionState:
    """Observable connection states, mirroring the provider health fields."""

    IDLE = "idle"
    CONNECTING = "connecting"
    SUBSCRIBED = "subscribed"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


@dataclass
class StateChange:
    """One normalized ``state_changed`` event."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str | None = None
    last_updated: str | None = None

    @classmethod
    def from_event(cls, raw: dict[str, Any]) -> StateChange | None:
        """Parse one HA ``state_changed`` frame. ``None`` for anything else.

        A frame that is not a state change — ``ping``, ``pong``, a
        subscription confirmation, or a future event type — is not an error;
        it simply has nothing to apply.
        """
        event = raw.get("event")
        if not isinstance(event, dict):
            return None
        if event.get("event_type") != "state_changed":
            return None
        data = event.get("data")
        if not isinstance(data, dict):
            return None
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            return None
        new_state = data.get("new_state")
        if not isinstance(new_state, dict):
            return None
        attributes = new_state.get("attributes")
        return cls(
            entity_id=entity_id,
            state=str(new_state.get("state") or ""),
            attributes=dict(attributes) if isinstance(attributes, dict) else {},
            last_changed=(
                str(new_state["last_changed"]) if new_state.get("last_changed") else None
            ),
            last_updated=(
                str(new_state["last_updated"]) if new_state.get("last_updated") else None
            ),
        )


#: One connection attempt's worth of behavior. Implemented over a websocket
#: library in production; injected in tests so the state machine is provable
#: without a real Home Assistant.
@dataclass
class StreamHandle:
    """A live connection: read frames, send a command, close."""

    receive: Callable[[], Awaitable[Any]]
    send: Callable[[dict[str, Any]], Awaitable[None]]
    close: Callable[[], Awaitable[None]]


class HomeAssistantSubscriber:
    """Owns one provider's WebSocket lifecycle.

    ``connect`` yields a :class:`StreamHandle`; the reconnect policy, the
    post-reconnect reconcile and the duplicate suppression all live here so
    the transport stays swappable.
    """

    def __init__(
        self,
        *,
        connect: Callable[[], Awaitable[StreamHandle]],
        reconcile: Callable[[], Awaitable[int]],
        on_change: Callable[[StateChange], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._connect = connect
        self._reconcile = reconcile
        self._on_change = on_change
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or (lambda s: s)
        self.state = SubscriptionState.IDLE
        self.attempt = 0
        self.reconciles = 0
        self.reconnects = 0
        self.applied = 0
        self.duplicates = 0
        self._stop = asyncio.Event()
        #: entity_id -> digest of the last applied frame, so a replayed or
        #: duplicated event is recognized and dropped instead of re-applied.
        self._seen: dict[str, str] = {}

    async def run(self) -> None:
        """Subscribe and keep the subscription alive until stopped."""
        self._stop.clear()
        while not self._stop.is_set():
            handle: StreamHandle | None = None
            try:
                self.state = (
                    SubscriptionState.RECONNECTING if self.attempt else SubscriptionState.CONNECTING
                )
                handle = await self._connect()
                self.attempt = 0
                await handle.send(
                    {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
                )
                # Anything that changed while we were away is invisible to an
                # incremental stream, so every (re)connect starts from truth.
                await self._reconcile()
                self.reconciles += 1
                self._seen.clear()
                self.state = SubscriptionState.SUBSCRIBED
                await self._pump(handle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a dead socket is normal
                logger.info(
                    "HomeAssistant: subscription dropped (%s); reconnecting",
                    type(exc).__name__,
                )
            finally:
                if handle is not None:
                    await _safe_close(handle)
            if self._stop.is_set():
                break
            self.attempt += 1
            if self.attempt == 1:
                self.reconnects += 1
            delay = self._backoff_for(self.attempt)
            logger.info("HomeAssistant: reconnect attempt %s in %.1fs", self.attempt, delay)
            await self._sleep(delay)
        self.state = SubscriptionState.STOPPED

    async def stop(self) -> None:
        self._stop.set()
        self.state = SubscriptionState.STOPPED

    def _backoff_for(self, attempt: int) -> float:
        index = min(max(attempt, 1), len(BACKOFF_SCHEDULE)) - 1
        return min(BACKOFF_SCHEDULE[index], MAX_BACKOFF_SECONDS) * self._jitter(1.0)

    async def _pump(self, handle: StreamHandle) -> None:
        """Read frames until the socket closes or stop is requested."""
        while not self._stop.is_set():
            raw = await handle.receive()
            if raw is None:
                return
            if isinstance(raw, (bytes, bytearray)):
                try:
                    raw = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            if not isinstance(raw, dict):
                continue
            change = StateChange.from_event(raw)
            if change is None:
                continue
            digest = _digest(change)
            if self._seen.get(change.entity_id) == digest:
                self.duplicates += 1
                continue
            self._seen[change.entity_id] = digest
            await self._on_change(change)
            self.applied += 1


def _digest(change: StateChange) -> str:
    """Stable fingerprint of an event's payload."""
    return json.dumps(
        [change.entity_id, change.state, change.attributes, change.last_updated],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


async def _safe_close(handle: StreamHandle) -> None:
    try:
        await handle.close()
    except Exception:  # noqa: BLE001 - closing must never mask the real error
        logger.debug("HomeAssistant: closing subscription socket failed", exc_info=True)


__all__ = [
    "BACKOFF_SCHEDULE",
    "MAX_BACKOFF_SECONDS",
    "HomeAssistantSubscriber",
    "StateChange",
    "StreamHandle",
    "SubscriptionState",
]
