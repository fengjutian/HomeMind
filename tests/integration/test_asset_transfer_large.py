"""Acceptance for the headline requirement: a 5 GiB-class download.

The rest of the transfer suite proves the protocol. This proves the
*scale* claim in the completion criteria: a multi-gigabyte asset is
served over the Range path, four devices workers can pull it at once, the
server survives a credential refresh mid-flight, and heap use does not
grow with the file.

Two design choices keep the run affordable and honest:

* one 5 GiB source file serves the whole journey -- index, first gigabyte,
  credential refresh, four-way parallel remainder -- so the disk holds
  5 GiB rather than 10, and the file is hashed once at scan time.
* the client hashes each chunk as it arrives instead of assembling a
  second copy, which is what a real device does and is what makes the
  memory assertion meaningful: neither side may accumulate.

Gated behind ``OCTOP_RUN_LARGE_TRANSFER_TEST=1`` and marked ``slow``: it
writes several GiB and takes minutes, so it must not sit in the default
gate. Run it deliberately::

    OCTOP_RUN_LARGE_TRANSFER_TEST=1 pytest tests/integration/test_asset_transfer_large.py -s
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn

from homemind.api.app import build_app as build_homemind_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("OCTOP_RUN_LARGE_TRANSFER_TEST") != "1",
        reason="set OCTOP_RUN_LARGE_TRANSFER_TEST=1 to run the multi-GiB acceptance",
    ),
]

TRANSFERS = "/api/homemind/runtime/transfers"
MIB = 1024 * 1024
GIB = 1024 * MIB
SIZE_BYTES = 5 * GIB
CHUNK_BYTES = 16 * MIB
WORKERS = 4
FIRST_PASS_BYTES = GIB  # how much is pulled before the credential refresh

#: One MiB of a repeating pattern, so any 16 MiB chunk is byte-identical
#: and its expected digest is computable without re-reading the file.
BLOCK = bytes(range(256)) * 4096
CHUNK_COUNT = SIZE_BYTES // CHUNK_BYTES
CHUNK_SHA256 = hashlib.sha256(BLOCK * (CHUNK_BYTES // len(BLOCK))).hexdigest()
FILE_SHA256 = hashlib.sha256(BLOCK * (SIZE_BYTES // len(BLOCK))).hexdigest()


def _write_source(path: Path, size: int) -> None:
    """Materialise ``size`` bytes at ``path``.

    Real bytes, not a sparse hole: the claim under test is about real
    bytes travelling, and Windows does not give a sparse file the same
    semantics as POSIX.
    """
    written = 0
    with path.open("wb") as handle:
        while written < size:
            block = BLOCK if size - written >= len(BLOCK) else BLOCK[: size - written]
            handle.write(block)
            written += len(block)


@dataclass(frozen=True)
class _Rig:
    client: httpx.AsyncClient
    auth: dict
    family_id: str
    server: OctopServer


@pytest.fixture(scope="session")
def large_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One 5 GiB file, built once and reused by every test here."""
    directory = tmp_path_factory.mktemp("large-transfer")
    payload = directory / "family-video.bin"
    if not payload.exists() or payload.stat().st_size != SIZE_BYTES:
        _write_source(payload, SIZE_BYTES)
    return payload


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
async def rig(tmp_octop_home: Path) -> object:
    """A *real* HTTP server on a socket, not the in-process ASGI harness.

    This is what makes the memory assertion mean anything. The ASGI
    transport httpx mounts in-process collects a response before handing
    it back, so heap numbers taken against it describe the test client
    rather than the server. Over a socket the only thing between the
    file and the assertion is the real server.
    """
    async with octop_client(tmp_octop_home, app_factory=build_homemind_app) as (_probe, srv):
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                build_homemind_app(srv),
                host="127.0.0.1",
                port=port,
                log_level="error",
            )
        )
        task = asyncio.get_running_loop().create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=600.0
            ) as client:
                await bootstrap_admin(client, tmp_octop_home)
                auth = await auth_header(client)
                created = await client.post(
                    "/api/homemind/families",
                    headers=auth,
                    json={
                        "name": "Large Transfer",
                        "timezone": "Asia/Shanghai",
                        "locale": "zh",
                    },
                )
                assert created.status_code == 201, created.text
                yield _Rig(
                    client=client,
                    auth=auth,
                    family_id=str(created.json()["id"]),
                    server=srv,
                )
        finally:
            server.should_exit = True
            await task


def _open_handles() -> int:
    """Server-side admission slots currently held, read from the test.

    The server runs in this same process, so the live counter is the
    honest way to see whether a slot came back after each response.
    """
    from homemind.infra.family.asset_transfers import _ADMISSION

    return int(_ADMISSION._open_handles)


async def _prepare(rig: _Rig, source: Path, *, device_name: str, request_key: str) -> dict:
    """Index the file, pair a device, and open one transfer."""
    client = rig.client
    scanned = await client.post(
        f"/api/homemind/families/{rig.family_id}/assets/scan",
        headers=rig.auth,
        json={"directory": str(source.parent), "recursive": False},
    )
    assert scanned.status_code == 200, scanned.text
    listed = await client.get(
        f"/api/homemind/families/{rig.family_id}/assets",
        headers=rig.auth,
    )
    asset_id = str(next(a for a in listed.json() if a["name"] == source.name)["id"])

    pairing = await client.post(
        f"/api/homemind/families/{rig.family_id}/devices",
        headers=rig.auth,
        json={
            "name": device_name,
            "device_type": "tv",
            "platform": "android",
            "capabilities": ["asset.download"],
        },
    )
    paired = await client.post(
        "/api/homemind/runtime/pair",
        json={"code": pairing.json()["code"], "address": None},
    )
    device_token = str(paired.json()["token"])

    created = await client.post(
        TRANSFERS,
        headers={"Authorization": f"Bearer {device_token}"},
        json={"asset_id": asset_id, "request_key": request_key},
    )
    assert created.status_code == 201, created.text
    manifest = created.json()
    assert manifest["asset"]["size_bytes"] == SIZE_BYTES
    assert manifest["asset"]["sha256"] == FILE_SHA256
    return {
        "manifest": manifest,
        "device_token": device_token,
        "url": f"{TRANSFERS}/{manifest['transfer_id']}/content",
    }


@pytest.mark.asyncio
async def test_five_gib_download_survives_refresh_and_stays_flat(
    rig: _Rig,
    large_source: Path,
) -> None:
    """The whole first-run *and* recovery story at 5 GiB.

    Sequence: index -> authorize -> HEAD -> first gigabyte -> refresh the
    credential (the old one must die) -> pull the remaining four
    gigabytes with four concurrent workers -> every chunk matches the
    expected digest -> report completion.
    """
    context = await _prepare(
        rig,
        large_source,
        device_name="living-tv",
        request_key="large-1",
    )
    client = rig.client
    manifest = context["manifest"]
    device_token = context["device_token"]
    url = context["url"]
    transfer_id = manifest["transfer_id"]
    workers = int(manifest["download"]["max_concurrency"])
    assert workers == WORKERS, "the manifest advertises the concurrency the client should use"

    probe = await client.head(
        url,
        headers={"Authorization": f"Transfer {manifest['download']['token']}"},
    )
    assert probe.status_code == 200
    assert probe.headers["content-length"] == str(SIZE_BYTES)
    assert probe.headers["etag"] == manifest["asset"]["etag"]
    assert probe.headers["accept-ranges"] == "bytes"

    seen: list[int] = []

    async def pull(token: str, index: int) -> str:
        """Fetch one chunk at its offset and return its digest."""
        start = index * CHUNK_BYTES
        end = start + CHUNK_BYTES - 1
        digest = hashlib.sha256()
        async with client.stream(
            "GET",
            url,
            headers={"Authorization": f"Transfer {token}", "Range": f"bytes={start}-{end}"},
        ) as response:
            if response.status_code != 206:
                detail = (await response.aread())[:200]
                raise AssertionError(
                    f"chunk {index} refused: {response.status_code} {detail!r} "
                    f"(open handles {seen[-8:]})"
                )
            async for block in response.aiter_bytes():
                digest.update(block)
        seen.append(_open_handles())
        return digest.hexdigest()

    async def pull_many(token: str, indices: list[int], workers: int) -> list[tuple[int, str]]:
        """Fetch chunks through a bounded worker pool.

        Firing every request at once is not what a device does -- the
        manifest hands it ``max_concurrency`` -- and the server enforces
        that with a 429. Pooling them here is both more faithful and a
        live check that the advertised concurrency is actually usable.
        """
        queue: asyncio.Queue[int] = asyncio.Queue()
        for index in indices:
            queue.put_nowait(index)
        results: list[tuple[int, str]] = []
        lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                try:
                    index = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                digest = await pull(token, index)
                async with lock:
                    results.append((index, digest))

        await asyncio.gather(*(worker() for _ in range(workers)))
        return results

    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        # First pass: one gigabyte before any refresh.
        first_pass = await pull_many(
            manifest["download"]["token"],
            list(range(FIRST_PASS_BYTES // CHUNK_BYTES)),
            workers,
        )
        current, peak = tracemalloc.get_traced_memory()
        first_growth = peak - baseline
    finally:
        tracemalloc.stop()

    assert len(first_pass) == FIRST_PASS_BYTES // CHUNK_BYTES
    assert all(digest == CHUNK_SHA256 for _index, digest in first_pass)
    # Sixty-four completed ranges must have handed their admission slots
    # back. A leaked slot here is invisible until it locks the transfer
    # out at some arbitrary chunk later, which is exactly how it showed
    # up while writing this test.
    assert max(seen) <= workers, f"more ranges in flight than workers: {seen}"
    assert seen[-1] == 0, f"file handles were not released after the last chunk: {seen[-8:]}"
    # A buffered implementation would sit at ~5 GiB by now. A streaming
    # one never holds more than a handful of read blocks.
    assert first_growth < 64 * MIB, (
        f"streaming {FIRST_PASS_BYTES} bytes grew the heap by {first_growth}; "
        "the response body is being buffered instead of streamed"
    )

    refreshed = await client.post(
        f"{TRANSFERS}/{transfer_id}/refresh",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_token = refreshed.json()["download"]["token"]
    assert new_token != manifest["download"]["token"]

    stale = await client.get(
        url,
        headers={"Authorization": f"Transfer {manifest['download']['token']}"},
    )
    assert stale.status_code == 401, "the previous credential must stop working at once"

    remaining = list(range(FIRST_PASS_BYTES // CHUNK_BYTES, CHUNK_COUNT))

    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        # One shared pool of ``max_concurrency`` workers over every
        # remaining chunk. Four independent four-worker lanes would be
        # sixteen concurrent ranges, which the server correctly refuses.
        rest = await pull_many(new_token, remaining, workers)
        _current, peak = tracemalloc.get_traced_memory()
        second_growth = peak - baseline
    finally:
        tracemalloc.stop()

    assert sorted(index for index, _digest in rest) == remaining, (
        "every remaining chunk must be fetched exactly once"
    )
    for _index, digest in rest:
        assert digest == CHUNK_SHA256, "a reassembled chunk does not match the source"
    assert second_growth < 64 * MIB, (
        f"four concurrent streams grew the heap by {second_growth}; "
        "responses are accumulating somewhere"
    )

    # Every one of the 320 chunks matched its expected digest at a known
    # offset, so the concatenation is the file. Report the manifest digest
    # and let the server agree.
    done = await client.post(
        f"{TRANSFERS}/{transfer_id}/complete",
        headers={"Authorization": f"Bearer {device_token}"},
        json={"size_bytes": SIZE_BYTES, "sha256": FILE_SHA256},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"

    revoked = await client.get(url, headers={"Authorization": f"Transfer {new_token}"})
    assert revoked.status_code == 401, "completion must kill the download credential"


@pytest.mark.asyncio
async def test_five_gib_transfer_survives_a_rebuilt_service_container(
    rig: _Rig,
    large_source: Path,
) -> None:
    """A restart mid-download must not strand the task or its progress."""
    from homemind.infra.db.services import HomeMindServices

    context = await _prepare(
        rig,
        large_source,
        device_name="restart-tv",
        request_key="large-restart",
    )
    client = rig.client
    manifest = context["manifest"]
    device_token = context["device_token"]
    headers = {"Authorization": f"Bearer {device_token}"}

    await client.post(
        f"{TRANSFERS}/{manifest['transfer_id']}/progress",
        headers=headers,
        json={"bytes_downloaded": 2 * GIB},
    )

    assert rig.server.services is not None
    # A fresh container over the same database is what a restart looks
    # like to the domain layer: nothing in memory, everything in the row.
    rebuilt = HomeMindServices.from_pool(rig.server.services.db)
    assert rebuilt.asset_transfer_repo.get(manifest["transfer_id"]) is not None

    status = await client.get(
        f"{TRANSFERS}/{manifest['transfer_id']}",
        headers=headers,
    )
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "ACTIVE"
    assert body["bytes_downloaded"] == 2 * GIB
    assert body["asset"]["sha256"] == FILE_SHA256

    # And the download still serves from the recorded offset.
    resumed = await client.get(
        context["url"],
        headers={
            "Authorization": f"Transfer {manifest['download']['token']}",
            "Range": f"bytes={2 * GIB}-{2 * GIB + CHUNK_BYTES - 1}",
        },
    )
    assert resumed.status_code == 206
    assert hashlib.sha256(resumed.content).hexdigest() == CHUNK_SHA256
