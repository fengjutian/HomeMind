"""Compose the HomeMind API on top of the Octop application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from homemind.api.routers import (
    active_family,
    albums,
    context,
    devices,
    families,
    filesystem,
    observability,
    photos,
    runtime,
    search,
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
        return JSONResponse(status_code=exc.status, content=exc.to_envelope())

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
        tasks.router,
        prefix="/api/homemind/families",
        tags=["homemind-tasks"],
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
    # Octop installs its dashboard catch-all while building the base app. Keep
    # that fallback behind HomeMind's routes so it cannot swallow API GETs.
    for route in app.routes:
        if getattr(route, "name", None) == "spa_fallback":
            app.routes.remove(route)
            app.routes.append(route)
            break
    return app
