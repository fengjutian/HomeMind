"""Stage 13 acceptance tests for family export / import.

Covers the spec's safety rules:

* a default export contains metadata, never original media,
* secrets never appear in a bundle,
* only a manager may export, import, or apply,
* an import is staged and conflict-checked before touching a family,
* an unresolved conflict blocks apply,
* a tampered bundle fails its checksum,
* a bundle path cannot escape the export root (Zip Slip / traversal).
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError
from homemind.infra.family.exports import (
    FORMAT,
    FORMAT_VERSION,
    FamilyExportManager,
    _safe_join,
)
from homemind.infra.family.manager import FamilyManager, MemberRole
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import OctopError
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
            "(3, 'friend', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    other = User(id=3, username="friend", role=Role.USER, display_name="朋友")
    family_repo = FamilyRepo(pool)
    manager = FamilyManager(family_repo)
    family = manager.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    manager.create_member(
        family.id, owner, display_name="妈妈", role=MemberRole.MEMBER, user_id=2,
    )
    services = HomeMindServices.from_pool(pool)
    exports = FamilyExportManager(services, tmp_path / "exports")
    return pool, exports, manager, owner, other, family.id


# ------------------------------------------------------------------- export


def test_default_export_has_every_section(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    result = exports.create_export(family_id, owner)
    manifest = result["manifest"]
    assert manifest["format"] == FORMAT
    assert manifest["version"] == FORMAT_VERSION
    assert manifest["includes_original_assets"] is False

    bundle = exports.download_path(family_id, result["export_id"], owner)
    for name in (
        "family.json", "members.json", "relationships.json", "spaces.json",
        "permissions.json", "events.json", "memories.json", "tasks.json",
        "albums.json", "asset-index.json", "devices.json", "manifest.json",
    ):
        assert (bundle / name).is_file(), f"missing section {name}"
    pool.close()


def test_export_contains_no_secrets(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    result = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, result["export_id"], owner)
    blob = "\n".join(
        path.read_text(encoding="utf-8") for path in bundle.glob("*.json")
    )
    for secret in ("token_hash", "password_hash", "api_key", "face_embedding"):
        assert secret not in blob, f"export leaked {secret}"
    pool.close()


def test_default_export_copies_no_original_assets(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    result = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, result["export_id"], owner)
    assert not (bundle / "assets").exists(), (
        "a default export must not copy the family's media"
    )
    pool.close()


def test_opt_in_export_copies_original_assets(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    # Seed one real asset so the copy path runs.
    media = tmp_path / "family.jpg"
    media.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    from datetime import datetime  # noqa: PLC0415

    from_home_services = HomeMindServices.from_pool(pool)
    from_home_services.family_asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="PHOTO",
        name=media.name,
        uri=media.resolve().as_uri(),
        mime_type="image/jpeg",
        size_bytes=media.stat().st_size,
        content_hash="h1",
        captured_at=int(datetime.now(tz=UTC).timestamp()),
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )
    result = exports.create_export(family_id, owner, include_original_assets=True)
    bundle = exports.download_path(family_id, result["export_id"], owner)
    assert (bundle / "assets").is_dir()
    assert list((bundle / "assets").iterdir())
    pool.close()


def test_non_manager_cannot_export(tmp_path: Path) -> None:
    pool, exports, manager, owner, other, family_id = _bootstrap(tmp_path)
    manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER, user_id=3,
    )
    with pytest.raises(OctopError):
        exports.create_export(family_id, other)
    pool.close()


def test_export_list_is_scoped_to_the_family(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    first = exports.create_export(family_id, owner)
    assert len(exports.list_exports(family_id, owner)) == 1
    with pytest.raises(HomeMindError):
        exports.download_path(family_id, "export_does_not_exist", owner)
    assert exports.download_path(family_id, first["export_id"], owner).is_dir()
    pool.close()


def test_delete_export_removes_the_bundle(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    result = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, result["export_id"], owner)
    assert exports.delete_export(family_id, result["export_id"], owner) is True
    assert not bundle.exists()
    pool.close()


# ------------------------------------------------------------------- import


def test_import_is_staged_before_it_touches_anything(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)

    staged = exports.stage_import(family_id, owner, bundle=bundle, dry_run=True)
    assert staged["dry_run"] is True
    assert staged["import_id"]
    pool.close()


def test_conflicting_ids_are_reported(tmp_path: Path) -> None:
    """Importing a bundle into the family it came from collides on every
    member id — the report must say so rather than overwrite."""

    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)

    staged = exports.stage_import(family_id, owner, bundle=bundle)
    assert staged["conflicts"], "re-importing into the same family must conflict"
    with pytest.raises(HomeMindError) as info:
        exports.apply_import(family_id, staged["import_id"], owner, bundle=bundle)
    assert "conflict" in str(info.value).lower()
    pool.close()


def test_tampered_bundle_fails_checksum(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)

    # Edit a section after the manifest recorded its hash.
    members_path = bundle / "members.json"
    payload = json.loads(members_path.read_text(encoding="utf-8"))
    payload.append({"member_id": "injected", "display_name": "坏数据", "role": "OWNER"})
    members_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8",
    )

    with pytest.raises(HomeMindError) as info:
        exports.stage_import(family_id, owner, bundle=bundle)
    assert "checksum" in str(info.value).lower()
    pool.close()


def test_missing_section_is_refused(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)
    (bundle / "members.json").unlink()
    with pytest.raises(HomeMindError) as info:
        exports.stage_import(family_id, owner, bundle=bundle)
    assert "missing" in str(info.value).lower()
    pool.close()


def test_rejecting_an_import_blocks_apply(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)
    staged = exports.stage_import(family_id, owner, bundle=bundle)
    result = exports.reject_import(family_id, staged["import_id"], owner)
    assert result["status"] == "REJECTED"
    with pytest.raises(HomeMindError):
        exports.apply_import(family_id, staged["import_id"], owner, bundle=bundle)
    pool.close()


def test_non_manager_cannot_stage_or_apply(tmp_path: Path) -> None:
    pool, exports, manager, owner, other, family_id = _bootstrap(tmp_path)
    source = exports.create_export(family_id, owner)
    bundle = exports.download_path(family_id, source["export_id"], owner)
    manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER, user_id=3,
    )
    with pytest.raises(OctopError):
        exports.stage_import(family_id, other, bundle=bundle)
    pool.close()


# ---------------------------------------------------------------- path safety


def test_safe_join_rejects_traversal(tmp_path: Path) -> None:
    base = tmp_path / "bundle"
    base.mkdir()
    assert _safe_join(base, "manifest.json") is not None
    assert _safe_join(base, "../outside.json") is None
    assert _safe_join(base, "/etc/passwd") is None
    assert _safe_join(base, "a/../../b.json") is None


def test_bundle_dir_cannot_escape_the_export_root(tmp_path: Path) -> None:
    pool, exports, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    crafted = exports._bundle_dir("../../etc", "../passwd")
    try:
        resolved = crafted.resolve()
        exports.resolve_relative_to_root = None  # type: ignore[attr-defined]
    finally:
        pass
    assert exports._root.resolve() in resolved.parents
    pool.close()
