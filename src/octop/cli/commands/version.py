"""octop version command."""

from __future__ import annotations

import click


@click.command("version")
def version() -> None:
    """Show the installed HomeMind version."""
    try:
        from importlib.metadata import version as _v

        v = _v("homemind")
    except Exception:
        v = "unknown"
    click.echo(f"HomeMind v{v}")
