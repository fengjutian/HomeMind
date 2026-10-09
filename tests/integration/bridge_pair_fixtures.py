"""Two temporary Octop instances for cross-instance drills (plan phase 16/17).

The plan requires the fault drills to run against *temporary* instances, never
a production account. Existing ``test_bridge_live.py`` is opt-in and points at a
real remote, so it cannot prove anything on CI.

Both instances run on real sockets rather than the in-process ASGI harness:
the drills exercise a WebSocket handshake between two servers, and an ASGI
transport mounted in one process cannot model a second process at all.

Imported directly by the drill modules (``fixtures`` are only auto-discovered
from a conftest, and a helper module of this size does not warrant one).
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from octop.api.app import build_app
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Instance:
    """One temporary instance, reachable over a real socket."""

    client: httpx.AsyncClient
    auth: dict[str, str]
    server: OctopServer
    base_url: str

    @property
    def manager(self) -> Any:
        assert self.server.app_runtime is not None
        return self.server.app_runtime.bridge_manager


@asynccontextmanager
async def running_instance(home: Path) -> AsyncIterator[Instance]:
    """Boot one instance on a free port and yield a ready client."""
    async with octop_client(home) as (_probe, srv):
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(build_app(srv), host="127.0.0.1", port=port, log_level="error")
        )
        task = asyncio.get_running_loop().create_task(server.serve())
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=60.0
            ) as client:
                await bootstrap_admin(client, home)
                auth = await auth_header(client)
                yield Instance(
                    client=client, auth=auth, server=srv, base_url=f"http://127.0.0.1:{port}"
                )
        finally:
            server.should_exit = True
            await task


async def two_instances(tmp_path: Path) -> AsyncIterator[tuple[Instance, Instance]]:
    """Two fully independent instances with separate homes and databases."""
    async with running_instance(tmp_path / "a") as a, running_instance(tmp_path / "b") as b:
        yield a, b
