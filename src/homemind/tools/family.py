"""Built-in HomeMind family tools for Octop agents."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.albums import FamilyAlbumManager, OrganizationStrategy
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import PhotoIntelligenceManager
from homemind.infra.family.photo_providers import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
    require_provider,
)
from homemind.infra.family.search import FamilySearchManager, SearchKind
from homemind.infra.family.tasks import FamilyTaskManager, TaskStatus
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.providers import ProviderRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import User


def _ok(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _error(exc: Exception) -> str:
    return _ok({"error": str(exc)})


def _current_user(user_repo: UserRepo) -> User:
    configurable = get_config().get("configurable") or {}
    raw_user_id = configurable.get("user")
    if raw_user_id is None:
        raise ValueError("missing configurable.user")
    row = user_repo.get(int(raw_user_id))
    if row is None or row.disabled:
        raise ValueError("current user not found")
    return User(
        id=row.id,
        username=row.username,
        role=row.role,
        display_name=row.display_name,
        locale=row.locale,
        permissions=row.permissions,
    )


def build_family_tools(
    db: DatabasePool,
    *,
    user_repo: UserRepo,
) -> list[StructuredTool]:
    """Build family tools backed by the current HomeMind control-plane database."""
    run_migrations(db)
    services = HomeMindServices.from_pool(db)
    families = FamilyManager(services.family_repo)
    context = FamilyContextManager(families, services.family_context_repo)
    assets = FamilyAssetManager(services.family_repo, services.family_asset_repo)
    albums = FamilyAlbumManager(families, assets, services.family_album_repo)
    photos = PhotoIntelligenceManager(
        families,
        assets,
        services.family_context_repo,
        services.photo_intelligence_repo,
    )
    search = FamilySearchManager(families, context, assets, photos)
    providers = ProviderRepo(db)
    tasks = FamilyTaskManager(families, services.family_task_repo)
    filesystem = FamilyFilesystemManager(
        families, services.family_asset_repo, services.family_transaction_repo
    )
    transactions = FamilyTransactionManager(
        families,
        context,
        tasks,
        services.family_transaction_repo,
        user_repo,
        filesystem,
    )
    devices = services.family_device_repo

    def family_list_members(family_id: str) -> str:
        try:
            user = _current_user(user_repo)
            families.require_access(family_id, user)
            return _ok([asdict(row) for row in families.repo.list_members(family_id)])
        except Exception as exc:
            return _error(exc)

    def family_get_member(family_id: str, member_id: str) -> str:
        try:
            user = _current_user(user_repo)
            families.require_access(family_id, user)
            row = families.repo.get_member(member_id)
            if row is None or row.family_id != family_id:
                raise ValueError("family member not found")
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_search_memory(family_id: str, query: str = "") -> str:
        try:
            rows = context.search_memories(
                family_id, _current_user(user_repo), query or None
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def family_create_memory(
        family_id: str,
        content: str,
        memory_type: str,
        subject_type: str = "FAMILY",
        subject_id: str | None = None,
    ) -> str:
        try:
            user = _current_user(user_repo)
            transaction, approval = transactions.plan(
                family_id,
                user,
                action="memory.create",
                payload={
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "content": content,
                    "memory_type": memory_type,
                    "importance": 0.5,
                    "confidence": 0.5,
                    "visibility": "FAMILY",
                    "source_type": "AGENT",
                    "source_id": None,
                    "expires_at": None,
                },
            )
            return _transaction_result(transaction, approval)
        except Exception as exc:
            return _error(exc)

    def family_search_assets(
        family_id: str,
        query: str = "",
        asset_type: str | None = None,
    ) -> str:
        try:
            rows = assets.search(
                family_id,
                _current_user(user_repo),
                query=query or None,
                asset_type=asset_type,
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def family_get_asset(family_id: str, asset_id: str) -> str:
        try:
            row = assets.get(family_id, asset_id, _current_user(user_repo))
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_list_events(family_id: str, query: str = "") -> str:
        try:
            rows = context.list_events(family_id, _current_user(user_repo))
            if query:
                normalized = query.casefold()
                rows = [
                    row
                    for row in rows
                    if normalized in row.title.casefold()
                    or normalized in (row.location or "").casefold()
                    or normalized in row.description.casefold()
                ]
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def family_create_event(
        family_id: str,
        title: str,
        event_type: str,
        start_at: int,
        end_at: int,
        location: str | None = None,
    ) -> str:
        try:
            user = _current_user(user_repo)
            transaction, approval = transactions.plan(
                family_id,
                user,
                action="event.create",
                payload={
                    "title": title,
                    "event_type": event_type,
                    "start_at": start_at,
                    "end_at": end_at,
                    "location": location,
                    "description": "",
                    "metadata": {},
                },
            )
            return _transaction_result(transaction, approval)
        except Exception as exc:
            return _error(exc)

    def family_list_tasks(family_id: str, status: str | None = None) -> str:
        try:
            parsed = TaskStatus(status) if status else None
            rows = tasks.list(
                family_id,
                _current_user(user_repo),
                status=parsed,
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def family_create_task(
        family_id: str,
        title: Annotated[str, Field(min_length=1, max_length=200)],
        description: str = "",
        assigned_member_id: str | None = None,
        due_at: int | None = None,
    ) -> str:
        try:
            user = _current_user(user_repo)
            transaction, approval = transactions.plan(
                family_id,
                user,
                action="task.create",
                payload={
                    "title": title,
                    "description": description,
                    "assigned_member_id": assigned_member_id,
                    "due_at": due_at,
                },
            )
            return _transaction_result(transaction, approval)
        except Exception as exc:
            return _error(exc)

    def family_get_devices(family_id: str) -> str:
        try:
            user = _current_user(user_repo)
            families.require_access(family_id, user)
            return _ok([asdict(row) for row in devices.list(family_id)])
        except Exception as exc:
            return _error(exc)

    def family_get_device(family_id: str, device_id: str) -> str:
        try:
            user = _current_user(user_repo)
            families.require_access(family_id, user)
            row = devices.get(device_id)
            if row is None or row.family_id != family_id:
                raise ValueError("family device not found")
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_search(
        family_id: str,
        query: str,
        kinds: list[str] | None = None,
        asset_type: str | None = None,
        limit: int = 50,
        embedding_provider_id: int | None = None,
        embedding_model: str | None = None,
    ) -> str:
        try:
            selected = {SearchKind(value) for value in kinds} if kinds else None
            embedding = None
            if embedding_provider_id is not None:
                if not embedding_model:
                    raise ValueError(
                        "embedding_model is required with embedding_provider_id"
                    )
                provider = require_provider(providers.get(embedding_provider_id))
                embedding = OpenAICompatibleEmbeddingProvider(
                    provider, embedding_model
                )
            rows = search.search(
                family_id,
                _current_user(user_repo),
                query=query,
                kinds=selected,
                asset_type=asset_type,
                limit=limit,
                embedding=embedding,
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def filesystem_list(family_id: str, source_id: str, path: str = ".") -> str:
        try:
            rows = filesystem.list(
                family_id, _current_user(user_repo), source_id=source_id, path=path
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def filesystem_search(
        family_id: str,
        source_id: str,
        query: str,
        path: str = ".",
        limit: int = 100,
    ) -> str:
        try:
            rows = filesystem.search(
                family_id,
                _current_user(user_repo),
                source_id=source_id,
                query=query,
                path=path,
                limit=limit,
            )
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def filesystem_read(
        family_id: str,
        source_id: str,
        path: str,
        max_bytes: int = 1024 * 1024,
    ) -> str:
        try:
            content = filesystem.read(
                family_id,
                _current_user(user_repo),
                source_id=source_id,
                path=path,
                max_bytes=max_bytes,
            )
            return _ok({"path": path, "content": content})
        except Exception as exc:
            return _error(exc)

    def filesystem_mutation(
        action: str,
        family_id: str,
        source_id: str,
        path: str,
        destination: str | None = None,
    ) -> str:
        try:
            payload: dict[str, Any] = {"source_id": source_id, "path": path}
            if destination is not None:
                payload["destination"] = destination
            transaction, approval = transactions.plan(
                family_id, _current_user(user_repo), action=action, payload=payload
            )
            return _transaction_result(transaction, approval)
        except Exception as exc:
            return _error(exc)

    def filesystem_copy(
        family_id: str, source_id: str, path: str, destination: str
    ) -> str:
        return filesystem_mutation(
            "filesystem.copy", family_id, source_id, path, destination
        )

    def filesystem_move(
        family_id: str, source_id: str, path: str, destination: str
    ) -> str:
        return filesystem_mutation(
            "filesystem.move", family_id, source_id, path, destination
        )

    def filesystem_rename(
        family_id: str, source_id: str, path: str, destination: str
    ) -> str:
        return filesystem_mutation(
            "filesystem.rename", family_id, source_id, path, destination
        )

    def filesystem_delete(family_id: str, source_id: str, path: str) -> str:
        return filesystem_mutation("filesystem.delete", family_id, source_id, path)

    def family_list_albums(family_id: str) -> str:
        try:
            rows = albums.list(family_id, _current_user(user_repo))
            return _ok([asdict(row) for row in rows])
        except Exception as exc:
            return _error(exc)

    def family_create_album(
        family_id: str, name: str, description: str = ""
    ) -> str:
        try:
            row = albums.create(
                family_id,
                _current_user(user_repo),
                name=name,
                description=description,
            )
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_add_album_asset(
        family_id: str, album_id: str, asset_id: str
    ) -> str:
        try:
            albums.add_asset(
                family_id, album_id, asset_id, _current_user(user_repo)
            )
            return _ok({"album_id": album_id, "asset_id": asset_id, "added": True})
        except Exception as exc:
            return _error(exc)

    def family_plan_photo_organization(family_id: str, strategy: str) -> str:
        try:
            row = albums.plan(
                family_id,
                _current_user(user_repo),
                OrganizationStrategy(strategy),
            )
            value = asdict(row)
            value["groups"] = json.loads(row.groups_json)
            del value["groups_json"]
            return _ok(value)
        except Exception as exc:
            return _error(exc)

    def family_apply_photo_organization(family_id: str, plan_id: str) -> str:
        try:
            row = albums.apply_plan(
                family_id, plan_id, _current_user(user_repo)
            )
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_analyze_photo_local(family_id: str, asset_id: str) -> str:
        try:
            row = photos.analyze(
                family_id, asset_id, _current_user(user_repo)
            )
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_analyze_photo(
        family_id: str,
        asset_id: str,
        vision_provider_id: int,
        vision_model: str,
        embedding_provider_id: int | None = None,
        embedding_model: str | None = None,
    ) -> str:
        try:
            vision_row = require_provider(providers.get(vision_provider_id))
            vision = OpenAICompatibleVisionProvider(vision_row, vision_model)
            embedding = None
            if embedding_provider_id is not None:
                if not embedding_model:
                    raise ValueError(
                        "embedding_model is required with embedding_provider_id"
                    )
                embedding_row = require_provider(
                    providers.get(embedding_provider_id)
                )
                embedding = OpenAICompatibleEmbeddingProvider(
                    embedding_row, embedding_model
                )
            row = photos.analyze(
                family_id,
                asset_id,
                _current_user(user_repo),
                vision=vision,
                embedding=embedding,
            )
            return _ok(asdict(row))
        except Exception as exc:
            return _error(exc)

    def family_find_similar_photos(
        family_id: str, asset_id: str, max_distance: int = 8
    ) -> str:
        try:
            rows = photos.similar(
                family_id,
                asset_id,
                _current_user(user_repo),
                max_distance=max_distance,
            )
            return _ok(
                [
                    {"asset_id": item_id, "hamming_distance": distance}
                    for item_id, distance in rows
                ]
            )
        except Exception as exc:
            return _error(exc)

    specs: list[tuple[str, Callable[..., str], str]] = [
        ("family.list_members", family_list_members, "List members of a family."),
        ("family.get_member", family_get_member, "Get one family member."),
        ("family.search_memory", family_search_memory, "Search durable family memories."),
        ("family.create_memory", family_create_memory, "Create a durable family memory."),
        ("family.search_assets", family_search_assets, "Search indexed family assets."),
        ("family.get_asset", family_get_asset, "Get one indexed family asset."),
        ("family.list_events", family_list_events, "List or search family events."),
        ("family.create_event", family_create_event, "Create a family event."),
        ("family.list_tasks", family_list_tasks, "List family tasks."),
        ("family.create_task", family_create_task, "Create a family task."),
        ("family.get_devices", family_get_devices, "List registered family devices."),
        ("family.get_device", family_get_device, "Get one registered family device."),
        (
            "family.search",
            family_search,
            "Search family members, events, memories, and indexed assets.",
        ),
        ("filesystem.list", filesystem_list, "List a registered family source safely."),
        ("filesystem.search", filesystem_search, "Search a registered family source safely."),
        ("filesystem.read", filesystem_read, "Read a bounded UTF-8 file from a registered family source."),
        ("filesystem.copy", filesystem_copy, "Copy a file after permission evaluation."),
        ("filesystem.move", filesystem_move, "Move a file after permission evaluation."),
        ("filesystem.rename", filesystem_rename, "Rename a file after permission evaluation."),
        ("filesystem.delete", filesystem_delete, "Move a file to recoverable HomeMind trash after approval."),
        ("family.list_albums", family_list_albums, "List family photo albums."),
        ("family.create_album", family_create_album, "Create a family photo album."),
        ("family.add_album_asset", family_add_album_asset, "Add an indexed asset to an album."),
        (
            "family.plan_photo_organization",
            family_plan_photo_organization,
            "Preview time-based or exact-duplicate photo organization.",
        ),
        (
            "family.apply_photo_organization",
            family_apply_photo_organization,
            "Apply a saved organization plan as non-destructive album links.",
        ),
        (
            "family.analyze_photo_local",
            family_analyze_photo_local,
            "Compute local perceptual similarity metadata for a photo.",
        ),
        (
            "family.analyze_photo",
            family_analyze_photo,
            "Describe and embed a photo with configured Octop providers.",
        ),
        (
            "family.find_similar_photos",
            family_find_similar_photos,
            "Find photos with a nearby perceptual hash.",
        ),
    ]
    return [
        StructuredTool.from_function(func=func, name=name, description=description)
        for name, func, description in specs
    ]


def _transaction_result(transaction: Any, approval: Any | None) -> str:
    result: dict[str, Any] = {
        "transaction_id": transaction.id,
        "status": transaction.status,
    }
    if transaction.result_json:
        result["result"] = json.loads(transaction.result_json)
    if transaction.error:
        result["error"] = transaction.error
    if approval is not None:
        result["approval_id"] = approval.id
    return _ok(result)
