"""BridgeSession JSON encoding must tolerate LangChain message objects."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from octop.infra.bridge.transport import BridgeSession, bridge_json_default


@pytest.mark.asyncio
async def test_send_json_serializes_human_message() -> None:
    sent: list[str] = []

    async def capture(text: str) -> None:
        sent.append(text)

    sess = BridgeSession(connection_id="c1", send_text=capture)
    await sess.send_json(
        {
            "type": "turn.chunk",
            "frame": {
                "type": "messages",
                "messages": [HumanMessage(content="hi"), AIMessage(content="yo")],
            },
        }
    )
    # send_json now hands the frame to the priority pump, which owns the
    # socket; delivery happens on a later loop turn.
    await asyncio.sleep(0)
    assert len(sent) == 1
    payload = json.loads(sent[0])
    assert payload["type"] == "turn.chunk"
    msgs = payload["frame"]["messages"]
    assert isinstance(msgs, list) and len(msgs) == 2
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["content"] == "yo"


@pytest.mark.asyncio
async def test_close_frame_records_deleted_reason() -> None:
    async def noop(_text: str) -> None:
        return None

    sess = BridgeSession(connection_id="c1", send_text=noop)
    await sess.handle_message(json.dumps({"type": "close", "reason": "deleted"}))
    assert sess.close_reason == "deleted"
    assert sess.closed is True


def test_bridge_json_default_falls_back_to_repr() -> None:
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    assert bridge_json_default(Weird()) == "<weird>"


@pytest.mark.asyncio
async def test_tunnel_request_is_single_shot_when_peer_closes() -> None:
    """Plan phase 16: in-flight turn must not be replayed on mid-flight close.

    ``BridgeSession.tunnel_request`` is the mechanism the hub uses to forward a
    user turn onto the peer. If the peer's WebSocket closes mid-call (the
    canonical "token expired, you must re-auth" scenario) the in-flight
    future must reject, and the same frame must NOT be re-sent — replaying
    the same turn would double-charge the peer's session / execution budget
    and break the rule that turns are single-shot from the user's point of
    view.
    """
    sent: list[str] = []

    async def capture(text: str) -> None:
        sent.append(text)

    sess = BridgeSession(connection_id="c1", send_text=capture)
    req_task = asyncio.create_task(
        sess.tunnel_request(method="POST", path="/api/agents/x/turn", timeout=2.0)
    )
    # The session's send_json routes through a priority pump that owns the
    # socket; yield enough loop turns for it to actually deliver the frame.
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(sent) == 1, sent
    payload = json.loads(sent[0])
    assert payload["type"] == "tunnel.request"
    assert payload["path"] == "/api/agents/x/turn"
    req_id = payload["id"]

    # Peer rejects the token mid-flight (close code 4001) — the hub's
    # supervisor would also catch this and route to REAUTH_REQUIRED, but the
    # property we are pinning here is "no replay inside the session".
    await sess.close()
    # Drain any pending future resolution in the same loop turn.
    for _ in range(5):
        await asyncio.sleep(0)

    with pytest.raises(Exception):
        await req_task

    # Critically: only the first send ever happened. If a retry path is ever
    # added inside tunnel_request or the pending-future plumbing, this number
    # will climb and the failure modes listed above return.
    assert len(sent) == 1, sent
    # The future has been popped so a late response cannot resurrect the call.
    assert req_id not in sess._pending
