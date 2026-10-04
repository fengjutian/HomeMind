from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.infra.server import OctopServer


@pytest.mark.asyncio
async def test_family_foundation_api(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    tmp_path: Path,
) -> None:
    client, _, auth = env

    response = await client.post(
        "/api/families",
        headers=auth,
        json={"name": "My Family", "timezone": "Asia/Shanghai", "locale": "zh"},
    )
    assert response.status_code == 201
    family = response.json()
    family_id = family["id"]

    response = await client.get("/api/families", headers=auth)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [family_id]

    response = await client.get(f"/api/families/{family_id}/members", headers=auth)
    assert response.status_code == 200
    owner = response.json()[0]
    assert owner["role"] == "OWNER"

    response = await client.post(
        f"/api/families/{family_id}/members",
        headers=auth,
        json={"display_name": "Child", "role": "CHILD"},
    )
    assert response.status_code == 201
    child = response.json()

    response = await client.post(
        f"/api/families/{family_id}/relationships",
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
        f"/api/families/{family_id}/spaces",
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
        f"/api/families/{family_id}/permissions",
        headers=auth,
        json=permission,
    )
    assert response.status_code == 201
    assert response.json()["effect"] == "REQUIRE_CONFIRMATION"

    response = await client.post(
        f"/api/families/{family_id}/permissions/evaluate",
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
        f"/api/families/{family_id}/members/{child['id']}",
        headers=auth,
        json={"display_name": "Teen", "role": "MEMBER"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Teen"

    response = await client.patch(
        f"/api/families/{family_id}",
        headers=auth,
        json={"name": "Our Family"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Our Family"

    response = await client.post(
        f"/api/families/{family_id}/spaces",
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
        f"/api/families/{family_id}/assets/scan",
        headers=auth,
        json={"directory": str(assets_dir)},
    )
    assert response.status_code == 200
    scan_result = response.json()
    assert scan_result["indexed"] == 2
    response = await client.get(f"/api/families/{family_id}/assets", headers=auth)
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 2
    response = await client.get(
        f"/api/families/{family_id}/assets/duplicates", headers=auth
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    (assets_dir / "second.txt").unlink()
    response = await client.post(
        f"/api/families/{family_id}/asset-sources/{scan_result['source_id']}/scan",
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["missing"] == 1
    response = await client.get(
        f"/api/families/{family_id}/assets", headers=auth, params={"status": "MISSING"}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = await client.delete(
        f"/api/families/{family_id}/assets/{assets[0]['id']}", headers=auth
    )
    assert response.status_code == 204
    assert first_file.exists()

    response = await client.delete(f"/api/families/{family_id}", headers=auth)
    assert response.status_code == 204
    response = await client.get(f"/api/families/{family_id}", headers=auth)
    assert response.status_code == 404
