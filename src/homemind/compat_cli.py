"""Deprecated ``octop`` console-script compatibility entry point."""

from __future__ import annotations

import click


def main() -> None:
    click.echo(
        "Warning: 'octop' is deprecated for this distribution; use 'homemind' instead.",
        err=True,
    )
    from homemind.cli import cli

    cli(prog_name="octop")
