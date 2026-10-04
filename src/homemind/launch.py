"""HomeMind composition root built on the Octop runtime."""

from __future__ import annotations

from typing import Any

from homemind.api.app import build_app
from octop.launch import run_foreground as run_octop_foreground


async def run_foreground(**kwargs: Any) -> None:
    await run_octop_foreground(app_factory=build_app, **kwargs)
