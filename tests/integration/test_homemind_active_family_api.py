"""Stage 3: integration coverage for the active-family endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


@pytest.fixture
async def homemind_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, srv, await auth_header(client)


@pytest.mark.asyncio
async def test_active_family_unset_returns_null(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.get("/api/homemind/me/active-family", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"family_id": None}


@pytest.mark.asyncio
async def test_active_family_set_then_get(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Active Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    response = await client.put(
        "/api/homemind/me/active-family",
        headers=auth,
        json={"family_id": family_id},
    )
    assert response.status_code == 200
    assert response.json() == {"family_id": family_id}

    response = await client.get("/api/homemind/me/active-family", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"family_id": family_id}


@pytest.mark.asyncio
async def test_active_family_set_rejects_unauthorized_family(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.put(
        "/api/homemind/me/active-family",
        headers=auth,
        json={"family_id": "ghost"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_active_family_clear_resets(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Clear Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]
    await client.put(
        "/api/homemind/me/active-family",
        headers=auth,
        json={"family_id": family_id},
    )

    response = await client.delete("/api/homemind/me/active-family", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"family_id": None}

    response = await client.get("/api/homemind/me/active-family", headers=auth)
    assert response.json() == {"family_id": None}
