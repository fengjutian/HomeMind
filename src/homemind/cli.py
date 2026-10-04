"""HomeMind command-line entry point with legacy Octop command coverage."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import ClassVar

import click

from homemind.compat import prepare_environment
from homemind.launch import run_foreground_blocking
from octop.cli.main import _LazyCLI, _ensure_utf8_stdio
from octop.cli.registry import COMMANDS

prepare_environment()


def _version() -> str:
    try:
        return version("homemind")
    except PackageNotFoundError:
        return "unknown"


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"homemind v{_version()}")
    ctx.exit()


class _HomeMindCLI(_LazyCLI):
    _registry: ClassVar[dict[str, tuple[str, str, str]]] = {
        **COMMANDS,
        "run": ("homemind.cli", "run", "Start the HomeMind server."),
    }


@click.group(
    cls=_HomeMindCLI,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "-v",
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show the installed HomeMind version.",
)
@click.option("--user", "as_user", envvar="HOMEMIND_USER", default=None)
@click.option("--agent", "agent_id", envvar="HOMEMIND_AGENT", default=None)
@click.option("--json", "json_out", is_flag=True, default=False)
@click.pass_context
def cli(
    ctx: click.Context,
    as_user: str | None,
    agent_id: str | None,
    json_out: bool,
) -> None:
    """HomeMind command-line interface."""
    _ensure_utf8_stdio()
    ctx.ensure_object(dict)
    ctx.obj["as_user"] = as_user
    ctx.obj["agent_id"] = agent_id
    ctx.obj["json_out"] = json_out


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


if __name__ == "__main__":
    cli()
