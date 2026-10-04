"""HomeMind repository container, separate from Octop SharedServices."""

from __future__ import annotations

from dataclasses import dataclass

from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_albums import FamilyAlbumRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.db.repos.photo_intelligence import PhotoIntelligenceRepo
from octop.infra.db.pool import DatabasePool


@dataclass(frozen=True)
class HomeMindServices:
    db: DatabasePool
    family_repo: FamilyRepo
    family_album_repo: FamilyAlbumRepo
    family_asset_repo: FamilyAssetRepo
    family_context_repo: FamilyContextRepo
    family_device_repo: FamilyDeviceRepo
    family_task_repo: FamilyTaskRepo
    family_transaction_repo: FamilyTransactionRepo
    photo_intelligence_repo: PhotoIntelligenceRepo

    @classmethod
    def from_pool(cls, db: DatabasePool) -> HomeMindServices:
        return cls(
            db=db,
            family_repo=FamilyRepo(db),
            family_album_repo=FamilyAlbumRepo(db),
            family_asset_repo=FamilyAssetRepo(db),
            family_context_repo=FamilyContextRepo(db),
            family_device_repo=FamilyDeviceRepo(db),
            family_task_repo=FamilyTaskRepo(db),
            family_transaction_repo=FamilyTransactionRepo(db),
            photo_intelligence_repo=PhotoIntelligenceRepo(db),
        )
