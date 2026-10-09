"""Fair multiplexed send queue (plan phase 15).

The property under test: a bulk frame must never be sent ahead of a control
frame, even when the bulk frame was queued first.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from octop.infra.bridge.send_queue import (
    DEFAULT_LANE_BUDGETS,
    Lane,
    LaneBudgetExceeded,
    PrioritySendQueue,
    lane_for_frame,
)

KB = 1024


class TestClassification:
    @pytest.mark.parametrize(
        "frame_type,lane",
        [
            ("close", Lane.CONTROL),
            ("hello", Lane.CONTROL),
            ("hello_ack", Lane.CONTROL),
            ("tunnel.request", Lane.TUNNEL),
            ("tunnel.response", Lane.TUNNEL),
            ("tunnel.error", Lane.TUNNEL),
            ("turn.frame", Lane.STREAM),
            ("browser.frame", Lane.STREAM),
            ("something.new", Lane.STREAM),
            ("", Lane.STREAM),
        ],
    )
    def test_frames_are_filed_by_type(self, frame_type: str, lane: Lane) -> None:
        assert lane_for_frame(frame_type) is lane

    def test_an_unknown_type_cannot_outrank_a_chat_token(self) -> None:
        assert lane_for_frame("totally.new") is Lane.STREAM


def _queue(sink, **kw) -> PrioritySendQueue:
    return PrioritySendQueue(sink, **kw)


class TestOrdering:
    async def test_control_frames_drain_first_when_nothing_is_in_flight(self) -> None:
        """With an idle socket, strict priority applies to the whole backlog."""
        sent: list[str] = []
        gate = asyncio.Event()

        async def sink(text: str) -> None:
            sent.append(text)
            await gate.wait()

        q = _queue(sink)
        await q.send({"type": "turn.frame", "n": 1})
        await q.send({"type": "close"})
        gate.set()
        await asyncio.sleep(0.05)

        order = [text for text in sent]
        assert '"close"' in order[0], "control must go first even though queued last"

    async def test_tunnel_outranks_stream(self) -> None:
        sent: list[str] = []
        gate = asyncio.Event()

        async def sink(text: str) -> None:
            sent.append(text)
            await gate.wait()

        q = _queue(sink)
        await q.send({"type": "turn.frame", "n": 1})
        await q.send({"type": "tunnel.request", "n": 2})
        gate.set()
        await asyncio.sleep(0.05)

        assert "tunnel.request" in sent[0]

    async def test_ordering_within_a_lane_is_fifo(self) -> None:
        sent: list[str] = []
        gate = asyncio.Event()

        async def sink(text: str) -> None:
            sent.append(text)
            await gate.wait()

        q = _queue(sink)
        for n in range(4):
            await q.send({"type": "turn.frame", "n": n})
        gate.set()
        await asyncio.sleep(0.05)

        assert [t[t.index('"n":') + 4 :].strip(" }") for t in sent] == ["0", "1", "2", "3"]


class TestBudgets:
    async def test_a_queued_frame_is_actually_sent(self) -> None:
        sent: list[str] = []

        async def sink(text: str) -> None:
            sent.append(text)

        q = _queue(sink)
        await q.send({"type": "close"})
        await asyncio.sleep(0.05)
        assert len(sent) == 1
        assert q.pending_frames == 0

    async def test_a_frame_bigger_than_its_whole_budget_is_shed(self) -> None:
        """Waiting would deadlock the lane, so shed instead of buffering."""
        sent: list[str] = []

        async def sink(text: str) -> None:
            sent.append(text)

        q = _queue(sink, budgets={Lane.STREAM: 64, Lane.TUNNEL: 64, Lane.CONTROL: 64})
        with pytest.raises(LaneBudgetExceeded) as excinfo:
            await q.send({"type": "turn.frame", "pad": "x" * 500})
        assert excinfo.value.lane is Lane.STREAM
        assert q.shed == 1

    async def test_queueing_stays_within_the_budget(self) -> None:
        """The whole point: a session cannot buffer without bound."""
        gate = asyncio.Event()

        async def sink(text: str) -> None:
            await gate.wait()

        q = _queue(sink, budgets={Lane.STREAM: 8 * KB, Lane.TUNNEL: 8 * KB, Lane.CONTROL: 8 * KB})
        for n in range(4):
            await q.send({"type": "turn.frame", "pad": "y" * 1024, "n": n})
        await asyncio.sleep(0.02)

        # Two frames in flight/budgeted; the rest are waiting for room rather
        # than being buffered.
        assert q.queued_bytes[Lane.STREAM] <= 8 * KB
        gate.set()
        await asyncio.sleep(0.05)

    async def test_bulk_never_queues_beyond_its_budget(self) -> None:
        """The anti-starvation guarantee, stated precisely.

        A single socket cannot preempt a write in progress, so the promise is
        not "a close is sent immediately" — it is that bulk traffic can never
        queue more than its lane budget ahead of anything. That bound is what
        stops a 2 GB upload from starving a keepalive.
        """
        budgets = {Lane.TUNNEL: 4 * KB, Lane.CONTROL: 4 * KB, Lane.STREAM: 4 * KB}
        peak: list[int] = []

        async def sink(_text: str) -> None:
            peak.append(q.queued_bytes[Lane.TUNNEL])

        q = _queue(sink, budgets=budgets)
        frame = {"type": "tunnel.request", "pad": "z" * 1000}
        for _ in range(50):
            await q.send(dict(frame))
            await asyncio.sleep(0)
            peak.append(q.queued_bytes[Lane.TUNNEL])

        assert peak, "the sink must have run"
        assert max(peak) <= budgets[Lane.TUNNEL], "the lane buffered past its budget"

    async def test_a_control_frame_is_not_blocked_by_a_full_bulk_lane(self) -> None:
        """Bulk backing up must not stop a close from being queued."""
        budgets = {Lane.TUNNEL: 4 * KB, Lane.CONTROL: 4 * KB, Lane.STREAM: 4 * KB}
        gate = asyncio.Event()
        sent: list[str] = []

        async def sink(text: str) -> None:
            sent.append(text)
            await gate.wait()

        q = _queue(sink, budgets=budgets)
        frame = {"type": "tunnel.request", "pad": "z" * 1000}
        size = len(json.dumps(frame).encode())
        while q.queued_bytes[Lane.TUNNEL] + size <= budgets[Lane.TUNNEL]:
            await q.send(dict(frame))

        # Queue a control frame *before* yielding, so the pump cannot have run.
        control = asyncio.ensure_future(q.send({"type": "close"}))
        gate.set()
        await asyncio.wait_for(control, timeout=1)
        await asyncio.sleep(0.05)
        assert any('"close"' in text for text in sent)

    def test_default_budgets_are_ordered_control_tunnel_stream(self) -> None:
        assert (
            DEFAULT_LANE_BUDGETS[Lane.CONTROL]
            < DEFAULT_LANE_BUDGETS[Lane.TUNNEL]
            < DEFAULT_LANE_BUDGETS[Lane.STREAM]
        )
