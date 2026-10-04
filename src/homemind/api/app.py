"""Compose the HomeMind API on top of the Octop application."""

from __future__ import annotations

from fastapi import FastAPI

from homemind.api.routers import families
from homemind.infra.db.migrate import run_migrations
from octop.api.app import build_app as build_octop_app
from octop.infra.server import OctopServer


def build_app(server: OctopServer) -> FastAPI:
    app = build_octop_app(server)
    app.title = "HomeMind API"
    if server.services is not None:
        run_migrations(server.services.db)
    app.include_router(families.router, prefix="/api/families", tags=["families"])
    return app
