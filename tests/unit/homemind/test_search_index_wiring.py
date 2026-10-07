"""End-to-end proof that the unified search index is actually populated.

A unit test of ``FamilySearchIndexer`` proves the indexer works; it
does *not* prove anything calls it. These tests go through the real
write path — ``FamilyContextManager.create_event`` /
``create_memory`` and ``FamilyAssetManager.scan_source_internal`` — and
then query the index the way ``GET /search/indexed`` does.

Without these, Stage 7 would ship a search endpoint that always returns
an empty result set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.search_index import KIND_EVENT, KIND_MEMORY
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.search_index_manager import (
    FamilySearchManager,
    FamilySearchQuery,
)
from homemind.infra.family.search_indexer import FamilySearchIndexer
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    services = HomeMindServices.from_pool(pool)
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    family = family_manager.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    indexer = FamilySearchIndexer(
        services.search_index_repo,
        family_repo=family_repo,
        context_repo=services.family_context_repo,
        asset_repo=services.family_asset_repo,
        album_repo=services.family_album_repo,
        photo_repo=services.photo_intelligence_repo,
    )
    permissions = FamilyPermissionEvaluator(family_repo)
    context = FamilyContextManager(
        family_manager,
        services.family_context_repo,
        permission_evaluator=permissions,
        search_indexer=indexer,
    )
    assets = FamilyAssetManager(
        family_repo,
        services.family_asset_repo,
        permission_evaluator=permissions,
        search_indexer=indexer,
    )
    search = FamilySearchManager(
        family_manager,
        services.search_index_repo,
        permission_evaluator=permissions,
    )
    return pool, services, family_manager, context, assets, search, indexer, owner, family.id


def _seed_event(context: FamilyContextManager, family_id: str, owner: User, title: str) -> None:
    start = datetime(2025, 5, 1, tzinfo=UTC)
    context.create_event(
        family_id, owner,
        title=title,
        event_type="BIRTHDAY",
        start_at=int(start.timestamp()),
        end_at=int((start + timedelta(hours=3)).timestamp()),
        description="在清水寺庆祝",
    )


# ----------------------------------------------------------------- events


def test_created_event_becomes_searchable(tmp_path: Path) -> None:
    pool, _s, _fm, context, _a, search, _i, owner, family_id = _bootstrap(tmp_path)
    _seed_event(context, family_id, owner, "妈妈生日宴")

    page = search.search(
        family_id, owner, FamilySearchQuery(text="清水寺", kinds=frozenset({KIND_EVENT})),
    )
    assert [item.title for item in page.items] == ["妈妈生日宴"]
    pool.close()


def test_updated_event_refreshes_the_index(tmp_path: Path) -> None:
    pool, _s, _fm, context, _a, search, _i, owner, family_id = _bootstrap(tmp_path)
    _seed_event(context, family_id, owner, "妈妈生日宴")
    assert search.search(
        family_id, owner, FamilySearchQuery(text="清水寺"),
    ).items

    events = context.list_events(family_id, owner)
    context.update_event(
        family_id, events[0].id, owner,
        {"title": "妈妈生日宴", "description": "在伏见稻荷庆祝"},
    )
    # The old text must be gone, the new one present.
    assert search.search(
        family_id, owner, FamilySearchQuery(text="清水寺"),
    ).items == ()
    refreshed = search.search(
        family_id, owner, FamilySearchQuery(text="伏见稻荷"),
    )
    assert [item.title for item in refreshed.items] == ["妈妈生日宴"]
    pool.close()


def test_deleted_event_leaves_the_index(tmp_path: Path) -> None:
    pool, _s, _fm, context, _a, search, _i, owner, family_id = _bootstrap(tmp_path)
    _seed_event(context, family_id, owner, "妈妈生日宴")
    events = context.list_events(family_id, owner)
    context.delete_event(family_id, events[0].id, owner)
    assert search.search(
        family_id, owner, FamilySearchQuery(text="清水寺"),
    ).items == ()
    pool.close()


# ---------------------------------------------------------------- memories


def test_created_memory_becomes_searchable(tmp_path: Path) -> None:
    pool, _s, _fm, context, _a, search, _i, owner, family_id = _bootstrap(tmp_path)
    context.create_memory(
        family_id, owner,
        subject_type="FAMILY", subject_id=None,
        content="妈妈喜欢清淡饮食",
        memory_type="PREFERENCE", source_type="USER",
    )
    page = search.search(
        family_id, owner, FamilySearchQuery(text="清淡", kinds=frozenset({KIND_MEMORY})),
    )
    assert len(page.items) == 1
    assert "清淡" in page.items[0].snippet
    pool.close()


def test_deleted_memory_leaves_the_index(tmp_path: Path) -> None:
    pool, _s, _fm, context, _a, search, _i, owner, family_id = _bootstrap(tmp_path)
    memory = context.create_memory(
        family_id, owner,
        subject_type="FAMILY", subject_id=None,
        content="妈妈喜欢清淡饮食",
        memory_type="PREFERENCE", source_type="USER",
    )
    assert search.search(family_id, owner, FamilySearchQuery(text="清淡")).items
    context.delete_memory(family_id, memory.id, owner)
    assert search.search(family_id, owner, FamilySearchQuery(text="清淡")).items == ()
    pool.close()


# ------------------------------------------------------------------ assets


def test_scanned_asset_becomes_searchable(tmp_path: Path) -> None:
    """A scanned photo must reach the index, otherwise the Photos page
    and photo search have nothing to show."""

    pool, services, family_manager, _c, assets, search, _i, owner, family_id = _bootstrap(
        tmp_path
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "IMG_2841.jpg").write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    (photos / "holiday.jpg").write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")

    source = assets.repo.upsert_source(
        family_id=family_id,
        directory_uri=photos.resolve().as_uri(),
        recursive=False,
        visibility="FAMILY",
        space_id=None,
        created_by=owner.id,
    )
    assets.scan_source_internal(
        family_id, source.id, created_by_user_id=owner.id,
    )

    # The file name is searchable without the extension token, which
    # exercises the LIKE fallback for short queries.
    page = search.search(
        family_id, owner, FamilySearchQuery(text="IMG_2841"),
    )
    assert page.items, "a scanned asset must reach the search index"
    # The index stores the file name in ``title`` for ASSET rows, so
    # match on any indexed field rather than assuming the shape.
    assert any("IMG_2841" in item.snippet or "IMG_2841" in item.title
               for item in page.items), [i.title for i in page.items]
    pool.close()


# ------------------------------------------------------------- resilience


def test_index_failure_does_not_fail_the_write(tmp_path: Path) -> None:
    """A broken indexer must not roll back a write the user already
    sees as successful."""

    pool, _s, _fm, context, _a, _search, _i, owner, family_id = _bootstrap(tmp_path)

    class _Exploding:
        def index_event(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("index is down")

        def remove_event(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("index is down")

    context.search_indexer = _Exploding()
    _seed_event(context, family_id, owner, "妈妈生日宴")
    # The event itself is still there.
    assert context.list_events(family_id, owner)
    pool.close()
