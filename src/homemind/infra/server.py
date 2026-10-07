"""HomeMind runtime composition on top of OctopServer."""

from __future__ import annotations

from collections.abc import Callable
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
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.device_transactions import resume_device_transaction
from homemind.infra.family.events import FamilyEventBus
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.memory_maintenance import MaintenanceRunner
from homemind.infra.family.notifications import NotificationManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.photo_intelligence import PhotoIntelligenceManager
from homemind.infra.family.privacy import ExternalProcessingGuard
from homemind.infra.family.reminder_runner import ReminderRunner
from homemind.infra.family.reminders import FamilyReminderManager
from homemind.infra.family.scan_job import FamilyAssetScanJob
from homemind.infra.family.search_indexer import FamilySearchIndexer
from homemind.infra.family.task_agent import FamilyTaskAgentExecutor
from homemind.infra.family.task_scheduler import FamilyTaskScheduler, TaskSchedulerRunner
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.thumbnails import ThumbnailService
from homemind.infra.family.transactions import FamilyTransactionManager
from homemind.tools.family import build_family_tools
from octop.infra.server import OctopServer


class HomeMindServer(OctopServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._asset_scan_job: FamilyAssetScanJob | None = None
        self._memory_maintenance: MaintenanceRunner | None = None
        self._asset_job_runner: AssetJobRunner | None = None
        self._reminder_runner: ReminderRunner | None = None
        self._task_scheduler_runner: TaskSchedulerRunner | None = None
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

    def _user_by_id(self, user_id: int) -> Any:
        """Load a ``User`` for a background job's permission checks.

        AI job handlers call services that take a ``User``; a worker has no
        session, so the user is resolved from the running registry.
        """
        manager = self.user_manager
        if manager is None:
            return None
        return manager.get_by_id(user_id)

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

    @property
    def reminder_runner(self) -> ReminderRunner | None:
        return self._reminder_runner

    @property
    def task_scheduler_runner(self) -> TaskSchedulerRunner | None:
        return self._task_scheduler_runner

    async def start(self) -> None:
        await super().start()
        self._start_asset_scan_job()
        self._start_family_event_bus()
        await self._start_memory_maintenance()
        await self._start_asset_job_runner()
        await self._start_reminder_runner()
        await self._start_task_scheduler()

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
            HomeMindServices.from_pool(self.services.db),
            hub=hub,
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
        notifications = self._notifications(hm, family_manager)
        context_manager = FamilyContextManager(
            family_manager,
            hm.family_context_repo,
            asset_repo=hm.family_asset_repo,
            search_indexer=self._search_indexer(hm),
        )
        # One transaction manager and one device runtime for the whole
        # process. They have to be the *same* instances: the runtime
        # resumes a parked transaction through a callback the manager
        # owns, and a second pair would park rows nothing ever resumes.
        transactions = FamilyTransactionManager(
            family_manager,
            context_manager,
            FamilyTaskManager(family_manager, hm.family_task_repo),
            hm.family_transaction_repo,
            self.services.user_repo,
        )
        device_runtime = DeviceRuntimeManager(
            family_manager,
            hm.family_device_repo,
            on_command_result=resume_device_transaction(transactions, hm.family_device_repo),
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
            notification_manager=notifications,
            transaction_manager=transactions,
            device_manager=device_runtime,
        )
        await self._memory_maintenance.start()

    def _notifications(
        self, services: HomeMindServices, families: FamilyManager
    ) -> NotificationManager:
        return NotificationManager(
            families,
            services.family_notification_repo,
            server_timezone=self._default_timezone(),
        )

    def _default_timezone(self) -> str:
        """The server's display timezone, read fresh from config.

        Read per call rather than cached at boot: ``config.json`` can be
        edited while the process runs, and a stale value would put
        reminders and notifications in the wrong hour.
        """
        from octop.config import load_config  # noqa: PLC0415 — keeps config out of module import

        try:
            return load_config(self.paths.config).default_timezone
        except Exception:  # noqa: BLE001 — a missing config must not break notifications
            return "UTC"

    async def _start_asset_job_runner(self) -> None:
        if self.services is None:
            return
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        family_manager = FamilyManager(hm.family_repo)
        search_indexer = self._search_indexer(hm)
        photo_intelligence = PhotoIntelligenceManager(
            family_manager,
            FamilyAssetManager(
                hm.family_repo,
                hm.family_asset_repo,
                search_indexer=search_indexer,
            ),
            hm.family_context_repo,
            hm.photo_intelligence_repo,
            # Every outbound vision / embedding call made by a job goes
            # through this guard, so the family's privacy settings are
            # enforced on the background path too — not only in the
            # request handlers.
            privacy_guard=ExternalProcessingGuard(hm),
            # Face matches become reviewable candidates, never labels.
            candidates=hm.face_candidate_repo,
        )
        self._asset_job_runner = AssetJobRunner(
            manager=AssetJobManager(
                family_manager,
                hm.asset_job_repo,
                asset_repo=hm.family_asset_repo,
            ),
            asset_manager=FamilyAssetManager(
                hm.family_repo,
                hm.family_asset_repo,
                search_indexer=search_indexer,
            ),
            family_manager=family_manager,
            asset_repo=hm.family_asset_repo,
            # Cache lives beside the rest of HomeMind's data, never beside
            # the original file — the source tree may be a read-only NAS.
            thumbnail_service=ThumbnailService(
                family_manager,
                self.paths.root / "homemind" / "thumbnails",
                asset_repo=hm.family_asset_repo,
                permission_evaluator=FamilyPermissionEvaluator(hm.family_repo),
            ),
            search_indexer=search_indexer,
            photo_intelligence=photo_intelligence,
            face_manager=photo_intelligence,
            provider_repo=self.services.provider_repo,
            user_factory=self._user_by_id,
            event_bus=lambda: self._family_event_bus,
        )
        await self._asset_job_runner.start()

    async def _start_reminder_runner(self) -> None:
        """Start the single reminder delivery loop.

        One runner per process. Claims are database leases, so a second
        process would simply lose the races rather than double-send.
        """
        if self.services is None:
            return
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        self._reminder_runner = ReminderRunner(
            manager=FamilyReminderManager(
                FamilyManager(hm.family_repo),
                hm.family_reminder_repo,
            ),
            notifications=self._notifications(hm, FamilyManager(hm.family_repo)),
            event_bus=lambda: self._family_event_bus,
        )
        await self._reminder_runner.start()

    async def _start_task_scheduler(self) -> None:
        """Start the family-task scheduler with its agent executor.

        Wired with the executor only when an agent manager exists. A
        deployment without agents still runs the scheduler: a claimed
        task fails loudly with "no executor is wired", which is
        visible, rather than never being claimed at all, which is not.
        """
        if self.services is None:
            return
        run_migrations(self.services.db)
        hm = HomeMindServices.from_pool(self.services.db)
        self._task_scheduler_runner = TaskSchedulerRunner(
            scheduler=FamilyTaskScheduler(FamilyManager(hm.family_repo), hm.family_task_repo),
            executor=self._task_executor(hm),
        )
        await self._task_scheduler_runner.start()

    def _task_executor(self, services: HomeMindServices) -> Any:
        """The agent executor, or ``None`` when no agent manager is wired.

        Reached through ``app_runtime`` because the agent registry is a
        boot-time singleton; before boot there is nothing to call.
        """
        if self.app_runtime is None:
            return None
        agent_manager = getattr(self.app_runtime, "agent_registry", None)
        if agent_manager is None:
            return None
        families = FamilyManager(services.family_repo)
        return FamilyTaskAgentExecutor(
            families,
            agent_manager,
            family_context=self._task_family_context(families, services),
        )

    def _task_family_context(
        self, families: FamilyManager, services: HomeMindServices
    ) -> Callable[[Any], str]:
        """Render the family background a scheduled agent is allowed to see.

        The same manager the interactive path uses, so a scheduled task
        cannot see more than the person who created it would.
        """
        manager = FamilyContextManager(
            families,
            services.family_context_repo,
            asset_repo=services.family_asset_repo,
            search_indexer=self._search_indexer(services),
        )

        def render(task: Any) -> str:
            try:
                resolved = manager.resolve(task.family_id, self._user_by_id(task.created_by))
            except Exception:  # noqa: BLE001 — context is a nicety, not a gate
                logger.warning("HomeMind: could not render family context for task %s", task.id)
                return ""
            return resolved.summary or ""

        return render

    async def stop(self) -> None:
        try:
            if self._task_scheduler_runner is not None:
                await self._task_scheduler_runner.stop()
                self._task_scheduler_runner = None
        finally:
            try:
                if self._reminder_runner is not None:
                    await self._reminder_runner.stop()
                    self._reminder_runner = None
            finally:
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
