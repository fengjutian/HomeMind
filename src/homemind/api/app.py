"""Compose the HomeMind API on top of the Octop application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from homemind.api.routers import context, families, transactions
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
        context.router,
        prefix="/api/homemind/families",
        tags=["homemind-context"],
    )
    app.include_router(
        transactions.router,
        prefix="/api/homemind/families",
        tags=["homemind-transactions"],
    )
    return app
