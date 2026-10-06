"""HomeMind runtime composition on top of OctopServer."""

from __future__ import annotations

from typing import Any

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.scan_job import FamilyAssetScanJob
from homemind.tools.family import build_family_tools
from octop.infra.server import OctopServer


class HomeMindServer(OctopServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._asset_scan_job: FamilyAssetScanJob | None = None

    def build_extra_agent_tools(self) -> list[Any]:
        if self.services is None:
            return []
        run_migrations(self.services.db)
        return build_family_tools(
            self.services.db,
            user_repo=self.services.user_repo,
        )

    @property
    def asset_scan_job(self) -> FamilyAssetScanJob | None:
        return self._asset_scan_job

    async def start(self) -> None:
        await super().start()
        self._start_asset_scan_job()

    def _start_asset_scan_job(self) -> None:
        if self.services is None:
            return
        run_migrations(self.services.db)
        services = HomeMindServices.from_pool(self.services.db)
        manager = FamilyAssetManager(
            services.family_repo, services.family_asset_repo,
        )
        self._asset_scan_job = FamilyAssetScanJob(
            asset_manager=manager,
            asset_repo=services.family_asset_repo,
            family_repo=services.family_repo,
        )
        self._asset_scan_job.start()

    async def stop(self) -> None:
        try:
            if self._asset_scan_job is not None:
                await self._asset_scan_job.shutdown()
                self._asset_scan_job = None
        finally:
            await super().stop()

