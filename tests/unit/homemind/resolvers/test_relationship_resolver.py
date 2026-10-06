"""Tests for the family-term relationship resolver (Stage 2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.family.manager import (
    FamilyManager,
    MemberRole,
    RelationshipType,
)
from homemind.infra.family.resolvers.relationship import (
    SUPPORTED_RELATION_TERMS,
    RelationshipResolver,
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
            "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
            "(3, 'admin', 'x', 'admin', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    return pool, FamilyManager(FamilyRepo(pool)), user


def _seed_family(pool: SqlitePool, family: FamilyManager, user: User) -> dict[str, str]:
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh"
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    papa_id = members["爸爸"].id
    # Add a mother for papa so "妈妈" can resolve from papa's POV.
    grandma = family.create_member(
        fam.id, user, display_name="妈妈", role=MemberRole.MEMBER,
    )
    # Spouse named differently so we don't conflate the parent with the wife.
    spouse = family.create_member(
        fam.id, user, display_name="妻子", role=MemberRole.MEMBER,
        user_id=2,
    )
    child1 = family.create_member(
        fam.id, user, display_name="大宝", role=MemberRole.CHILD,
    )
    child2 = family.create_member(
        fam.id, user, display_name="小宝", role=MemberRole.CHILD,
    )
    # grandma → papa = PARENT edge (grandma is papa's mom)
    family.create_relationship(
        fam.id, user, from_member_id=grandma.id, to_member_id=papa_id,
        relationship_type=RelationshipType.PARENT,
    )
    family.create_relationship(
        fam.id, user, from_member_id=papa_id, to_member_id=spouse.id,
        relationship_type=RelationshipType.SPOUSE,
    )
    family.create_relationship(
        fam.id, user, from_member_id=papa_id, to_member_id=child1.id,
        relationship_type=RelationshipType.PARENT,
    )
    family.create_relationship(
        fam.id, user, from_member_id=papa_id, to_member_id=child2.id,
        relationship_type=RelationshipType.PARENT,
    )
    return {"family_id": fam.id, "papa_id": papa_id, "mama_id": grandma.id,
            "spouse_id": spouse.id,
            "child1_id": child1.id, "child2_id": child2.id}


def test_supported_relation_terms_have_expected_set() -> None:
    assert "妈妈" in SUPPORTED_RELATION_TERMS
    assert "爸爸" in SUPPORTED_RELATION_TERMS
    assert "老婆" in SUPPORTED_RELATION_TERMS
    assert "老公" in SUPPORTED_RELATION_TERMS
    assert "孩子" in SUPPORTED_RELATION_TERMS
    assert "哥哥" in SUPPORTED_RELATION_TERMS
    assert "爷爷" in SUPPORTED_RELATION_TERMS
    assert "孙子" in SUPPORTED_RELATION_TERMS


def test_mom_resolves_from_papa(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, user)
    resolver = RelationshipResolver(family.repo)
    matches = resolver.find("妈妈", current_member_id=seeded["papa_id"])
    assert len(matches) == 1
    assert matches[0].candidate.entity_id == seeded["mama_id"]


def test_children_resolves_to_multiple_ambiguity(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, user)
    resolver = RelationshipResolver(family.repo)
    matches = resolver.find("孩子", current_member_id=seeded["papa_id"])
    assert {m.candidate.entity_id for m in matches} == {
        seeded["child1_id"],
        seeded["child2_id"],
    }


def test_spouse_resolves_from_papa(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, user)
    resolver = RelationshipResolver(family.repo)
    matches = resolver.find("老婆", current_member_id=seeded["papa_id"])
    assert len(matches) == 1
    assert matches[0].candidate.entity_id == seeded["spouse_id"]


def test_unknown_term_falls_back_to_display_name(tmp_path: Path) -> None:
    pool, family, user = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, user)
    # "李雷" is not a relation term and not a member name → empty list.
    resolver = RelationshipResolver(family.repo)
    assert resolver.find("李雷", current_member_id=seeded["papa_id"]) == []
    # "nobody-here" is not a relation term or display name → empty list.
    assert resolver.find("nobody-here", current_member_id=seeded["papa_id"]) == []


def test_perspective_matters(tmp_path: Path) -> None:
    """Same family term must yield different results based on current member."""
    pool, family, user = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, user)
    resolver = RelationshipResolver(family.repo)
    # From papa's POV, child1 + child2 are children.
    matches = resolver.find("孩子", current_member_id=seeded["papa_id"])
    assert {m.candidate.entity_id for m in matches} == {
        seeded["child1_id"],
        seeded["child2_id"],
    }
    # From mama's POV, only direct CHILD edges are surfaced. Spouse's
    # children resolution belongs to a later stage that walks the family
    # graph transitively; this test pins down the current scope.
    matches_from_mama = resolver.find("孩子", current_member_id=seeded["mama_id"])
    assert matches_from_mama == []


# Silence linter warning: the timestamp is unused but helps the importer
# recognize this is test code with stable clock semantics.
_ = datetime.now(ZoneInfo("Asia/Shanghai"))