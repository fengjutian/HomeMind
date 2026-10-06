"""Stage 6: device pairing, credential rotation, command dispatch, and heartbeat.

The runtime is the abstraction both the family-side manager (used by
the dashboard / cron) and the embedded runtime client (used by the
``homemind_runtime`` long-lived process) speak to. It keeps token
rotation, command safety, and online-state bookkeeping in one place
so the API and the runtime client can't drift apart.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

from homemind.infra.db.repos.family_devices import (
    FamilyDeviceCommandRow,
    FamilyDeviceCredentialRow,
    FamilyDeviceRepo,
    FamilyDeviceRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User


def hash_token(token: str) -> str:
    """Stable token fingerprint stored in ``homemind_device_credentials.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token(length_bytes: int = 32) -> str:
    """Generate a fresh opaque bearer token."""
    return secrets.token_urlsafe(length_bytes)


@dataclass(frozen=True)
class PairingCode:
    """Short-lived pairing code returned to a family manager.

    The family admin passes ``code`` to the runtime client which uses
    it in ``POST /runtime/pair`` to obtain a long-lived credential.
    """

    code: str
    expires_at: int


# In-memory pairing store. Persisting pairing codes in the DB would
# require yet another table; for V0.1 we keep them in process memory
# and document that a multi-process deploy must add a backing store.
# Module-level so the store survives across per-request manager instances.
_PAIRING_TTL_SECONDS = 600
_PAIRING_LOCK = __import__("threading").Lock()
_PAIRING_STORE: dict[str, "_PairingEntry"] = {}


@dataclass
class _PairingEntry:
    family_id: str
    device_name: str
    device_type: str
    platform: str | None
    capabilities: list[str]
    expires_at: int


def _purge_expired_pairings(now: int) -> None:
    expired = [code for code, entry in _PAIRING_STORE.items() if entry.expires_at < now]
    for code in expired:
        _PAIRING_STORE.pop(code, None)


class DeviceRuntimeManager:
    """Owns device pairing, command dispatch, and heartbeat bookkeeping."""

    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyDeviceRepo,
    ) -> None:
        self.family = family
        self.repo = repo

    # ---------------------------------------------------------------- pairing

    def create_pairing_code(
        self,
        family_id: str,
        user: User,
        *,
        device_name: str,
        device_type: str,
        platform: str | None,
        capabilities: list[str],
    ) -> PairingCode:
        """Family-side: return a short-lived pairing code the runtime
        client exchanges for a credential. The placeholder device row
        is NOT created here; the runtime's ``complete_pairing`` builds
        the actual record so address and capabilities match reality.
        """
        self.family.require_manager(family_id, user)
        if not device_name:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device name is required",
            )
        if not capabilities:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "device must declare at least one capability",
            )
        code = secrets.token_urlsafe(8)
        expires_at = int(time.time()) + _PAIRING_TTL_SECONDS
        with _PAIRING_LOCK:
            _purge_expired_pairings(int(time.time()))
            _PAIRING_STORE[code] = _PairingEntry(
                family_id=family_id,
                device_name=device_name,
                device_type=device_type,
                platform=platform,
                capabilities=capabilities,
                expires_at=expires_at,
            )
        return PairingCode(code=code, expires_at=expires_at)

    def complete_pairing(
        self,
        code: str,
        *,
        address: str | None,
    ) -> tuple[FamilyDeviceRow, str, int]:
        """Runtime-side: exchange a pairing code for a long-lived
        credential token. The plaintext token is returned exactly
        once and never persisted.
        """
        now = int(time.time())
        with _PAIRING_LOCK:
            _purge_expired_pairings(now)
            entry = _PAIRING_STORE.pop(code, None)
        if entry is None or entry.expires_at < now:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "pairing code is invalid or expired",
            )
        device = self.repo.create(
            entry.family_id,
            name=entry.device_name,
            device_type=entry.device_type,
            platform=entry.platform,
            capabilities=entry.capabilities,
            address=address,
        )
        token = mint_token()
        credential = self.repo.issue_credential(
            device.id, device.family_id, hash_token(token)
        )
        refreshed = self.repo.heartbeat(device.id, status="ONLINE", address=address)
        return refreshed or device, token, credential.expires_at or 0

    # ---------------------------------------------------------- credentials

    def rotate_token(self, family_id: str, device_id: str, user: User) -> str:
        self.family.require_manager(family_id, user)
        device = self._assert_device(family_id, device_id)
        # Revoke existing credentials first so the new one is the only
        # active row when the manager picks it up. (We don't need the
        # rotated_from pointer — it's metadata, not load-bearing.)
        self.repo.revoke_all_credentials_for_device(device.id)
        token = mint_token()
        self.repo.issue_credential(device.id, device.family_id, hash_token(token))
        _hm_inc("device_rotate_token_total")
        return token

    def revoke_token(self, family_id: str, device_id: str, user: User) -> int:
        self.family.require_manager(family_id, user)
        device = self._assert_device(family_id, device_id)
        count = self.repo.revoke_all_credentials_for_device(device.id)
        _hm_inc("device_revoke_token_total")
        return count

    # ------------------------------------------------------------ heartbeat

    def heartbeat(
        self,
        token: str,
        *,
        address: str | None = None,
        status: str = "ONLINE",
    ) -> FamilyDeviceRow:
        credential = self.repo.find_active_credential_by_hash(hash_token(token))
        if credential is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device credential rejected"
            )
        if credential.expires_at is not None and credential.expires_at < int(time.time()):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device credential expired"
            )
        device = self.repo.get(credential.device_id)
        if device is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "credential points at missing device"
            )
        updated = self.repo.heartbeat(device.id, status=status, address=address)
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device heartbeat failed"
            )
        _hm_inc("device_heartbeat_total")
        return updated

    def list_recent_commands(
        self, family_id: str, device_id: str, user: User, *, limit: int = 50,
    ) -> list[FamilyDeviceCommandRow]:
        self.family.require_access(family_id, user)
        device = self._assert_device(family_id, device_id)
        return self.repo.list_commands(device.id, limit=limit)

    def list_commands(
        self, family_id: str, device_id: str, user: User, *, limit: int = 50,
    ) -> list[FamilyDeviceCommandRow]:
        """Alias for ``list_recent_commands`` retained for naming parity
        with the plan spec; the dashboard uses ``list_recent_commands``.
        """
        return self.list_recent_commands(family_id, device_id, user, limit=limit)

    # -------------------------------------------------------- device CRUD

    def list_devices(self, family_id: str, user: User) -> list[FamilyDeviceRow]:
        self.family.require_access(family_id, user)
        return self.repo.list(family_id)

    def get_device(
        self, family_id: str, device_id: str, user: User,
    ) -> FamilyDeviceRow:
        self.family.require_access(family_id, user)
        return self._assert_device(family_id, device_id)

    def update_device(
        self,
        family_id: str,
        device_id: str,
        user: User,
        *,
        name: str | None = None,
        platform: str | None = None,
        capabilities: list[str] | None = None,
    ) -> FamilyDeviceRow:
        self.family.require_manager(family_id, user)
        self._assert_device(family_id, device_id)
        updated = self.repo.update(
            device_id,
            name=name,
            platform=platform,
            capabilities=capabilities,
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device update failed",
            )
        return updated

    def delete_device(self, family_id: str, device_id: str, user: User) -> bool:
        self.family.require_manager(family_id, user)
        self._assert_device(family_id, device_id)
        return self.repo.delete(device_id)

    # ------------------------------------------------------------ commands

    def enqueue_command(
        self,
        family_id: str,
        device_id: str,
        *,
        capability: str,
        payload: dict[str, Any],
        requested_by: int,
        expires_at: int,
        transaction_id: str | None = None,
    ) -> FamilyDeviceCommandRow:
        device = self._assert_device(family_id, device_id)
        if capability not in device.capabilities:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"device has not declared capability {capability!r}",
            )
        row = self.repo.enqueue_command(
            family_id,
            device.id,
            capability=capability,
            payload=payload,
            requested_by=requested_by,
            expires_at=expires_at,
            transaction_id=transaction_id,
        )
        _hm_inc("device_command_enqueued_total")
        return row

    def next_pending_command(self, device_id: str) -> FamilyDeviceCommandRow | None:
        """Runtime-side: fetch the next pending command for ``device_id``."""
        for command in self.repo.list_commands(device_id, status="PENDING"):
            if command.expires_at < int(time.time()):
                self.repo.transition_command(
                    command.id, from_status="PENDING", to_status="EXPIRED",
                )
                continue
            claimed = self.repo.transition_command(
                command.id,
                from_status="PENDING",
                to_status="DISPATCHED",
            )
            if claimed is not None:
                return claimed
        return None

    def report_command_result(
        self,
        command_id: str,
        *,
        status: str,
        result: dict[str, Any],
        error: str | None = None,
    ) -> FamilyDeviceCommandRow | None:
        import json
        row = self.repo.transition_command(
            command_id,
            from_status=("DISPATCHED", "RUNNING"),
            to_status=status,
            result_json=json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            error=error,
        )
        if row is not None and status in {"SUCCEEDED", "FAILED"}:
            _hm_inc(
                "device_command_succeeded_total"
                if status == "SUCCEEDED"
                else "device_command_failed_total",
            )
        return row

    # ------------------------------------------------------------ helpers

    def _assert_device(self, family_id: str, device_id: str) -> FamilyDeviceRow:
        device = self.repo.get(device_id)
        if device is None or device.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family device not found",
            )
        return device


__all__ = [
    "DeviceRuntimeManager",
    "PairingCode",
    "hash_token",
    "mint_token",
]