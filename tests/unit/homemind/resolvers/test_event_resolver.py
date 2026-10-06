"""Tests for the event resolver (Stage 2)."""

from __future__ import annotations

import json
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.resolvers.event import EventResolver
from homemind.infra.family.resolvers.models import ResolvedTimeRange
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool


def _pool(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    return pool


def _family(pool: SqlitePool) -> str:
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
        cur = conn.execute(
            "INSERT INTO homemind_families(family_id, name, owner_user_id, timezone, locale, "
            "created_at, updated_at) VALUES (?, 'Happy', 1, 'Asia/Shanghai', 'zh', 1, 1)",
            ("fam_x",),
        )
        conn.execute(
            "INSERT INTO homemind_family_members(member_id, family_id, user_id, display_name, role, "
            "status, created_at, updated_at) VALUES ('m_owner', 'fam_x', 1, 'u', 'OWNER', "
            "'ACTIVE', ?, ?)",
            (1, 1),
        )
    return "fam_x"


def _seed_events(repo: FamilyContextRepo, family_id: str) -> dict[str, str]:
    kyoto = repo.create_event(
        family_id=family_id,
        event_type="TRIP",
        title="京都赏樱",
        start_at=1700000000,
        end_at=1700050000,
        location="京都",
        description="樱花季",
        metadata_json=json.dumps({"member_ids": ["mama"]}, ensure_ascii=False),
        created_by=1,
    )
    tokyo = repo.create_event(
        family_id=family_id,
        event_type="TRIP",
        title="东京迪士尼",
        start_at=1800000000,
        end_at=1800050000,
        location="东京",
        description="家庭出游",
        metadata_json=json.dumps({"member_ids": ["papa", "mama"]}, ensure_ascii=False),
        created_by=1,
    )
    unrelated = repo.create_event(
        family_id=family_id,
        event_type="MEETING",
        title="家长会",
        start_at=1900000000,
        end_at=1900050000,
        location="学校",
        description="",
        metadata_json="{}",
        created_by=1,
    )
    return {"kyoto": kyoto.id, "tokyo": tokyo.id, "unrelated": unrelated.id}


def test_title_match_wins(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    ids = _seed_events(repo, family_id)
    matches = EventResolver(repo).search(family_id, text="京都")
    assert [m.event.id for m in matches] == [ids["kyoto"]]


def test_no_match_returns_empty_not_all(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    _seed_events(repo, family_id)
    matches = EventResolver(repo).search(family_id, text="完全不相关")
    assert matches == []


def test_member_match_boosts_confidence(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    ids = _seed_events(repo, family_id)
    matches = EventResolver(repo).search(
        family_id, text="家庭", member_ids=["mama"],
    )
    matched_ids = {m.event.id for m in matches}
    assert ids["tokyo"] in matched_ids
    assert ids["kyoto"] in matched_ids


def test_time_range_filters(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    ids = _seed_events(repo, family_id)
    tr = ResolvedTimeRange(
        start_at=1750000000, end_at=1850000000, expression="2024", confidence=0.9,
    )
    matches = EventResolver(repo).search(family_id, time_range=tr)
    assert [m.event.id for m in matches] == [ids["tokyo"]]


def test_event_type_filter(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    ids = _seed_events(repo, family_id)
    matches = EventResolver(repo).search(family_id, event_type="MEETING")
    assert [m.event.id for m in matches] == [ids["unrelated"]]


def test_results_sorted_by_confidence(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    family_id = _family(pool)
    repo = FamilyContextRepo(pool)
    ids = _seed_events(repo, family_id)
    matches = EventResolver(repo).search(family_id, text="东京")
    # The 东京 event should be the top match for "东京" query.
    assert matches[0].event.id == ids["tokyo"]
    assert matches[0].candidate.confidence >= matches[-1].candidate.confidence