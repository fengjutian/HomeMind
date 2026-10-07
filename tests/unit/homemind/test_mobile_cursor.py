"""Cursor pagination and the mobile API contract (Stage 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from homemind.infra.cursor import InvalidCursor, build_page, decode_cursor, encode_cursor

# -------------------------------------------------------------------- cursor


def test_a_cursor_round_trips() -> None:
    token = encode_cursor(1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert decode_cursor(token) == (1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAV")


def test_a_cursor_carries_both_halves() -> None:
    """The token must round-trip the exact pair the resume query needs."""
    token = encode_cursor(1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    created_at, row_id = decode_cursor(token)
    assert created_at == 1893456000
    assert row_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_distinct_rows_get_distinct_cursors() -> None:
    """Two rows in the same second must not collide on one cursor."""
    first = encode_cursor(1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAA")
    second = encode_cursor(1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAB")
    assert first != second


@pytest.mark.parametrize("bad", ["", "x", "not-a-cursor", "!!!!!!", "MDA"])
def test_a_token_we_did_not_issue_is_refused(bad: str) -> None:
    """A malformed cursor sends the client back to page one.

    Best-effort parsing would return a page from an unexpected
    position, which is worse than an error the client can act on.
    """
    with pytest.raises(InvalidCursor):
        decode_cursor(bad)


def test_a_cursor_with_no_id_half_is_refused() -> None:
    import base64

    token = base64.urlsafe_b64encode(b"1893456000|").decode().rstrip("=")
    with pytest.raises(InvalidCursor):
        decode_cursor(token)


def test_a_cursor_with_a_non_numeric_time_is_refused() -> None:
    import base64

    token = base64.urlsafe_b64encode(b"yesterday|01ABC").decode().rstrip("=")
    with pytest.raises(InvalidCursor):
        decode_cursor(token)


def test_the_cursor_is_url_safe() -> None:
    token = encode_cursor(1893456000, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert "+" not in token
    assert "/" not in token
    assert "=" not in token


# ---------------------------------------------------------------- page shape


class _Row:
    def __init__(self, created_at: int, row_id: str) -> None:
        self.created_at = created_at
        self.id = row_id


def test_the_last_page_has_no_cursor() -> None:
    rows = [_Row(3, "c"), _Row(2, "b")]
    page = build_page(rows, to_item=lambda row: row.id, limit=2)
    assert page.has_more is False
    # None, not "": a client testing truthiness must be able to stop.
    assert page.next_cursor is None
    assert page.items == ["c", "b"]


def test_an_extra_row_proves_there_is_more() -> None:
    rows = [_Row(3, "c"), _Row(2, "b"), _Row(1, "a")]
    page = build_page(rows, to_item=lambda row: row.id, limit=2)
    assert page.has_more is True
    assert page.items == ["c", "b"]
    # The cursor points at the last *returned* row, not the extra one.
    assert decode_cursor(str(page.next_cursor)) == (2, "b")


def test_an_empty_result_is_an_empty_page_not_an_error() -> None:
    page = build_page([], to_item=lambda row: row.id, limit=20)
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None
    assert page.as_dict() == {"items": [], "next_cursor": None, "has_more": False}


# ------------------------------------------------------------- repo paging


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
    from homemind.infra.db.repos.family_notifications import FamilyNotificationRepo
    from homemind.infra.db.services import HomeMindServices
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import SqlitePool
    from octop.infra.users.identity import Role, User

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    repo = FamilyNotificationRepo(pool)
    family_id = "FAM1"
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO homemind_families(family_id, owner_user_id, name, timezone, locale, "
            "created_at, updated_at) VALUES (?, 1, 'F', 'Asia/Shanghai', 'zh', 1, 1)",
            (family_id,),
        )
    return {
        "repo": repo,
        "family_id": family_id,
        "owner": User(1, "owner", Role.USER, "Owner"),
        "spouse": User(2, "spouse", Role.USER, "Spouse"),
        "services": services,
    }


def _seed(env, count: int = 7) -> None:  # noqa: ANN001, ANN201
    """Insert ``count`` notifications with distinct, increasing times."""
    base = int(datetime(2026, 3, 1, tzinfo=UTC).timestamp())
    for index in range(count):
        env["repo"].create(
            env["family_id"],
            user_id=env["owner"].id,
            type="TASK_DUE",
            title_key="notifications.taskDue.title",
            body_key="notifications.taskDue.body",
            dedupe_key=f"seed-{index}",
            params={"title": f"task {index}"},
            target_type="TASK",
            target_id=f"t{index}",
            now=base + index,
        )


def test_paging_walks_every_row_exactly_once(env) -> None:  # noqa: ANN001
    _seed(env, 7)
    seen: list[str] = []
    cursor: str | None = None
    for _page in range(10):
        # ``limit + 1`` so ``has_more`` is provable without a second
        # query — the same thing the manager's page helper does.
        page = build_page(
            env["repo"].list_inbox(env["owner"].id, limit=4, cursor=cursor),
            to_item=lambda row: row.id,
            limit=3,
        )
        seen.extend(page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == 7
    assert len(set(seen)) == 7


def test_a_notification_added_mid_paging_does_not_shift_the_page(env) -> None:  # noqa: ANN001
    """The reason for a cursor rather than an offset.

    With ``OFFSET`` a row inserted at the top shifts every later page
    and the client silently skips one.
    """
    _seed(env, 6)
    first = env["repo"].list_inbox(env["owner"].id, limit=4)
    page_one = build_page(first, to_item=lambda row: row.id, limit=3)
    newest_first = page_one.items[0]

    # Something new arrives while the client is still scrolling.
    env["repo"].create(
        env["family_id"],
        user_id=env["owner"].id,
        type="TASK_DUE",
        title_key="notifications.taskDue.title",
        body_key="notifications.taskDue.body",
        dedupe_key="brand-new",
        target_type="TASK",
        target_id="new",
        now=int(datetime(2026, 4, 1, tzinfo=UTC).timestamp()),
    )

    second = build_page(
        env["repo"].list_inbox(env["owner"].id, limit=4, cursor=str(page_one.next_cursor)),
        to_item=lambda row: row.id,
        limit=3,
    )
    assert newest_first not in second.items
    assert len(second.items) == 3


def test_a_bogus_cursor_is_refused_by_the_repo(env) -> None:  # noqa: ANN001
    _seed(env, 3)
    with pytest.raises(InvalidCursor):
        env["repo"].list_inbox(env["owner"].id, limit=2, cursor="nonsense-token")


def test_paging_never_leaks_another_users_rows(env) -> None:  # noqa: ANN001
    _seed(env, 4)
    env["repo"].create(
        env["family_id"],
        user_id=env["spouse"].id,
        type="TASK_DUE",
        title_key="notifications.taskDue.title",
        body_key="notifications.taskDue.body",
        dedupe_key="spouse-only",
        target_type="TASK",
        target_id="theirs",
    )
    rows = env["repo"].list_inbox(env["owner"].id, limit=50)
    assert all(row.user_id == env["owner"].id for row in rows)
    assert len(rows) == 4


def test_paging_respects_the_unread_filter(env) -> None:  # noqa: ANN001
    _seed(env, 5)
    oldest = env["repo"].list_inbox(env["owner"].id, limit=5)[-1]
    env["repo"].mark_read(oldest.id, user_id=env["owner"].id)
    page = build_page(
        env["repo"].list_inbox(env["owner"].id, limit=10, unread_only=True),
        to_item=lambda row: row.id,
        limit=10,
    )
    assert oldest.id not in page.items
    assert len(page.items) == 4


def test_paging_skips_expired_rows(env) -> None:  # noqa: ANN001
    now = int(datetime.now(UTC).timestamp())
    _seed(env, 3)
    env["repo"].create(
        env["family_id"],
        user_id=env["owner"].id,
        type="TASK_DUE",
        title_key="k",
        body_key="b",
        dedupe_key="lapsed",
        expires_at=now - 10,
        now=now - 100,
    )
    page = build_page(
        env["repo"].list_inbox(env["owner"].id, limit=10),
        to_item=lambda row: row.id,
        limit=10,
    )
    assert len(page.items) == 3
