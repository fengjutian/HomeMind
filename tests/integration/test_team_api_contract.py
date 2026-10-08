"""Frozen HTTP contract for ``/api/teams``.

Baseline freeze for AgentTeams productization (plan phases 9-12): the new
``/api/teams/{team_id}/runs/*`` endpoints must be added without changing the
shape of the existing roster CRUD responses, the member sub-object, or the
``TEAM_*`` error codes.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.auth import create_agent, create_user

TEAM_KEYS = {
    "team_id",
    "agent_id",
    "name",
    "description",
    "default_model",
    "color",
    "icon_name",
    "icon_url",
    "welcome_message",
    "state",
    "kind",
    "member_ids",
    "members",
}

MEMBER_KEYS = {
    "agent_id",
    "name",
    "color",
    "icon_name",
    "icon_url",
    "state",
    "is_shared",
    "user_id",
}


async def _team(client: Any, auth: dict[str, str], members: list[str]) -> dict[str, Any]:
    r = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "Ops", "member_ids": members},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def two_experts(env_with_agent: Any) -> tuple[Any, dict[str, str], list[str]]:
    client, _srv, auth, first = env_with_agent
    second = await create_agent(client, auth, name="beta")
    return client, auth, [first, second]


async def test_team_crud_response_shape_is_frozen(two_experts: Any) -> None:
    client, auth, members = two_experts

    created = await _team(client, auth, members)
    assert set(created) == TEAM_KEYS, sorted(set(created) ^ TEAM_KEYS)
    assert created["kind"] == "team"
    assert created["team_id"] == created["agent_id"]
    assert created["member_ids"] == members
    assert [m["agent_id"] for m in created["members"]] == members
    assert set(created["members"][0]) == MEMBER_KEYS, sorted(
        set(created["members"][0]) ^ MEMBER_KEYS
    )
    team_id = created["team_id"]

    listed = await client.get("/api/teams", headers=auth)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["team_id"] for row in rows] == [team_id]
    assert set(rows[0]) == TEAM_KEYS

    fetched = await client.get(f"/api/teams/{team_id}", headers=auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created

    patched = await client.patch(
        f"/api/teams/{team_id}",
        headers=auth,
        json={"name": "Ops Renamed", "welcome_message": "hi"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert set(body) == TEAM_KEYS
    assert body["name"] == "Ops Renamed"
    assert body["welcome_message"] == "hi"
    assert body["member_ids"] == members

    dropped = await client.delete(f"/api/teams/{team_id}", headers=auth)
    assert dropped.status_code == 204, dropped.text

    gone = await client.get(f"/api/teams/{team_id}", headers=auth)
    assert gone.status_code == 404, gone.text
    assert gone.json()["error"]["code"] == "TEAM_NOT_FOUND"


async def test_patch_member_ids_replaces_whole_roster(two_experts: Any) -> None:
    client, auth, members = two_experts
    created = await _team(client, auth, members)
    team_id = created["team_id"]
    third = await create_agent(client, auth, name="gamma")

    patched = await client.patch(
        f"/api/teams/{team_id}",
        headers=auth,
        json={"member_ids": [members[0], third]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["member_ids"] == [members[0], third]


async def test_create_rejects_too_few_members(two_experts: Any) -> None:
    client, auth, members = two_experts
    r = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "Lonely", "member_ids": [members[0]]},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "TEAM_MEMBERS_TOO_FEW"


async def test_create_rejects_unknown_member(two_experts: Any) -> None:
    client, auth, members = two_experts
    r = await client.post(
        "/api/teams",
        headers=auth,
        json={"name": "Ghost", "member_ids": [*members, "NOPE"]},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "TEAM_MEMBER_INVALID"
    assert "NOPE" in err["details"]["member_agent_ids"]


async def test_non_team_agent_is_not_addressable_as_team(two_experts: Any) -> None:
    client, auth, members = two_experts
    r = await client.get(f"/api/teams/{members[0]}", headers=auth)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "TEAM_NOT_FOUND"


async def test_other_user_cannot_read_or_mutate_team(env_with_agent: Any) -> None:
    client, _srv, auth, first = env_with_agent
    second = await create_agent(client, auth, name="beta")
    created = await _team(client, auth, [first, second])
    team_id = created["team_id"]

    other_auth = await create_user(client, auth, username="eve", password="eve-pw-123456")

    read = await client.get(f"/api/teams/{team_id}", headers=other_auth)
    assert read.status_code == 403, read.text
    assert read.json()["error"]["code"] == "FORBIDDEN"

    patched = await client.patch(
        f"/api/teams/{team_id}",
        headers=other_auth,
        json={"name": "hijacked"},
    )
    assert patched.status_code == 403, patched.text
    assert patched.json()["error"]["code"] == "FORBIDDEN"

    deleted = await client.delete(f"/api/teams/{team_id}", headers=other_auth)
    assert deleted.status_code == 403, deleted.text

    mine = await client.get(f"/api/teams/{team_id}", headers=auth)
    assert mine.status_code == 200, mine.text
    assert mine.json()["name"] == "Ops"


async def test_agents_list_exposes_member_ids_for_team_rows(two_experts: Any) -> None:
    client, auth, members = two_experts
    created = await _team(client, auth, members)

    r = await client.get("/api/agents", headers=auth)
    assert r.status_code == 200, r.text
    rows = {row["agent_id"]: row for row in r.json()}
    assert rows[created["team_id"]]["member_ids"] == members
    assert "member_ids" not in rows[members[0]]


async def test_team_template_endpoint_returns_markdown_only(two_experts: Any) -> None:
    client, auth, _members = two_experts
    r = await client.get("/api/teams/template", headers=auth)
    assert r.status_code == 200, r.text
    files = r.json()
    assert files, "team template workspace files should be shipped"
    for entry in files:
        assert set(entry) == {"name", "content"}
        assert entry["name"].endswith(".md")
