"""Stage 6: device pairing, credential rotation, command dispatch, and heartbeat.

The runtime is the abstraction both the family-side manager (used by
the dashboard / cron) and the embedded runtime client (used by the
``homemind_runtime`` long-lived process) speak to. It keeps token
rotation, command safety, and online-state bookkeeping in one place
so the API and the runtime client can't drift apart.

Security invariants enforced here (Stage 5):

* A plaintext token is returned exactly once; only ``sha256`` lands in
  the database, and lookups settle with :func:`hmac.compare_digest`.
* Every runtime entry point resolves the caller's device *from the
  credential*, never from a request parameter, so device A can neither
  claim nor report on device B's commands.
* A command claim takes a time-boxed lease. An expired lease may be
  reclaimed — except for ``is_unsafe`` capabilities, which escalate to a
  human because replaying a delete is not idempotent.
* ``filesystem.*`` payloads are confined to the device's declared root
  so a compromised runtime cannot be steered at ``/etc``.
* A revoked credential stops heartbeat, dispatch, and result reporting
  immediately, because all three go through :meth:`_authenticate`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import secrets
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
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

logger = logging.getLogger(__name__)


# How long a runtime may hold a claimed command before another runtime is
# allowed to reclaim it. Long enough for a slow ``filesystem.copy`` on a
# NAS, short enough that a crashed runtime does not strand a command for
# a full day.
DEFAULT_COMMAND_LEASE_SECONDS = 300

# A device that has not sent a heartbeat inside this window is treated
# as offline by the background sweep.
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90

# Capabilities whose side effects are NOT idempotent, so a reclaimed lease
# must never replay them automatically.
UNSAFE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "filesystem.delete",
        "filesystem.move",
        "filesystem.rename",
        "device.format",
        "device.shutdown",
        "device.reboot",
    }
)

# Capabilities whose payload may name a filesystem path. The path must
# stay under the device's declared root.
PATH_BEARING_CAPABILITY_PREFIXES: tuple[str, ...] = ("filesystem.",)


def hash_token(token: str) -> str:
    """Stable token fingerprint stored in ``homemind_device_credentials.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token(length_bytes: int = 32) -> str:
    """Generate a fresh opaque bearer token."""
    return secrets.token_urlsafe(length_bytes)


def _normalize_device_path(raw: str) -> str:
    """Return ``raw`` as a canonical, traversal-free absolute POSIX path.

    ``..`` segments are collapsed, so ``photos/../../etc/passwd`` cannot
    smuggle its way out of the root it is later joined onto.
    """
    candidate = raw.replace("\\", "/").strip()
    if not candidate:
        return ""
    normalized = posixpath.normpath("/" + candidate.lstrip("/"))
    return normalized if normalized != "/" else ""


def path_within_root(candidate: str, root: str | None) -> bool:
    """Return True when ``candidate`` resolves inside ``root``.

    Device payloads name paths *relative to the device root*, so the
    candidate is joined onto ``root`` before the containment check. An
    absolute candidate still works because ``posixpath.join`` lets it
    win — which then fails the prefix check, keeping an absolute path
    from being used as an escape hatch.

    ``root`` of ``None`` or ``""`` means "device declared no root", which
    forbids every path-bearing capability — fail closed rather than
    letting an unconstrained runtime wander the host filesystem.
    """
    if not root:
        return False
    normalized_root = _normalize_device_path(root)
    if not normalized_root:
        return False
    resolved = _normalize_device_path(posixpath.join(normalized_root, candidate))
    if not resolved:
        return False
    resolved_parts = PurePosixPath(resolved).parts
    root_parts = PurePosixPath(normalized_root).parts
    return resolved_parts[: len(root_parts)] == root_parts


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
_PAIRING_STORE: dict[str, _PairingEntry] = {}


@dataclass
class _PairingEntry:
    family_id: str
    device_name: str
    device_type: str
    platform: str | None
    capabilities: list[str]
    expires_at: int
    root_path: str | None = None


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
        *,
        command_lease_seconds: int = DEFAULT_COMMAND_LEASE_SECONDS,
        heartbeat_timeout_seconds: int = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    ) -> None:
        self.family = family
        self.repo = repo
        self.command_lease_seconds = command_lease_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

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
        root_path: str | None = None,
    ) -> PairingCode:
        """Family-side: return a short-lived pairing code the runtime
        client exchanges for a credential. The placeholder device row
        is NOT created here; the runtime's ``complete_pairing`` builds
        the actual record so address and capabilities match reality.

        ``root_path`` is the filesystem root the device is authorized to
        touch. Path-bearing commands are refused outside it, so a device
        that will run ``filesystem.*`` capabilities must declare one.
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
        if not root_path and any(
            capability.startswith(PATH_BEARING_CAPABILITY_PREFIXES)
            for capability in capabilities
        ):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "device must declare root_path for filesystem capabilities",
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
                root_path=root_path,
                expires_at=expires_at,
            )
        return PairingCode(code=code, expires_at=expires_at)

    def complete_pairing(
        self,
        code: str,
        *,
        address: str | None,
        root_path: str | None = None,
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
            root_path=entry.root_path or root_path,
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

    def _authenticate(self, token: str) -> tuple[FamilyDeviceCredentialRow, FamilyDeviceRow]:
        """Resolve a bearer token to ``(credential, device)``.

        Every runtime entry point funnels through here, so a revoked or
        expired credential stops heartbeat, dispatch, and result reporting
        in one place. The token itself is never logged.
        """
        credential = self.repo.find_active_credential_by_hash(hash_token(token))
        if credential is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device credential rejected",
            )
        now = int(time.time())
        if credential.expires_at is not None and credential.expires_at < now:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device credential expired",
            )
        device = self.repo.get(credential.device_id)
        if device is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "credential points at missing device",
            )
        return credential, device

    def heartbeat(
        self,
        token: str,
        *,
        address: str | None = None,
        status: str = "ONLINE",
        runtime_version: str | None = None,
    ) -> FamilyDeviceRow:
        _, device = self._authenticate(token)
        updated = self.repo.heartbeat(
            device.id,
            status=status,
            address=address,
            runtime_version=runtime_version,
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "device heartbeat failed",
            )
        _hm_inc("device_heartbeat_total")
        return updated

    def mark_stale_devices_offline(self, *, now: int | None = None) -> int:
        """Flip devices whose heartbeat lapsed to ``OFFLINE``.

        Called from the background maintenance runner so online state does
        not depend on a dashboard page being open.
        """
        count = self.repo.mark_stale_devices_offline(
            heartbeat_timeout_seconds=self.heartbeat_timeout_seconds, now=now,
        )
        if count:
            _hm_inc("device_marked_offline_total", count)
        return count

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
        return self.repo.list_for_family(family_id)

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
        root_path: str | None = None,
    ) -> FamilyDeviceRow:
        self.family.require_manager(family_id, user)
        self._assert_device(family_id, device_id)
        if capabilities is not None and any(
            capability.startswith(PATH_BEARING_CAPABILITY_PREFIXES)
            for capability in capabilities
        ):
            effective_root = root_path
            if effective_root is None:
                effective_root = (self.repo.get(device_id) or None) and self.repo.get(device_id).root_path  # type: ignore[union-attr]
            if not effective_root:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    "device must declare root_path for filesystem capabilities",
                )
        updated = self.repo.update(
            device_id,
            name=name,
            platform=platform,
            capabilities=capabilities,
            root_path=root_path,
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
        user: User | None = None,
        requires_approval: bool | None = None,
    ) -> FamilyDeviceCommandRow:
        """Queue a command for ``device_id``.

        Validates before persisting:

        * the device belongs to ``family_id``,
        * the device declared ``capability``,
        * a path-bearing capability's paths stay inside the device root,
        * the requesting user may act on the family.

        ``requires_approval`` defaults to "the capability is unsafe", which
        lands the row in ``WAITING_APPROVAL`` so the runtime cannot claim
        it until a family manager calls :meth:`approve_command`.
        """
        device = self._assert_device(family_id, device_id)
        if user is not None:
            self.family.require_access(family_id, user)
        if capability not in device.capabilities:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"device has not declared capability {capability!r}",
            )
        self._assert_payload_paths_allowed(device, capability, payload)
        is_unsafe = capability in UNSAFE_CAPABILITIES
        needs_approval = is_unsafe if requires_approval is None else requires_approval
        row = self.repo.enqueue_command(
            family_id,
            device.id,
            capability=capability,
            payload=payload,
            requested_by=requested_by,
            expires_at=expires_at,
            transaction_id=transaction_id,
            is_unsafe=is_unsafe,
            initial_status="WAITING_APPROVAL" if needs_approval else "PENDING",
        )
        _hm_inc("device_command_enqueued_total")
        return row

    def approve_command(
        self, family_id: str, command_id: str, user: User,
    ) -> FamilyDeviceCommandRow:
        """Move a ``WAITING_APPROVAL`` command to ``PENDING``.

        Manager-only: the runtime has no path into this method, so a
        compromised device cannot approve its own command.
        """
        self.family.require_manager(family_id, user)
        command = self.repo.get_command(command_id)
        if command is None or command.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family command not found",
            )
        approved = self.repo.transition_command(
            command.id,
            from_status="WAITING_APPROVAL",
            to_status="PENDING",
            approved_by=user.id,
            clear_lease=True,
        )
        if approved is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "command is not awaiting approval",
            )
        _hm_inc("device_command_approved_total")
        return approved

    def cancel_command(
        self, family_id: str, command_id: str, user: User, *, reason: str | None = None,
    ) -> FamilyDeviceCommandRow:
        """Manager cancels a command that has not finished yet."""
        self.family.require_manager(family_id, user)
        command = self.repo.get_command(command_id)
        if command is None or command.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family command not found",
            )
        cancelled = self.repo.transition_command(
            command.id,
            from_status=("PENDING", "WAITING_APPROVAL", "DISPATCHED", "RUNNING"),
            to_status="CANCELLED",
            error=reason,
            clear_lease=True,
        )
        if cancelled is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "command is already terminal",
            )
        return cancelled

    def next_pending_command(self, token: str) -> FamilyDeviceCommandRow | None:
        """Runtime-side: claim the next dispatchable command.

        The device is resolved from ``token``, never from a parameter, so
        this cannot be steered at another device's queue. Approval-gated
        rows stay invisible until a manager approves them, and expired
        PENDING rows are retired to ``EXPIRED`` rather than dispatched.
        """
        credential, device = self._authenticate(token)
        owner = f"device:{device.id}"
        self._expire_lapsed_pending(device.id)
        claimed = self.repo.claim_next_command(
            device.id,
            lease_owner=owner,
            lease_ttl_seconds=self.command_lease_seconds,
        )
        if claimed is None:
            return None
        _hm_inc("device_command_dispatched_total")
        return claimed

    def _expire_lapsed_pending(self, device_id: str) -> None:
        now = int(time.time())
        for command in self.repo.list_commands(device_id, status="PENDING"):
            if command.expires_at < now:
                self.repo.transition_command(
                    command.id, from_status="PENDING", to_status="EXPIRED",
                )

    def list_reclaimable_commands(
        self, token: str,
    ) -> list[FamilyDeviceCommandRow]:
        """Commands whose lease lapsed and are still safe to reclaim."""
        _, device = self._authenticate(token)
        return self.repo.list_reclaimable_commands(device.id)

    def report_command_result(
        self,
        token: str,
        command_id: str,
        *,
        status: str,
        result: dict[str, Any],
        error: str | None = None,
    ) -> FamilyDeviceCommandRow | None:
        """Runtime-side: report the outcome of a command this device owns.

        Ownership is re-derived from the credential, so device B cannot
        report on device A's command even with a leaked ``command_id``.
        Repeated submissions are idempotent: a command already in a
        terminal state returns the stored row instead of erroring, so a
        retrying runtime does not have to distinguish "lost" from
        "already done".
        """
        _, device = self._authenticate(token)
        command = self.repo.get_command(command_id)
        if command is None or command.device_id != device.id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "command does not belong to this device",
            )
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
        if command.status in terminal:
            # Idempotent replay of an already-reported outcome.
            return command
        if command.status == "PENDING" or command.status == "WAITING_APPROVAL":
            # Never report on a command we were never handed.
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "command is not in a claimable state",
            )
        updated = self.repo.transition_command(
            command.id,
            from_status=("DISPATCHED", "RUNNING"),
            to_status=status,
            result_json=json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            error=error,
            clear_lease=True,
        )
        if updated is not None and status in {"SUCCEEDED", "FAILED"}:
            _hm_inc(
                "device_command_succeeded_total"
                if status == "SUCCEEDED"
                else "device_command_failed_total",
            )
        return updated

    def acknowledge_command(
        self, token: str, command_id: str, *,
        runtime_version: str | None = None,
    ) -> FamilyDeviceCommandRow:
        """Runtime-side: mark a claimed command as RUNNING.

        Records the lease so a slow execution is not reclaimed mid-flight,
        and refreshes the lease window. A ``runtime_version`` supplied
        here is also stamped onto the device row, so the panel shows
        which build is actually running a command.
        """
        _, device = self._authenticate(token)
        if runtime_version:
            self.repo.heartbeat(
                device.id, status=device.status, runtime_version=runtime_version,
            )
        command = self.repo.get_command(command_id)
        if command is None or command.device_id != device.id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "command does not belong to this device",
            )
        updated = self.repo.transition_command(
            command.id,
            from_status="DISPATCHED",
            to_status="RUNNING",
            lease_owner=f"device:{device.id}",
            lease_expires_at=int(time.time()) + self.command_lease_seconds,
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "command is not in a dispatchable state",
            )
        return updated

    # ------------------------------------------------------------ helpers

    def _assert_payload_paths_allowed(
        self,
        device: FamilyDeviceRow,
        capability: str,
        payload: dict[str, Any],
    ) -> None:
        """Reject a command whose paths escape the device's declared root.

        Only path-bearing capabilities are checked; ``source_path`` /
        ``destination_path`` / ``path`` are the keys the filesystem
        handlers consume. A device that never registered a ``root_path``
        fails closed — it may still run non-filesystem capabilities, but
        nothing that touches the host filesystem.
        """
        if not capability.startswith(PATH_BEARING_CAPABILITY_PREFIXES):
            return
        for key in ("source_path", "destination_path", "path"):
            raw = payload.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            if not path_within_root(raw, device.root_path):
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    f"{key} escapes the device's authorized root",
                )

    def _assert_device(self, family_id: str, device_id: str) -> FamilyDeviceRow:
        device = self.repo.get(device_id)
        if device is None or device.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family device not found",
            )
        return device


__all__ = [
    "DEFAULT_COMMAND_LEASE_SECONDS",
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "DeviceRuntimeManager",
    "PairingCode",
    "UNSAFE_CAPABILITIES",
    "hash_token",
    "mint_token",
    "path_within_root",
]
