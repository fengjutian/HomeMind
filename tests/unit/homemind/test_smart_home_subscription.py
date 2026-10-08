"""WebSocket subscription lifecycle (plan phase 6).

The state machine is provable without a real Home Assistant: the transport is
injected, so these tests drive a scripted socket — including a mid-stream drop
and a replayed frame — and assert what a reconnect must and must not do.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from homemind.infra.family.smart_home_subscription import (
    BACKOFF_SCHEDULE,
    HomeAssistantSubscriber,
    StateChange,
    StreamHandle,
    SubscriptionState,
)


def _event(entity_id: str, state: str, *, updated: str = "t1", **attrs: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "new_state": {
                    "state": state,
                    "attributes": attrs,
                    "last_changed": updated,
                    "last_updated": updated,
                },
            },
        },
    }


class _ScriptedSocket:
    """A connection that yields *frames* and then ends, as a dropped socket does."""

    def __init__(self, frames: list[Any], *, fail_on_receive: bool = False) -> None:
        self.frames = list(frames)
        self.fail_on_receive = fail_on_receive
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def receive(self) -> Any:
        if self.fail_on_receive:
            raise ConnectionError("socket died")
        if not self.frames:
            return None
        return self.frames.pop(0)

    async def send(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)

    async def close(self) -> None:
        self.closed = True

    def handle(self) -> StreamHandle:
        return StreamHandle(receive=self.receive, send=self.send, close=self.close)


def _subscriber(
    sockets: list[_ScriptedSocket],
    applied: list[StateChange],
    reconciles: list[int],
    *,
    stop_after: int,
) -> HomeAssistantSubscriber:
    """Build a subscriber that stops once *stop_after* connections were made."""
    remaining = {"n": stop_after}

    async def connect() -> StreamHandle:
        if not sockets:
            raise ConnectionError("no more sockets")
        if remaining["n"] <= 0:
            await asyncio.sleep(3600)  # park; the test will stop us
        remaining["n"] -= 1
        return sockets.pop(0).handle()

    async def reconcile() -> int:
        reconciles.append(1)
        return len(sockets)

    async def on_change(change: StateChange) -> None:
        applied.append(change)

    async def sleep(_seconds: float) -> None:
        # Must actually yield: a no-op coroutine would turn the retry loop
        # into a busy wait and starve the test's own scheduling.
        await asyncio.sleep(0)

    return HomeAssistantSubscriber(
        connect=connect, reconcile=reconcile, on_change=on_change, sleep=sleep
    )


class TestFrameParsing:
    def test_parses_a_state_changed_event(self) -> None:
        change = StateChange.from_event(_event("light.a", "on", brightness=10))
        assert change is not None
        assert change.entity_id == "light.a"
        assert change.state == "on"
        assert change.attributes == {"brightness": 10}

    @pytest.mark.parametrize(
        "raw",
        [
            {},
            {"type": "event"},
            {"event": {"event_type": "call_service", "data": {}}},
            {"event": {"event_type": "state_changed"}},
            {"event": {"event_type": "state_changed", "data": {"entity_id": ""}}},
            {"event": {"event_type": "state_changed", "data": {"entity_id": "x"}}},
            {"event": {"event_type": "state_changed", "data": {"entity_id": "x", "new_state": 1}}},
        ],
    )
    def test_ignores_anything_that_is_not_a_state_change(self, raw: dict[str, Any]) -> None:
        assert StateChange.from_event(raw) is None


class TestIncrementalUpdates:
    async def test_applies_events_in_order(self) -> None:
        socket = _ScriptedSocket(
            [
                _event("light.a", "on"),
                _event("light.a", "off", updated="t2"),
                _event("light.b", "on"),
            ]
        )
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sub = _subscriber([socket], applied, reconciles, stop_after=1)

        task = asyncio.create_task(sub.run())
        await asyncio.sleep(0)
        for _ in range(10):
            if sub.state == SubscriptionState.SUBSCRIBED and len(applied) >= 3:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()

        assert [c.entity_id for c in applied] == ["light.a", "light.a", "light.b"]
        assert applied[1].state == "off"
        assert sub.applied == 3

    async def test_duplicate_event_is_dropped(self) -> None:
        """A replayed frame must not re-apply and oscillate the recorded state."""
        frame = _event("light.a", "on")
        socket = _ScriptedSocket([frame, dict(frame), _event("light.a", "off", updated="t2")])
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sub = _subscriber([socket], applied, reconciles, stop_after=1)

        task = asyncio.create_task(sub.run())
        for _ in range(12):
            if len(applied) >= 2:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()

        assert [c.state for c in applied] == ["on", "off"]
        assert sub.duplicates == 1

    async def test_byte_frames_are_decoded(self) -> None:
        import json as _json

        payload = _json.dumps(_event("light.a", "on")).encode()
        socket = _ScriptedSocket([payload, b"not json", None])
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sub = _subscriber([socket], applied, reconciles, stop_after=1)

        task = asyncio.create_task(sub.run())
        for _ in range(12):
            if applied:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()

        assert len(applied) == 1


class TestReconnect:
    async def test_reconcile_runs_on_every_connect(self) -> None:
        """Events missed while the socket was down are only recoverable by a
        full read, so every connection must start from truth."""
        first = _ScriptedSocket([_event("light.a", "on")])
        second = _ScriptedSocket([_event("light.a", "off", updated="t2")])
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sub = _subscriber([first, second], applied, reconciles, stop_after=2)

        task = asyncio.create_task(sub.run())
        for _ in range(20):
            if len(reconciles) >= 2:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()

        assert len(reconciles) == 2, "reconnect without a reconcile serves stale data"
        assert sub.reconciles == 2

    async def test_reconnect_count_increments(self) -> None:
        first = _ScriptedSocket([])
        second = _ScriptedSocket([])
        third = _ScriptedSocket([])
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sub = _subscriber([first, second, third], applied, reconciles, stop_after=3)

        task = asyncio.create_task(sub.run())
        for _ in range(20):
            if sub.reconciles >= 3:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()

        assert sub.reconnects >= 2, "the first connect must not count as a reconnect"

    async def test_backoff_is_bounded_and_indexed(self) -> None:
        sub = HomeAssistantSubscriber(connect=_never, reconcile=_no_reconcile, on_change=_no_change)
        delays = [sub._backoff_for(n) for n in range(1, 12)]
        # Walks the schedule, then stays clamped at the ceiling.
        assert delays[: len(BACKOFF_SCHEDULE)] == list(BACKOFF_SCHEDULE)
        assert all(d == BACKOFF_SCHEDULE[-1] for d in delays[len(BACKOFF_SCHEDULE) :])
        assert max(delays) <= BACKOFF_SCHEDULE[-1]
        # Never shrinks back to the front after a long outage.
        assert delays == sorted(delays)

    async def test_failure_to_connect_is_retried(self) -> None:
        applied: list[StateChange] = []
        reconciles: list[int] = []
        sockets: list[_ScriptedSocket] = []
        sub = _subscriber(sockets, applied, reconciles, stop_after=1)
        task = asyncio.create_task(sub.run())
        for _ in range(12):
            if sub.attempt >= 2:
                break
            await asyncio.sleep(0)
        await sub.stop()
        task.cancel()
        assert sub.attempt >= 2, "an empty socket list must not stop the loop"


async def _never() -> StreamHandle:
    raise AssertionError("connect must not be called")


async def _no_reconcile() -> int:
    return 0


async def _no_change(_change: StateChange) -> None:
    return None
