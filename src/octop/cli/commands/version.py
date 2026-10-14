"""octop version command."""

from __future__ import annotations

import click


@click.command("version")
def version() -> None:
    """Show the integrated Octop upstream version and commit."""
    from octop.upstream_version import OCTOP_UPSTREAM_COMMIT, OCTOP_UPSTREAM_VERSION

    click.echo(f"Octop v{OCTOP_UPSTREAM_VERSION} ({OCTOP_UPSTREAM_COMMIT[:8]})")
