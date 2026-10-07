"""HomeMind repository container, separate from Octop SharedServices."""

from __future__ import annotations

from dataclasses import dataclass

from homemind.infra.db.repos.asset_jobs import AssetJobRepo
from homemind.infra.db.repos.face_candidates import FaceCandidateRepo
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_albums import FamilyAlbumRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_calendars import FamilyCalendarRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.db.repos.family_invites import FamilyInviteRepo
from homemind.infra.db.repos.family_reminders import FamilyReminderRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.db.repos.knowledge_documents import KnowledgeRepo
from homemind.infra.db.repos.memory_candidates import (
    MemoryCandidateRepo,
    MemoryEvidenceRepo,
)
from homemind.infra.db.repos.photo_intelligence import PhotoIntelligenceRepo
from homemind.infra.db.repos.search_index import SearchIndexRepo
from octop.infra.db.pool import DatabasePool


@dataclass(frozen=True)
class HomeMindServices:
    db: DatabasePool
    family_repo: FamilyRepo
    family_album_repo: FamilyAlbumRepo
    family_asset_repo: FamilyAssetRepo
    family_calendar_repo: FamilyCalendarRepo
    family_context_repo: FamilyContextRepo
    family_device_repo: FamilyDeviceRepo
    family_invite_repo: FamilyInviteRepo
    family_reminder_repo: FamilyReminderRepo
    family_task_repo: FamilyTaskRepo
    family_transaction_repo: FamilyTransactionRepo
    memory_candidate_repo: MemoryCandidateRepo
    memory_evidence_repo: MemoryEvidenceRepo
    photo_intelligence_repo: PhotoIntelligenceRepo
    asset_job_repo: AssetJobRepo
    search_index_repo: SearchIndexRepo
    face_candidate_repo: FaceCandidateRepo
    knowledge_repo: KnowledgeRepo

    @classmethod
    def from_pool(cls, db: DatabasePool) -> HomeMindServices:
        return cls(
            db=db,
            family_repo=FamilyRepo(db),
            family_album_repo=FamilyAlbumRepo(db),
            family_asset_repo=FamilyAssetRepo(db),
            family_calendar_repo=FamilyCalendarRepo(db),
            family_context_repo=FamilyContextRepo(db),
            family_device_repo=FamilyDeviceRepo(db),
            family_invite_repo=FamilyInviteRepo(db),
            family_reminder_repo=FamilyReminderRepo(db),
            family_task_repo=FamilyTaskRepo(db),
            family_transaction_repo=FamilyTransactionRepo(db),
            memory_candidate_repo=MemoryCandidateRepo(db),
            memory_evidence_repo=MemoryEvidenceRepo(db),
            photo_intelligence_repo=PhotoIntelligenceRepo(db),
            asset_job_repo=AssetJobRepo(db),
            search_index_repo=SearchIndexRepo(db),
            face_candidate_repo=FaceCandidateRepo(db),
            knowledge_repo=KnowledgeRepo(db),
        )
