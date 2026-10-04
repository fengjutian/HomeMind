"""Family albums and non-destructive photo organization plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from homemind.infra.db.repos.family_albums import (
    FamilyAlbumRepo,
    FamilyAlbumRow,
    OrganizationPlanRow,
)
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class OrganizationStrategy(StrEnum):
    TIME_MONTH = "TIME_MONTH"
    EXACT_DUPLICATES = "EXACT_DUPLICATES"


@dataclass(frozen=True)
class OrganizationGroup:
    name: str
    asset_ids: list[str]
    reason: str


class FamilyAlbumManager:
    def __init__(
        self,
        family: FamilyManager,
        assets: FamilyAssetManager,
        repo: FamilyAlbumRepo,
    ) -> None:
        self.family = family
        self.assets = assets
        self.repo = repo

    def create(
        self, family_id: str, user: User, *, name: str, description: str = ""
    ) -> FamilyAlbumRow:
        self.family.require_access(family_id, user)
        normalized = name.strip()
        if not normalized:
            raise ValueError("album name is required")
        return self.repo.create_album(family_id, normalized, description.strip(), user.id)

    def list(self, family_id: str, user: User) -> list[FamilyAlbumRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_albums(family_id)

    def add_asset(
        self, family_id: str, album_id: str, asset_id: str, user: User
    ) -> None:
        self._album(family_id, album_id, user)
        self.assets.get(family_id, asset_id, user)
        self.repo.add_asset(album_id, asset_id, user.id)

    def remove_asset(
        self, family_id: str, album_id: str, asset_id: str, user: User
    ) -> bool:
        self._album(family_id, album_id, user)
        return self.repo.remove_asset(album_id, asset_id)

    def asset_ids(self, family_id: str, album_id: str, user: User) -> list[str]:
        self._album(family_id, album_id, user)
        return self.repo.list_asset_ids(album_id)

    def plan(
        self, family_id: str, user: User, strategy: OrganizationStrategy
    ) -> OrganizationPlanRow:
        family = self.family.require_access(family_id, user)
        if strategy is OrganizationStrategy.TIME_MONTH:
            buckets: dict[str, list[str]] = {}
            for asset in self.assets.search(
                family_id, user, asset_type="PHOTO", limit=500
            ):
                if asset.captured_at is None:
                    continue
                month = datetime.fromtimestamp(
                    asset.captured_at, ZoneInfo(family.timezone)
                ).strftime("%Y-%m")
                buckets.setdefault(month, []).append(asset.id)
            groups = [
                OrganizationGroup(month, asset_ids, "captured_at month")
                for month, asset_ids in sorted(buckets.items())
            ]
        else:
            groups = [
                OrganizationGroup(
                    f"疑似重复-{index:03d}",
                    [asset.id for asset in rows],
                    "identical SHA-256",
                )
                for index, rows in enumerate(
                    self.assets.duplicate_groups(family_id, user), start=1
                )
            ]
        payload = json.dumps(
            [
                {"name": group.name, "asset_ids": group.asset_ids, "reason": group.reason}
                for group in groups
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return self.repo.create_plan(family_id, strategy.value, payload, user.id)

    def apply_plan(
        self, family_id: str, plan_id: str, user: User
    ) -> OrganizationPlanRow:
        self.family.require_manager(family_id, user)
        plan = self._plan(family_id, plan_id)
        if plan.status != "PLANNED":
            raise ValueError("organization plan is not pending")
        groups = json.loads(plan.groups_json)
        for group in groups:
            album = self.repo.get_album_by_name(family_id, str(group["name"]))
            if album is None:
                album = self.repo.create_album(
                    family_id, str(group["name"]), str(group["reason"]), user.id
                )
            for asset_id in group["asset_ids"]:
                self.assets.get(family_id, str(asset_id), user)
                self.repo.add_asset(album.id, str(asset_id), user.id)
        applied = self.repo.mark_plan_applied(plan_id)
        for group in groups:
            album = self.repo.get_album_by_name(family_id, str(group["name"]))
            if album is None or len(self.repo.list_asset_ids(album.id)) < len(group["asset_ids"]):
                raise RuntimeError("organization plan verification failed")
        return applied

    def get_plan(
        self, family_id: str, plan_id: str, user: User
    ) -> OrganizationPlanRow:
        self.family.require_access(family_id, user)
        return self._plan(family_id, plan_id)

    def _album(self, family_id: str, album_id: str, user: User) -> FamilyAlbumRow:
        self.family.require_access(family_id, user)
        album = self.repo.get_album(album_id)
        if album is None or album.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family album not found")
        return album

    def _plan(self, family_id: str, plan_id: str) -> OrganizationPlanRow:
        plan = self.repo.get_plan(plan_id)
        if plan is None or plan.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "organization plan not found")
        return plan
