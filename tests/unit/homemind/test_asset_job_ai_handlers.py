"""Stage C2 / C3 acceptance tests: VISION and EMBEDDING job handlers.

These are the only handlers that call out of the house, so the tests care
about three things above all else:

* **no call leaves without permission** - the family privacy guard is on
  the background path, not just the request path,
* **the provider is resolved per item** - a provider disabled after
  queueing must fail the item rather than silently doing nothing,
* **an external failure is one item's failure** - a 10k-photo run must not
  die because photo 4217 timed out.

The HTTP layer of the provider adapters is replaced, so no test opens a
socket, but the handler still goes through ``require_provider`` and the
real adapter constructor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import (
    ITEM_STATUS_FAILED,
    JOB_STATUS_COMPLETED,
    AssetJobRepo,
)
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.asset_job_config import AssetJobConfig
from homemind.infra.family.asset_job_handlers import build_handler
from homemind.infra.family.asset_jobs import AssetJobManager
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.photo_intelligence import (
    DetectedFace,
    EmbeddingProvider,
    PhotoIntelligenceManager,
    VisionProvider,
    VisionResult,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User

# ------------------------------------------------------------------ fakes


class _FakeVision(VisionProvider):
    """Returns a fixed result, or raises.

    Doubles as its own ``ProviderRow``: the handler wraps whatever the
    provider repo returns in a real adapter, and the tests point that
    constructor at this object, so no socket is ever opened.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.name = "fake/vision"
        self.calls = 0
        self._fail = fail
        self.kind = "openai"
        self.base_url = "https://example.invalid/v1"
        self.api_key = "not-a-real-key"
        self.enabled = 1

    def analyze(self, image_path: Path) -> VisionResult:
        self.calls += 1
        if self._fail:
            raise TimeoutError("vision provider timed out")
        return VisionResult(
            description="a family photo",
            objects=["cake"],
            scenes=["outdoors"],
            faces=[DetectedFace(confidence=0.4)],
        )


class _FakeEmbedding(EmbeddingProvider):
    def __init__(self, *, vector: list[float] | None = None) -> None:
        self.name = "fake/embedding"
        self.calls = 0
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self.kind = "openai"
        self.base_url = "https://example.invalid/v1"
        self.api_key = "not-a-real-key"
        self.enabled = 1

    def embed_image(self, image_path: Path) -> list[float]:
        self.calls += 1
        return list(self._vector)

    def embed_text(self, text: str) -> list[float]:
        return list(self._vector)


class _DisabledProvider:
    """A provider row that fails ``require_provider``."""

    def __init__(self) -> None:
        self.name = "disabled"
        self.kind = "openai"
        self.base_url = None
        self.api_key = None
        self.enabled = 0


class _StubProviderRepo:
    """Resolves provider ids to rows the handler will accept."""

    def __init__(self, vision_row: Any, embedding_row: Any) -> None:
        self._vision = vision_row
        self._embedding = embedding_row

    def get(self, provider_id: int) -> Any:
        if provider_id == 12:
            return self._vision
        if provider_id == 13:
            return self._embedding
        return None


class _RecordingGuard:
    """Privacy guard double that records what it was asked to authorise."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.authorised: list[dict[str, Any]] = []
        self.audited: list[dict[str, Any]] = []

    def authorize_external_call(self, family_id: str, user: Any, **kwargs: Any) -> Any:
        self.authorised.append(kwargs)
        return _Decision(allow=self.allow)

    def audit_call(self, family_id: str, user: Any, **kwargs: Any) -> None:
        self.audited.append(kwargs)


class _Decision:
    def __init__(self, *, allow: bool) -> None:
        self._allow = allow

    def require(self) -> None:
        if not self._allow:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_ACCESS_DENIED,
                "external processing is not allowed",
            )


class _AnalysisRow:
    """Stands in for a ``PhotoIntelligenceRow`` returned by analyze()."""

    def __init__(self, *, asset_id: str, embedding_json: str | None) -> None:
        self.asset_id = asset_id
        self.embedding_json = embedding_json


class _NullRepo:
    def list(self, family_id: str) -> list[Any]:
        return []


class _StubIntelligence:
    """Photo-intelligence double that records the providers it was handed."""

    def __init__(self, vision: Any, embedding: Any) -> None:
        self.vision = vision
        self.embedding = embedding
        self.calls: list[dict[str, Any]] = []
        self.repo: Any = _NullRepo()

    def analyze(self, family_id: str, asset_id: str, user: Any, **kwargs: Any) -> _AnalysisRow:
        self.calls.append(kwargs)
        # Exercise each provider exactly as the real manager would, so the
        # fakes' call counters reflect real attempts.
        if kwargs.get("vision") is not None:
            kwargs["vision"].analyze(Path("x"))
        vector = None
        if kwargs.get("embedding") is not None:
            vector = list(kwargs["embedding"].embed_image(Path("x")))
        return _AnalysisRow(
            asset_id=asset_id,
            embedding_json=json.dumps(vector) if vector is not None else None,
        )


def _patch_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the provider adapters at the fakes.

    The handler must still go through ``require_provider`` and the real
    constructor call, so a regression that skipped the provider-readiness
    check would still fail these tests.
    """
    from homemind.infra.family import asset_job_handlers as handlers

    monkeypatch.setattr(handlers, "OpenAICompatibleVisionProvider", lambda row, model: row)
    monkeypatch.setattr(handlers, "OpenAICompatibleEmbeddingProvider", lambda row, model: row)


class _FakeJobRow:
    def __init__(self, job_type: str) -> None:
        self.id = "job"
        self.family_id = "f"
        self.job_type = job_type
        self.status = "PENDING"
        self.config_json = "{}"
        self.cursor_json = "{}"


# -------------------------------------------------------------- bootstrap


class _Ctx:
    """Everything a handler test needs, in one bundle."""

    def __init__(self, tmp_path: Path, *, vision: Any, embedding: Any, guard: Any) -> None:
        pool = SqlitePool(tmp_path / "octop.db")
        run_migrations(pool)
        run_homemind_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1)"
            )
        self.user = User(id=1, username="papa", role=Role.USER, display_name="papa")
        family = FamilyManager(FamilyRepo(pool))
        fam = family.create_family(self.user, name="Happy", timezone="Asia/Shanghai", locale="zh")
        self.family = family
        self.family_id = fam.id
        self.job_repo = AssetJobRepo(pool)
        self.asset_repo = FamilyAssetRepo(pool)
        self.manager = AssetJobManager(
            family, self.job_repo, asset_repo=self.asset_repo, batch_size=10
        )
        self.vision = vision
        self.embedding = embedding
        self.guard = guard
        self.providers = _StubProviderRepo(vision, embedding)
        if guard is None:
            self.intelligence: Any = _StubIntelligence(vision, embedding)
        else:
            services = HomeMindServices.from_pool(pool)
            self.intelligence = PhotoIntelligenceManager(
                family,
                FamilyAssetManager(family.repo, self.asset_repo),
                services.family_context_repo,
                services.photo_intelligence_repo,
                privacy_guard=guard,
            )
        self.pool = pool

    def handler_for(self, job: Any) -> Any:
        """Build the real handler for an existing job row."""
        return build_handler(
            self.job_repo.get_job(job.id),
            asset_manager=object(),
            asset_repo=self.asset_repo,
            photo_intelligence=self.intelligence,
            provider_repo=self.providers,
            user=self.user,
            family_id=self.family_id,
            created_by_user_id=self.user.id,
        )

    def run(self, job_type: str, asset_ids: list[str], **config: Any) -> tuple[Any, Any]:
        """Create one job, build its real handler, drain it, return both."""
        job = self.manager.create_job(
            self.family_id,
            self.user,
            job_type=job_type,
            asset_ids=asset_ids,
            config=AssetJobConfig(**config) if config else None,
        )
        return job, self.manager.run_job(job.id, self.handler_for(job))

    def close(self) -> None:
        self.pool.close()


def _seed_photo(asset_repo: FamilyAssetRepo, family_id: str, tmp_path: Path, name: str) -> Any:
    from PIL import Image

    source = tmp_path / name
    Image.new("RGB", (32, 32), (10, 20, 30)).save(source, "JPEG")
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


def _seed_video(asset_repo: FamilyAssetRepo, family_id: str, tmp_path: Path) -> Any:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    return asset_repo.upsert_asset(
        family_id=family_id,
        source_id=None,
        space_id=None,
        asset_type="VIDEO",
        name="clip.mp4",
        uri=clip.resolve().as_uri(),
        mime_type="video/mp4",
        size_bytes=clip.stat().st_size,
        content_hash="hash-clip",
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )


_VISION_CONFIG = {"vision_provider_id": 12, "vision_model": "vision-model"}
_EMBEDDING_CONFIG = {"embedding_provider_id": 13, "embedding_model": "embedding-model"}


# ================================================================ VISION


def test_vision_job_describes_a_photo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adapters(monkeypatch)
    vision = _FakeVision()
    ctx = _Ctx(tmp_path, vision=vision, embedding=_FakeEmbedding(), guard=None)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    job, summary = ctx.run("VISION", [asset.id], **_VISION_CONFIG)
    assert summary.status == JOB_STATUS_COMPLETED
    assert summary.succeeded_items == 1
    assert vision.calls == 1
    assert ctx.intelligence.calls[0]["vision"] is vision
    ctx.close()


def test_vision_job_fails_the_item_when_the_provider_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider failure is one item's failure, not the whole batch."""
    _patch_adapters(monkeypatch)
    vision = _FakeVision(fail=True)
    ctx = _Ctx(tmp_path, vision=vision, embedding=_FakeEmbedding(), guard=None)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    job, summary = ctx.run("VISION", [asset.id], **_VISION_CONFIG)
    assert summary.failed_items == 1
    failed = ctx.job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert "timed out" in (failed[0].error or "")
    ctx.close()


def test_vision_job_fails_when_the_provider_was_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider deleted after queueing must not silently no-op."""
    _patch_adapters(monkeypatch)
    ctx = _Ctx(tmp_path, vision=_DisabledProvider(), embedding=_FakeEmbedding(), guard=None)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    job, summary = ctx.run("VISION", [asset.id], **_VISION_CONFIG)
    assert summary.failed_items == 1
    failed = ctx.job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert "provider is not ready" in (failed[0].error or "")
    ctx.close()


def test_vision_job_skips_a_non_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adapters(monkeypatch)
    vision = _FakeVision()
    ctx = _Ctx(tmp_path, vision=vision, embedding=_FakeEmbedding(), guard=None)
    clip = _seed_video(ctx.asset_repo, ctx.family_id, tmp_path)
    _, summary = ctx.run("VISION", [clip.id], **_VISION_CONFIG)
    assert summary.skipped_items == 1
    assert vision.calls == 0, "a skipped item must not call the provider"
    ctx.close()


def test_vision_handler_requires_a_user() -> None:
    from homemind.infra.db.repos.asset_jobs import JOB_TYPE_VISION

    with pytest.raises(NotImplementedError):
        build_handler(
            _FakeJobRow(JOB_TYPE_VISION),
            asset_manager=object(),
            asset_repo=object(),
            photo_intelligence=object(),
            provider_repo=object(),
            user=None,
            family_id="f",
            created_by_user_id=1,
        )


def test_vision_handler_requires_the_intelligence_service() -> None:
    from homemind.infra.db.repos.asset_jobs import JOB_TYPE_VISION

    with pytest.raises(NotImplementedError):
        build_handler(
            _FakeJobRow(JOB_TYPE_VISION),
            asset_manager=object(),
            asset_repo=object(),
            photo_intelligence=None,
            provider_repo=None,
            user=User(id=1, username="papa", role=Role.USER, display_name="papa"),
            family_id="f",
            created_by_user_id=1,
        )


# ============================================================= EMBEDDING


def test_embedding_job_stores_a_vector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adapters(monkeypatch)
    embedding = _FakeEmbedding(vector=[0.1, 0.2, 0.3])
    ctx = _Ctx(tmp_path, vision=_FakeVision(), embedding=embedding, guard=None)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    _, summary = ctx.run("EMBEDDING", [asset.id], **_EMBEDDING_CONFIG)
    assert summary.succeeded_items == 1
    assert embedding.calls == 1
    assert ctx.intelligence.calls[0]["embedding"] is embedding
    ctx.close()


def test_embedding_job_fails_on_an_empty_vector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty vector is a provider bug and must not be stored as success."""
    _patch_adapters(monkeypatch)
    ctx = _Ctx(tmp_path, vision=_FakeVision(), embedding=_FakeEmbedding(vector=[]), guard=None)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    _, summary = ctx.run("EMBEDDING", [asset.id], **_EMBEDDING_CONFIG)
    assert summary.failed_items == 1
    ctx.close()


def test_embedding_job_fails_when_the_model_changes_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixing vector sizes would corrupt every neighbour score."""
    _patch_adapters(monkeypatch)
    ctx = _Ctx(
        tmp_path,
        vision=_FakeVision(),
        embedding=_FakeEmbedding(vector=[0.1, 0.2, 0.3]),
        guard=None,
    )

    class _MixedRepo:
        def list(self, family_id: str) -> list[Any]:
            return [_AnalysisRow(asset_id="other", embedding_json=json.dumps([0.1, 0.2]))]

    ctx.intelligence.repo = _MixedRepo()
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    job, summary = ctx.run("EMBEDDING", [asset.id], **_EMBEDDING_CONFIG)
    assert summary.failed_items == 1
    failed = ctx.job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert "dimension changed" in (failed[0].error or "")
    ctx.close()


def test_embedding_job_needs_its_config(tmp_path: Path) -> None:
    """Stage B refuses a config-less EMBEDDING job at creation time."""
    ctx = _Ctx(tmp_path, vision=_FakeVision(), embedding=_FakeEmbedding(), guard=None)
    with pytest.raises(HomeMindError):
        ctx.manager.create_job(ctx.family_id, ctx.user, job_type="EMBEDDING", asset_ids=[])
    ctx.close()


# =============================================================== privacy


def test_vision_job_is_blocked_when_external_processing_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The family said no outbound photos: nothing may leave the house."""
    _patch_adapters(monkeypatch)
    vision = _FakeVision()
    guard = _RecordingGuard(allow=False)
    ctx = _Ctx(tmp_path, vision=vision, embedding=_FakeEmbedding(), guard=guard)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    job, summary = ctx.run("VISION", [asset.id], **_VISION_CONFIG)
    assert summary.failed_items == 1
    assert vision.calls == 0, "a denied call must not reach the provider"
    assert guard.authorised, "the guard must be consulted before the call"
    assert guard.audited == [], "a blocked call must not be audited as done"
    failed = ctx.job_repo.list_items(job.id, status=ITEM_STATUS_FAILED)
    assert "external processing" in (failed[0].error or "")
    ctx.close()


def test_vision_job_audits_the_outbound_call_when_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data that actually left the house must leave a record."""
    _patch_adapters(monkeypatch)
    vision = _FakeVision()
    guard = _RecordingGuard(allow=True)
    ctx = _Ctx(tmp_path, vision=vision, embedding=_FakeEmbedding(), guard=guard)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    _, summary = ctx.run("VISION", [asset.id], **_VISION_CONFIG)
    assert summary.succeeded_items == 1
    assert guard.audited, "an allowed outbound call must be audited"
    recorded = guard.audited[0]
    assert recorded["operation"] == "VISION"
    assert recorded["asset_id"] == asset.id
    ctx.close()


def test_embedding_job_is_blocked_when_external_processing_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_adapters(monkeypatch)
    embedding = _FakeEmbedding()
    guard = _RecordingGuard(allow=False)
    ctx = _Ctx(tmp_path, vision=_FakeVision(), embedding=embedding, guard=guard)
    asset = _seed_photo(ctx.asset_repo, ctx.family_id, tmp_path, "a.jpg")
    _, summary = ctx.run("EMBEDDING", [asset.id], **_EMBEDDING_CONFIG)
    assert summary.failed_items == 1
    assert embedding.calls == 0
    ctx.close()
