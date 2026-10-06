"""PostgreSQL-gated integration tests for HomeMind memory FTS.

Run only when ``OCTOP_TEST_DATABASE_URL`` points at a live PostgreSQL
instance; otherwise the whole module is skipped so CI on SQLite-only
machines stays green.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.users.identity import Role, User

from tests.support.postgresql import requires_postgresql


pytestmark = [requires_postgresql, pytest.mark.asyncio]


def _build_pool() -> PostgresPool:
    url = os.environ["OCTOP_TEST_DATABASE_URL"]
    return PostgresPool(url)


@pytest.fixture
async def pg_pool():
    pool = _build_pool()
    run_migrations(pool)
    run_homemind_migrations(pool)
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
async def bootstrap(pg_pool: PostgresPool):
    pool = pg_pool
    from homemind.infra.db.repos.families import FamilyRepo

    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="FTS PG", timezone="Asia/Shanghai", locale="zh",
    )
    context_repo = FamilyContextRepo(pool)
    context_manager = FamilyContextManager(family_manager, context_repo)
    return pool, context_manager, family_manager, owner, family.id


@pytest.mark.asyncio
async def test_pg_fts_finds_english_text(bootstrap: tuple) -> None:
    pool, context_manager, _fm, owner, family_id = bootstrap
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="we visited Kyoto during the cherry blossom festival",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    rows = context_manager.search_memories_fts(family_id, owner, "cherry")
    assert len(rows) == 1
    assert "Kyoto" in rows[0].content


@pytest.mark.asyncio
async def test_pg_fts_finds_chinese_text(bootstrap: tuple) -> None:
    pool, context_manager, _fm, owner, family_id = bootstrap
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="妈妈去年生日在京都过的",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    rows = context_manager.search_memories_fts(family_id, owner, "京都")
    assert len(rows) == 1
    assert "京都" in rows[0].content


@pytest.mark.asyncio
async def test_pg_fts_excludes_other_family(bootstrap: tuple) -> None:
    pool, context_manager, family_manager, owner, family_id = bootstrap
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="京都旅行非常难忘",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    other = family_manager.create_family(
        owner, name="Other", timezone="Asia/Shanghai", locale="zh",
    )
    context_manager.create_memory(
        other.id, owner,
        subject_type="FAMILY",
        content="京都旅行非常难忘",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    rows = context_manager.search_memories_fts(family_id, owner, "京都")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_pg_fts_excludes_archived_and_expired(
    bootstrap: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archived memories and memories whose ``expires_at`` has passed
    must never show up in FTS results — same guarantee as the SQLite
    path."""

    pool, context_manager, _fm, owner, family_id = bootstrap

    # Pin "now" so expires_at comparisons are deterministic.
    import homemind.infra.db.repos.family_context as repo_mod

    class _FrozenNow(int):  # type: ignore[misc]
        pass

    now = _FrozenNow(1_700_000_000)
    monkeypatch.setattr(repo_mod, "now_ts", lambda: now)

    active = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="a unique phrase alphagamma",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    archived = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="another phrase alphagamma",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    expired = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="another phrase alphagamma expired",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    # Archive one row and expire another.
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE homemind_family_memories SET status = 'ARCHIVED' WHERE memory_id = ?",
            (archived.id,),
        )
        conn.execute(
            "UPDATE homemind_family_memories SET expires_at = ? WHERE memory_id = ?",
            (now - 1, expired.id),
        )

    rows = context_manager.search_memories_fts(family_id, owner, "alphagamma")
    ids = {row.id for row in rows}
    assert active.id in ids
    assert archived.id not in ids
    assert expired.id not in ids
