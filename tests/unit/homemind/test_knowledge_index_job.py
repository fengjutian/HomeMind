"""The KNOWLEDGE_INDEX persistent job (Stage 5).

Document indexing is minutes of work for one scanned contract: OCR,
chunking, embedding. Running it inside an HTTP request would hold a
connection open for all of it, so it goes through the same leased job
queue as everything else — which also gives per-file retry for free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_TYPES,
    AssetJobItemRow,
    AssetJobRow,
)
from homemind.infra.family.asset_job_handlers import build_handler
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _Asset:
    def __init__(self, asset_id: str, family_id: str) -> None:
        self.id = asset_id
        self.family_id = family_id


class _AssetRepo:
    def __init__(self, assets: dict[str, str]) -> None:
        self._assets = assets

    def get(self, asset_id: str) -> Any:
        family_id = self._assets.get(asset_id)
        return _Asset(asset_id, family_id) if family_id is not None else None


class _Knowledge:
    """Records index calls and replays a canned document outcome."""

    def __init__(self, status: str = "INDEXED", error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.indexed: list[tuple[str, str, str]] = []
        self.forgotten: list[tuple[str, str]] = []

    def index_asset(
        self,
        family_id: str,
        asset_id: str,
        user: Any,
        *,
        ocr_provider: Any = None,
        privacy_guard: Any = None,
    ) -> Any:
        self.indexed.append((family_id, asset_id, type(user).__name__))
        return _Document(self.status, self.error)

    def forget_asset(self, family_id: str, asset_id: str) -> bool:
        self.forgotten.append((family_id, asset_id))
        return True


class _Document:
    def __init__(self, status: str, error: str | None) -> None:
        self.status = status
        self.error = error
        self.chunk_count = 3


def _register(services: Any, family_id: str, name: str) -> str:
    """Register a real asset so the job can be bound to it."""
    return services.family_asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="DOCUMENT",
        name=name,
        uri=f"file:///docs/{name}",
        mime_type="application/pdf",
        size_bytes=1024,
        content_hash=f"hash-{name}",
        captured_at=None,
        metadata_json="{}",
        visibility="FAMILY",
        created_by=1,
    ).id


def _job(job_type: str = "KNOWLEDGE_INDEX") -> AssetJobRow:
    return AssetJobRow(
        id="job-1",
        pk=1,
        family_id="FAM1",
        source_id=None,
        job_type=job_type,
        status="PENDING",
        cursor_json="{}",
        config_json="{}",
        total_items=1,
        processed_items=0,
        succeeded_items=0,
        skipped_items=0,
        failed_items=0,
        error_summary=None,
        requested_by=1,
        created_at=1,
        started_at=None,
        updated_at=1,
        finished_at=None,
        lease_owner=None,
        lease_expires_at=None,
    )


def _item(asset_id: str | None) -> AssetJobItemRow:
    return AssetJobItemRow(
        id="item-1",
        pk=1,
        job_id="job-1",
        asset_id=asset_id,
        source_path="/docs/scan.pdf",
        status="PENDING",
        attempt_count=0,
        error=None,
        created_at=1,
        updated_at=1,
    )


@pytest.fixture
def user() -> User:
    return User(1, "owner", Role.USER, "Owner")


# ------------------------------------------------------------------ registry


def test_the_job_type_is_registered() -> None:
    assert "KNOWLEDGE_INDEX" in JOB_TYPES


# ------------------------------------------------------------------- handler


def test_a_document_is_indexed_with_its_acting_user(user: User) -> None:
    knowledge = _Knowledge()
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({"a1": "FAM1"}),
        knowledge=knowledge,
        ocr_provider="ocr",
        privacy_guard="guard",
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    handler(_item("a1"))
    assert knowledge.indexed == [("FAM1", "a1", type(user).__name__)]


def test_a_deleted_asset_drops_its_document_instead_of_failing(user: User) -> None:
    """A removed photo must stop answering queries."""
    knowledge = _Knowledge()
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({}),
        knowledge=knowledge,
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    handler(_item("gone"))
    assert knowledge.indexed == []
    assert knowledge.forgotten == [("FAM1", "gone")]


def test_an_item_with_no_asset_is_refused(user: User) -> None:
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({}),
        knowledge=_Knowledge(),
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    with pytest.raises(ValueError, match="not bound to an asset"):
        handler(_item(None))


def test_another_familys_asset_is_refused(user: User) -> None:
    """A stale job must never index another household's document."""
    knowledge = _Knowledge()
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({"a1": "OTHER"}),
        knowledge=knowledge,
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    with pytest.raises(ValueError, match="does not belong"):
        handler(_item("a1"))
    assert knowledge.indexed == []


def test_an_unreadable_document_fails_only_its_own_item(user: User) -> None:
    knowledge = _Knowledge(status="FAILED", error="OCR produced no readable text")
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({"a1": "FAM1"}),
        knowledge=knowledge,
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    with pytest.raises(ValueError, match="OCR produced no readable text"):
        handler(_item("a1"))


def test_an_unsupported_format_is_not_a_failure(user: User) -> None:
    """A file this build cannot read is recorded, not retried forever."""
    knowledge = _Knowledge(status="UNSUPPORTED", error=".xyz is not a supported format")
    handler = build_handler(
        _job(),
        asset_manager=None,
        asset_repo=_AssetRepo({"a1": "FAM1"}),
        knowledge=knowledge,
        user=user,
        family_id="FAM1",
        created_by_user_id=1,
    )
    # No raise: the job item finishes as skipped rather than failed.
    handler(_item("a1"))


def test_the_handler_refuses_to_build_without_a_knowledge_manager(user: User) -> None:
    with pytest.raises(NotImplementedError, match="knowledge manager"):
        build_handler(
            _job(),
            asset_manager=None,
            asset_repo=_AssetRepo({}),
            knowledge=None,
            user=user,
            family_id="FAM1",
            created_by_user_id=1,
        )


def test_the_handler_refuses_to_build_without_an_acting_user() -> None:
    """Permission and family scope are resolved against a real identity."""
    with pytest.raises(NotImplementedError, match="acting user"):
        build_handler(
            _job(),
            asset_manager=None,
            asset_repo=_AssetRepo({}),
            knowledge=_Knowledge(),
            user=None,
            family_id="FAM1",
            created_by_user_id=1,
        )


def test_an_unknown_job_type_still_fails_loudly(user: User) -> None:
    with pytest.raises(NotImplementedError):
        build_handler(
            _job("NOT_A_JOB"),
            asset_manager=None,
            asset_repo=_AssetRepo({}),
            knowledge=_Knowledge(),
            user=user,
            family_id="FAM1",
            created_by_user_id=1,
        )


# --------------------------------------------------------------------- queue


def test_a_document_job_is_created_through_the_existing_queue(
    tmp_path: Path, user: User
) -> None:
    """The endpoint the Files page uses needs no new route."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
            "created_at) VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )


    from homemind.infra.db.services import HomeMindServices
    from homemind.infra.family.asset_jobs import AssetJobManager
    from homemind.infra.family.manager import FamilyManager

    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    family = families.create_family(
        user, name="Doc Family", timezone="Asia/Shanghai", locale="zh"
    )
    manager = AssetJobManager(
        families, services.asset_job_repo, asset_repo=services.family_asset_repo
    )
    ids = [
        _register(services, family.id, f"doc{i}.pdf") for i in range(2)
    ]
    job = manager.create_job(
        family.id, user, job_type="KNOWLEDGE_INDEX", asset_ids=ids
    )
    assert job.job_type == "KNOWLEDGE_INDEX"
    assert job.total_items == 2
    items = services.asset_job_repo.list_items(job.id)
    assert sorted(item.asset_id for item in items) == sorted(ids)


def test_the_job_type_survives_a_persisted_round_trip(tmp_path: Path, user: User) -> None:
    """A restart must not lose what the job was for."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
            "created_at) VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
        conn.execute(
            "INSERT INTO homemind_families(family_id, owner_user_id, name, timezone, "
            "locale, created_at, updated_at) VALUES ('FAM1', 1, 'F', 'Asia/Shanghai', "
            "'zh', 1, 1)"
        )
    from homemind.infra.db.services import HomeMindServices
    from homemind.infra.family.asset_jobs import AssetJobManager
    from homemind.infra.family.manager import FamilyManager

    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    family = families.create_family(
        user, name="Doc Family 2", timezone="Asia/Shanghai", locale="zh"
    )
    manager = AssetJobManager(
        families, services.asset_job_repo, asset_repo=services.family_asset_repo
    )
    asset_id = _register(services, family.id, "contract.pdf")
    created = manager.create_job(
        family.id, user, job_type="KNOWLEDGE_INDEX", asset_ids=[asset_id]
    )
    reopened = services.asset_job_repo.get_job(created.id)
    assert reopened is not None
    assert reopened.job_type == "KNOWLEDGE_INDEX"
    assert reopened.status in {JOB_STATUS_COMPLETED, "PENDING", JOB_STATUS_FAILED}
