"""Stage 10: integration coverage for family invite endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user


@pytest.fixture
async def homemind_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        new_user = await create_user(client, admin, username="redeemer")
        yield client, srv, {**admin, "redeemer": new_user["Authorization"]}


@pytest.mark.asyncio
async def test_invite_full_flow(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Invite Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    # Manager mints an invite.
    response = await client.post(
        f"/api/homemind/families/{family_id}/invites",
        headers=auth,
        json={"display_name": "Child", "role": "CHILD", "ttl_seconds": 600},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["token"]
    assert created["expires_at"] > 0
    invite_id = created["invite_id"]
    token = created["token"]

    # Listing omits redeemed invites by default.
    response = await client.get(
        f"/api/homemind/families/{family_id}/invites", headers=auth,
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == invite_id
    assert rows[0]["redeemed_at"] is None

    # Redeemer exchanges the token for a membership bound to their user.
    redeemer_auth = {"Authorization": auth["redeemer"]}
    response = await client.post(
        "/api/homemind/families/invites/redeem",
        headers=redeemer_auth,
        json={"token": token},
    )
    assert response.status_code == 201
    redeemed = response.json()
    assert redeemed["family_id"] == family_id
    assert redeemed["role"] == "CHILD"
    assert redeemed["display_name"] == "Child"

    # Redeeming twice must fail.
    response = await client.post(
        "/api/homemind/families/invites/redeem",
        headers=redeemer_auth,
        json={"token": token},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FAMILY_INVALID"

    # Default list now hides the redeemed invite.
    response = await client.get(
        f"/api/homemind/families/{family_id}/invites", headers=auth,
    )
    assert response.status_code == 200
    assert response.json() == []

    # But include_redeemed=true brings it back.
    response = await client.get(
        f"/api/homemind/families/{family_id}/invites",
        headers=auth,
        params={"include_redeemed": "true"},
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["redeemed_at"] is not None


@pytest.mark.asyncio
async def test_invite_revoke_blocks_redemption(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Revoke Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/invites",
        headers=auth,
        json={"display_name": "Grandma"},
    )
    assert response.status_code == 201
    invite_id = response.json()["invite_id"]
    token = response.json()["token"]

    response = await client.delete(
        f"/api/homemind/families/{family_id}/invites/{invite_id}",
        headers=auth,
    )
    assert response.status_code == 204

    redeemer_auth = {"Authorization": auth["redeemer"]}
    response = await client.post(
        "/api/homemind/families/invites/redeem",
        headers=redeemer_auth,
        json={"token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_invite_requires_manager_role(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Auth Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    # Redeemer has no membership — invite mint must be rejected.
    redeemer_auth = {"Authorization": auth["redeemer"]}
    response = await client.post(
        f"/api/homemind/families/{family_id}/invites",
        headers=redeemer_auth,
        json={"display_name": "Sneaky"},
    )
    assert response.status_code in (403, 400)


@pytest.mark.asyncio
async def test_invite_validation_errors(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Validate Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/invites",
        headers=auth,
        json={"display_name": "", "ttl_seconds": 600},
    )
    assert response.status_code == 422

    response = await client.post(
        f"/api/homemind/families/{family_id}/invites",
        headers=auth,
        json={"display_name": "Child", "ttl_seconds": 5},
    )
    assert response.status_code == 422
