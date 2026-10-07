"""Stage 12 wiring: the privacy guard must gate real outbound calls.

``ExternalProcessingGuard`` existing with passing unit tests proves
nothing about enforcement. These tests drive
``PhotoIntelligenceManager.analyze`` — the function that actually hands
a photo to a vision / embedding / geocoding provider — and assert the
provider is never reached when the family's settings forbid it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_hm
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import (
    PhotoIntelligenceManager,
    VisionResult,
)
from homemind.infra.family.privacy import (
    ExternalProcessingGuard,
    ProcessingMode,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _ExplodingVision:
    """A provider that must never be reached when the gate is closed."""

    name = "vision-provider"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, image_path: Path) -> VisionResult:
        self.calls += 1
        return VisionResult("should not happen", [], [], [])


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_hm(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    family = families.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    assets = FamilyAssetManager(services.family_repo, services.family_asset_repo)

    # One real photo so ``analyze`` gets past its asset-type check.
    photo = tmp_path / "a.jpg"
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not installed")
    Image.new("RGB", (32, 32), (10, 20, 30)).save(photo, "JPEG")
    asset = assets.repo.upsert_asset(
        family_id=family.id,
        source_id=None,
        space_id=None,
        asset_type="PHOTO",
        name=photo.name,
        uri=photo.resolve().as_uri(),
        mime_type="image/jpeg",
        size_bytes=photo.stat().st_size,
        content_hash="h",
        captured_at=None,
        metadata_json="{}",
        created_by=owner.id,
        visibility="FAMILY",
    )
    guard = ExternalProcessingGuard(services)
    photos = PhotoIntelligenceManager(
        families, assets, services.family_context_repo,
        services.photo_intelligence_repo, privacy_guard=guard,
    )
    return pool, services, guard, photos, owner, family.id, asset.id


def test_local_only_blocks_the_provider_before_it_runs(tmp_path: Path) -> None:
    pool, _services, guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.LOCAL_ONLY,
        allow_external_vision=True,
        allowed_provider_ids=["vision-provider"],
    )
    vision = _ExplodingVision()
    with pytest.raises(HomeMindError):
        photos.analyze(family_id, asset_id, owner, vision=vision)
    assert vision.calls == 0, "the provider must not be reached at all"
    pool.close()


def test_default_settings_block_external_vision(tmp_path: Path) -> None:
    """A family that never configured a policy must still be blocked."""

    pool, _services, _guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    vision = _ExplodingVision()
    with pytest.raises(HomeMindError):
        photos.analyze(family_id, asset_id, owner, vision=vision)
    assert vision.calls == 0
    pool.close()


def test_provider_outside_allow_list_is_blocked(tmp_path: Path) -> None:
    pool, _services, guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=["some-other-provider"],
    )
    vision = _ExplodingVision()
    with pytest.raises(HomeMindError):
        photos.analyze(family_id, asset_id, owner, vision=vision)
    assert vision.calls == 0
    pool.close()


def test_allowed_call_reaches_the_provider_and_is_audited(tmp_path: Path) -> None:
    pool, services, guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=["vision-provider"],
    )
    vision = _ExplodingVision()
    photos.analyze(family_id, asset_id, owner, vision=vision)
    assert vision.calls == 1

    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT operation, provider_id, data_categories, asset_id "
            "FROM homemind_external_processing_audit"
        ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["operation"] == "VISION"
    assert row["provider_id"] == "vision-provider"
    assert row["asset_id"] == asset_id
    assert "PHOTO" in str(row["data_categories"])
    pool.close()


def test_audit_row_contains_no_file_path(tmp_path: Path) -> None:
    """The audit records *what* left, never *which file on disk*."""

    pool, _services, guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=["vision-provider"],
    )
    photos.analyze(family_id, asset_id, owner, vision=_ExplodingVision())
    with pool.connect() as conn:
        blob = str([dict(r) for r in conn.execute(
            "SELECT * FROM homemind_external_processing_audit"
        ).fetchall()])
    assert "a.jpg" not in blob
    assert str(tmp_path) not in blob
    pool.close()


def test_missing_guard_still_allows_local_analysis(tmp_path: Path) -> None:
    """No guard means an unconfigured install keeps working — the gate
    is additive, not a hard dependency."""

    pool, services, _guard, photos, owner, family_id, asset_id = _bootstrap(tmp_path)
    photos.privacy_guard = None
    row = photos.analyze(family_id, asset_id, owner, vision=_ExplodingVision())
    assert row.asset_id == asset_id
    pool.close()
