"""Compose the HomeMind API on top of the Octop application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from homemind.api.routers import (
    active_family,
    albums,
    asset_jobs,
    asset_transfers,
    calendar,
    context,
    dashboard,
    devices,
    exports,
    families,
    filesystem,
    homemind_mcp,
    knowledge,
    memory_candidates,
    notifications,
    observability,
    photos,
    reminders,
    runtime,
    search,
    search_index,
    smart_home,
    task_scheduler,
    tasks,
    transactions,
)
from homemind.infra.db.migrate import run_migrations
from homemind.infra.errors import HomeMindError
from octop.api.app import build_app as build_octop_app
from octop.infra.server import OctopServer


def build_app(server: OctopServer) -> FastAPI:
    app = build_octop_app(server)
    app.title = "HomeMind API"
    if server.services is not None:
        run_migrations(server.services.db)

    @app.exception_handler(HomeMindError)
    async def _homemind_error(_request: Request, exc: HomeMindError) -> JSONResponse:
        # 429 must tell the caller when to come back; without the header a
        # client can only guess and retry into the same limit.
        headers = {"Retry-After": "1"} if exc.status == 429 else None
        return JSONResponse(status_code=exc.status, content=exc.to_envelope(), headers=headers)

    app.include_router(
        families.router,
        prefix="/api/homemind/families",
        tags=["homemind-families"],
    )
    app.include_router(
        devices.router,
        prefix="/api/homemind/families",
        tags=["homemind-devices"],
    )
    app.include_router(
        runtime.router,
        prefix="/api/homemind",
        tags=["homemind-runtime"],
    )
    app.include_router(
        asset_transfers.router,
        prefix="/api/homemind",
        tags=["homemind-asset-transfers"],
    )
    app.include_router(
        observability.router,
        prefix="/api/homemind",
        tags=["homemind-observability"],
    )
    app.include_router(
        active_family.router,
        prefix="/api/homemind",
        tags=["homemind-active-family"],
    )
    app.include_router(
        context.router,
        prefix="/api/homemind/families",
        tags=["homemind-context"],
    )
    app.include_router(
        transactions.router,
        prefix="/api/homemind/families",
        tags=["homemind-transactions"],
    )
    app.include_router(
        search.router,
        prefix="/api/homemind/families",
        tags=["homemind-search"],
    )
    app.include_router(
        search_index.router,
        prefix="/api/homemind/families",
        tags=["homemind-search-index"],
    )
    app.include_router(
        tasks.router,
        prefix="/api/homemind/families",
        tags=["homemind-tasks"],
    )
    app.include_router(
        task_scheduler.router,
        prefix="/api/homemind/families",
        tags=["homemind-task-scheduler"],
    )
    app.include_router(
        albums.router,
        prefix="/api/homemind/families",
        tags=["homemind-albums"],
    )
    app.include_router(
        photos.router,
        prefix="/api/homemind/families",
        tags=["homemind-photos"],
    )
    app.include_router(
        filesystem.router,
        prefix="/api/homemind/families",
        tags=["homemind-filesystem"],
    )
    app.include_router(
        memory_candidates.router,
        prefix="/api/homemind/families",
        tags=["homemind-memory-candidates"],
    )
    app.include_router(
        asset_jobs.router,
        prefix="/api/homemind/families",
        tags=["homemind-asset-jobs"],
    )
    app.include_router(
        knowledge.router,
        prefix="/api/homemind/families",
        tags=["homemind-knowledge"],
    )
    app.include_router(
        calendar.router,
        prefix="/api/homemind/families",
        tags=["homemind-calendar"],
    )
    app.include_router(
        reminders.router,
        prefix="/api/homemind/families",
        tags=["homemind-reminders"],
    )
    app.include_router(
        notifications.router,
        prefix="/api/homemind/families",
        tags=["homemind-notifications"],
    )

    app.include_router(
        exports.router,
        prefix="/api/homemind/families",
        tags=["homemind-exports"],
    )

    # JSON-RPC transport for the HomeMind MCP server. Not family-
    # prefixed: a client authenticates with its own dashboard JWT and
    # the server resolves the active family per call.
    app.include_router(
        homemind_mcp.router,
        prefix="/api/homemind",
        tags=["homemind-mcp"],
    )

    app.include_router(
        dashboard.router,
        prefix="/api/homemind/families",
        tags=["homemind-dashboard"],
    )

    # Smart home: providers, entity mappings and the command catalogue. This
    # router existed but was never mounted, which is why every adapter call
    # short-circuited and the feature was unreachable over HTTP.
    app.include_router(
        smart_home.router,
        prefix="/api/homemind/families",
        tags=["homemind-smart-home"],
    )
    # Octop installs its dashboard catch-all while building the base app. Keep
    # that fallback behind HomeMind's routes so it cannot swallow API GETs.
    for route in app.routes:
        if getattr(route, "name", None) == "spa_fallback":
            app.routes.remove(route)
            app.routes.append(route)
            break
    return app
