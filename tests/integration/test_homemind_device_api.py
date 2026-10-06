"""Stage 6: integration coverage for device pairing + runtime command flow."""

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
async def test_device_pairing_and_runtime_flow(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env

    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Device Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    assert response.status_code == 201
    family_id = response.json()["id"]

    # Family manager kicks off pairing for a new device.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices",
        headers=auth,
        json={
            "name": "kitchen-display",
            "device_type": "tablet",
            "platform": "android",
            "capabilities": ["display.show", "display.screensaver"],
        },
    )
    assert response.status_code == 201
    pairing = response.json()
    assert pairing["device_name"] == "kitchen-display"
    assert pairing["code"]
    code = pairing["code"]

    # Runtime client exchanges the pairing code for a credential.
    response = await client.post(
        "/api/homemind/runtime/pair",
        json={"code": code, "address": "192.168.1.10"},
    )
    assert response.status_code == 201
    paired = response.json()
    token = paired["token"]
    device_id = paired["device"]["id"]
    assert paired["device"]["status"] == "ONLINE"
    assert paired["device"]["last_seen"] is not None
    assert paired["expires_at"] >= 0

    # Repeating the same pairing code must fail.
    response = await client.post(
        "/api/homemind/runtime/pair",
        json={"code": code, "address": "192.168.1.10"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FAMILY_INVALID"

    # Runtime heartbeat refresh.
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/homemind/runtime/heartbeat", headers=headers,
        json={"status": "ONLINE", "address": "192.168.1.10"},
    )
    assert response.status_code == 200
    assert response.json()["device"]["id"] == device_id

    # Missing token should be rejected.
    response = await client.get("/api/homemind/runtime/commands")
    assert response.status_code == 400

    # No command enqueued yet, runtime sees no-op.
    response = await client.get("/api/homemind/runtime/commands", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"command": None}

    # Family manager enqueues a command for the device.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices/{device_id}/commands",
        headers=auth,
        json={
            "capability": "display.show",
            "payload": {"text": "晚饭准备好了"},
            "expires_in_seconds": 60,
        },
    )
    assert response.status_code == 201
    command_id = response.json()["id"]
    assert response.json()["status"] == "PENDING"

    # Capability the device never declared must be rejected.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices/{device_id}/commands",
        headers=auth,
        json={"capability": "lights.off", "payload": {}, "expires_in_seconds": 60},
    )
    assert response.status_code == 400
    assert "lights.off" in response.json()["error"]["message"]

    # Runtime picks up the command and reports success.
    response = await client.get("/api/homemind/runtime/commands", headers=headers)
    assert response.status_code == 200
    command = response.json()
    assert command["id"] == command_id
    assert command["capability"] == "display.show"

    response = await client.post(
        f"/api/homemind/runtime/commands/{command_id}/result",
        headers=headers,
        json={"status": "SUCCEEDED", "result": {"displayed": True}},
    )
    assert response.status_code == 204

    # Subsequent pulls see no-op again.
    response = await client.get("/api/homemind/runtime/commands", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"command": None}

    # Listing recent commands from the dashboard returns the one we just ran.
    response = await client.get(
        f"/api/homemind/families/{family_id}/devices/{device_id}/commands",
        headers=auth,
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "SUCCEEDED"
    assert rows[0]["result"] == {"displayed": True}

    # Rotating the token invalidates the old credential.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices/{device_id}/rotate-token",
        headers=auth,
    )
    assert response.status_code == 200
    new_token = response.json()["token"]
    assert new_token != token

    response = await client.post(
        "/api/homemind/runtime/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "ONLINE"},
    )
    assert response.status_code == 400

    response = await client.post(
        "/api/homemind/runtime/heartbeat",
        headers={"Authorization": f"Bearer {new_token}"},
        json={"status": "ONLINE"},
    )
    assert response.status_code == 200

    # Revoking the new token blocks it too.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices/{device_id}/revoke-token",
        headers=auth,
    )
    assert response.status_code == 204

    response = await client.post(
        "/api/homemind/runtime/heartbeat",
        headers={"Authorization": f"Bearer {new_token}"},
        json={"status": "ONLINE"},
    )
    assert response.status_code == 400

    # Deleting the device clears it from the listing.
    response = await client.delete(
        f"/api/homemind/families/{family_id}/devices/{device_id}", headers=auth,
    )
    assert response.status_code == 204

    response = await client.get(
        f"/api/homemind/families/{family_id}/devices", headers=auth,
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_device_update(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env

    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Update Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/devices",
        headers=auth,
        json={
            "name": "old-name",
            "device_type": "speaker",
            "platform": "linux",
            "capabilities": ["audio.play"],
        },
    )
    code = response.json()["code"]

    response = await client.post(
        "/api/homemind/runtime/pair",
        json={"code": code, "address": "10.0.0.5"},
    )
    device_id = response.json()["device"]["id"]

    response = await client.patch(
        f"/api/homemind/families/{family_id}/devices/{device_id}",
        headers=auth,
        json={
            "name": "kitchen-speaker",
            "capabilities": ["audio.play", "audio.tts"],
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "kitchen-speaker"
    assert updated["capabilities"] == ["audio.play", "audio.tts"]


@pytest.mark.asyncio
async def test_runtime_authorization_validation(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env

    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Auth Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    # Bad scheme on runtime heartbeat.
    response = await client.post(
        "/api/homemind/runtime/heartbeat",
        headers={"Authorization": "Basic abc"},
        json={"status": "ONLINE"},
    )
    assert response.status_code == 400
    assert "Bearer" in response.json()["error"]["message"]

    # Pairing code validation on the family side.
    response = await client.post(
        f"/api/homemind/families/{family_id}/devices",
        headers=auth,
        json={"name": "", "device_type": "phone", "capabilities": ["audio.play"]},
    )
    assert response.status_code == 400 or response.status_code == 422

    response = await client.post(
        f"/api/homemind/families/{family_id}/devices",
        headers=auth,
        json={"name": "no-caps", "device_type": "phone", "capabilities": []},
    )
    assert response.status_code == 400
    assert "capability" in response.json()["error"]["message"]
