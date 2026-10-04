from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.search import FamilySearchManager, SearchKind
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def test_unified_search_resolves_last_year_and_kind_filters(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    user = User(1, "owner", Role.USER, "Owner")
    families = FamilyManager(services.family_repo)
    context = FamilyContextManager(families, services.family_context_repo)
    search = FamilySearchManager(
        families,
        context,
        FamilyAssetManager(services.family_repo, services.family_asset_repo),
    )
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    year = datetime.now(ZoneInfo("Asia/Shanghai")).year - 1
    start_at = int(datetime(year, 4, 1, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    event = context.create_event(
        family.id,
        user,
        event_type="TRIP",
        title="日本旅行",
        start_at=start_at,
        end_at=start_at + 86400,
        location="京都",
        description="春季旅行",
        metadata={},
    )
    context.create_memory(
        family.id,
        user,
        subject_type="EVENT",
        subject_id=event.id,
        content="我们去过京都",
        memory_type=MemoryType.EXPERIENCE,
        importance=0.8,
        confidence=0.9,
        visibility="FAMILY",
        source_type="USER",
        source_id=None,
        expires_at=None,
    )

    results = search.search(
        family.id,
        user,
        query="去年我们去了哪里？",
        kinds={SearchKind.EVENT},
    )
    kyoto = search.search(family.id, user, query="京都")

    assert [(row.kind, row.id) for row in results] == [(SearchKind.EVENT, event.id)]
    assert {row.kind for row in kyoto} == {SearchKind.EVENT, SearchKind.MEMORY}
