from __future__ import annotations

from pathlib import Path

from PIL import Image

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import (
    DetectedFace,
    PhotoIntelligenceManager,
    VisionResult,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class FakeVision:
    name = "fake-vision"

    def __init__(self, member_id: str) -> None:
        self.member_id = member_id

    def analyze(self, image_path: Path) -> VisionResult:
        assert image_path.is_file()
        return VisionResult(
            "全家人在樱花树下",
            ["人物", "樱花"],
            ["户外"],
            [DetectedFace(0.98, self.member_id, "家庭成员")],
        )


class FakeEmbedding:
    name = "fake-embedding"

    def embed_image(self, image_path: Path) -> list[float]:
        return [1.0, 0.0] if image_path.name.startswith("kyoto") else [0.0, 1.0]

    def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0] if "京都" in text else [0.0, 1.0]


class FakeGeocoder:
    name = "fake-geocoder"

    def reverse(self, latitude: float, longitude: float) -> str:
        return "日本京都"


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
    member = families.repo.list_members(family.id)[0]
    assets = FamilyAssetManager(services.family_repo, services.family_asset_repo)
    manager = PhotoIntelligenceManager(
        families,
        assets,
        services.family_context_repo,
        services.photo_intelligence_repo,
    )
    return services, family, member, user, assets, manager


def test_provider_analysis_embedding_search_and_event_link(tmp_path: Path) -> None:
    services, family, member, user, assets, manager = _setup(tmp_path)
    source = tmp_path / "photos"
    source.mkdir()
    image_path = source / "kyoto.jpg"
    Image.new("RGB", (16, 16), "pink").save(image_path)
    scan = assets.scan_directory(family.id, user, directory=str(source))
    asset = assets.get(family.id, scan.asset_ids[0], user)
    assert asset.captured_at is not None
    context = FamilyContextManager(
        FamilyManager(services.family_repo), services.family_context_repo
    )
    event = context.create_event(
        family.id,
        user,
        event_type="TRIP",
        title="京都旅行",
        start_at=asset.captured_at - 10,
        end_at=asset.captured_at + 10,
        location=None,
        description="",
        metadata={},
    )

    analyzed = manager.analyze(
        family.id,
        asset.id,
        user,
        vision=FakeVision(member.id),
        embedding=FakeEmbedding(),
        geocoder=FakeGeocoder(),
    )

    assert analyzed.description == "全家人在樱花树下"
    assert analyzed.perceptual_hash is not None
    assert analyzed.embedding_provider == "fake-embedding"
    assert services.family_context_repo.list_event_ids_for_asset(asset.id) == [event.id]
    results = manager.search(
        family.id, user, query="京都全家福", embedding=FakeEmbedding()
    )
    assert results[0].asset_id == asset.id
    assert results[0].score == 1.0


def test_perceptual_hash_finds_near_duplicate_images(tmp_path: Path) -> None:
    _, family, _, user, assets, manager = _setup(tmp_path)
    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (16, 16), "red").save(source / "one.png")
    Image.new("RGB", (32, 32), "red").save(source / "two.png")
    scan = assets.scan_directory(family.id, user, directory=str(source))
    for asset_id in scan.asset_ids:
        manager.analyze(family.id, asset_id, user)

    matches = manager.similar(family.id, scan.asset_ids[0], user, max_distance=0)

    assert matches == [(scan.asset_ids[1], 0)]
