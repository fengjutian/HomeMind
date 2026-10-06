"""Unit tests for the family context middleware identity + payload wiring.

These tests exercise the middleware without spinning up a LangGraph
runtime. We monkey-patch ``get_config`` to inject a per-request
``configurable`` payload so the middleware sees a stable identity and
we can assert which fields end up in the rewritten system message.
"""

from __future__ import annotations

import asyncio
import homemind.infra.agents.family_context_middleware  # noqa: F401 — exposes the patch hook
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.agents.family_context_middleware import FamilyContextMiddleware
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole, RelationshipType
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


@dataclass
class _StubRequest:
    """Minimal stand-in for langchain's ``ModelRequest``."""

    system_message: SystemMessage | None = None
    messages: list[Any] = field(default_factory=list)

    def override(self, **kwargs: Any) -> "_StubRequest":
        new = _StubRequest(
            system_message=kwargs.get("system_message", self.system_message),
            messages=list(kwargs.get("messages", self.messages)),
        )
        return new


def _bootstrap(tmp_path: Path) -> tuple[
    SqlitePool, FamilyContextMiddleware, FamilyContextManager, FamilyManager, User, str,
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
    user_repo = UserRepo(pool)
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    context_repo = FamilyContextRepo(pool)
    context_manager = FamilyContextManager(family_manager, context_repo)
    permissions = FamilyPermissionEvaluator(family_repo)
    resolver = ActiveFamilyResolver(_SettingsRepo(pool), family_manager)
    middleware = FamilyContextMiddleware(
        user_repo=user_repo,
        active_family_resolver=resolver,
        family_manager=family_manager,
        context_manager=context_manager,
        permission_evaluator=permissions,
    )
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, middleware, context_manager, family_manager, owner, family.id


class _SettingsRepo:
    """Tiny in-memory stand-in so the middleware does not depend on the
    full control-plane ``SettingsRepo`` shape."""

    def __init__(self, pool: Any) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key) or None

    def set(self, key: str, value: str) -> None:
        self._store[key] = value


def _run_middleware(middleware: FamilyContextMiddleware, request: _StubRequest, configurable: dict[str, Any]) -> _StubRequest:
    """Drive ``awrap_model_call`` with a stubbed ``get_config``."""

    import homemind.infra.agents.family_context_middleware as mw_mod
    from langgraph.config import get_config as _real_get_config

    captured: dict[str, _StubRequest] = {}

    async def fake_handler(req: _StubRequest) -> _StubRequest:
        captured["req"] = req
        return req

    async def _drive() -> _StubRequest:
        with mw_mod._monkeypatch_config(configurable):  # type: ignore[attr-defined]
            return await middleware.awrap_model_call(request, fake_handler)

    return asyncio.run(_drive())


def test_middleware_injects_no_active_family_hint(tmp_path: Path) -> None:
    pool, middleware, _cm, _fm, owner, family_id = _bootstrap(tmp_path)
    request = _StubRequest(
        system_message=SystemMessage(content="base"),
        messages=[HumanMessage(content="我妈妈去年生日在哪过的")],
    )
    new_request = _run_middleware(middleware, request, {"user": owner.id, "thread_id": "t1"})
    assert new_request.system_message is not None
    body = new_request.system_message.content
    assert "<homemind_family_context" in body
    assert "<status>no_active_family</status>" in body
    pool.close()


def test_middleware_injects_active_family_block(tmp_path: Path) -> None:
    pool, middleware, context_manager, family_manager, owner, family_id = _bootstrap(tmp_path)

    # Create a child member + relationship + memory + event so the
    # resolved context has something to render.
    family_manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.MEMBER,
    )
    family_manager.create_member(
        family_id, owner, display_name="我", role=MemberRole.MEMBER,
    )
    members = family_manager.repo.list_members(family_id)
    me = next(m for m in members if m.display_name == "我")
    mom = next(m for m in members if m.display_name == "妈妈")
    family_manager.create_relationship(
        family_id, owner, from_member_id=me.id, to_member_id=mom.id,
        relationship_type=RelationshipType.PARENT,
    )
    context_manager.create_memory(
        family_id, owner,
        subject_type="MEMBER", subject_id=mom.id,
        content="妈妈喜欢清淡饮食",
        memory_type="PREFERENCE",
        source_type="USER",
    )

    # Set the active family on the in-memory settings repo.
    resolver = middleware._active_family  # type: ignore[attr-defined]
    resolver.set(owner, family_id)

    request = _StubRequest(
        system_message=SystemMessage(content="base"),
        messages=[HumanMessage(content="我妈妈的口味偏好是什么")],
    )
    new_request = _run_middleware(middleware, request, {"user": owner.id, "thread_id": "t1"})
    body = new_request.system_message.content
    assert "<homemind_family_context" in body
    assert "<name>F</name>" in body
    assert "妈妈" in body
    assert "<related_members>" in body
    pool.close()


def test_middleware_does_not_persist_injected_block_into_messages(tmp_path: Path) -> None:
    """The injected XML must live in the system message only — the
    user-message history must not contain the family block, otherwise
    we'd train on our own injected payload."""

    pool, middleware, _cm, _fm, owner, family_id = _bootstrap(tmp_path)
    request = _StubRequest(
        system_message=SystemMessage(content="base"),
        messages=[HumanMessage(content="hello"), AIMessage(content="hi back")],
    )
    new_request = _run_middleware(middleware, request, {"user": owner.id, "thread_id": "t1"})
    # No HumanMessage or AIMessage in the rewritten request may
    # contain the family block.
    for message in new_request.messages:
        content = getattr(message, "content", "")
        assert "<homemind_family_context" not in str(content)
    pool.close()


def test_middleware_ignores_request_without_authenticated_user(tmp_path: Path) -> None:
    pool, middleware, _cm, _fm, owner, family_id = _bootstrap(tmp_path)
    request = _StubRequest(
        system_message=SystemMessage(content="base"),
        messages=[HumanMessage(content="hello")],
    )
    new_request = _run_middleware(middleware, request, {})
    # Without a user, the middleware is a no-op and forwards the
    # original request untouched.
    assert new_request.system_message is request.system_message
    pool.close()


def test_middleware_uses_per_request_identity(tmp_path: Path) -> None:
    """Two concurrent turns with different ``configurable.user``
    values must not leak each other's active family — proves the
    middleware does not read a process global."""

    pool, middleware, context_manager, family_manager, owner, family_id = _bootstrap(tmp_path)
    # Add a second user with a different active family so we can
    # prove isolation.
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (2, 'other', 'x', 'user', 0, 'zh', 1)"
        )
    other = User(id=2, username="other", role=Role.USER, display_name="Other")
    other_family = family_manager.create_family(other, name="Other", timezone="UTC", locale="en")
    middleware._active_family.set(owner, family_id)  # type: ignore[attr-defined]
    middleware._active_family.set(other, other_family.id)  # type: ignore[attr-defined]

    request_a = _StubRequest(
        system_message=SystemMessage(content=""),
        messages=[HumanMessage(content="a")],
    )
    request_b = _StubRequest(
        system_message=SystemMessage(content=""),
        messages=[HumanMessage(content="b")],
    )
    out_a = _run_middleware(middleware, request_a, {"user": owner.id, "thread_id": "tA"})
    out_b = _run_middleware(middleware, request_b, {"user": other.id, "thread_id": "tB"})

    body_a = out_a.system_message.content
    body_b = out_b.system_message.content
    assert "<name>F</name>" in body_a
    assert "<name>F</name>" not in body_b
    assert "<name>Other</name>" in body_b
    assert "<name>Other</name>" not in body_a
    pool.close()


def _patch_config(configurable: dict[str, Any]):  # noqa: ANN201
    """Context manager that swaps ``_request_helpers.get_config`` for
    a stub returning ``{"configurable": configurable}``."""

    from contextlib import contextmanager

    @contextmanager
    def _ctx() -> Any:
        from homemind.infra.agents import _request_helpers as helpers

        original = helpers.get_config

        def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"configurable": configurable}

        helpers.get_config = _stub  # type: ignore[assignment]
        try:
            yield
        finally:
            helpers.get_config = original  # type: ignore[assignment]

    return _ctx()


# Expose the helper under the underscore-prefixed name the runtime expects.
homemind.infra.agents.family_context_middleware._monkeypatch_config = _patch_config  # type: ignore[attr-defined]
