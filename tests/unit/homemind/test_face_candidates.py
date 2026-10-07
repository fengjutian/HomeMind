"""Stage C4 acceptance tests: FACE_MATCH as a review queue, never a label.

The design point these tests defend: a face-recognition confidence is a
probability, and turning one into "this is your son" without a human
looking at it is the failure this whole queue exists to prevent. So every
match is stored as ``PENDING``, a manager decides, and a rejected match is
never re-offered.

Nothing here stores or compares biometric data — only a member id, a
confidence, and who decided.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.asset_jobs import AssetJobRepo
from homemind.infra.db.repos.face_candidates import (
    CANDIDATE_CONFIRMED,
    CANDIDATE_PENDING,
    CANDIDATE_REJECTED,
    FaceCandidateRepo,
)
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.asset_job_config import AssetJobConfig
from homemind.infra.family.asset_job_handlers import build_handler
from homemind.infra.family.asset_jobs import AssetJobManager
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.photo_intelligence import (
    DetectedFace,
    FaceReference,
    PhotoIntelligenceManager,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import OctopError
from octop.infra.users.identity import Role, User


class _FakeFaceProvider:
    """Returns a fixed face list and counts calls."""

    name = "fake/face"

    def __init__(self, faces: list[DetectedFace]) -> None:
        self._faces = faces
        self.calls = 0

    def recognize(self, image_path: Path, references: list[FaceReference]) -> list[DetectedFace]:
        self.calls += 1
        return list(self._faces)


class _Ctx:
    def __init__(self, tmp_path: Path) -> None:
        self.workdir = tmp_path
        pool = SqlitePool(tmp_path / "octop.db")
        run_migrations(pool)
        run_homemind_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
                "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
                "(9, 'teen', 'x', 'user', 0, 'zh', 1)"
            )
        self.owner = User(id=1, username="papa", role=Role.USER, display_name="papa")
        self.sibling = User(id=2, username="mama", role=Role.USER, display_name="mama")
        family = FamilyManager(FamilyRepo(pool))
        fam = family.create_family(self.owner, name="Happy", timezone="Asia/Shanghai", locale="zh")
        self.family = family
        self.family_id = fam.id
        self.member = family.create_member(
            fam.id,
            self.owner,
            display_name="Kid",
            role=MemberRole.MEMBER,
        )
        self.job_repo = AssetJobRepo(pool)
        self.asset_repo = FamilyAssetRepo(pool)
        self.candidates = FaceCandidateRepo(pool)
        self.manager = AssetJobManager(
            family, self.job_repo, asset_repo=self.asset_repo, batch_size=10
        )
        self.pool = pool
        self.intelligence: PhotoIntelligenceManager | None = None

    def intelligence_with(self, provider: Any) -> PhotoIntelligenceManager:
        from homemind.infra.db.repos.family_context import FamilyContextRepo
        from homemind.infra.db.repos.photo_intelligence import PhotoIntelligenceRepo

        self.intelligence = PhotoIntelligenceManager(
            self.family,
            FamilyAssetManager(self.family.repo, self.asset_repo),
            FamilyContextRepo(self.pool),
            PhotoIntelligenceRepo(self.pool),
            candidates=self.candidates,
            face_provider=provider,
        )
        return self.intelligence

    def seed_photo(self, name: str) -> Any:
        from PIL import Image

        path = self.workdir / name
        Image.new("RGB", (32, 32), (5, 5, 5)).save(path, "JPEG")
        return self.asset_repo.upsert_asset(
            family_id=self.family_id,
            source_id=None,
            space_id=None,
            asset_type="PHOTO",
            name=name,
            uri=path.resolve().as_uri(),
            mime_type="image/jpeg",
            size_bytes=path.stat().st_size,
            content_hash=f"hash-{name}",
            captured_at=None,
            metadata_json="{}",
            created_by=1,
            visibility="FAMILY",
        )

    def add_reference(self, asset: Any) -> None:
        self.candidates_photo_intelligence().set_face_reference(
            self.family_id,
            self.member.id,
            asset.id,
            self.owner,
        )

    def candidates_photo_intelligence(self) -> PhotoIntelligenceManager:
        assert self.intelligence is not None, "call intelligence_with() first"
        return self.intelligence

    def run_face_match(self, asset: Any) -> Any:
        job = self.manager.create_job(
            self.family_id,
            self.owner,
            job_type="FACE_MATCH",
            asset_ids=[asset.id],
            config=AssetJobConfig(),
        )
        handler = build_handler(
            self.job_repo.get_job(job.id),
            asset_manager=object(),
            asset_repo=self.asset_repo,
            face_manager=self.candidates_photo_intelligence(),
            user=self.owner,
            family_id=self.family_id,
            created_by_user_id=self.owner.id,
        )
        return self.manager.run_job(job.id, handler)

    def close(self) -> None:
        self.pool.close()


# ========================================================== the queue


def test_a_match_becomes_a_pending_candidate(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    provider = _FakeFaceProvider([DetectedFace(confidence=0.99, member_id=ctx.member.id)])
    ctx.intelligence_with(provider)
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    summary = ctx.run_face_match(asset)
    assert summary.succeeded_items == 1
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    assert len(pending) == 1
    assert pending[0].member_id == ctx.member.id
    assert pending[0].confidence == pytest.approx(0.99)
    # The whole point: even 0.99 confidence is not a label.
    assert ctx.candidates.confirmed_member_for(ctx.family_id, asset.id) is None
    ctx.close()


def test_a_manager_can_confirm_a_candidate(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    confirmed = intelligence.confirm_face_candidate(
        ctx.family_id,
        pending[0].id,
        ctx.owner,
    )
    assert confirmed.status == CANDIDATE_CONFIRMED
    assert confirmed.decided_by == ctx.owner.id
    assert confirmed.decided_at is not None
    assert ctx.candidates.confirmed_member_for(ctx.family_id, asset.id) == ctx.member.id
    ctx.close()


def test_a_manager_can_reject_a_candidate(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    rejected = intelligence.reject_face_candidate(
        ctx.family_id,
        pending[0].id,
        ctx.owner,
    )
    assert rejected.status == CANDIDATE_REJECTED
    assert ctx.candidates.confirmed_member_for(ctx.family_id, asset.id) is None
    ctx.close()


def test_a_rejected_candidate_is_not_resurrected_by_a_rerun(tmp_path: Path) -> None:
    """Re-running the job must not reopen a decision a manager already made."""
    ctx = _Ctx(tmp_path)
    provider = _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)])
    intelligence = ctx.intelligence_with(provider)
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    intelligence.reject_face_candidate(ctx.family_id, pending[0].id, ctx.owner)

    ctx.run_face_match(asset)
    assert ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING) == []
    rows = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_REJECTED)
    assert len(rows) == 1
    ctx.close()


def test_rerunning_does_not_duplicate_a_pending_candidate(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    ctx.run_face_match(asset)
    rows = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    assert len(rows) == 1
    ctx.close()


def test_a_decided_candidate_cannot_be_decided_again(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    intelligence.reject_face_candidate(ctx.family_id, pending[0].id, ctx.owner)
    with pytest.raises(HomeMindError):
        intelligence.confirm_face_candidate(ctx.family_id, pending[0].id, ctx.owner)
    ctx.close()


def test_an_unlabelled_detection_creates_no_candidate(tmp_path: Path) -> None:
    """A face with no member id is a detection, not a suggestion about
    someone; there is nothing for a manager to decide."""
    ctx = _Ctx(tmp_path)
    ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.9, member_id=None)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    summary = ctx.run_face_match(asset)
    assert summary.succeeded_items == 1
    assert ctx.candidates.list_candidates(ctx.family_id) == []
    ctx.close()


def test_a_candidate_cannot_be_decided_from_another_family(tmp_path: Path) -> None:
    """Candidate ids are family-scoped: family B cannot confirm family A's."""
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    candidate = ctx.candidates.list_candidates(ctx.family_id)[0]
    other = ctx.family.create_family(
        ctx.owner,
        name="Other",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    with pytest.raises(HomeMindError):
        intelligence.confirm_face_candidate(other.id, candidate.id, ctx.owner)
    ctx.close()


# ============================================================ no reference


def test_a_family_with_no_reference_photo_produces_nothing(tmp_path: Path) -> None:
    """Nothing to match against is a skip, not a failure."""
    ctx = _Ctx(tmp_path)
    provider = _FakeFaceProvider([DetectedFace(confidence=0.9, member_id=ctx.member.id)])
    intelligence = ctx.intelligence_with(provider)
    asset = ctx.seed_photo("a.jpg")
    # No reference registered.
    assert intelligence.match_faces(ctx.family_id, asset.id, ctx.owner) == []
    assert provider.calls == 0
    ctx.close()


# ============================================================ permissions


def test_a_plain_member_cannot_confirm_a_candidate(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    plain = ctx.family.create_member(
        ctx.family_id,
        ctx.owner,
        display_name="Teen",
        role=MemberRole.MEMBER,
    )
    del plain
    plain_user = User(id=9, username="teen", role=Role.USER, display_name="teen")
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    pending = ctx.candidates.list_candidates(ctx.family_id, status=CANDIDATE_PENDING)
    with pytest.raises(OctopError):
        intelligence.confirm_face_candidate(ctx.family_id, pending[0].id, plain_user)
    ctx.close()


def test_the_review_queue_is_readable_by_any_member(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    intelligence = ctx.intelligence_with(
        _FakeFaceProvider([DetectedFace(confidence=0.6, member_id=ctx.member.id)]),
    )
    plain_user = User(id=9, username="teen", role=Role.USER, display_name="teen")
    ctx.family.create_member(
        ctx.family_id,
        ctx.owner,
        display_name="Teen",
        role=MemberRole.MEMBER,
        user_id=9,
    )
    asset = ctx.seed_photo("a.jpg")
    ctx.add_reference(asset)
    ctx.run_face_match(asset)
    rows = intelligence.list_face_candidates(ctx.family_id, plain_user)
    assert len(rows) == 1
    ctx.close()
