from __future__ import annotations

from pathlib import Path

from PIL import Image

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager, MemberRole, SpaceType
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _repos(tmp_path: Path) -> tuple[FamilyRepo, FamilyAssetRepo]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'child', 'x', 'user', 0, 'zh', 1), "
            "(3, 'other', 'x', 'user', 0, 'zh', 1)"
        )
    return FamilyRepo(pool), FamilyAssetRepo(pool)


def test_scan_search_duplicates_and_delete_index(tmp_path: Path) -> None:
    family_repo, asset_repo = _repos(tmp_path)
    user = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = FamilyManager(family_repo).create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    source = tmp_path / "photos"
    source.mkdir()
    first = source / "first.txt"
    second = source / "second.txt"
    image = source / "photo.jpg"
    first.write_text("same content", encoding="utf-8")
    second.write_text("same content", encoding="utf-8")
    Image.new("RGB", (4, 3), color="red").save(image, format="JPEG")
    manager = FamilyAssetManager(family_repo, asset_repo)

    result = manager.scan_directory(family.id, user, directory=str(source))

    assert (result.indexed, result.failed) == (3, 0)
    assets = manager.search(family.id, user)
    assert len(assets) == 3
    assert {asset.asset_type for asset in assets} == {"DOCUMENT", "PHOTO"}
    assert len(manager.duplicate_groups(family.id, user)) == 1
    photo = next(asset for asset in assets if asset.asset_type == "PHOTO")
    metadata = manager.photo_metadata(family.id, photo.id, user)
    assert metadata is not None
    assert (metadata.width, metadata.height) == (4, 3)

    second_scan = manager.scan_directory(family.id, user, directory=str(source))
    assert second_scan.source_id == result.source_id
    assert (second_scan.indexed, second_scan.unchanged) == (0, 3)
    first.unlink()
    missing_scan = manager.scan_source(family.id, result.source_id, user)
    assert missing_scan.missing == 1
    assert len(manager.search(family.id, user)) == 2
    assert len(manager.search(family.id, user, status="MISSING")) == 1
    manager.delete_index(family.id, photo.id, user)
    assert image.exists()
    assert asset_repo.get(photo.id) is None


def test_non_recursive_scan_ignores_nested_files(tmp_path: Path) -> None:
    family_repo, asset_repo = _repos(tmp_path)
    user = User(id=1, username="owner", role=Role.USER, display_name=None)
    family = FamilyManager(family_repo).create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    source = tmp_path / "files"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "top.txt").write_text("top", encoding="utf-8")
    (nested / "nested.txt").write_text("nested", encoding="utf-8")

    result = FamilyAssetManager(family_repo, asset_repo).scan_directory(
        family.id, user, directory=str(source), recursive=False
    )

    assert result.indexed == 1


def test_private_assets_are_visible_only_to_space_owner(tmp_path: Path) -> None:
    family_repo, asset_repo = _repos(tmp_path)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    child_user = User(id=2, username="child", role=Role.USER, display_name="Child")
    other_user = User(id=3, username="other", role=Role.USER, display_name="Other")
    family_manager = FamilyManager(family_repo)
    family = family_manager.create_family(
        owner, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    child = family_manager.create_member(
        family.id,
        owner,
        display_name="Child",
        role=MemberRole.CHILD,
        user_id=child_user.id,
    )
    family_manager.create_member(
        family.id,
        owner,
        display_name="Other",
        role=MemberRole.MEMBER,
        user_id=other_user.id,
    )
    private_space = family_manager.create_space(
        family.id,
        owner,
        name="Child private",
        space_type=SpaceType.PRIVATE,
        owner_member_id=child.id,
    )
    source = tmp_path / "private"
    source.mkdir()
    (source / "secret.txt").write_text("secret", encoding="utf-8")
    assets = FamilyAssetManager(family_repo, asset_repo)
    assets.scan_directory(
        family.id,
        owner,
        directory=str(source),
        space_id=private_space.id,
        visibility="PRIVATE",
    )

    assert len(assets.search(family.id, child_user)) == 1
    assert assets.search(family.id, other_user) == []
