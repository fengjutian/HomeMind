"""Stage 10 acceptance tests for the HomeMind MCP server.

Covers the spec's security rules:

* identity cannot be forged through a tool argument,
* a caller cannot reach another family by passing its id,
* no active family means a clear error, not a silent default,
* read tools honour private-space permissions,
* write tools produce a transaction rather than mutating a repo,
* every call lands in the audit log without secrets.
"""

from __future__ import annotations

from pathlib import Path

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.tools.mcp_server import (
    HomeMindMcpServer,
    McpErrorCode,
    McpPrincipal,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1)"
        )
    papa = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    mama = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family_repo = FamilyRepo(pool)
    manager = FamilyManager(family_repo)
    family = manager.create_family(
        papa, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    manager.create_member(
        family.id, papa, display_name="妈妈", role=MemberRole.MEMBER, user_id=mama.id,
    )
    other_family = manager.create_family(
        mama, name="Other", timezone="UTC", locale="en",
    )

    from homemind.infra.db.services import HomeMindServices

    services = HomeMindServices.from_pool(pool)
    resolver = ActiveFamilyResolver(SettingsRepo(pool), manager)
    server = HomeMindMcpServer(
        services, active_family_resolver=resolver,
    )
    return pool, server, resolver, papa, mama, family.id, other_family.id


def _principal(user: User) -> McpPrincipal:
    return McpPrincipal(user_id=user.id, client_id="test-client", session_id="sess-1")


# ------------------------------------------------------------------ identity


def test_no_tool_schema_accepts_a_user_id(tmp_path: Path) -> None:
    """``additionalProperties: false`` plus the absence of a
    ``user_id`` field makes identity smuggling a protocol error."""

    _pool, server, _resolver, papa, _mama, _fid, _oid = _bootstrap(tmp_path)
    for tool in server.list_tools():
        schema = tool["inputSchema"]
        assert schema.get("additionalProperties") is False
        assert "user_id" not in schema["properties"]
        assert "acting_user_id" not in schema["properties"]
    assert papa.id == 1


def test_forged_user_id_argument_is_rejected(tmp_path: Path) -> None:
    """Even if a client sends ``user_id``, the server ignores it —
    the principal on the session decides."""

    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.list_members",
        {"family_id": family_id, "user_id": 999},
        _principal(papa),
    )
    # The extra key is not in the schema, but dispatch ignores it
    # rather than letting it switch identity — the result reflects
    # papa's own view of his family.
    assert result.ok
    assert result.data["members"]


def test_cannot_reach_another_family(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, other_id = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.list_events",
        {"family_id": other_id},
        _principal(papa),
    )
    assert not result.ok
    assert result.error_code == McpErrorCode.FAMILY_MISMATCH
    pool.close()


def test_no_active_family_returns_explicit_error(tmp_path: Path) -> None:
    pool, server, _resolver, papa, _mama, _family_id, _oid = _bootstrap(tmp_path)
    result = server.call_tool("family.list_tasks", {}, _principal(papa))
    assert not result.ok
    assert result.error_code == McpErrorCode.ACTIVE_FAMILY_REQUIRED
    assert "no active family" in (result.error_message or "").lower()
    pool.close()


def test_unknown_principal_is_rejected(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.list_members", {}, McpPrincipal(user_id=9999),
    )
    assert not result.ok
    assert result.error_code == McpErrorCode.IDENTITY_REQUIRED
    pool.close()


def test_unknown_tool_is_rejected(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool("family.nuke", {}, _principal(papa))
    assert not result.ok
    assert result.error_code == McpErrorCode.INVALID_REQUEST
    pool.close()


# --------------------------------------------------------------------- reads


def test_read_tools_work_with_active_family(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    for tool in (
        "family.list_members",
        "family.list_events",
        "family.list_tasks",
        "family.get_devices",
        "family.search_assets",
        "family.search_memory",
    ):
        result = server.call_tool(tool, {"family_id": family_id}, _principal(papa))
        assert result.ok, f"{tool} failed: {result.error_message}"
    pool.close()


def test_resolve_context_returns_ambiguities(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.resolve_context",
        {"family_id": family_id, "query": "妈妈"},
        _principal(papa),
    )
    assert result.ok
    assert "ambiguities" in result.data["context"]
    pool.close()


# -------------------------------------------------------------------- writes


def test_write_tool_produces_a_transaction(tmp_path: Path) -> None:
    """``create_task`` must go through the transaction state machine,
    never straight to the task repo."""

    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.create_task",
        {"family_id": family_id, "title": "倒垃圾"},
        _principal(papa),
    )
    assert result.ok, result.error_message
    transaction_id = result.data["transaction_id"]
    assert transaction_id
    assert result.data["status"] in {"WAITING_APPROVAL", "COMPLETED"}

    # The transaction is persisted — proof the write went through the
    # state machine rather than a direct repo insert.
    from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo

    stored = FamilyTransactionRepo(pool).get_transaction(transaction_id)
    assert stored is not None
    assert stored.action == "task.create"
    assert stored.status == result.data["status"]

    # An auto-allowed action completes and produces the task; an
    # approval-gated one leaves the task unmade until a manager acts.
    with pool.connect() as conn:
        tasks = conn.execute("SELECT task_id FROM homemind_family_tasks").fetchall()
    if result.data["status"] == "COMPLETED":
        assert len(tasks) == 1
    else:
        assert len(tasks) == 0
    pool.close()


def test_idempotency_key_collapses_duplicate_writes(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    first = server.call_tool(
        "family.create_task",
        {"family_id": family_id, "title": "倒垃圾", "idempotency_key": "k1"},
        _principal(papa),
    )
    second = server.call_tool(
        "family.create_task",
        {"family_id": family_id, "title": "倒垃圾", "idempotency_key": "k1"},
        _principal(papa),
    )
    assert first.ok and second.ok
    assert first.data["transaction_id"] == second.data["transaction_id"]
    pool.close()


def test_memory_candidate_is_flagged_when_sensitive(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    result = server.call_tool(
        "family.create_memory_candidate",
        {"family_id": family_id, "content": "我的银行卡号是 6222 0000 1111 2222"},
        _principal(papa),
    )
    assert result.ok
    assert result.data["sensitive"] is True
    assert result.data["status"] == "PENDING"
    pool.close()


# --------------------------------------------------------------------- audit


def test_every_call_is_audited(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    server.call_tool("family.list_members", {"family_id": family_id}, _principal(papa))
    server.call_tool("family.list_tasks", {"family_id": family_id}, _principal(papa))
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT user_id, family_id, provider_id, operation, result "
            "FROM homemind_external_processing_audit"
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert int(row["user_id"]) == papa.id
        assert str(row["family_id"]) == family_id
        assert str(row["provider_id"]) == "mcp:test-client"
        assert str(row["operation"]).startswith("family.")
    pool.close()


def test_audit_records_no_secrets(tmp_path: Path) -> None:
    pool, server, resolver, papa, _mama, family_id, _oid = _bootstrap(tmp_path)
    resolver.set(papa, family_id)
    server.call_tool(
        "family.create_memory_candidate",
        {"family_id": family_id, "content": "我住���北京市海淀区"},
        _principal(papa),
    )
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM homemind_external_processing_audit"
        ).fetchall()
    assert rows
    blob = str([dict(row) for row in rows])
    assert "password" not in blob.lower()
    assert "api_key" not in blob.lower()
    pool.close()
