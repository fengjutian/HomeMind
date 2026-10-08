"""Oversized uploads land in the blob store instead of the bytes-only workspace.

This exercises the same code path a 2 GB upload takes. ``max_upload_mb`` is
lowered to 1 so the switch to blob storage can be proven with a 1.5 MB payload
instead of gigabytes: the routing rule depends only on
``total_bytes > max_upload_bytes``.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

ONE_MB = 1024 * 1024
CHUNK = 512 * 1024
TOTAL = ONE_MB + CHUNK // 2  # strictly above the lowered cap


def _lower_cap(srv: Any, mb: int) -> None:
    srv.services = replace(srv.services, config=replace(srv.services.config, max_upload_mb=mb))


def _payload() -> bytes:
    body = b"0123456789abcdef"
    return (body * (TOTAL // len(body) + 1))[:TOTAL]


async def _upload_whole_file(
    client: Any, auth: dict[str, str], agent_id: str, payload: bytes
) -> dict[str, Any]:
    session = (
        await client.post(
            "/api/uploads/sessions",
            headers=auth,
            json={
                "filename": "huge-archive.bin",
                "total_bytes": len(payload),
                "purpose": "CHAT_ATTACHMENT",
                "agent_id": agent_id,
                "chunk_size": CHUNK,
                "total_sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
    ).json()
    for index, start in enumerate(range(0, len(payload), CHUNK), start=1):
        blob = payload[start : start + CHUNK]
        r = await client.put(
            f"/api/uploads/sessions/{session['upload_id']}/parts/{index}",
            headers={
                **auth,
                "X-Chunk-SHA256": hashlib.sha256(blob).hexdigest(),
                "Content-Length": str(len(blob)),
            },
            content=blob,
        )
        assert r.status_code == 200, r.text
    done = await client.post(f"/api/uploads/sessions/{session['upload_id']}/complete", headers=auth)
    assert done.status_code == 200, done.text
    return done.json()


async def test_oversized_upload_lands_in_the_blob_store(env_with_agent: Any) -> None:
    client, srv, auth, agent_id = env_with_agent
    _lower_cap(srv, 1)
    payload = _payload()

    body = await _upload_whole_file(client, auth, agent_id, payload)
    assert body["status"] == "COMPLETED"
    assert body["storage"] == "blob", "above the cap it must not use the workspace"
    assert body["access_url"].startswith("/api/uploads/blobs/")
    assert body["size"] == len(payload)

    streamed = await client.get(body["access_url"], headers=auth)
    assert streamed.status_code == 200, streamed.text
    assert streamed.content == payload
    assert streamed.headers["content-length"] == str(len(payload))
    assert streamed.headers["x-octop-upload-storage"] == "blob"
    assert "huge-archive.bin" in streamed.headers["content-disposition"]


async def test_blob_is_not_readable_by_another_user(env_with_agent: Any) -> None:
    from tests.support.auth import create_user

    client, srv, auth, agent_id = env_with_agent
    _lower_cap(srv, 1)
    body = await _upload_whole_file(client, auth, agent_id, _payload())

    other = await create_user(client, auth, username="eve", password="eve-pw-123456")
    blocked = await client.get(body["access_url"], headers=other)
    assert blocked.status_code == 404, blocked.text
    assert blocked.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"


async def test_blob_route_requires_auth(env_with_agent: Any) -> None:
    client, srv, auth, agent_id = env_with_agent
    _lower_cap(srv, 1)
    body = await _upload_whole_file(client, auth, agent_id, _payload())
    assert (await client.get(body["access_url"])).status_code == 401


async def test_unfinished_upload_is_not_streamable(env_with_agent: Any) -> None:
    """An open session must not hand out a file, even to its owner."""
    client, _srv, auth, agent_id = env_with_agent
    session = (
        await client.post(
            "/api/uploads/sessions",
            headers=auth,
            json={
                "filename": "pending.bin",
                "total_bytes": 2048,
                "purpose": "CHAT_ATTACHMENT",
                "agent_id": agent_id,
                "chunk_size": CHUNK,
            },
        )
    ).json()
    r = await client.get(f"/api/uploads/blobs/{session['upload_id']}", headers=auth)
    assert r.status_code == 404, r.text


async def test_small_upload_still_lands_in_the_workspace(env_with_agent: Any) -> None:
    """Below the cap nothing changes: the workspace path keeps working."""
    client, srv, auth, agent_id = env_with_agent
    _lower_cap(srv, 64)
    payload = b"small enough for the workspace"
    session = (
        await client.post(
            "/api/uploads/sessions",
            headers=auth,
            json={
                "filename": "note.txt",
                "total_bytes": len(payload),
                "purpose": "CHAT_ATTACHMENT",
                "agent_id": agent_id,
                "chunk_size": CHUNK,
            },
        )
    ).json()
    await client.put(
        f"/api/uploads/sessions/{session['upload_id']}/parts/1",
        headers={
            **auth,
            "X-Chunk-SHA256": hashlib.sha256(payload).hexdigest(),
            "Content-Length": str(len(payload)),
        },
        content=payload,
    )
    done = await client.post(f"/api/uploads/sessions/{session['upload_id']}/complete", headers=auth)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["storage"] == "workspace"
    assert body["workspace_path"].startswith("inbound/")
    fetched = await client.get(body["access_url"], headers=auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == payload


async def test_blob_path_traversal_is_refused(env_with_agent: Any) -> None:
    client, _srv, auth, _agent_id = env_with_agent
    for bad in ("..%2F..%2Fetc", "..", "%2e%2e"):
        r = await client.get(f"/api/uploads/blobs/{bad}", headers=auth)
        assert r.status_code in (404, 400), r.text
