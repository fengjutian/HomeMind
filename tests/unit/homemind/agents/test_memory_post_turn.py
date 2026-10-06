"""End-to-end test for the post-turn memory extractor pipeline."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from homemind.infra.agents.memory_post_turn import run_for_turn
from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.memory_candidates import MemoryCandidateRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User
from homemind.infra.agents._request_helpers import ResolvedTurnUser


def _bootstrap(tmp_path: Path):
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
    lifecycle = MemoryLifecycleManager(
        family_manager,
        context_manager,
        MemoryCandidateRepo(pool),
        pool if False else MemoryCandidateRepo(pool),
    )
    # Re-construct with the real EvidenceRepo too — the lifecycle
    # manager needs both repos to wire approve / merge correctly.
    from homemind.infra.db.repos.memory_candidates import MemoryEvidenceRepo

    lifecycle = MemoryLifecycleManager(
        family_manager,
        context_manager,
        MemoryCandidateRepo(pool),
        MemoryEvidenceRepo(pool),
    )
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    family_manager.create_member(
        family.id, owner, display_name="妈妈", role=MemberRole.MEMBER,
    )
    return pool, user_repo, family_manager, context_manager, lifecycle, owner, family.id


def test_post_turn_creates_pending_candidate(tmp_path: Path) -> None:
    pool, user_repo, family_manager, context_manager, lifecycle, owner, family_id = _bootstrap(tmp_path)
    messages = [
        SystemMessage(content="base"),
        HumanMessage(content="妈妈喜欢清淡饮食"),
        AIMessage(content="好的,我记下来了"),
    ]
    result = run_for_turn(
        user_repo=user_repo,
        family_manager=family_manager,
        context_repo=context_manager.repo,
        lifecycle=lifecycle,
        active_family_id=family_id,
        turn_user=ResolvedTurnUser(user_id=owner.id, thread_id="t1"),
        messages=messages,
    )
    assert result.candidates_created == 1
    rows = lifecycle.candidates.list_for_family(family_id, status="PENDING")
    assert len(rows) == 1
    assert "清淡饮食" in rows[0].content
    pool.close()


def test_post_turn_sensitive_text_does_not_create_candidate(tmp_path: Path) -> None:
    pool, user_repo, family_manager, context_manager, lifecycle, owner, family_id = _bootstrap(tmp_path)
    messages = [
        HumanMessage(content="我的身份证号是 110101199001011234"),
    ]
    result = run_for_turn(
        user_repo=user_repo,
        family_manager=family_manager,
        context_repo=context_manager.repo,
        lifecycle=lifecycle,
        active_family_id=family_id,
        turn_user=ResolvedTurnUser(user_id=owner.id, thread_id="t1"),
        messages=messages,
    )
    assert result.candidates_created == 0
    rows = lifecycle.candidates.list_for_family(family_id, status="PENDING")
    assert rows == []
    assert result.skipped_sensitive >= 1
    pool.close()


def test_post_turn_does_not_touch_messages(tmp_path: Path) -> None:
    """The orchestrator must not mutate the message list — the
    middleware relies on the messages being unchanged so the model
    still sees the original conversation."""

    pool, user_repo, family_manager, context_manager, lifecycle, owner, family_id = _bootstrap(tmp_path)
    messages = [
        HumanMessage(content="妈妈喜欢清淡饮食"),
    ]
    snapshot = list(messages)
    run_for_turn(
        user_repo=user_repo,
        family_manager=family_manager,
        context_repo=context_manager.repo,
        lifecycle=lifecycle,
        active_family_id=family_id,
        turn_user=ResolvedTurnUser(user_id=owner.id, thread_id="t1"),
        messages=messages,
    )
    assert messages == snapshot
    pool.close()


def test_post_turn_no_active_family_is_noop(tmp_path: Path) -> None:
    pool, user_repo, family_manager, context_manager, lifecycle, owner, _ = _bootstrap(tmp_path)
    messages = [HumanMessage(content="妈妈喜欢清淡饮食")]
    result = run_for_turn(
        user_repo=user_repo,
        family_manager=family_manager,
        context_repo=context_manager.repo,
        lifecycle=lifecycle,
        active_family_id=None,
        turn_user=ResolvedTurnUser(user_id=owner.id, thread_id="t1"),
        messages=messages,
    )
    assert result.candidates_created == 0
    pool.close()
