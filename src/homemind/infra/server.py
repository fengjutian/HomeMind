"""HomeMind runtime composition on top of OctopServer."""

from __future__ import annotations

from typing import Any

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.agents.family_context_middleware import FamilyContextMiddleware
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.memory_candidates import (
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.asset_job_runner import AssetJobRunner
from homemind.infra.family.asset_jobs import AssetJobManager
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.events import FamilyEventBus
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.memory_maintenance import MaintenanceRunner
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.scan_job import FamilyAssetScanJob
from homemind.infra.family.search_indexer import FamilySearchIndexer
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from homemind.tools.family import build_family_tools
from octop.infra.server import OctopServer


class HomeMindServer(OctopServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._asset_scan_job: FamilyAssetScanJob | None = None
        self._memory_maintenance: MaintenanceRunner | None = None
        self._asset_job_runner: AssetJobRunner | None = None
        self._family_event_bus: FamilyEventBus | None = None

    def build_extra_agent_tools(self) -> list[Any]:
        if self.services is None:
            return []
        run_migrations(self.services.db)
        return build_family_tools(
            self.services.db,
            user_repo=self.services.user_repo,
        )

    def build_extra_agent_middleware(self) -> list[Any]:
        """Append HomeMind-specific AgentMiddleware to every agent turn.

        The middleware reads the active family from per-request
        ``configurable`` (never a process global) and renders the
        resolved context as a server-controlled XML block inside the
        system message. The block is not part of the persisted user
        history. The post-turn extractor runs from inside the same
        middleware so the wiring is colocated with the context
        resolver.
        """
        if self.services is None:
            return []
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        family_manager = FamilyManager(hm.family_repo)
        context_manager = FamilyContextManager(
            family_manager,
            hm.family_context_repo,
            asset_repo=hm.family_asset_repo,
            search_indexer=self._search_indexer(hm),
        )
        lifecycle = MemoryLifecycleManager(
            family_manager,
            context_manager,
            MemoryCandidateRepo(self.services.db),
            MemoryEvidenceRepo(self.services.db),
        )
        return [
            FamilyContextMiddleware(
                user_repo=self.services.user_repo,
                active_family_resolver=ActiveFamilyResolver(
                    self.services.settings_repo,
                    family_manager,
                ),
                family_manager=family_manager,
                context_manager=context_manager,
                permission_evaluator=FamilyPermissionEvaluator(hm.family_repo),
                memory_lifecycle=lifecycle,
                event_bus=lambda: self._family_event_bus,
            ),
        ]

    @staticmethod
    def _search_indexer(services: HomeMindServices) -> FamilySearchIndexer:
        """Build the unified search index writer.

        Every `FamilyContextManager` gets one so event and memory
        writes keep the index fresh; without it Stage 7's search would
        be permanently empty.
        """

        return FamilySearchIndexer(
            services.search_index_repo,
            family_repo=services.family_repo,
            context_repo=services.family_context_repo,
            asset_repo=services.family_asset_repo,
            album_repo=services.family_album_repo,
            photo_repo=services.photo_intelligence_repo,
        )

    @property
    def memory_maintenance(self) -> MaintenanceRunner | None:
        return self._memory_maintenance

    @property
    def family_event_bus(self) -> FamilyEventBus | None:
        return self._family_event_bus

    @property
    def asset_scan_job(self) -> FamilyAssetScanJob | None:
        return self._asset_scan_job

    @property
    def asset_job_runner(self) -> AssetJobRunner | None:
        return self._asset_job_runner

    async def start(self) -> None:
        await super().start()
        self._start_asset_scan_job()
        self._start_family_event_bus()
        await self._start_memory_maintenance()
        await self._start_asset_job_runner()

    def _start_family_event_bus(self) -> None:
        """Bind the family event bus to Octop's WebSocket hub.

        The hub already tracks per-user dashboard sockets; the bus only
        supplies the "who in this family may see this" decision. When
        the gateway is unavailable the bus stays ``None`` — family
        writes still work, they just do not push.
        """

        if self.services is None or self.app_runtime is None:
            return
        hub = getattr(self.app_runtime.gateway, "ws_hub", None)
        if hub is None:
            return
        run_migrations(self.services.db)
        self._family_event_bus = FamilyEventBus(
            HomeMindServices.from_pool(self.services.db), hub=hub,
        )

    def _start_asset_scan_job(self) -> None:
        if self.services is None:
            return
        run_migrations(self.services.db)
        services = HomeMindServices.from_pool(self.services.db)
        manager = FamilyAssetManager(
            services.family_repo,
            services.family_asset_repo,
            search_indexer=self._search_indexer(services),
        )
        self._asset_scan_job = FamilyAssetScanJob(
            asset_manager=manager,
            asset_repo=services.family_asset_repo,
            family_repo=services.family_repo,
        )
        self._asset_scan_job.start()

    async def _start_memory_maintenance(self) -> None:
        if self.services is None:
            return
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        family_manager = FamilyManager(hm.family_repo)
        context_manager = FamilyContextManager(
            family_manager,
            hm.family_context_repo,
            asset_repo=hm.family_asset_repo,
            search_indexer=self._search_indexer(hm),
        )
        self._memory_maintenance = MaintenanceRunner(
            db=self.services.db,
            family_repo=hm.family_repo,
            context_repo=hm.family_context_repo,
            candidate_repo=hm.memory_candidate_repo,
            evidence_repo=hm.memory_evidence_repo,
            device_repo=hm.family_device_repo,
            family_manager=family_manager,
            context_manager=context_manager,
            transaction_manager=FamilyTransactionManager(
                family_manager,
                context_manager,
                FamilyTaskManager(family_manager, hm.family_task_repo),
                hm.family_transaction_repo,
                self.services.user_repo,
            ),
        )
        await self._memory_maintenance.start()

    async def _start_asset_job_runner(self) -> None:
        if self.services is None:
            return
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        family_manager = FamilyManager(hm.family_repo)
        self._asset_job_runner = AssetJobRunner(
            manager=AssetJobManager(family_manager, hm.asset_job_repo),
            asset_manager=FamilyAssetManager(
                hm.family_repo,
                hm.family_asset_repo,
                search_indexer=self._search_indexer(hm),
            ),
            family_manager=family_manager,
            asset_repo=hm.family_asset_repo,
            event_bus=lambda: self._family_event_bus,
        )
        await self._asset_job_runner.start()

    async def stop(self) -> None:
        try:
            if self._asset_job_runner is not None:
                await self._asset_job_runner.stop()
                self._asset_job_runner = None
        finally:
            try:
                if self._memory_maintenance is not None:
                    await self._memory_maintenance.stop()
                    self._memory_maintenance = None
            finally:
                try:
                    if self._asset_scan_job is not None:
                        await self._asset_scan_job.shutdown()
                        self._asset_scan_job = None
                finally:
                    await super().stop()
