from __future__ import annotations

from pathlib import Path

from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.families import FamilyRepo
from octop.infra.db.repos.family_assets import FamilyAssetRepo
from octop.infra.family.assets import FamilyAssetManager
from octop.infra.family.manager import FamilyManager
from octop.infra.users.identity import Role, User


def _repos(tmp_path: Path) -> tuple[FamilyRepo, FamilyAssetRepo]:
    pool = SqlitePool(tmp_path / "octop.db")
    with pool.connect() as conn:
        root = Path(__file__).resolve().parents[3]
        conn.executescript(
            (root / "src/octop/infra/db/migrations/001_initial.sql").read_text(encoding="utf-8")
        )
        conn.executescript(
            (root / "src/octop/infra/db/migrations/020_family_foundation.sql").read_text(
                encoding="utf-8"
            )
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
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
    image.write_bytes(b"not a real jpeg")
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
    assert metadata.width is None

    second_scan = manager.scan_directory(family.id, user, directory=str(source))
    assert set(second_scan.asset_ids) == {asset.id for asset in assets}
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
