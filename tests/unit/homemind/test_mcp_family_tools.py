"""Stage 11: unit tests for the umbrella MCP family tools."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.tools.mcp_family_tools import (
    ContextResolveInput,
    FamilyQueryInput,
    McpFamilyToolkit,
    MemoryCommitInput,
    TransactionPlanInput,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import User


def _bootstrap(tmp_path: Path) -> tuple[SqlitePool, HomeMindServices, FamilyManager, User, str]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    user_repo = UserRepo(pool)
    user = User(id=1, username="owner", role="user", display_name="Owner", locale="zh")
    family_manager = FamilyManager(services.family_repo)
    family = family_manager.create_family(
        user, name="MCP Family", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, services, family_manager, user, family.id


def _make_toolkit(pool: SqlitePool) -> McpFamilyToolkit:
    """Build a toolkit with the resolver patched to the seeded owner.

    The four umbrella tools all call ``_resolve_user(repo)``; in an
    agent run this comes from the ``configurable.user`` LangGraph
    config, but for unit tests we short-circuit it to the seeded
    owner so the bootstrap permission checks pass.
    """
    import homemind.tools.mcp_family_tools as mod

    user = User(id=1, username="owner", role="user", display_name="Owner", locale="zh")
    mod._resolve_user = lambda _repo: user  # type: ignore[assignment]
    return McpFamilyToolkit(pool)


def test_family_query_members_returns_list(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    toolkit = _make_toolkit(pool)

    result = toolkit.family_query(FamilyQueryInput(family_id=family_id, target="members"))

    assert result["tool"] == "family_query"
    assert result["target"] == "members"
    assert result["count"] >= 1  # owner is auto-mapped
    assert isinstance(result["items"], list)
    pool.close()


def test_family_query_events_uses_context_manager(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    services.family_context_repo.create_event(
        family_id,
        title="周末聚餐",
        event_type="MEAL",
        start_at=1,
        end_at=2,
        created_by=user.id,
    )
    toolkit = _make_toolkit(pool)

    result = toolkit.family_query(FamilyQueryInput(family_id=family_id, target="events"))

    assert result["tool"] == "family_query"
    assert result["target"] == "events"
    assert result["count"] == 1
    pool.close()


def test_context_resolve_returns_structured_payload(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    services.family_context_repo.create_event(
        family_id,
        title="京都旅行",
        event_type="TRIP",
        start_at=1,
        end_at=2,
        location="京都",
        created_by=user.id,
    )
    services.family_context_repo.create_memory(
        family_id=family_id,
        subject_type="EVENT",
        subject_id=None,
        content="我们去过京都",
        memory_type="EXPERIENCE",
        source_type="USER",
        created_by=user.id,
    )
    toolkit = _make_toolkit(pool)

    result = toolkit.context_resolve(
        ContextResolveInput(family_id=family_id, query="日本旅行去了哪里"),
    )

    assert result["tool"] == "context_resolve"
    assert result["query"] == "日本旅行去了哪里"
    assert len(result["event_ids"]) == 1
    assert len(result["memory_ids"]) == 1
    pool.close()


def test_memory_commit_round_trip(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    toolkit = _make_toolkit(pool)

    result = toolkit.memory_commit(
        MemoryCommitInput(
            family_id=family_id,
            content="外婆的拿手菜是红烧肉",
            memory_type="EXPERIENCE",
            subject_type="MEMBER",
        ),
    )

    assert result["tool"] == "memory_commit"
    assert result["status"] == "APPROVED"
    assert "candidate_id" in result
    pool.close()


def test_memory_commit_flags_sensitive_content(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    toolkit = _make_toolkit(pool)

    result = toolkit.memory_commit(
        MemoryCommitInput(
            family_id=family_id,
            content="我的身份证号码是 110101199001011234",
            memory_type="FACT",
        ),
    )

    assert result["tool"] == "memory_commit"
    assert result["status"] == "PENDING"
    assert "sensitive" in result["reason"]
    pool.close()


def test_transaction_plan_records_pending_state(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    toolkit = _make_toolkit(pool)

    result = toolkit.transaction_plan(
        TransactionPlanInput(
            family_id=family_id,
            action="task.create",
            payload={"title": "买菜", "description": "周末前完成"},
            idempotency_key="plan-1",
        ),
    )

    assert result["tool"] == "transaction_plan"
    # The default handler is to wait for manager approval, so the
    # transaction should land in WAITING_APPROVAL. Status must NOT be
    # FAILED on a well-formed payload.
    assert result["status"] != "FAILED"
    assert "transaction_id" in result
    pool.close()


def test_transaction_plan_idempotent_replay_returns_same_id(tmp_path: Path) -> None:
    pool, services, family_manager, user, family_id = _bootstrap(tmp_path)
    toolkit = _make_toolkit(pool)

    first = toolkit.transaction_plan(
        TransactionPlanInput(
            family_id=family_id,
            action="task.create",
            payload={"title": "买菜"},
            idempotency_key="plan-replay",
        ),
    )
    second = toolkit.transaction_plan(
        TransactionPlanInput(
            family_id=family_id,
            action="task.create",
            payload={"title": "买菜"},
            idempotency_key="plan-replay",
        ),
    )

    assert first["transaction_id"] == second["transaction_id"]
    pool.close()


def test_wire_returns_four_structured_tools(tmp_path: Path) -> None:
    from homemind.tools.mcp_family_tools import wire_mcp_family_tools

    pool = SqlitePool(tmp_path / "wire.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    tools = wire_mcp_family_tools(pool)
    names = {tool.name for tool in tools}
    assert names == {"family_query", "context_resolve", "memory_commit", "transaction_plan"}
    # Every tool must have an explicit args_schema so MCP JSON-RPC
    # clients can introspect the input model.
    for tool in tools:
        assert tool.args_schema is not None
    pool.close()


def test_build_family_tools_appends_mcp_tools(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "wired.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    user_repo = UserRepo(pool)
    from homemind.tools.family import build_family_tools
    tools = build_family_tools(pool, user_repo=user_repo)
    names = {tool.name for tool in tools}
    assert {
        "family_query", "context_resolve", "memory_commit", "transaction_plan",
    } <= names
    pool.close()
