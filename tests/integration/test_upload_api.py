"""Integration tests for dashboard chat attachments in agent workspace."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any


def _set_upload_limit_mb(srv: Any, mb: int) -> None:
    """Pin ``max_upload_mb`` for the request path that reads config per call."""
    srv.services = replace(srv.services, config=replace(srv.services.config, max_upload_mb=mb))


async def test_upload_and_workspace_download(env_with_agent: Any) -> None:
    client, _srv, auth, aid = env_with_agent
    files = {"file": ("note.txt", b"hello attachment", "text/plain")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == body["workspace_path"]
    assert body["access_url"].startswith(f"/api/agents/{aid}/workspace/download")
    assert body["filename"] == "note.txt"
    assert body["media_type"] == "text/plain"
    assert body["workspace_path"].endswith(".txt")
    assert body["workspace_path"].startswith("inbound/")
    assert re.search(r"inbound/\d{10,}_note\.txt$", body["workspace_path"])
    assert "file_id" not in body
    assert "legacy_url" not in body

    r2 = await client.get(body["access_url"], headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.content == b"hello attachment"


async def test_upload_pdf_workspace_path(env_with_agent: Any) -> None:
    client, _srv, auth, aid = env_with_agent
    files = {"file": ("report.pdf", b"%PDF-1.4", "application/pdf")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_path"].endswith(".pdf")
    assert body["filename"] == "report.pdf"

    r2 = await client.get(body["access_url"], headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.content == b"%PDF-1.4"


async def test_upload_requires_auth(env: Any) -> None:
    client, _srv, _auth = env
    files = {"file": ("note.txt", b"x", "text/plain")}
    r = await client.post("/api/agents/NOPE/upload", files=files)
    assert r.status_code == 401, r.text


async def test_upload_limit_still_rejects_oversize_body(env_with_agent: Any) -> None:
    """Frozen contract: the multipart path still enforces ``max_upload_bytes``."""
    client, srv, auth, aid = env_with_agent
    _set_upload_limit_mb(srv, 1)

    files = {"file": ("big.bin", b"a" * (2 * 1024 * 1024), "application/octet-stream")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 413, r.text
    err = r.json()["error"]
    assert err["code"] == "ATTACHMENT_TOO_LARGE"
    assert err["details"]["max_mb"] == 1


async def test_upload_limit_allows_body_under_cap(env_with_agent: Any) -> None:
    client, srv, auth, aid = env_with_agent
    _set_upload_limit_mb(srv, 1)

    files = {"file": ("small.txt", b"still fine", "text/plain")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "small.txt"


async def test_advertised_upload_limit_matches_enforced_limit(env_with_agent: Any) -> None:
    """The dashboard reads ``GET /api/settings/upload``; it must not drift."""
    client, srv, auth, aid = env_with_agent
    _set_upload_limit_mb(srv, 2)

    settings = await client.get("/api/settings/upload", headers=auth)
    assert settings.status_code == 200, settings.text
    assert settings.json() == {"max_upload_mb": 2, "max_upload_bytes": 2 * 1024 * 1024}

    files = {"file": ("over.bin", b"a" * (3 * 1024 * 1024), "application/octet-stream")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 413, r.text
    assert r.json()["error"]["details"]["max_mb"] == 2
