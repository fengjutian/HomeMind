"""HomeMind command-line entry point."""

from __future__ import annotations

import click

from homemind.launch import run_foreground_blocking


@click.group()
def cli() -> None:
    """Run and manage the HomeMind product extension."""


@cli.command("run")
@click.option("--host", default=None, help="Override bind host.")
@click.option("--port", default=None, type=click.IntRange(0, 65535), help="Override port.")
@click.option("--reload", is_flag=True, default=False, help="Enable development reload.")
@click.option("--workers", default=1, type=click.IntRange(1), help="Worker process count.")
@click.option("--log-level", default=None)
@click.option("--ssl-certfile", default=None)
@click.option("--ssl-keyfile", default=None)
def run(
    host: str | None,
    port: int | None,
    reload: bool,
    workers: int,
    log_level: str | None,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
) -> None:
    """Start HomeMind with the Octop runtime."""
    run_foreground_blocking(
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level=log_level,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
