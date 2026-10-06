"""Tests for the memory candidate lifecycle (Stage 4)."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.memory_candidates import (
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_MERGED,
    CANDIDATE_STATUS_PENDING,
    CANDIDATE_STATUS_REJECTED,
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.memory_lifecycle import (
    CandidateSpec,
    MemoryLifecycleManager,
    content_hash,
    is_sensitive,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path) -> tuple[SqlitePool, FamilyManager, User]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'outsider', 'x', 'user', 0, 'zh', 1)"
        )
    family = FamilyManager(FamilyRepo(pool))
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    return pool, family, user


def _seed(pool: SqlitePool, family: FamilyManager, user: User) -> tuple[str, str]:
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh"
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    papa_id = members["爸爸"].id
    family.create_member(
        fam.id, user, display_name="大宝", role=MemberRole.CHILD,
    )
    return fam.id, papa_id


def _lifecycle(pool: SqlitePool, family: FamilyManager) -> MemoryLifecycleManager:
    context = FamilyContextManager(family, family.repo)  # type: ignore[arg-type]
    # Use the context repo for FamilyMemoryRow persistence.
    from homemind.infra.db.repos.family_context import FamilyContextRepo
    context = FamilyContextManager(family, FamilyContextRepo(pool))
    return MemoryLifecycleManager(
        family,
        context,
        MemoryCandidateRepo(pool),
        MemoryEvidenceRepo(pool),
    )


# --------------------------------------------------------------- sensitivity


def test_is_sensitive_matches_sensitive_topics() -> None:
    assert is_sensitive("妈妈的身份证号码是...")
    assert is_sensitive("我的 password = 12345")
    assert is_sensitive("信用卡 6225 1234")
    assert is_sensitive("诊断意见：高血压")
    assert is_sensitive("工资条 23000元")
    assert not is_sensitive("我们今天去了杭州")
    assert not is_sensitive("妈妈喜欢樱花")


def test_content_hash_is_stable() -> None:
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


# -------------------------------------------------------------- create / approve


def test_create_candidate_creates_pending_row_and_evidence(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="妈妈喜欢樱花",
            memory_type=MemoryType.PREFERENCE.value,
            source_type="AGENT",
            source_id=None,
        ),
        creator=user,
    )
    assert candidate.status == CANDIDATE_STATUS_PENDING
    assert lifecycle.evidence.list_for_candidate(candidate.id) != []


def test_sensitive_candidate_still_pending(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="妈妈的身份证号码是 ABC123",
            memory_type=MemoryType.FACT.value,
        ),
        creator=user,
    )
    # Sensitive content MUST NOT auto-approve — stays PENDING until a manager
    # explicitly approves.
    assert candidate.status == CANDIDATE_STATUS_PENDING
    assert is_sensitive(candidate.content) is True


def test_approve_candidate_creates_memory_and_anchors_evidence(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="妈妈喜欢樱花",
            memory_type=MemoryType.PREFERENCE.value,
        ),
        creator=user,
    )
    decided, memory = lifecycle.approve_candidate(
        family_id, candidate.id, reviewer=user,
    )
    assert decided.status == CANDIDATE_STATUS_APPROVED
    assert memory.content == "妈妈喜欢樱花"
    assert decided.merged_into == memory.id


def test_non_member_cannot_approve(tmp_path: Path) -> None:
    import pytest

    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="妈妈喜欢樱花",
            memory_type=MemoryType.PREFERENCE.value,
        ),
        creator=user,
    )
    outsider = User(id=2, username="outsider", role=Role.USER, display_name="外人")
    from octop.infra.errors import OctopError
    with pytest.raises(OctopError):
        lifecycle.approve_candidate(family_id, candidate.id, reviewer=outsider)


def test_reject_candidate(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="不喜欢樱花",
            memory_type=MemoryType.PREFERENCE.value,
        ),
        creator=user,
    )
    rejected = lifecycle.reject_candidate(
        family_id, candidate.id, reviewer=user, reason="low confidence",
    )
    assert rejected.status == CANDIDATE_STATUS_REJECTED
    assert rejected.rejection_reason == "low confidence"


# -------------------------------------------------------------- merge


def test_merge_candidate_preserves_target_and_marks_merged(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    target = lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="妈妈喜欢樱花", memory_type=MemoryType.PREFERENCE,
        importance=0.6, confidence=0.6, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )
    candidate = lifecycle.create_candidate(
        CandidateSpec(
            family_id=family_id,
            subject_type="MEMBER",
            subject_id=None,
            content="妈妈喜欢樱花和海棠",
            memory_type=MemoryType.PREFERENCE.value,
            confidence=0.9,
        ),
        creator=user,
    )
    decided, updated = lifecycle.merge_candidate(
        family_id, candidate.id, target.id, reviewer=user,
    )
    assert decided.status == CANDIDATE_STATUS_MERGED
    assert updated.id == target.id
    # Confidence should have bumped by the candidate's contribution.
    assert updated.confidence >= target.confidence


def test_find_similar_memory_returns_overlapping_row(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="妈妈喜欢樱花和海棠", memory_type=MemoryType.PREFERENCE,
        importance=0.6, confidence=0.6, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )
    similar = lifecycle.find_similar_memory(
        family_id,
        content="妈妈喜欢樱花和茶花",
        subject_type="MEMBER",
        subject_id=None,
        memory_type=MemoryType.PREFERENCE.value,
    )
    assert len(similar) >= 1
    assert similar[0].content.startswith("妈妈喜欢樱花")


# -------------------------------------------------------------- maintenance


def test_decay_memories_lowers_confidence(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="很久以前妈妈喜欢樱花", memory_type=MemoryType.PREFERENCE,
        importance=0.05, confidence=0.05, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )
    decayed = lifecycle.decay_memories(family_id, threshold=0.1)
    assert decayed >= 1


def test_archive_expired_marks_old_memory(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="old memory", memory_type=MemoryType.FACT,
        importance=0.5, confidence=0.5, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=1,
    )
    archived = lifecycle.archive_expired_memories(family_id, now=2_000_000_000)
    assert archived >= 1


def test_detect_duplicates_groups_overlap(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    family_id, _ = _seed(pool, family, user)
    lifecycle = _lifecycle(pool, family)
    lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="妈妈喜欢樱花和海棠和茶花", memory_type=MemoryType.PREFERENCE,
        importance=0.5, confidence=0.5, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )
    lifecycle.context.create_memory(
        family_id, user, subject_type="MEMBER", subject_id=None,
        content="妈妈喜欢樱花和海棠和茶花和桂花",
        memory_type=MemoryType.PREFERENCE,
        importance=0.5, confidence=0.5, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )
    groups = lifecycle.detect_duplicates(family_id)
    assert len(groups) >= 1
    assert len(groups[0]) >= 2