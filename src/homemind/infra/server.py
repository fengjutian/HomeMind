"""HomeMind runtime composition on top of OctopServer."""

from __future__ import annotations

from typing import Any

from homemind.infra.db.migrate import run_migrations
from homemind.tools.family import build_family_tools
from octop.infra.server import OctopServer


class HomeMindServer(OctopServer):
    def build_extra_agent_tools(self) -> list[Any]:
        if self.services is None:
            return []
        run_migrations(self.services.db)
        return build_family_tools(
            self.services.db,
            user_repo=self.services.user_repo,
        )
