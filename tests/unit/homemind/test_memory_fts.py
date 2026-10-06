"""Stage 8: unit tests for FTS5-backed memory search."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path) -> tuple[
    SqlitePool, FamilyContextManager, FamilyContextRepo, FamilyManager, User, str,
]:
    from homemind.infra.db.repos.families import FamilyRepo

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="FTS Family", timezone="Asia/Shanghai", locale="zh",
    )
    context_repo = FamilyContextRepo(pool)
    context_manager = FamilyContextManager(family_manager, context_repo)
    return pool, context_manager, context_repo, family_manager, owner, family.id


def test_fts_table_populated_by_trigger(tmp_path: Path) -> None:
    pool, context_manager, _repo, family_manager, owner, family_id = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="京都旅行非常难忘",
        memory_type="EXPERIENCE",
        source_type="USER",
    )

    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT memory_id, content FROM homemind_family_memory_fts"
        ).fetchall()
    assert len(rows) == 1
    assert "京都旅行非常难忘" in str(rows[0]["content"])
    pool.close()


def test_search_memories_fts_finds_tokenized_match(tmp_path: Path) -> None:
    pool, context_manager, _repo, _family_manager, owner, family_id = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="外婆的拿手菜是红烧肉",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="上周末去了公园",
        memory_type="EVENT",
        source_type="USER",
    )

    rows = context_manager.search_memories_fts(
        family_id, owner, "红烧肉",
    )
    assert len(rows) == 1
    assert "红烧肉" in rows[0].content
    pool.close()


def test_search_memories_fts_excludes_other_family(tmp_path: Path) -> None:
    pool, context_manager, _repo, family_manager, owner, family_id = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="京都旅行非常难忘",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    other_family = family_manager.create_family(
        owner, name="Other", timezone="Asia/Shanghai", locale="zh",
    )
    context_manager.create_memory(
        other_family.id, owner,
        subject_type="FAMILY",
        content="京都旅行非常难忘",
        memory_type="EXPERIENCE",
        source_type="USER",
    )

    rows = context_manager.search_memories_fts(
        family_id, owner, "京都",
    )
    assert len(rows) == 1
    pool.close()


def test_search_memories_fts_falls_back_when_fts_unavailable(
    tmp_path: Path,
) -> None:
    """When the FTS virtual table doesn't exist, the manager falls
    back to the LIKE-based ``search_memories`` so the API surface
    keeps working on PostgreSQL."""
    pool, context_manager, _repo, _family_manager, owner, family_id = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="野餐在公园",
        memory_type="EXPERIENCE",
        source_type="USER",
    )
    # Drop the FTS table to simulate PostgreSQL.
    with pool.connect() as conn:
        conn.execute("DROP TABLE homemind_family_memory_fts")

    rows = context_manager.search_memories_fts(
        family_id, owner, "野餐",
    )
    assert len(rows) == 1
    assert "野餐" in rows[0].content
    pool.close()


def test_update_triggers_fts_refresh(tmp_path: Path) -> None:
    pool, context_manager, _repo, _family_manager, owner, family_id = _bootstrap(tmp_path)
    memory = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="old content",
        memory_type="FACT",
        source_type="USER",
    )
    context_manager.update_memory(
        family_id, memory.id, owner,
        {"content": "new content"},
    )

    # Old token should not be searchable.
    rows = context_manager.search_memories_fts(
        family_id, owner, "old",
    )
    assert rows == []
    rows = context_manager.search_memories_fts(
        family_id, owner, "new",
    )
    assert len(rows) == 1
    pool.close()


def test_delete_removes_from_fts(tmp_path: Path) -> None:
    pool, context_manager, _repo, _family_manager, owner, family_id = _bootstrap(tmp_path)
    memory = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="unique phrase xyz",
        memory_type="FACT",
        source_type="USER",
    )
    context_manager.delete_memory(family_id, memory.id, owner)

    rows = context_manager.search_memories_fts(
        family_id, owner, "xyz",
    )
    assert rows == []
    pool.close()
