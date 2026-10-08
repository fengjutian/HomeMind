"""HTTP contract for the device asset transfer protocol.

This layer locks in what a device client can rely on *without* trusting an
implicit framework behaviour: exact status codes, the exact header set on
the data plane, and the exact bytes for each accepted range form. The
domain tests prove the rules; these prove the wire contract.

Two invariants get explicit tests here because they are the ones a client
gets wrong silently:

* a dashboard JWT or a device credential cannot stand in for a transfer
  credential on the data plane;
* a transfer is readable only by the device that created it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin

#: 1000 deterministic bytes, identical on every platform.
BODY = b"0123456789" * 100

TRANSFERS = "/api/homemind/runtime/transfers"


class _Rig:
    def __init__(self, client: httpx.AsyncClient, auth: dict, server: OctopServer) -> None:
        self.client = client
        self.auth = auth
        self.server = server

    async def family(self, name: str = "Transfer Family") -> str:
        response = await self.client.post(
            "/api/homemind/families",
            headers=self.auth,
            json={"name": name, "timezone": "Asia/Shanghai", "locale": "zh"},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    async def pair(self, family_id: str, name: str = "living-tv") -> tuple[str, str]:
        created = await self.client.post(
            f"/api/homemind/families/{family_id}/devices",
            headers=self.auth,
            json={
                "name": name,
                "device_type": "tv",
                "platform": "android",
                "capabilities": ["asset.download"],
            },
        )
        assert created.status_code == 201, created.text
        paired = await self.client.post(
            "/api/homemind/runtime/pair",
            json={"code": created.json()["code"], "address": None},
        )
        assert paired.status_code == 201, paired.text
        return str(paired.json()["device"]["id"]), str(paired.json()["token"])

    async def index_asset(self, family_id: str, payload: Path) -> str:
        scanned = await self.client.post(
            f"/api/homemind/families/{family_id}/assets/scan",
            headers=self.auth,
            json={"directory": str(payload.parent), "recursive": False},
        )
        assert scanned.status_code == 200, scanned.text
        listed = await self.client.get(
            f"/api/homemind/families/{family_id}/assets",
            headers=self.auth,
        )
        assert listed.status_code == 200, listed.text
        assets = listed.json()
        match = [a for a in assets if a["name"] == payload.name]
        assert match, listed.text
        return str(match[0]["id"])

    async def transfer(
        self,
        device_token: str,
        asset_id: str,
        request_key: str = "k1",
    ) -> tuple[int, dict]:
        response = await self.client.post(
            TRANSFERS,
            headers={"Authorization": f"Bearer {device_token}"},
            json={"asset_id": asset_id, "request_key": request_key},
        )
        return response.status_code, response.json()


@pytest.fixture
async def rig(
    tmp_octop_home: Path,
) -> AsyncIterator[_Rig]:
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        auth = await auth_header(client)
        yield _Rig(client, auth, srv)


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    target = tmp_path / "library"
    target.mkdir(exist_ok=True)
    media = target / "family-video.mp4"
    media.write_bytes(BODY)
    return media


async def _ready(rig: _Rig, payload: Path, suffix: str = ""):
    """``(family_id, device_id, device_token, asset_id, manifest)`` for one asset."""
    family_id = await rig.family(f"Transfer Family{suffix}")
    device_id, device_token = await rig.pair(family_id, f"living-tv{suffix}")
    asset_id = await rig.index_asset(family_id, payload)
    status, manifest = await rig.transfer(device_token, asset_id)
    assert status == 201, manifest
    return family_id, device_id, device_token, asset_id, manifest


def _data_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Transfer {token}"}


# ------------------------------------------------------------ control plane


@pytest.mark.asyncio
async def test_create_returns_201_then_200_on_replay(rig: _Rig, payload: Path) -> None:
    family_id = await rig.family()
    _device_id, device_token = await rig.pair(family_id)
    asset_id = await rig.index_asset(family_id, payload)

    status, manifest = await rig.transfer(device_token, asset_id)
    assert status == 201
    assert manifest["status"] == "ACTIVE"
    assert manifest["asset"]["size_bytes"] == len(BODY)
    assert manifest["asset"]["etag"].startswith('"')
    assert manifest["download"]["accept_ranges"] is True
    assert manifest["download"]["chunk_size"] == 16 * 1024 * 1024
    assert manifest["download"]["max_concurrency"] == 4
    assert manifest["download"]["token"]

    replay_status, replay = await rig.transfer(device_token, asset_id)
    assert replay_status == 200, "an idempotent replay is not a new creation"
    assert replay["transfer_id"] == manifest["transfer_id"]


@pytest.mark.asyncio
async def test_get_never_returns_a_plaintext_credential(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["download"] is None


@pytest.mark.asyncio
async def test_progress_is_monotonic_over_http(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    headers = {"Authorization": f"Bearer {device_token}"}
    url = f"{TRANSFERS}/{manifest['transfer_id']}/progress"

    first = await rig.client.post(url, headers=headers, json={"bytes_downloaded": 400})
    assert first.status_code == 200 and first.json()["bytes_downloaded"] == 400

    stale = await rig.client.post(url, headers=headers, json={"bytes_downloaded": 100})
    assert stale.status_code == 200, "a smaller report is not an error"
    assert stale.json()["bytes_downloaded"] == 400


@pytest.mark.asyncio
async def test_complete_requires_the_manifest_digest(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    headers = {"Authorization": f"Bearer {device_token}"}

    # A transfer whose digest disagrees is failed, not completed.
    bad = await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/complete",
        headers=headers,
        json={"size_bytes": len(BODY), "sha256": "b" * 64},
    )
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_HASH_MISMATCH"

    # FAILED is terminal: a corrected retry must not sneak a success in.
    retry = await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/complete",
        headers=headers,
        json={"size_bytes": len(BODY), "sha256": manifest["asset"]["sha256"]},
    )
    assert retry.status_code == 409

    # A clean transfer completes, and stays completed on replay.
    _f2, _d2, token2, _a2, good = await _ready(rig, payload, suffix="-b")
    second = {"Authorization": f"Bearer {token2}"}
    ok = await rig.client.post(
        f"{TRANSFERS}/{good['transfer_id']}/complete",
        headers=second,
        json={"size_bytes": len(BODY), "sha256": good["asset"]["sha256"]},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "COMPLETED"
    replay = await rig.client.post(
        f"{TRANSFERS}/{good['transfer_id']}/complete",
        headers=second,
        json={"size_bytes": len(BODY), "sha256": good["asset"]["sha256"]},
    )
    assert replay.status_code == 200 and replay.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_refresh_rotates_the_download_credential(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    old_token = manifest["download"]["token"]

    refreshed = await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/refresh",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_token = refreshed.json()["download"]["token"]
    assert new_token != old_token

    content = f"{TRANSFERS}/{manifest['transfer_id']}/content"
    stale = await rig.client.head(content, headers=_data_headers(old_token))
    assert stale.status_code == 401, "the previous credential must stop working at once"

    fresh = await rig.client.head(content, headers=_data_headers(new_token))
    assert fresh.status_code == 200


@pytest.mark.asyncio
async def test_fail_report_is_recorded(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    response = await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/fail",
        headers={"Authorization": f"Bearer {device_token}"},
        json={"code": "DISK_FULL", "detail": "no space left on device"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "FAILED"
    assert response.json()["failure_code"] == "DISK_FULL"


@pytest.mark.asyncio
async def test_missing_credential_is_rejected(rig: _Rig, payload: Path) -> None:
    family_id = await rig.family()
    _device_id, _token = await rig.pair(family_id)
    asset_id = await rig.index_asset(family_id, payload)
    response = await rig.client.post(
        TRANSFERS,
        json={"asset_id": asset_id, "request_key": "k"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FAMILY_INVALID"


@pytest.mark.asyncio
async def test_transfer_from_another_device_is_not_found(rig: _Rig, payload: Path) -> None:
    family_id = await rig.family()
    _d1, token_a = await rig.pair(family_id, "tv-a")
    _d2, token_b = await rig.pair(family_id, "tv-b")
    asset_id = await rig.index_asset(family_id, payload)
    _status, manifest = await rig.transfer(token_a, asset_id)

    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_cross_family_asset_is_not_found(rig: _Rig, payload: Path) -> None:
    other_family = await rig.family("Other Family")
    _d, device_token = await rig.pair(other_family)
    # The asset lives in a different family; the device must not learn that.
    stranger_family = await rig.family("Stranger")
    asset_id = await rig.index_asset(stranger_family, payload)

    response = await rig.client.post(
        TRANSFERS,
        headers={"Authorization": f"Bearer {device_token}"},
        json={"asset_id": asset_id, "request_key": "k"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_NOT_FOUND"


# --------------------------------------------------------------- data plane


@pytest.mark.asyncio
async def test_head_reports_length_etag_and_ranges(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.head(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=_data_headers(manifest["download"]["token"]),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-length"] == str(len(BODY))
    assert response.headers["etag"] == manifest["asset"]["etag"]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == b""


@pytest.mark.asyncio
async def test_whole_file_get_returns_200_and_exact_bytes(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=_data_headers(manifest["download"]["token"]),
    )
    assert response.status_code == 200, response.text
    assert response.content == BODY
    assert response.headers["content-length"] == str(len(BODY))
    assert "content-range" not in response.headers


@pytest.mark.asyncio
async def test_single_range_returns_206_and_exact_bytes(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers={
            **_data_headers(manifest["download"]["token"]),
            "Range": "bytes=0-9",
        },
    )
    assert response.status_code == 206, response.text
    assert response.content == BODY[0:10]
    assert response.headers["content-length"] == "10"
    assert response.headers["content-range"] == f"bytes 0-9/{len(BODY)}"
    assert response.headers["etag"] == manifest["asset"]["etag"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_open_ended_and_suffix_ranges(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    token = _data_headers(manifest["download"]["token"])
    url = f"{TRANSFERS}/{manifest['transfer_id']}/content"

    open_ended = await rig.client.get(url, headers={**token, "Range": "bytes=990-"})
    assert open_ended.status_code == 206
    assert open_ended.content == BODY[990:]
    assert open_ended.headers["content-range"] == f"bytes 990-999/{len(BODY)}"

    suffix = await rig.client.get(url, headers={**token, "Range": "bytes=-10"})
    assert suffix.status_code == 206
    assert suffix.content == BODY[-10:]
    assert suffix.headers["content-range"] == f"bytes 990-999/{len(BODY)}"


@pytest.mark.asyncio
async def test_invalid_ranges_return_416_with_total(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    headers = _data_headers(manifest["download"]["token"])
    url = f"{TRANSFERS}/{manifest['transfer_id']}/content"

    for bad in ("bytes=5000-6000", "bytes=0-99,200-299", "bytes=abc", "items=0-1"):
        response = await rig.client.get(url, headers={**headers, "Range": bad})
        assert response.status_code == 416, bad
        assert response.headers["content-range"] == f"bytes */{len(BODY)}", bad


@pytest.mark.asyncio
async def test_if_match_mismatch_returns_412(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    headers = {
        **_data_headers(manifest["download"]["token"]),
        "If-Match": '"definitely-not-this-version"',
    }
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers={**headers, "Range": "bytes=0-9"},
    )
    assert response.status_code == 412
    assert response.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_if_match_with_the_manifest_etag_succeeds(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers={
            **_data_headers(manifest["download"]["token"]),
            "Range": "bytes=0-9",
            "If-Match": manifest["asset"]["etag"],
        },
    )
    assert response.status_code == 206
    assert response.content == BODY[0:10]


@pytest.mark.asyncio
async def test_device_credential_cannot_read_the_data_plane(rig: _Rig, payload: Path) -> None:
    """A long-lived device token must not work as a download token."""
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    url = f"{TRANSFERS}/{manifest['transfer_id']}/content"

    as_bearer = await rig.client.get(url, headers={"Authorization": f"Bearer {device_token}"})
    assert as_bearer.status_code == 401
    assert as_bearer.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_dashboard_jwt_cannot_read_the_data_plane(rig: _Rig, payload: Path) -> None:
    """A logged-in family member is not a download credential either."""
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=rig.auth,
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_forged_credential_is_refused(rig: _Rig, payload: Path) -> None:
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=_data_headers("not-a-real-token"),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_completion_revokes_every_download_credential(rig: _Rig, payload: Path) -> None:
    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    token = manifest["download"]["token"]
    await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/complete",
        headers={"Authorization": f"Bearer {device_token}"},
        json={"size_bytes": len(BODY), "sha256": manifest["asset"]["sha256"]},
    )
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=_data_headers(token),
    )
    assert response.status_code == 401, "completion must kill every download credential"
    assert response.json()["error"]["code"] == "HOMEMIND_ASSET_TRANSFER_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_asset_deletion_cancels_an_active_download(rig: _Rig, payload: Path) -> None:
    family_id, _d, device_token, asset_id, manifest = await _ready(rig, payload)
    token = manifest["download"]["token"]
    headers = {"Authorization": f"Bearer {device_token}"}

    deleted = await rig.client.delete(
        f"/api/homemind/families/{family_id}/assets/{asset_id}",
        headers=rig.auth,
    )
    assert deleted.status_code == 204

    # The bytes stop. The task row survives as CANCELLED, so the device's
    # own status report still gets an answer instead of a 404.
    response = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}/content",
        headers=_data_headers(token),
    )
    assert response.status_code in (401, 404, 409), "a deleted asset serves no bytes"

    status = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}",
        headers=headers,
    )
    assert status.status_code == 200
    assert status.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_four_parallel_ranges_reassemble_the_file(rig: _Rig, payload: Path) -> None:
    """The property a device client depends on: four workers, four
    offsets, one original file."""
    _f, _d, _dt, _a, manifest = await _ready(rig, payload)
    token = manifest["download"]["token"]
    url = f"{TRANSFERS}/{manifest['transfer_id']}/content"
    block = 250

    async def fetch(index: int) -> tuple[int, bytes]:
        start = index * block
        end = min(start + block, len(BODY)) - 1
        response = await rig.client.get(
            url,
            headers={**_data_headers(token), "Range": f"bytes={start}-{end}"},
        )
        assert response.status_code == 206, response.text
        return start, response.content

    import asyncio

    parts = await asyncio.gather(*(fetch(index) for index in range(4)))
    assembled = bytearray(len(BODY))
    for start, data in parts:
        assembled[start : start + len(data)] = data
    assert bytes(assembled) == BODY


@pytest.mark.asyncio
async def test_resume_after_interruption_only_needs_missing_ranges(
    rig: _Rig,
    payload: Path,
) -> None:
    """A device that stops at 35% and restarts re-reads only the rest."""
    import hashlib

    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    token = manifest["download"]["token"]
    url = f"{TRANSFERS}/{manifest['transfer_id']}/content"
    stop_at = int(len(BODY) * 0.35)

    partial = await rig.client.get(
        url,
        headers={**_data_headers(token), "Range": f"bytes=0-{stop_at - 1}"},
    )
    assert partial.status_code == 206
    local = bytearray(len(BODY))
    local[:stop_at] = partial.content

    progress = await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/progress",
        headers={"Authorization": f"Bearer {device_token}"},
        json={"bytes_downloaded": stop_at},
    )
    assert progress.json()["bytes_downloaded"] == stop_at

    # Restart: only the tail is requested.
    tail = await rig.client.get(
        url,
        headers={**_data_headers(token), "Range": f"bytes={stop_at}-"},
    )
    assert tail.status_code == 206
    local[stop_at:] = tail.content
    assert bytes(local) == BODY
    assert hashlib.sha256(bytes(local)).hexdigest() == manifest["asset"]["sha256"]


@pytest.mark.asyncio
async def test_state_survives_a_restart(rig: _Rig, payload: Path) -> None:
    """The server rebuilds every service object per request; the row is
    the durable state, so a restart must not lose a transfer."""
    from homemind.infra.db.services import HomeMindServices

    _f, _d, device_token, _a, manifest = await _ready(rig, payload)
    headers = {"Authorization": f"Bearer {device_token}"}
    await rig.client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/progress",
        headers=headers,
        json={"bytes_downloaded": 321},
    )

    assert rig.server.services is not None
    rebuilt = HomeMindServices.from_pool(rig.server.services.db)
    row = rebuilt.asset_transfer_repo.get(manifest["transfer_id"])
    assert row is not None
    assert row.status == "ACTIVE"
    assert row.bytes_reported == 321

    # And the HTTP surface still answers from the rebuilt state.
    again = await rig.client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}",
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["bytes_downloaded"] == 321


async def test_openapi_documents_the_data_plane(tmp_octop_home: Path) -> None:
    """The device author reads Scalar, so the routes must be described.

    API docs are off by default, so this one runs its own app with
    ``enable_api_docs`` rather than weakening the default.
    """
    from fastapi.testclient import TestClient

    from tests.support.app import write_octop_config

    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (_client, srv):
        app = build_homemind_app(srv)
        with TestClient(app) as http:
            response = http.get("/api/openapi.json")
    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert "/api/homemind/runtime/transfers" in paths
    content = paths["/api/homemind/runtime/transfers/{transfer_id}/content"]
    assert "head" in content and "get" in content
    for operation in content.values():
        assert operation["summary"], "every data-plane route needs a summary"
        assert operation["description"]
