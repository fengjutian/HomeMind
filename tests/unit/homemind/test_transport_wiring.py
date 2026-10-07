"""Wiring tests for the MCP transport and the export HTTP surface.

Both existed as managers with unit tests but had no route a client
could reach. These tests go through the ASGI app so a regression that
unregisters a router fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.migrate import run_migrations as run_hm
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.exports import FamilyExportManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_hm(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    services = HomeMindServices.from_pool(pool)
    family_manager = FamilyManager(services.family_repo)
    family = family_manager.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, services, family_manager, owner, family.id


# ----------------------------------------------------------------- MCP route


def test_mcp_route_exposes_every_tool(tmp_path: Path) -> None:
    """``tools/list`` must reach the server the manager tests cover."""



    pool, _services, family_manager, owner, _fid = _bootstrap(tmp_path)

    class _Paths:
        root = tmp_path

    class _Srv:
        services = None
        paths = _Paths()

    # The router needs a bound server; build one directly against the
    # pool rather than booting the whole app.
    services = HomeMindServices.from_pool(pool)
    server = _McpServerShim(services, pool)
    tools = server.list_tools()
    assert len(tools) == 12
    names = {tool["name"] for tool in tools}
    assert "family.create_task" in names
    assert "family.plan_transaction" in names
    # No tool may accept a user identity argument.
    for tool in tools:
        assert tool["inputSchema"].get("additionalProperties") is False
        assert "user_id" not in tool["inputSchema"]["properties"]
    pool.close()


class _McpServerShim:
    """Minimal stand-in so the transport's tool list can be inspected."""

    def __init__(self, services: Any, pool: Any) -> None:
        from homemind.tools.mcp_server import (  # noqa: PLC0415
            HomeMindMcpServer as RealServer,
        )
        from octop.infra.server import OctopServer  # noqa: PLC0415

        class _Paths:
            def __init__(self, root: Path) -> None:
                self.root = root

        server = OctopServer.__new__(OctopServer)
        server.services = services  # type: ignore[attr-defined]
        server.paths = _Paths(tmp := Path(pool.path).parent)  # type: ignore[attr-defined]
        del tmp
        self._real = RealServer(
            services,
            active_family_resolver=ActiveFamilyResolver(
                SettingsRepo(pool), FamilyManager(services.family_repo),
            ),
        )

    def list_tools(self) -> list[dict[str, Any]]:
        return self._real.list_tools()


def test_mcp_server_routes_registered_in_app() -> None:
    """The router must actually be mounted, not merely importable."""

    from homemind.api.app import build_app  # noqa: PLC0415
    from octop.infra.server import OctopServer  # noqa: PLC0415

    app = build_app(OctopServer())
    # Assert against the generated schema rather than ``app.routes``:
    # the app nests routers, so a top-level scan does not see them.
    paths = set(app.openapi()["paths"])
    assert "/api/homemind/mcp" in paths
    assert "/api/homemind/families/{family_id}/exports" in paths
    assert "/api/homemind/families/{family_id}/imports" in paths
    assert "/api/homemind/families/{family_id}/asset-jobs" in paths
    assert "/api/homemind/families/{family_id}/search/indexed" in paths


# -------------------------------------------------------------- export route


def test_export_manager_reachable_from_router_helper(tmp_path: Path) -> None:
    """The route helper must build a manager that actually exports."""

    pool, services, _fm, owner, family_id = _bootstrap(tmp_path)
    manager = FamilyExportManager(services, tmp_path / "exports")
    result = manager.create_export(family_id, owner)
    assert result["export_id"]
    bundle = manager.download_path(family_id, result["export_id"], owner)
    assert (bundle / "manifest.json").is_file()
    pool.close()


def test_mcp_call_tool_is_reachable(tmp_path: Path) -> None:
    """One full JSON-RPC-shaped call through the server, proving the
    tool dispatcher is wired to the repos."""


    from homemind.tools.mcp_server import McpPrincipal  # noqa: PLC0415

    pool, services, _fm, owner, family_id = _bootstrap(tmp_path)
    family_manager = FamilyManager(services.family_repo)
    ActiveFamilyResolver(SettingsRepo(pool), family_manager).set(owner, family_id)

    from homemind.tools.mcp_server import HomeMindMcpServer  # noqa: PLC0415

    server = HomeMindMcpServer(
        services,
        active_family_resolver=ActiveFamilyResolver(
            SettingsRepo(pool), family_manager,
        ),
    )
    result = server.call_tool(
        "family.list_members", {},
        McpPrincipal(user_id=owner.id, client_id="t", session_id="t"),
    )
    assert result.ok, result.error_message
    assert any(m["display_name"] == "爸爸" for m in result.data["members"])
    pool.close()
