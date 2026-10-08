"""Resumable upload session HTTP contract (phase 2)."""

from __future__ import annotations

import hashlib
from typing import Any

from tests.support.auth import create_agent, create_user


async def _session(
    client: Any,
    auth: dict[str, str],
    agent_id: str,
    *,
    name: str = "movie.mp4",
    total: int = 2048,
    chunk: int = 1024,
    purpose: str = "CHAT_ATTACHMENT",
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filename": name,
        "total_bytes": total,
        "purpose": purpose,
        "agent_id": agent_id,
        "chunk_size": chunk,
        **extra,
    }
    r = await client.post("/api/uploads/sessions", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _put_part(
    client: Any,
    auth: dict[str, str],
    upload_id: str,
    part_number: int,
    blob: bytes,
    *,
    digest: str | None = None,
    declared_length: int | None = None,
) -> Any:
    return await client.put(
        f"/api/uploads/sessions/{upload_id}/parts/{part_number}",
        headers={
            **auth,
            "X-Chunk-SHA256": digest or hashlib.sha256(blob).hexdigest(),
            "Content-Length": str(declared_length if declared_length is not None else len(blob)),
        },
        content=blob,
    )


def _parts_of(payload: bytes, chunk: int) -> list[bytes]:
    """Split *payload* using the chunk size the **server** assigned.

    Asking for a chunk below the protocol minimum is clamped up, so the client
    must always slice by the value echoed back at session creation.
    """
    return [payload[start : start + chunk] for start in range(0, len(payload), chunk)]


def _payload(size: int, seed: bytes = b"0123456789") -> bytes:
    """Exactly *size* bytes built from *seed*."""
    return (seed * (size // len(seed) + 1))[:size]


async def test_upload_session_lifecycle_over_http(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    chunk = 256 * 1024
    payload = _payload(chunk * 2 + 500)
    session = await _session(
        client,
        auth,
        agent_id,
        total=len(payload),
        chunk_size=chunk,
        total_sha256=hashlib.sha256(payload).hexdigest(),
    )
    upload_id = session["upload_id"]
    assigned = session["chunk_size"]
    assert assigned == chunk
    assert session["status"] == "OPEN"
    assert session["received_bytes"] == 0
    assert session["missing_ranges"] == [{"offset": 0, "size": len(payload)}]

    parts = _parts_of(payload, assigned)
    assert len(parts) == 3, "last part is short"
    seen = []
    for index, blob in enumerate(parts, start=1):
        r = await _put_part(client, auth, upload_id, index, blob)
        assert r.status_code == 200, r.text
        seen.append(r.json())

    assert seen[-1]["received_bytes"] == len(payload)
    assert seen[-1]["missing_ranges"] == []
    assert seen[-1]["part_count"] == 3

    done = await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=auth)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "COMPLETED"
    assert body["workspace_path"].startswith("inbound/")
    # mp4 is a previewable type, so the URL points at the media preview route.
    assert body["access_url"].startswith(f"/api/agents/{agent_id}/media/preview")

    fetched = await client.get(body["access_url"], headers=auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == payload


async def test_status_survives_a_page_refresh_and_resumes(
    env_with_agent: Any,
) -> None:
    """After a refresh the client re-queries and only sends what is missing."""
    client, _srv, auth, agent_id = env_with_agent
    chunk = 256 * 1024
    payload = _payload(chunk * 2 + 500, seed=b"abcdefgh")
    session = await _session(client, auth, agent_id, total=len(payload), chunk_size=chunk)
    upload_id = session["upload_id"]
    assigned = session["chunk_size"]
    parts = _parts_of(payload, assigned)
    await _put_part(client, auth, upload_id, 1, parts[0])

    resumed = (await client.get(f"/api/uploads/sessions/{upload_id}", headers=auth)).json()
    assert resumed["received_bytes"] == assigned
    # The two remaining gaps are contiguous, so the server coalesces them.
    assert resumed["missing_ranges"] == [{"offset": assigned, "size": len(payload) - assigned}]

    for index, blob in enumerate(parts[1:], start=2):
        r = await _put_part(client, auth, upload_id, index, blob)
        assert r.status_code == 200, r.text

    done = await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=auth)
    assert done.status_code == 200, done.text


async def test_resent_part_is_idempotent_over_http(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"z" * 1024
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    upload_id = session["upload_id"]
    blob = payload

    first = await _put_part(client, auth, upload_id, 1, blob)
    assert first.status_code == 200, first.text
    second = await _put_part(client, auth, upload_id, 1, blob)
    assert second.status_code == 200, second.text
    assert second.json()["received_bytes"] == 1024
    assert second.json()["part_count"] == 1


async def test_tampered_part_is_rejected_with_409(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"a" * 1024
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    upload_id = session["upload_id"]
    await _put_part(client, auth, upload_id, 1, payload)

    other = b"b" * 1024
    r = await _put_part(client, auth, upload_id, 1, other)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "UPLOAD_PART_CONFLICT"


async def test_bad_chunk_digest_is_rejected_with_422(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"c" * 1024
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    r = await _put_part(client, auth, session["upload_id"], 1, payload, digest="0" * 64)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "UPLOAD_CHECKSUM_MISMATCH"


async def test_malformed_chunk_digest_is_rejected(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    session = await _session(client, auth, agent_id, total=1024, chunk=1024)
    r = await _put_part(client, auth, session["upload_id"], 1, b"a" * 1024, digest="nothex")
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "UPLOAD_CHECKSUM_MISMATCH"


async def test_wrong_content_length_is_rejected(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"d" * 1024
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    r = await _put_part(client, auth, session["upload_id"], 1, payload, declared_length=999)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "UPLOAD_PART_CONFLICT"


async def test_incomplete_complete_is_rejected_with_409(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"e" * 2048
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    upload_id = session["upload_id"]
    await _put_part(client, auth, upload_id, 1, payload[:1024])

    r = await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=auth)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "UPLOAD_INCOMPLETE"

    # The session stays usable so the client can finish the upload.
    assert (await client.get(f"/api/uploads/sessions/{upload_id}", headers=auth)).json()[
        "status"
    ] == "OPEN"


async def test_whole_file_checksum_mismatch_is_rejected(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"f" * 1024
    session = await _session(
        client, auth, agent_id, total=len(payload), chunk=1024, total_sha256="1" * 64
    )
    upload_id = session["upload_id"]
    await _put_part(client, auth, upload_id, 1, payload)

    r = await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=auth)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "UPLOAD_CHECKSUM_MISMATCH"


async def test_cancel_reclaims_the_session(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    session = await _session(client, auth, agent_id, total=1024, chunk=1024)
    upload_id = session["upload_id"]

    r = await client.delete(f"/api/uploads/sessions/{upload_id}", headers=auth)
    assert r.status_code == 204, r.text

    after = await client.get(f"/api/uploads/sessions/{upload_id}", headers=auth)
    assert after.status_code == 200, after.text
    assert after.json()["status"] == "ABORTED"

    blocked = await _put_part(client, auth, upload_id, 1, b"a" * 1024)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "UPLOAD_SESSION_NOT_OPEN"


async def test_other_user_cannot_touch_the_session(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"h" * 1024
    session = await _session(client, auth, agent_id, total=len(payload), chunk=1024)
    upload_id = session["upload_id"]
    other_auth = await create_user(client, auth, username="eve", password="eve-pw-123456")

    assert (
        await client.get(f"/api/uploads/sessions/{upload_id}", headers=other_auth)
    ).status_code == 404
    read_back = await _put_part(client, other_auth, upload_id, 1, payload)
    assert read_back.status_code == 404, read_back.text
    assert read_back.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"
    assert (
        await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=other_auth)
    ).status_code == 404
    assert (
        await client.delete(f"/api/uploads/sessions/{upload_id}", headers=other_auth)
    ).status_code == 404

    # The owner's session is untouched.
    assert (await client.get(f"/api/uploads/sessions/{upload_id}", headers=auth)).status_code == 200


async def test_unknown_session_is_404(env_with_agent: Any) -> None:
    client, _srv, auth, _agent_id = env_with_agent
    r = await client.get("/api/uploads/sessions/01HQ0000000000000000000099", headers=auth)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"


async def test_upload_sessions_require_auth(env_with_agent: Any) -> None:
    client, _srv, _auth, agent_id = env_with_agent
    r = await client.post(
        "/api/uploads/sessions",
        json={
            "filename": "a.bin",
            "total_bytes": 1024,
            "purpose": "CHAT_ATTACHMENT",
            "agent_id": agent_id,
        },
    )
    assert r.status_code == 401, r.text


async def test_create_requires_an_agent_for_workspace_purposes(env_with_agent: Any) -> None:
    client, _srv, auth, _agent_id = env_with_agent
    r = await client.post(
        "/api/uploads/sessions",
        headers=auth,
        json={"filename": "a.bin", "total_bytes": 1024, "purpose": "CHAT_ATTACHMENT"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "UPLOAD_SESSION_NOT_OPEN"


async def test_create_rejects_a_non_owner_of_the_agent(env_alice_bob_agent: Any) -> None:
    client, _srv, _alice_auth, bob_auth, alice_agent = env_alice_bob_agent
    r = await client.post(
        "/api/uploads/sessions",
        headers=bob_auth,
        json={
            "filename": "a.bin",
            "total_bytes": 1024,
            "purpose": "CHAT_ATTACHMENT",
            "agent_id": alice_agent,
        },
    )
    assert r.status_code in (403, 404), r.text


async def test_unavailable_purposes_are_refused_explicitly(env_with_agent: Any) -> None:
    """KNOWLEDGE_DOCUMENT / FAMILY_ASSET are not wired yet and must say so."""
    client, _srv, auth, agent_id = env_with_agent
    for purpose in ("KNOWLEDGE_DOCUMENT", "FAMILY_ASSET"):
        r = await client.post(
            "/api/uploads/sessions",
            headers=auth,
            json={
                "filename": "a.bin",
                "total_bytes": 1024,
                "purpose": purpose,
                "agent_id": agent_id,
            },
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "UPLOAD_PURPOSE_UNSUPPORTED"
        assert r.json()["error"]["details"]["purpose"] == purpose


async def test_oversized_declaration_is_rejected(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    r = await client.post(
        "/api/uploads/sessions",
        headers=auth,
        json={
            "filename": "huge.bin",
            "total_bytes": 3 * 1024**3,
            "purpose": "CHAT_ATTACHMENT",
            "agent_id": agent_id,
        },
    )
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "ATTACHMENT_TOO_LARGE"


async def test_legacy_multipart_upload_still_works(env_with_agent: Any) -> None:
    """The pre-existing endpoint must be unaffected by the session API."""
    client, _srv, auth, agent_id = env_with_agent
    files = {"file": ("note.txt", b"legacy path", "text/plain")}
    r = await client.post(f"/api/agents/{agent_id}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["workspace_path"].startswith("inbound/")


async def test_workspace_file_purpose_lands_in_inbound_with_a_display_name(
    env_with_agent: Any,
) -> None:
    client, _srv, auth, agent_id = env_with_agent
    payload = b"workspace payload"
    session = await _session(
        client,
        auth,
        agent_id,
        name="report.pdf",
        total=len(payload),
        chunk=1024,
        purpose="WORKSPACE_FILE",
    )
    upload_id = session["upload_id"]
    await _put_part(client, auth, upload_id, 1, payload)
    done = await client.post(f"/api/uploads/sessions/{upload_id}/complete", headers=auth)
    assert done.status_code == 200, done.text
    assert done.json()["filename"] == "report.pdf"


async def test_agent_created_for_session_is_owned_by_the_caller(env_with_agent: Any) -> None:
    client, _srv, auth, _existing = env_with_agent
    agent_id = await create_agent(client, auth, name="upload-target")
    session = await _session(client, auth, agent_id, total=1024, chunk=1024)
    assert session["agent_id"] == agent_id
