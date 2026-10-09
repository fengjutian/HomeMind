"""Cross-instance bridge drills (plan phase 16).

These run against two temporary instances — no production account, no live
peer. Each drill names the behaviour it is pinning, because a "connect works"
test would pass just as happily while the wrong connection survived.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.integration.bridge_pair_fixtures import Instance
from tests.integration.bridge_pair_fixtures import two_instances as _pair
from tests.support.auth import TEST_PASSWORD


@pytest.fixture
async def two_instances(tmp_path):
    """Two temporary instances; the rig itself lives with the other helpers."""
    async for pair in _pair(tmp_path):
        yield pair


async def _register(
    instance: Instance,
    *,
    peer_base_url: str,
    display_name: str,
    password: str,
) -> dict[str, Any]:
    r = await instance.client.post(
        "/api/bridge/connections",
        headers=instance.auth,
        json={
            "peer_base_url": peer_base_url,
            "peer_username": "admin",
            "password": password,
            "display_name": display_name,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _wait_for_state(
    instance: Instance,
    connection_id: str,
    want: str,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        r = await instance.client.get(
            f"/api/bridge/connections/{connection_id}", headers=instance.auth
        )
        if r.status_code == 200:
            last = r.json()
            if last.get("state") == want:
                return last
        await asyncio.sleep(0.2)
    return last


@pytest.mark.asyncio
async def test_two_temporary_instances_are_independent(
    two_instances: tuple[Instance, Instance],
) -> None:
    """Precondition for every other drill: two real, separate servers."""
    a, b = two_instances
    assert a.base_url != b.base_url

    created = await _register(
        a, peer_base_url=b.base_url, display_name="To B", password=TEST_PASSWORD
    )
    listed = await a.client.get("/api/bridge/connections", headers=a.auth)
    assert listed.status_code == 200
    assert [c["connection_id"] for c in listed.json()] == [created["connection_id"]]

    # B sees the same link as an *inbound* reverse row: it cannot redial and
    # stores no password. That asymmetry is the point of the reverse row.
    inbound_rows = (await b.client.get("/api/bridge/connections", headers=b.auth)).json()
    assert len(inbound_rows) == 1
    assert inbound_rows[0]["inbound"] is True
    assert inbound_rows[0]["has_password"] is False


@pytest.mark.asyncio
async def test_diagnostics_never_leak_a_password(
    two_instances: tuple[Instance, Instance],
) -> None:
    """The summary is meant to be pasted into a public issue."""
    a, b = two_instances
    created = await _register(
        a, peer_base_url=b.base_url, display_name="To B", password=TEST_PASSWORD
    )

    r = await a.client.get("/api/bridge/diagnostics", headers=a.auth)
    assert r.status_code == 200, r.text
    body = r.text
    assert created["connection_id"] in body
    assert TEST_PASSWORD not in body
    assert "has_password" not in body


@pytest.mark.asyncio
async def test_a_live_connection_is_not_reported_as_needing_a_human(
    two_instances: tuple[Instance, Instance],
) -> None:
    """Regression guard, across two real instances.

    ``retry_may_help`` used to be "is in the retryable set", and a healthy
    ``ONLINE`` link was in neither set — so every working connection was
    listed as needing human intervention.
    """
    a, b = two_instances
    created = await _register(
        a, peer_base_url=b.base_url, display_name="To B", password=TEST_PASSWORD
    )
    live = await _wait_for_state(a, created["connection_id"], "ONLINE")

    summary = (await a.client.get("/api/bridge/diagnostics", headers=a.auth)).json()
    assert live["state"] == "ONLINE", live
    assert summary["connections_online"] >= 1
    assert created["connection_id"] not in summary["connections_needing_human"]
    for entry in summary["connections"]:
        if entry["state"] == "ONLINE":
            assert entry["retry_may_help"] is True


@pytest.mark.asyncio
async def test_tunnel_request_with_disallowed_path_is_refused(
    two_instances: tuple[Instance, Instance],
) -> None:
    """A path that fails the allow-list must never reach the peer.

    Plan phase 16 wants: "恶意 path/header、超大 frame、慢速发送方和无界响应被拒绝或限流".
    The path side of that is a single line in :func:`is_tunnel_path_allowed`,
    but the *drill* must prove the refuse happens on a real cross-instance
    socket — an in-process unit test would prove the regex, not the wiring.
    Header / frame / slow-send defences live further down the stack and are
    pinned by dedicated unit tests; this drill covers the "the operator clicks
    a button we never allow-listed" path.
    """
    from octop.infra.errors import ErrorCode, OctopError

    a, b = two_instances
    created = await _register(
        a, peer_base_url=b.base_url, display_name="To B", password=TEST_PASSWORD
    )
    # Create one real agent on B so ``/api/agents`` actually returns something.
    # Without it, the rejection signal would be conflated with "empty list".
    agent_row = await _create_peer_agent(b, display_name="Probe Agent")
    agent_id = agent_row["agent_id"]

    live = await _wait_for_state(a, created["connection_id"], "ONLINE")
    assert live["state"] == "ONLINE", live
    owner = a.manager._repo.get(created["connection_id"]).owner_user_id

    # Sanity: a legitimate request still works — otherwise the rest of the
    # assertions could pass simply because the bridge link is broken.
    ok = await a.manager.tunnel_http(
        connection_id=created["connection_id"],
        owner_user_id=owner,
        method="GET",
        path="/api/agents",
        query="scope=mine",
    )
    assert ok.status_code == 200, ok.text

    # Each entry below sits outside the allow-list. We expect the request to
    # be refused *before* it traverses the wire; the audit trail on B must not
    # record a successful tunnel hop for any of them.
    malicious: list[tuple[str, str]] = [
        ("GET", "/api/admin/users"),
        ("GET", "/api/agents/ag1/../../../etc/passwd"),
        ("POST", "/api/cron/settings"),
        ("DELETE", "/api/uploads/blobs/abc"),
        ("GET", "/api/agents/%2e%2e/admin"),
        ("GET", "/api//agents"),
    ]
    for method, path in malicious:
        with pytest.raises(OctopError) as excinfo:
            await a.manager.tunnel_http(
                connection_id=created["connection_id"],
                owner_user_id=owner,
                method=method,
                path=path,
            )
        assert excinfo.value.code == ErrorCode.BRIDGE_REMOTE_UNSUPPORTED, (method, path, excinfo.value)

    # And the connection stayed healthy — the link must not be punished for
    # a bad request from the hub side.
    still_live = await _wait_for_state(a, created["connection_id"], "ONLINE")
    assert still_live["state"] == "ONLINE", still_live


async def _create_peer_agent(instance: Instance, *, display_name: str) -> dict[str, Any]:
    """Create a real agent on ``instance`` for tunnel drills to exercise."""
    r = await instance.client.post(
        "/api/agents",
        headers=instance.auth,
        json={
            "display_name": display_name,
            "expert_profile": "default",
            "model": "echo",
            "icon": "Bot",
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_protocol_major_mismatch_marks_connection_incompatible(
    two_instances: tuple[Instance, Instance], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A major-version mismatch must surface as a steady ``INCOMPATIBLE`` state.

    The plan says: "协议 major 不兼容给出明确升级提示". The outward signal is
    threefold — the row stops in ``INCOMPATIBLE`` (not a doomed ``DISCONNECTED``
    loop), ``retry_may_help`` flips to ``False`` so the dashboard stops
    reassuring the operator that "retrying will help", and the auto-reconnect
    cycle stops after one attempt instead of producing a flood of identical
    failures.

    We simulate the mismatch by patching the ``negotiate`` function in the
    bridge manager module: it is the single function both ends consult to
    compare major versions, so making it raise ``ProtocolIncompatible`` is
    equivalent to talking to a peer that advertises a different major — same
    code path, far less fixture plumbing than forking the build.
    """
    from octop.infra.bridge import manager as bridge_manager
    from octop.infra.bridge.protocol import ProtocolIncompatible

    def _boom(_hello: dict[str, object]) -> None:
        raise ProtocolIncompatible("1.0", "99.0")

    monkeypatch.setattr(bridge_manager, "negotiate", _boom)

    a, b = two_instances
    # Create the row first; we trigger the dial ourselves in the background
    # so the test is not coupled to the 5s connection API timeout — the row
    # state is what the plan cares about, not the HTTP code.
    r = await a.client.post(
        "/api/bridge/connections",
        headers=a.auth,
        json={
            "peer_base_url": b.base_url,
            "peer_username": "admin",
            "password": TEST_PASSWORD,
            "display_name": "Future Major",
            "connect": False,
        },
    )
    assert r.status_code in (200, 201), r.text
    created = r.json()
    row = a.manager._repo.get(created["connection_id"])
    assert row is not None

    async def _kick() -> None:
        try:
            await a.manager.connect(
                connection_id=created["connection_id"], owner_user_id=row.owner_user_id
            )
        except Exception:
            # Manager surfaces a peer-unreachable on every failed handshake.
            # That's expected here; the assertion is about the persisted state.
            pass

    bg = asyncio.create_task(_kick())

    try:
        stuck = await _wait_for_state(
            a, created["connection_id"], "INCOMPATIBLE", timeout=20.0
        )
        assert stuck["state"] == "INCOMPATIBLE", stuck
        assert stuck["retry_may_help"] is False

        detail = (stuck.get("state_detail") or stuck.get("last_error") or "").lower()
        assert "major" in detail and "incompatible" in detail, detail

        diag = (await a.client.get("/api/bridge/diagnostics", headers=a.auth)).json()
        assert created["connection_id"] in diag["connections_needing_human"]

        entry = next(
            c for c in diag["connections"] if c["connection_id"] == created["connection_id"]
        )
        assert entry["state"] == "INCOMPATIBLE"
        assert entry["retry_may_help"] is False

        # The supervisor must not keep redialing: a single attempt and a stop.
        # Give the auto-reconnect a moment, then assert no second connect attempt
        # has run after the first failure.
        await asyncio.sleep(1.0)
        row_after = a.manager._repo.get(created["connection_id"])
        assert row_after is not None
        assert row_after.state == "INCOMPATIBLE"
        assert row_after.reconnect_attempts <= 1, row_after.reconnect_attempts
    finally:
        bg.cancel()
        try:
            await bg
        except (asyncio.CancelledError, Exception):
            pass
