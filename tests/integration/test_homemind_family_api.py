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
async def homemind_env(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, srv, await auth_header(client)


@pytest.mark.asyncio
async def test_family_foundation_api(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    tmp_path: Path,
) -> None:
    client, _, auth = homemind_env

    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "My Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    assert response.status_code == 201
    family = response.json()
    family_id = family["id"]

    response = await client.get("/api/homemind/families", headers=auth)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [family_id]

    response = await client.get(f"/api/homemind/families/{family_id}/members", headers=auth)
    assert response.status_code == 200
    owner = response.json()[0]
    assert owner["role"] == "OWNER"

    response = await client.post(
        f"/api/homemind/families/{family_id}/members",
        headers=auth,
        json={"display_name": "Child", "role": "CHILD"},
    )
    assert response.status_code == 201
    child = response.json()

    response = await client.post(
        f"/api/homemind/families/{family_id}/relationships",
        headers=auth,
        json={
            "from_member_id": owner["id"],
            "to_member_id": child["id"],
            "relationship_type": "PARENT",
        },
    )
    assert response.status_code == 201
    assert response.json()["relationship_type"] == "PARENT"

    response = await client.post(
        f"/api/homemind/families/{family_id}/spaces",
        headers=auth,
        json={
            "name": "Child private",
            "space_type": "PRIVATE",
            "owner_member_id": child["id"],
        },
    )
    assert response.status_code == 201
    space = response.json()

    permission: dict[str, Any] = {
        "subject_member_id": child["id"],
        "space_id": space["id"],
        "action": "photo.delete",
        "effect": "REQUIRE_CONFIRMATION",
    }
    response = await client.post(
        f"/api/homemind/families/{family_id}/permissions",
        headers=auth,
        json=permission,
    )
    assert response.status_code == 201
    assert response.json()["effect"] == "REQUIRE_CONFIRMATION"

    response = await client.post(
        f"/api/homemind/families/{family_id}/permissions/evaluate",
        headers=auth,
        json={
            "subject_member_id": child["id"],
            "space_id": space["id"],
            "action": "photo.delete",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"effect": "REQUIRE_CONFIRMATION"}

    response = await client.patch(
        f"/api/homemind/families/{family_id}/members/{child['id']}",
        headers=auth,
        json={"display_name": "Teen", "role": "MEMBER"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Teen"

    response = await client.patch(
        f"/api/homemind/families/{family_id}",
        headers=auth,
        json={"name": "Our Family"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Our Family"

    response = await client.post(
        f"/api/homemind/families/{family_id}/spaces",
        headers=auth,
        json={"name": "Shared", "space_type": "SHARED"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FAMILY_CONFLICT"

    assets_dir = tmp_path / "family-assets"
    assets_dir.mkdir()
    first_file = assets_dir / "first.txt"
    first_file.write_text("duplicate", encoding="utf-8")
    (assets_dir / "second.txt").write_text("duplicate", encoding="utf-8")
    response = await client.post(
        f"/api/homemind/families/{family_id}/assets/scan",
        headers=auth,
        json={"directory": str(assets_dir)},
    )
    assert response.status_code == 200
    scan_result = response.json()
    assert scan_result["indexed"] == 2
    response = await client.get(f"/api/homemind/families/{family_id}/assets", headers=auth)
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 2
    response = await client.get(
        f"/api/homemind/families/{family_id}/assets/duplicates", headers=auth
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    (assets_dir / "second.txt").unlink()
    response = await client.post(
        f"/api/homemind/families/{family_id}/asset-sources/{scan_result['source_id']}/scan",
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["missing"] == 1
    response = await client.get(
        f"/api/homemind/families/{family_id}/assets", headers=auth, params={"status": "MISSING"}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = await client.delete(
        f"/api/homemind/families/{family_id}/assets/{assets[0]['id']}", headers=auth
    )
    assert response.status_code == 204
    assert first_file.exists()

    response = await client.delete(f"/api/homemind/families/{family_id}", headers=auth)
    assert response.status_code == 204
    response = await client.get(f"/api/homemind/families/{family_id}", headers=auth)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_family_context_api(
    homemind_env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _, auth = homemind_env
    response = await client.post(
        "/api/homemind/families",
        headers=auth,
        json={"name": "Context Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    family_id = response.json()["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/events",
        headers=auth,
        json={
            "event_type": "TRIP",
            "title": "日本旅行",
            "start_at": 1,
            "end_at": 2,
            "location": "京都",
        },
    )
    assert response.status_code == 201
    event_id = response.json()["id"]

    response = await client.post(
        f"/api/homemind/families/{family_id}/memories",
        headers=auth,
        json={
            "subject_type": "EVENT",
            "subject_id": event_id,
            "content": "我们去过京都",
            "memory_type": "EXPERIENCE",
            "source_type": "USER",
        },
    )
    assert response.status_code == 201
    memory_id = response.json()["id"]

    response = await client.get(
        f"/api/homemind/families/{family_id}/memories",
        headers=auth,
        params={"query": "京都"},
    )
    assert [row["id"] for row in response.json()] == [memory_id]

    response = await client.post(
        f"/api/homemind/families/{family_id}/context/resolve",
        headers=auth,
        json={"query": "日本旅行去了哪里？"},
    )
    assert response.status_code == 200
    assert response.json()["event_ids"] == [event_id]
    assert response.json()["memory_ids"] == [memory_id]
