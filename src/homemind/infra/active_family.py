"""Stage 3: per-user active-family state.

The dashboard talks to many families; the chat agent and the MCP
tools benefit from knowing which family the caller is currently in
so callers don't have to pass ``family_id`` on every invocation.

Storage: one settings row per user, key
``homemind.active_family_id``, value is the family ULID. The user
keeps access through ``FamilyManager.require_access`` so changing
the active family to one the user can't see must fail fast.
"""

from __future__ import annotations

from homemind.infra.family.manager import FamilyManager
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

_ACTIVE_FAMILY_KEY_PREFIX = "homemind.active_family_id::"


def _key_for(user_id: int) -> str:
    return f"{_ACTIVE_FAMILY_KEY_PREFIX}{user_id}"


class ActiveFamilyResolver:
    def __init__(
        self,
        settings: SettingsRepo,
        family: FamilyManager,
    ) -> None:
        self._settings = settings
        self._family = family

    def get(self, user: User) -> str | None:
        value = self._settings.get(_key_for(user.id))
        if not value:
            return None
        # Defensive: confirm the saved family is still visible to the
        # user. A member removed between sessions must not silently
        # leave the agent talking to a family the user can no longer see.
        try:
            self._family.require_access(value, user)
        except OctopError:
            self._settings.set(_key_for(user.id), "")
            return None
        return value

    def set(self, user: User, family_id: str) -> str:
        self._family.require_access(family_id, user)
        self._settings.set(_key_for(user.id), family_id)
        return family_id

    def clear(self, user: User) -> None:
        self._settings.set(_key_for(user.id), "")


__all__ = ["ActiveFamilyResolver", "_ACTIVE_FAMILY_KEY_PREFIX"]
