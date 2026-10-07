"""Stage B acceptance tests for the asset-job data contract.

The gap these close: ``AssetJobCreateBody`` used to accept only
``job_type / source_id / paths``, which is not enough to run an AI job. A
worker that starts hours later — or in a new process after a restart — has
no way to learn which provider and model to call, and the API accepted raw
absolute paths for jobs that should only ever touch registered assets.

Covered here:

* the ``config_json`` column exists on both dialects and survives a boot
  that already recorded v15,
* the config round-trips through a real restart and rejects credentials,
  unknown versions and unknown fields,
* job creation is manager-only and refuses another family's asset id,
* the request model accepts and refuses the right combinations per job type.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import (
    JOB_STATUS_COMPLETED,
    AssetJobRepo,
)
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.asset_job_config import (
    AssetJobConfig,
    reject_secrets,
    validate_config_for_job_type,
)
from homemind.infra.family.asset_jobs import AssetJobManager, AssetJobSummary
from homemind.infra.family.manager import FamilyManager, MemberRole
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
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
    other = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family = FamilyManager(FamilyRepo(pool))
    fam = family.create_family(
        user,
        name="Happy",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    job_repo = AssetJobRepo(pool)
    asset_repo = FamilyAssetRepo(pool)
    manager = AssetJobManager(family, job_repo, asset_repo=asset_repo, batch_size=2)
    return pool, manager, job_repo, asset_repo, family, user, other, fam.id


def _seed_asset(asset_repo, family_id: str, tmp_path: Path, name: str = "photo.jpg"):
    source = tmp_path / name
    source.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    return asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="PHOTO",
        name=name,
        uri=source.resolve().as_uri(),
        mime_type="image/jpeg",
        size_bytes=source.stat().st_size,
        content_hash=f"hash-{name}",
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )


# ------------------------------------------------------------- the column


def test_config_json_column_exists(tmp_path: Path) -> None:
    pool, *_rest = _bootstrap(tmp_path)
    with pool.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(homemind_asset_jobs)",
            ).fetchall()
        }
    assert "config_json" in columns
    pool.close()


def test_v15_database_gains_config_json_on_next_boot(tmp_path: Path) -> None:
    """A database that already recorded v15 must still gain the column.

    Editing an unreleased migration is safe only because ``run_migrations``
    re-applies the change idempotently. Without this, every existing local
    install would keep running against a schema that lacks the column.
    """
    db_path = tmp_path / "octop.db"
    pool = SqlitePool(db_path)
    run_migrations(pool)
    run_homemind_migrations(pool)
    # Simulate a database stamped at v15 by the *previous* file, which had
    # no ``config_json``.
    with pool.transaction() as conn:
        conn.execute("DROP TABLE homemind_asset_jobs")
        conn.execute(
            "CREATE TABLE homemind_asset_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL UNIQUE, "
            "family_id TEXT NOT NULL, source_id TEXT, job_type TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'PENDING', "
            "cursor_json TEXT NOT NULL DEFAULT '{}', "
            "total_items INTEGER NOT NULL DEFAULT 0, "
            "processed_items INTEGER NOT NULL DEFAULT 0, "
            "succeeded_items INTEGER NOT NULL DEFAULT 0, "
            "skipped_items INTEGER NOT NULL DEFAULT 0, "
            "failed_items INTEGER NOT NULL DEFAULT 0, error_summary TEXT, "
            "requested_by INTEGER NOT NULL, created_at INTEGER NOT NULL, "
            "started_at INTEGER, updated_at INTEGER NOT NULL, finished_at INTEGER, "
            "lease_owner TEXT, lease_expires_at INTEGER)",
        )
    pool.close()

    reopened = SqlitePool(db_path)
    run_homemind_migrations(reopened)
    with reopened.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(homemind_asset_jobs)",
            ).fetchall()
        }
    assert "config_json" in columns
    # Idempotent: a second boot must not raise.
    run_homemind_migrations(reopened)
    reopened.close()


# ------------------------------------------------------------- the config


def test_config_round_trips_through_the_job_row(tmp_path: Path) -> None:
    pool, manager, job_repo, _ar, _f, user, _o, family_id = _bootstrap(tmp_path)
    config = AssetJobConfig(
        vision_provider_id=12,
        vision_model="qwen-vl",
        thumbnail_width=512,
    )
    job = manager.create_job(
        family_id,
        user,
        job_type="VISION",
        asset_ids=[],
        config=config,
    )
    reloaded = job_repo.get_job(job.id)
    assert reloaded is not None
    parsed = AssetJobConfig.from_json(reloaded.config_json)
    assert parsed.vision_provider_id == 12
    assert parsed.vision_model == "qwen-vl"
    assert parsed.thumbnail_width == 512
    pool.close()


def test_config_survives_a_restart(tmp_path: Path) -> None:
    """The whole point of persisting it: a new process reads it back."""
    pool, manager, _jr, _ar, family, user, _o, family_id = _bootstrap(tmp_path)
    job = manager.create_job(
        family_id,
        user,
        job_type="EMBEDDING",
        asset_ids=[],
        config=AssetJobConfig(embedding_provider_id=13, embedding_model="bge-m3"),
    )
    pool.close()

    reopened = SqlitePool(tmp_path / "octop.db")
    fresh = AssetJobRepo(reopened)
    reloaded = fresh.get_job(job.id)
    assert reloaded is not None
    parsed = AssetJobConfig.from_json(reloaded.config_json)
    assert parsed.embedding_provider_id == 13
    assert parsed.embedding_model == "bge-m3"
    reopened.close()
    assert family is not None


def test_config_rejects_credentials() -> None:
    """A key that looks like a secret must never reach the job table."""
    for payload in (
        {"vision_api_key": "sk-live-abc"},
        {"token": "abc"},
        {"embedding_provider_secret": "x"},
    ):
        with pytest.raises(HomeMindError):
            AssetJobConfig.from_json(json.dumps(payload))


def test_reject_secrets_allows_a_model_named_secret_model() -> None:
    """A model legitimately named like that must not be blocked.

    The check is on field names, not values, so a value containing the word
    "secret" is fine.
    """
    reject_secrets({"vision_model": "secret-model-v2"})
    config = AssetJobConfig(vision_provider_id=1, vision_model="secret-model-v2")
    assert config.vision_model == "secret-model-v2"


def test_config_rejects_unknown_version() -> None:
    with pytest.raises(HomeMindError):
        AssetJobConfig.from_json(json.dumps({"version": 99, "vision_model": "x"}))


def test_config_rejects_unknown_field() -> None:
    with pytest.raises(HomeMindError):
        AssetJobConfig.from_json(json.dumps({"vision_modle": "typo"}))


def test_config_rejects_malformed_payload() -> None:
    with pytest.raises(HomeMindError):
        AssetJobConfig.from_json("not json")
    with pytest.raises(HomeMindError):
        AssetJobConfig.from_json("[1, 2, 3]")


@pytest.mark.parametrize(
    ("job_type", "config", "ok"),
    [
        ("VISION", AssetJobConfig(), False),
        ("VISION", AssetJobConfig(vision_provider_id=1), False),
        ("VISION", AssetJobConfig(vision_model="m"), False),
        ("VISION", AssetJobConfig(vision_provider_id=1, vision_model="m"), True),
        ("EMBEDDING", AssetJobConfig(), False),
        (
            "EMBEDDING",
            AssetJobConfig(embedding_provider_id=1, embedding_model="m"),
            True,
        ),
        ("THUMBNAIL", AssetJobConfig(), True),
        ("REINDEX", AssetJobConfig(), True),
    ],
)
def test_job_type_requires_its_config(job_type: str, config: AssetJobConfig, *, ok: bool) -> None:
    if ok:
        validate_config_for_job_type(job_type, config)
    else:
        with pytest.raises(HomeMindError):
            validate_config_for_job_type(job_type, config)


def test_creating_a_vision_job_without_a_model_is_refused(tmp_path: Path) -> None:
    """Fail at creation, not hours later in a worker thread."""
    pool, manager, _jr, _ar, _f, user, _o, family_id = _bootstrap(tmp_path)
    with pytest.raises(HomeMindError):
        manager.create_job(family_id, user, job_type="VISION", asset_ids=[])
    pool.close()


# -------------------------------------------------------------- seeding


def test_asset_ids_are_resolved_to_registered_paths(tmp_path: Path) -> None:
    pool, manager, job_repo, asset_repo, _f, user, _o, family_id = _bootstrap(tmp_path)
    asset = _seed_asset(asset_repo, family_id, tmp_path)
    job = manager.create_job(
        family_id,
        user,
        job_type="THUMBNAIL",
        asset_ids=[asset.id],
    )
    assert job.total_items == 1
    item = job_repo.list_items(job.id)[0]
    assert item.asset_id == asset.id
    assert item.source_path == asset.uri
    pool.close()


def test_cross_family_asset_id_is_refused(tmp_path: Path) -> None:
    """An id from another family must not become a work item."""
    pool, manager, _jr, asset_repo, family, user, _o, family_id = _bootstrap(tmp_path)
    other_family = family.create_family(
        user,
        name="Other",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    foreign = _seed_asset(asset_repo, other_family.id, tmp_path, name="foreign.jpg")
    with pytest.raises(HomeMindError):
        manager.create_job(
            family_id,
            user,
            job_type="THUMBNAIL",
            asset_ids=[foreign.id],
        )
    pool.close()


def test_unknown_asset_id_is_refused(tmp_path: Path) -> None:
    pool, manager, _jr, _ar, _f, user, _o, family_id = _bootstrap(tmp_path)
    with pytest.raises(HomeMindError):
        manager.create_job(
            family_id,
            user,
            job_type="REINDEX",
            asset_ids=["asset_missing"],
        )
    pool.close()


def test_plain_member_cannot_create_a_job(tmp_path: Path) -> None:
    """Queueing writes rows and indexes files: manager-only, in the domain.

    Previously this was enforced only in the router, so any other caller of
    the manager got access-level access instead of manager-level.
    """
    pool, manager, _jr, _ar, family, user, other, family_id = _bootstrap(tmp_path)
    family.create_member(
        family_id,
        user,
        display_name="Sibling",
        role=MemberRole.MEMBER,
        user_id=other.id,
    )
    with pytest.raises(OctopError) as excinfo:
        manager.create_job(family_id, other, job_type="SCAN", paths=["/photos/a.jpg"])
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    pool.close()


# ------------------------------------------------------------ empty jobs


def test_empty_asset_job_completes_immediately(tmp_path: Path) -> None:
    """Zero items is a valid answer, not a job that hangs forever.

    A REINDEX over a family with no assets must report COMPLETED at 100%
    so the dashboard does not show a spinner that never resolves.
    """
    pool, manager, _jr, _ar, _f, user, _o, family_id = _bootstrap(tmp_path)
    job = manager.create_job(family_id, user, job_type="REINDEX", asset_ids=[])
    assert job.status == JOB_STATUS_COMPLETED
    assert job.total_items == 0
    assert job.finished_at is not None
    summary = AssetJobSummary.from_row(job)
    assert summary.progress_percent() == 100.0
    pool.close()


def test_a_pending_job_with_no_items_is_not_reported_as_done() -> None:
    """Only a *completed* zero-item job is 100%."""
    summary = AssetJobSummary(
        job_id="j",
        family_id="f",
        job_type="SCAN",
        status="PENDING",
        total_items=0,
        processed_items=0,
        succeeded_items=0,
        skipped_items=0,
        failed_items=0,
        error_summary=None,
    )
    assert summary.progress_percent() == 0.0


# --------------------------------------------------------- request model


def test_create_body_converts_to_a_validated_domain_config() -> None:
    """The request model must funnel through the domain model.

    It shares the field names but not the validation, so the conversion is
    what makes a credential impossible to persist.
    """
    from homemind.api.routers.asset_jobs import AssetJobConfigBody  # noqa: PLC0415

    body = AssetJobConfigBody(vision_provider_id=12, vision_model="qwen-vl")
    config = body.to_domain()
    assert config.vision_provider_id == 12
    assert config.vision_model == "qwen-vl"


def test_create_body_extra_field_is_rejected() -> None:
    """``extra="forbid"`` on the domain model is what stops a typo'd
    ``vision_api_key`` from being silently dropped or stored."""
    from pydantic import ValidationError  # noqa: PLC0415

    from homemind.api.routers.asset_jobs import AssetJobConfigBody  # noqa: PLC0415

    with pytest.raises(ValidationError):
        AssetJobConfigBody.model_validate(
            {"vision_provider_id": 1, "vision_api_key": "sk-live"},
        )
