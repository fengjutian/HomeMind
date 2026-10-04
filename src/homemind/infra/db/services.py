"""HomeMind repository container, separate from Octop SharedServices."""

from __future__ import annotations

from dataclasses import dataclass

from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from octop.infra.db.pool import DatabasePool


@dataclass(frozen=True)
class HomeMindServices:
    db: DatabasePool
    family_repo: FamilyRepo
    family_asset_repo: FamilyAssetRepo
    family_context_repo: FamilyContextRepo
    family_task_repo: FamilyTaskRepo

    @classmethod
    def from_pool(cls, db: DatabasePool) -> HomeMindServices:
        return cls(
            db=db,
            family_repo=FamilyRepo(db),
            family_asset_repo=FamilyAssetRepo(db),
            family_context_repo=FamilyContextRepo(db),
            family_task_repo=FamilyTaskRepo(db),
        )
