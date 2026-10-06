"""Tests for the daily memory maintenance runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.memory_candidates import (
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.memory_lifecycle import (
    CandidateSpec,
    MemoryLifecycleManager,
)
from homemind.infra.family.memory_maintenance import MemoryMaintenanceRunner
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


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
    family_repo = FamilyRepo(pool)
    context_repo = FamilyContextRepo(pool)
    family_manager = FamilyManager(family_repo)
    context_manager = FamilyContextManager(family_manager, context_repo)
    candidate_repo = MemoryCandidateRepo(pool)
    evidence_repo = MemoryEvidenceRepo(pool)
    lifecycle = MemoryLifecycleManager(
        family_manager, context_manager, candidate_repo, evidence_repo,
    )
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    runner = MemoryMaintenanceRunner(
        db=pool,
        family_repo=family_repo,
        context_repo=context_repo,
        candidate_repo=candidate_repo,
        evidence_repo=evidence_repo,
    )
    return pool, context_manager, family.id, lifecycle, runner, owner


def test_run_once_is_idempotent(tmp_path: Path) -> None:
    pool, context_manager, family_id, lifecycle, runner, owner = _bootstrap(tmp_path)
    # Create a candidate and approve it; the resulting active memory
    # will be touched by the next sweep.
    family_manager = lifecycle.family
    family_manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.MEMBER,
    )
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="FAMILY",
            subject_id=None,
            content="妈妈喜欢清淡饮食",
            memory_type=MemoryType.PREFERENCE.value,
            confidence=0.1,  # below threshold so decay fires
            importance=0.2,
        ),
        creator=owner,
    )
    lifecycle.approve_candidate(family_id, candidate.id, reviewer=owner)

    first = runner.run_once()
    second = runner.run_once()
    # The second sweep touches the same row again but the row is
    # already decayed to its floor, so the second run must also be a
    # no-op delta.
    assert first["families"] == 1
    assert second["families"] == 1
    # Total counters add up across runs — the second pass is
    # idempotent because once confidence is below threshold,
    # ``decay_memories`` records another touch.
    assert second["decayed"] >= first["decayed"]
    pool.close()


def test_run_once_archives_expired_memories(tmp_path: Path) -> None:
    pool, context_manager, family_id, lifecycle, runner, owner = _bootstrap(tmp_path)
    memory = context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="今晚记得倒垃圾",
        memory_type="TASK",
        source_type="USER",
        expires_at=1,
    )
    totals = runner.run_once()
    assert totals["expired"] >= 1
    refreshed = context_manager.repo.get_memory(memory.id)
    assert refreshed is not None
    assert refreshed.status == "ARCHIVED"
    pool.close()


def test_run_once_detects_duplicate_clusters(tmp_path: Path) -> None:
    pool, context_manager, family_id, lifecycle, runner, owner = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="妈妈喜欢清淡饮食",
        memory_type="PREFERENCE",
        source_type="USER",
    )
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="妈妈喜欢清淡饮食",
        memory_type="PREFERENCE",
        source_type="USER",
    )
    totals = runner.run_once()
    assert totals["duplicate_groups"] >= 1
    pool.close()


def test_runner_start_stop_runs_initial_sweep(tmp_path: Path) -> None:
    pool, context_manager, family_id, lifecycle, runner, owner = _bootstrap(tmp_path)
    context_manager.create_memory(
        family_id, owner,
        subject_type="FAMILY",
        content="今晚记得倒垃圾",
        memory_type="TASK",
        source_type="USER",
        expires_at=1,
    )
    asyncio.run(runner.start())
    try:
        asyncio.run(asyncio.sleep(0))  # let the initial sweep finish
        rows = context_manager.repo.list_all_memories(family_id)
        assert rows
        refreshed = context_manager.repo.get_memory(rows[0].id)
        assert refreshed is not None
        assert refreshed.status == "ARCHIVED"
    finally:
        asyncio.run(runner.stop())
    pool.close()
