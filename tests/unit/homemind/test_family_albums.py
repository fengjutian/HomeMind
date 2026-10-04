from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.albums import FamilyAlbumManager, OrganizationStrategy
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _setup(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    user = User(1, "owner", Role.USER, "Owner")
    families = FamilyManager(services.family_repo)
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    asset_manager = FamilyAssetManager(services.family_repo, services.family_asset_repo)
    album_manager = FamilyAlbumManager(families, asset_manager, services.family_album_repo)
    return family, user, asset_manager, album_manager


def test_create_album_and_manage_assets(tmp_path: Path) -> None:
    family, user, assets, albums = _setup(tmp_path)
    source = tmp_path / "photos"
    source.mkdir()
    (source / "one.jpg").write_bytes(b"photo")
    scanned = assets.scan_directory(family.id, user, directory=str(source))
    album = albums.create(family.id, user, name="旅行", description="家庭旅行")

    albums.add_asset(family.id, album.id, scanned.asset_ids[0], user)

    assert albums.asset_ids(family.id, album.id, user) == [scanned.asset_ids[0]]
    assert albums.remove_asset(family.id, album.id, scanned.asset_ids[0], user)
    assert albums.asset_ids(family.id, album.id, user) == []


def test_time_plan_is_previewed_then_applied_without_moving_files(tmp_path: Path) -> None:
    family, user, assets, albums = _setup(tmp_path)
    source = tmp_path / "photos"
    source.mkdir()
    photo = source / "kyoto.jpg"
    photo.write_bytes(b"photo")
    timestamp = int(datetime(2025, 4, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    os.utime(photo, (timestamp, timestamp))
    scanned = assets.scan_directory(family.id, user, directory=str(source))

    plan = albums.plan(family.id, user, OrganizationStrategy.TIME_MONTH)

    groups = json.loads(plan.groups_json)
    assert groups == [
        {
            "asset_ids": scanned.asset_ids,
            "name": "2025-04",
            "reason": "captured_at month",
        }
    ]
    assert albums.list(family.id, user) == []
    applied = albums.apply_plan(family.id, plan.id, user)
    assert applied.status == "APPLIED"
    album = albums.list(family.id, user)[0]
    assert albums.asset_ids(family.id, album.id, user) == scanned.asset_ids
    assert photo.read_bytes() == b"photo"


def test_exact_duplicate_plan_groups_matching_hashes(tmp_path: Path) -> None:
    family, user, assets, albums = _setup(tmp_path)
    source = tmp_path / "photos"
    source.mkdir()
    (source / "one.jpg").write_bytes(b"same")
    (source / "two.jpg").write_bytes(b"same")
    scanned = assets.scan_directory(family.id, user, directory=str(source))

    plan = albums.plan(family.id, user, OrganizationStrategy.EXACT_DUPLICATES)

    groups = json.loads(plan.groups_json)
    assert len(groups) == 1
    assert set(groups[0]["asset_ids"]) == set(scanned.asset_ids)
    assert groups[0]["reason"] == "identical SHA-256"
