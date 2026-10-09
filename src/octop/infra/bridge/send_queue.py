"""Fair multiplexed send queue for a Bridge session (plan phase 15).

A single Bridge socket carries control frames, HTTP tunnel traffic, and chat /
browser streams. They are wildly different in size and latency need, and today
they share one write path: a 32 MB tunnel body is handed to the socket and
nothing — including a ``close`` or a keepalive — goes out until it has flushed.

That is the starvation the plan names: a large upload must not delay control
frames or a chat token. The fix is not more sockets; it is a queue that knows
which frame matters most.

Design notes:

* **Lanes are derived from the frame type**, so no call site changes and a new
  frame type cannot accidentally be filed as "important".
* **Each lane has a byte budget.** A large frame waits for room instead of
  being buffered, so the total memory a session can queue is bounded by the
  budgets rather than by the size of whatever happens to arrive.
* **Priority is strict, ordering within a lane is FIFO.**

What this does *not* do: it cannot preempt a frame that is already being
written. On a single socket a write in progress has to finish. The guarantee is
therefore precise rather than absolute — a control frame waits for *at most one
in-flight frame plus one lane budget* ahead of it, not for the whole backlog.
That bound is what keeps a multi-gigabyte upload from starving a keepalive:
the tunnel lane cannot queue more than its budget, so at most a megabyte of
tunnel data can ever stand in front of a control frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["Lane", "PrioritySendQueue", "lane_for_frame"]


class Lane(StrEnum):
    """Traffic classes, highest priority first."""

    #: Teardown, keepalive, protocol negotiation. Must never queue behind data.
    CONTROL = "control"
    #: HTTP requests and responses. Bounded body, user-visible latency.
    TUNNEL = "tunnel"
    #: Chat turns and browser frames. Bulk, latency-tolerant, easiest to shed.
    STREAM = "stream"


_PRIORITY: tuple[Lane, ...] = (Lane.CONTROL, Lane.TUNNEL, Lane.STREAM)

#: Queued bytes allowed per lane. The tunnel lane is the tightest one because a
#: single request body is what actually competes with a keepalive.
DEFAULT_LANE_BUDGETS: dict[Lane, int] = {
    Lane.CONTROL: 256 * 1024,
    Lane.TUNNEL: 1024 * 1024,
    Lane.STREAM: 4 * 1024 * 1024,
}


def lane_for_frame(frame_type: str) -> Lane:
    """Classify a frame by its ``type``.

    Unknown types go to the lowest-priority lane: a frame nobody recognises
    must not be able to outrank a chat token or a close.
    """
    if frame_type in {"close", "ping", "pong", "hello", "hello_ack"}:
        return Lane.CONTROL
    if frame_type.startswith("tunnel."):
        return Lane.TUNNEL
    if frame_type.startswith("turn.") or frame_type.startswith("browser."):
        return Lane.STREAM
    return Lane.STREAM


def _frame_size(text: str) -> int:
    return len(text.encode("utf-8"))


class PrioritySendQueue:
    """Per-lane byte-budgeted queue with strict priority draining."""

    def __init__(
        self,
        sink: Callable[[str], Awaitable[None]],
        *,
        budgets: dict[Lane, int] | None = None,
        dumps: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        self._sink = sink
        self._budgets = dict(budgets or DEFAULT_LANE_BUDGETS)
        # The caller's serializer, not a generic one: the session serialises
        # LangChain messages and octop datetimes, and a plain ``default=str``
        # would silently flatten them into text.
        self._dumps = dumps or (
            lambda payload: json.dumps(payload, ensure_ascii=False, default=str)
        )
        self._queues: dict[Lane, deque[tuple[str, int]]] = {lane: deque() for lane in _PRIORITY}
        self._queued_bytes: dict[Lane, int] = dict.fromkeys(_PRIORITY, 0)
        self._space = {lane: asyncio.Event() for lane in _PRIORITY}
        for event in self._space.values():
            event.set()
        self._pumping = False
        self.shed = 0

    @property
    def queued_bytes(self) -> dict[Lane, int]:
        return dict(self._queued_bytes)

    @property
    def pending_frames(self) -> int:
        return sum(len(q) for q in self._queues.values())

    async def send(self, payload: dict[str, Any]) -> None:
        """Enqueue one frame, waiting for lane budget before it is buffered."""
        text = self._dumps(payload)
        size = _frame_size(text)
        lane = lane_for_frame(str(payload.get("type") or ""))

        if size > self._budgets[lane]:
            # Larger than the whole budget: waiting would deadlock the lane, so
            # shed it rather than buffer without bound. Dropping a bulk frame
            # surfaces as a failed request; buffering it would take the session
            # down with it.
            self.shed += 1
            logger.warning(
                "bridge frame %s (%d B) exceeds the %s lane budget; shedding",
                payload.get("type"),
                size,
                lane.value,
            )
            raise LaneBudgetExceeded(lane, size, self._budgets[lane])

        await self._acquire(lane, size)
        self._queues[lane].append((text, size))
        self._queued_bytes[lane] += size
        self._ensure_pump()

    async def _acquire(self, lane: Lane, size: int) -> None:
        while self._queued_bytes[lane] + size > self._budgets[lane]:
            self._space[lane].clear()
            await self._space[lane].wait()

    def _release(self, lane: Lane, size: int) -> None:
        self._queued_bytes[lane] = max(0, self._queued_bytes[lane] - size)
        self._space[lane].set()

    def _ensure_pump(self) -> None:
        if self._pumping:
            return
        self._pumping = True
        asyncio.get_running_loop().create_task(self._pump())

    def _next_frame(self) -> tuple[Lane, str, int] | None:
        for lane in _PRIORITY:
            queue = self._queues[lane]
            if queue:
                text, size = queue[0]
                return lane, text, size
        return None

    async def _pump(self) -> None:
        try:
            while True:
                item = self._next_frame()
                if item is None:
                    return
                lane, text, size = item
                self._queues[lane].popleft()
                # Release the slot before the write: the frame is already out of
                # our hands, and holding the budget across a slow write would
                # stall every other frame in the lane.
                self._queued_bytes[lane] = max(0, self._queued_bytes[lane] - size)
                self._space[lane].set()
                await self._sink(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("bridge send pump failed")
        finally:
            self._pumping = False


class LaneBudgetExceeded(Exception):
    """A frame is larger than its lane can ever buffer."""

    def __init__(self, lane: Lane, size: int, budget: int) -> None:
        super().__init__(f"{lane.value} frame of {size} B exceeds its {budget} B budget")
        self.lane = lane
        self.size = size
        self.budget = budget
