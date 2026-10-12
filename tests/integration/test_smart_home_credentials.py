"""Smart-home provider credential lifecycle over the public API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


@pytest.fixture
async def smart_home_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, server):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, server, await auth_header(client)


async def test_home_assistant_token_is_stored_and_removed_with_provider(
    smart_home_env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = smart_home_env
    family_response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Smart home"},
    )
    assert family_response.status_code == 201, family_response.text
    family_id = family_response.json()["id"]

    token = "ha-token-that-must-not-be-returned"
    response = await client.post(
        f"/api/homemind/families/{family_id}/smart-home/providers",
        headers=auth,
        json={
            "kind": "HOME_ASSISTANT",
            "name": "Home Assistant",
            "base_url": "http://homeassistant.local:8123",
            "credential": token,
        },
    )
    assert response.status_code == 201, response.text
    provider = response.json()
    assert token not in response.text
    assert provider["secret_ref"].startswith(f"homemind/smart-home/{family_id}/")
    assert server.services is not None
    assert server.services.secret_repo.get(provider["secret_ref"]) == token.encode()

    deleted = await client.delete(
        f"/api/homemind/families/{family_id}/smart-home/providers/{provider['id']}",
        headers=auth,
    )
    assert deleted.status_code == 204, deleted.text
    assert server.services.secret_repo.get(provider["secret_ref"]) is None
