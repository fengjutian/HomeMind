"""Stage 7 acceptance tests for the unified search index.

Covers the spec's bullets:

* permission filtering happens during the query, not after it,
* cursor pagination neither repeats nor skips a row,
* a new document becomes searchable, an update refreshes it, and an
  archive takes it out of the default result set,
* vectors from different models are never compared,
* Chinese, English, filename, location, and event text all match,
* a tampered cursor is rejected rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.search_index import (
    KIND_ASSET,
    KIND_EVENT,
    KIND_LOCATION,
    KIND_MEMORY,
    VISIBILITY_FAMILY,
    VISIBILITY_PRIVATE,
    SearchIndexRepo,
    cosine_similarity,
    decode_position,
    document_id_for,
    encode_position,
    pack_embedding,
    sanitize_visibility,
    sign_token,
    unpack_embedding,
    verify_token,
)
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.search_index_manager import (
    FamilySearchManager,
    FamilySearchQuery,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
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
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    sibling = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    family.create_member(
        fam.id, user, display_name="妈妈", role=MemberRole.MEMBER, user_id=sibling.id,
    )
    context_repo = FamilyContextManager(family, __import__(
        "homemind.infra.db.repos.family_context", fromlist=["FamilyContextRepo"]
    ).FamilyContextRepo(pool))
    index = SearchIndexRepo(pool)
    asset_repo = FamilyAssetRepo(pool)
    manager = FamilySearchManager(
        family,
        index,
        permission_evaluator=FamilyPermissionEvaluator(family_repo),
        cursor_secret=b"test-secret",
    )
    return pool, index, manager, family, context_repo, asset_repo, user, sibling, fam.id


def _seed(index: SearchIndexRepo, family_id: str, **overrides) -> str:
    payload = {
        "kind": KIND_MEMORY,
        "entity_id": "mem_1",
        "title": "口味",
        "text": "妈妈喜欢清淡饮食",
        "visibility": VISIBILITY_FAMILY,
    }
    payload.update(overrides)
    return index.upsert_document(family_id, **payload)


# ------------------------------------------------------------- visibility


def test_visibility_filter_is_applied() -> None:
    assert sanitize_visibility(None) == ("PUBLIC", "FAMILY")
    assert sanitize_visibility("public,family") == ("PUBLIC", "FAMILY")
    # Unknown values are dropped, never interpolated.
    assert sanitize_visibility("'; DROP TABLE x --") == ("PUBLIC", "FAMILY")
    assert sanitize_visibility("private") == ("PRIVATE",)


def test_private_memory_is_excluded_from_another_members_results(
    tmp_path: Path,
) -> None:
    pool, index, manager, family, context, assets, user, sibling, family_id = _bootstrap(tmp_path)
    _seed(
        index, family_id, entity_id="shared", text="京都旅行全家一起",
        visibility=VISIBILITY_FAMILY,
    )
    _seed(
        index, family_id, entity_id="secret", text="京都旅行是私密的",
        visibility=VISIBILITY_PRIVATE, owner_member_id="someone_else",
    )
    page = manager.search(family_id, user, FamilySearchQuery(text="京都"))
    ids = {item.entity_id for item in page.items}
    assert "shared" in ids
    assert "secret" not in ids, "private memory must not reach another member"
    pool.close()


def test_private_memory_is_visible_to_its_owner(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    membership = family.repo.get_membership(family_id, user.id)
    assert membership is not None
    _seed(
        index, family_id, entity_id="mine", text="京都旅行",
        visibility=VISIBILITY_PRIVATE, owner_member_id=str(membership["member_id"]),
    )
    page = manager.search(family_id, user, FamilySearchQuery(text="京都"))
    assert "mine" in {item.entity_id for item in page.items}
    pool.close()


def test_archived_document_leaves_the_default_results(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="gone", text="京都旅行", status="ARCHIVED")
    page = manager.search(family_id, user, FamilySearchQuery(text="京都"))
    assert "gone" not in {item.entity_id for item in page.items}
    pool.close()


# ------------------------------------------------------------- reindexing


def test_new_document_becomes_searchable(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="new", text="樱花盛开的春天")
    page = manager.search(family_id, user, FamilySearchQuery(text="樱花"))
    assert "new" in {item.entity_id for item in page.items}
    pool.close()


def test_update_refreshes_the_same_document(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    first = _seed(index, family_id, entity_id="m", text="旧的关键词")
    second = _seed(index, family_id, entity_id="m", text="新的关键词")
    assert first == second, "re-indexing must update in place"
    assert index.count_documents(family_id) == 1

    assert manager.search(
        family_id, user, FamilySearchQuery(text="旧的关键词"),
    ).items == ()
    assert manager.search(
        family_id, user, FamilySearchQuery(text="新的关键词"),
    ).items
    pool.close()


def test_remove_document_takes_it_out_of_results(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="temp", text="临时记录")
    assert manager.search(family_id, user, FamilySearchQuery(text="临时")).items
    assert index.remove_document(family_id, KIND_MEMORY, "temp") is True
    assert manager.search(family_id, user, FamilySearchQuery(text="临时")).items == ()
    pool.close()


# ----------------------------------------------------------- text matching


def test_chinese_search(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="cn", text="妈妈喜欢清淡饮食")
    page = manager.search(family_id, user, FamilySearchQuery(text="清淡"))
    assert "cn" in {item.entity_id for item in page.items}
    pool.close()


def test_english_search(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="en", text="the cherry blossoms were wonderful")
    page = manager.search(family_id, user, FamilySearchQuery(text="cherry"))
    assert "en" in {item.entity_id for item in page.items}
    pool.close()


def test_filename_search(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, kind=KIND_ASSET, entity_id="a1", text="IMG_2841.jpg PHOTO")
    page = manager.search(
        family_id, user, FamilySearchQuery(text="IMG_2841", kinds=frozenset({KIND_ASSET})),
    )
    assert "a1" in {item.entity_id for item in page.items}
    pool.close()


def test_kind_filter_narrows_results(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, kind=KIND_EVENT, entity_id="e", text="京都庆生")
    _seed(index, family_id, kind=KIND_MEMORY, entity_id="m", text="京都回忆")
    only_events = manager.search(
        family_id, user,
        FamilySearchQuery(text="京都", kinds=frozenset({KIND_EVENT})),
    )
    assert {item.entity_id for item in only_events.items} == {"e"}
    pool.close()


def test_location_kind_is_searchable(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, kind=KIND_LOCATION, entity_id="loc", text="清水寺")
    page = manager.search(
        family_id, user, FamilySearchQuery(text="清水寺", kinds=frozenset({KIND_LOCATION})),
    )
    assert "loc" in {item.entity_id for item in page.items}
    pool.close()


# -------------------------------------------------------------- pagination


def test_cursor_pagination_has_no_duplicates_or_gaps(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    for number in range(12):
        _seed(
            index, family_id,
            entity_id=f"m{number}",
            text=f"共同关键词 第{number}条",
        )
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = manager.search(
            family_id, user,
            FamilySearchQuery(text="共同关键词", limit=5, cursor=cursor),
        )
        seen.extend(item.entity_id for item in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
        assert cursor is not None
    assert len(seen) == 12
    assert len(set(seen)) == 12, "pagination must not repeat a row"
    pool.close()


def test_tampered_cursor_is_rejected(tmp_path: Path) -> None:
    from octop.infra.errors import OctopError  # noqa: PLC0415

    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    for number in range(6):
        _seed(index, family_id, entity_id=f"m{number}", text=f"关键词{number}")
    first = manager.search(
        family_id, user, FamilySearchQuery(text="关键词", limit=2),
    )
    assert first.next_cursor is not None
    tampered = first.next_cursor[:-2] + ("aa" if not first.next_cursor.endswith("aa") else "bb")
    with pytest.raises(OctopError):
        manager.search(
            family_id, user, FamilySearchQuery(text="关键词", limit=2, cursor=tampered),
        )
    pool.close()


def test_token_signing_round_trip() -> None:
    payload = sign_token("abc.def", secret=b"s")
    assert verify_token(payload, secret=b"s") == "abc.def"
    assert verify_token(payload, secret=b"other") is None
    assert verify_token("nodot", secret=b"s") is None


def test_position_round_trip() -> None:
    token = encode_position(0.87, 1700, "ASSET", "a1")
    payload = decode_position(token)
    assert payload is not None
    assert payload["s"] == 0.87
    assert payload["u"] == 1700
    assert payload["k"] == "ASSET"
    assert decode_position("!!!not-base64!!!") is None


# --------------------------------------------------------------- embeddings


def test_embedding_round_trip_and_similarity() -> None:
    vector = [0.1, 0.2, 0.3]
    assert [round(x, 6) for x in unpack_embedding(pack_embedding(vector))] == vector
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert cosine_similarity(vector, [-0.1, -0.2, -0.3]) == pytest.approx(-1.0)
    # Mismatched dimensions must not be compared at all.
    assert cosine_similarity(vector, [1.0, 0.0]) == 0.0
    assert cosine_similarity([], vector) == 0.0


def test_vectors_from_different_models_are_never_compared(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="a", text="京都")
    # Attach the same vector under two different models.
    index.upsert_document(
        family_id, kind=KIND_MEMORY, entity_id="a", title="", text="",
        embedding=[1.0, 0.0], embedding_model="model-A",
        embedding_dimensions=2, embedding_version=1,
    )
    index.upsert_document(
        family_id, kind=KIND_MEMORY, entity_id="b", title="", text="",
        embedding=[0.0, 1.0], embedding_model="model-B",
        embedding_dimensions=2, embedding_version=1,
    )
    page = manager.search(
        family_id, user,
        FamilySearchQuery(
            text="京都",
            embedding=(1.0, 0.0),
            embedding_model="model-A",
            embedding_dimensions=2,
            embedding_version=1,
        ),
    )
    scored = {item.entity_id: item for item in page.items}
    # The model-B document is invisible to a model-A query.
    assert "b" not in scored
    assert "a" in scored
    pool.close()


def test_dimension_mismatch_excludes_the_row(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="a", text="京都")
    index.upsert_document(
        family_id, kind=KIND_MEMORY, entity_id="a", title="", text="",
        embedding=[1.0, 0.0, 0.0], embedding_model="m",
        embedding_dimensions=3, embedding_version=1,
    )
    page = manager.search(
        family_id, user,
        FamilySearchQuery(
            text="京都", embedding=(1.0, 0.0),
            embedding_model="m", embedding_dimensions=2, embedding_version=1,
        ),
    )
    # The stored vector has 3 dimensions and must be skipped rather
    # than truncated into a bogus comparison.
    assert all("semantic" not in item.matched_by for item in page.items)
    pool.close()


def test_clear_embeddings_is_model_scoped(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="a", text="x")
    for entity, model in (("a", "m1"), ("b", "m2")):
        index.upsert_document(
            family_id, kind=KIND_MEMORY, entity_id=entity, title="", text="x",
            embedding=[1.0], embedding_model=model,
            embedding_dimensions=1, embedding_version=1,
        )
    index.clear_embeddings(family_id, model="m1")
    remaining = {
        row.entity_id
        for row in index.list_documents(family_id, kinds=(KIND_MEMORY,))
        if row.embedding
    }
    assert remaining == {"b"}
    pool.close()


# ----------------------------------------------------------------- scoring


def test_scores_are_normalised_and_explainable(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    _seed(index, family_id, entity_id="a", text="京都樱花")
    page = manager.search(family_id, user, FamilySearchQuery(text="京都"))
    assert page.items
    for item in page.items:
        assert 0.0 <= item.score <= 1.0
        assert item.matched_by
    assert page.as_payload()["items"][0]["highlights"]
    pool.close()


def test_document_id_is_stable_per_entity(tmp_path: Path) -> None:
    pool, index, manager, family, context, assets, user, _sibling, family_id = _bootstrap(tmp_path)
    assert document_id_for("f", KIND_ASSET, "a") == document_id_for("f", KIND_ASSET, "a")
    assert document_id_for("f", KIND_ASSET, "a") != document_id_for("f", KIND_MEMORY, "a")
    pool.close()
