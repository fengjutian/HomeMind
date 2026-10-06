"""Stage 12: integration coverage for the metrics endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from homemind.infra.metrics import METRICS
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


@pytest.fixture
async def homemind_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    METRICS.reset()
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, srv, await auth_header(client)


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_snapshot_for_admin(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.get("/api/homemind/metrics", headers=auth)
    assert response.status_code == 200
    payload = response.json()
    expected_keys = {
        "permission_allow_total",
        "transaction_plan_total",
        "memory_candidate_create_total",
        "asset_scan_sweep_total",
        "device_heartbeat_total",
        "invite_mint_total",
    }
    assert expected_keys <= set(payload.keys())
    assert all(isinstance(value, int) for value in payload.values())


@pytest.mark.asyncio
async def test_metrics_reflect_traffic(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env

    # Trigger one permission evaluation so the counter moves.
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Metric Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    # Look up the auto-created owner member so we evaluate on a real id.
    response = await client.get(
        f"/api/homemind/families/{family_id}/members", headers=auth,
    )
    assert response.status_code == 200
    owner_member_id = response.json()[0]["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/permissions/evaluate",
        headers=auth,
        json={"action": "memory.read"},
    )
    assert response.status_code == 200

    response = await client.get("/api/homemind/metrics", headers=auth)
    payload = response.json()
    assert payload["permission_deny_total"] + payload["permission_allow_total"] >= 1


@pytest.mark.asyncio
async def test_metrics_rejects_non_admin(
    tmp_octop_home: Path,
) -> None:
    from tests.support.auth import create_user

    METRICS.reset()
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        admin_auth = await auth_header(client := client) if False else None  # noqa: E501
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        regular = await create_user(client, admin, username="regular")
        regular_auth = {"Authorization": regular["Authorization"]}
        response = await client.get(
            "/api/homemind/metrics", headers=regular_auth,
        )
        assert response.status_code == 400
