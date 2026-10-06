"""Read/write access for registered HomeMind family devices, credentials, and commands."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, optional_updates
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyDeviceRow:
    id: str
    pk: int
    family_id: str
    name: str
    device_type: str
    platform: str | None
    status: str
    address: str | None
    capabilities: list[str]
    last_seen: int | None
    created_at: int
    updated_at: int
    root_path: str | None = None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceRow:
        # ``sqlite3.Row`` iterates *values*, so column names must come
        # from ``.keys()``; ``.get`` is not available on it either.
        # ruff SIM118 wants ``for key in row`` here, which would yield
        # values and raise IndexError — keep ``.keys()``.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["device_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            name=str(data["name"]),
            device_type=str(data["device_type"]),
            platform=data["platform"],
            status=str(data["status"]),
            address=data["address"],
            capabilities=[str(value) for value in json.loads(str(data["capabilities"]))],
            last_seen=int(data["last_seen"]) if data["last_seen"] is not None else None,
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
            root_path=data.get("root_path"),
        )


@dataclass(frozen=True)
class FamilyDeviceCredentialRow:
    id: str
    pk: int
    device_id: str
    family_id: str
    token_hash: str
    issued_at: int
    expires_at: int | None
    revoked_at: int | None
    rotated_from: str | None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceCredentialRow:
        # `sqlite3.Row` iterates *values*, so column names come from
        # `.keys()`; `.get` is not available on it either.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["credential_id"]),
            pk=int(data["id"]),
            device_id=str(data["device_id"]),
            family_id=str(data["family_id"]),
            token_hash=str(data["token_hash"]),
            issued_at=int(data["issued_at"]),
            expires_at=int(data["expires_at"]) if data["expires_at"] is not None else None,
            revoked_at=int(data["revoked_at"]) if data["revoked_at"] is not None else None,
            rotated_from=data["rotated_from"],
        )


@dataclass(frozen=True)
class FamilyDeviceCommandRow:
    id: str
    pk: int
    family_id: str
    device_id: str
    capability: str
    payload_json: str
    requested_by: int
    transaction_id: str | None
    expires_at: int
    status: str
    result_json: str | None
    error: str | None
    created_at: int
    updated_at: int
    is_unsafe: bool = False
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    approved_at: int | None = None
    approved_by: int | None = None
    retry_count: int = 0

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceCommandRow:
        # `sqlite3.Row` iterates *values*, so column names come from
        # `.keys()`; `.get` is not available on it either.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["command_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            device_id=str(data["device_id"]),
            capability=str(data["capability"]),
            payload_json=str(data["payload_json"]),
            requested_by=int(data["requested_by"]),
            transaction_id=data["transaction_id"],
            expires_at=int(data["expires_at"]),
            status=str(data["status"]),
            result_json=data["result_json"],
            error=data["error"],
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
            is_unsafe=bool(data.get("is_unsafe")),
            lease_owner=data.get("lease_owner"),
            lease_expires_at=(
                int(data["lease_expires_at"])
                if data.get("lease_expires_at") is not None
                else None
            ),
            approved_at=(
                int(data["approved_at"]) if data.get("approved_at") is not None else None
            ),
            approved_by=(
                int(data["approved_by"]) if data.get("approved_by") is not None else None
            ),
            retry_count=int(data.get("retry_count") or 0),
        )


class FamilyDeviceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # --------------------------------------------------------------- devices

    def get(self, device_id: str) -> FamilyDeviceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return FamilyDeviceRow.from_row(row) if row else None

    def list_for_family(self, family_id: str) -> list[FamilyDeviceRow]:
        """All devices in ``family_id``.

        Named ``list_for_family`` rather than ``list`` because a method
        named ``list`` shadows the builtin inside the class body, which
        breaks every ``list[...]`` annotation below it.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_devices WHERE family_id = ? "
                "ORDER BY name, device_id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyDeviceRow)

    def create(
        self,
        family_id: str,
        *,
        name: str,
        device_type: str,
        platform: str | None,
        capabilities: list[str],
        address: str | None = None,
        root_path: str | None = None,
    ) -> FamilyDeviceRow:
        device_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_devices(device_id, family_id, name, "
                "device_type, platform, status, address, capabilities, last_seen, "
                "root_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'OFFLINE', ?, ?, NULL, ?, ?, ?)",
                (
                    device_id,
                    family_id,
                    name.strip(),
                    device_type,
                    platform,
                    address,
                    json.dumps(capabilities, ensure_ascii=False, sort_keys=True),
                    root_path,
                    ts,
                    ts,
                ),
            )
        return self.get(device_id)  # type: ignore[return-value]

    def update(
        self,
        device_id: str,
        *,
        name: str | None = None,
        platform: str | None = None,
        capabilities: list[str] | None = None,
        status: str | None = None,
        address: str | None = None,
        root_path: str | None = None,
    ) -> FamilyDeviceRow | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name.strip()
        if platform is not None:
            values["platform"] = platform
        if capabilities is not None:
            values["capabilities"] = json.dumps(
                capabilities, ensure_ascii=False, sort_keys=True
            )
        if status is not None:
            values["status"] = status
        if address is not None:
            values["address"] = address
        if root_path is not None:
            values["root_path"] = root_path
        fields, params = optional_updates(list(values.items()))
        if not fields:
            return self.get(device_id)
        fields.append("updated_at = ?")
        params.extend((now_ts(), device_id))
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE homemind_family_devices SET {', '.join(fields)} "
                "WHERE device_id = ?",
                params,
            )
        return self.get(device_id)

    def delete(self, device_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_devices WHERE device_id = ?",
                (device_id,),
            )
        return int(cursor.rowcount or 0) > 0

    def heartbeat(
        self,
        device_id: str,
        *,
        status: str = "ONLINE",
        address: str | None = None,
    ) -> FamilyDeviceRow | None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_devices SET status = ?, last_seen = ?, "
                "address = COALESCE(?, address), updated_at = ? "
                "WHERE device_id = ?",
                (status, ts, address, ts, device_id),
            )
        return self.get(device_id)

    # ---------------------------------------------------------- credentials

    def issue_credential(
        self,
        device_id: str,
        family_id: str,
        token_hash: str,
        *,
        expires_at: int | None = None,
        rotated_from: str | None = None,
    ) -> FamilyDeviceCredentialRow:
        credential_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_device_credentials(credential_id, device_id, "
                "family_id, token_hash, issued_at, expires_at, rotated_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    credential_id,
                    device_id,
                    family_id,
                    token_hash,
                    ts,
                    expires_at,
                    rotated_from,
                ],
            )
        return self.get_credential(credential_id)  # type: ignore[return-value]

    def get_credential(self, credential_id: str) -> FamilyDeviceCredentialRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_device_credentials WHERE credential_id = ?",
                (credential_id,),
            ).fetchone()
        return FamilyDeviceCredentialRow.from_row(row) if row else None

    def find_active_credential_by_hash(
        self, token_hash: str,
    ) -> FamilyDeviceCredentialRow | None:
        """Look up a live credential for ``token_hash``.

        The SQL ``WHERE token_hash = ?`` narrows the candidate set, but
        SQLite / PostgreSQL string comparison is *not* constant-time, so
        a timing oracle would let an attacker who can time the endpoint
        recover a token hash byte-by-byte. The spec requires constant
        time, so we scan the non-revoked rows and settle the match with
        :func:`hmac.compare_digest`. The active-credential set per
        install is small (one row per paired device), so the linear scan
        is cheap and keeps the token itself out of the SQL layer.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_device_credentials "
                "WHERE revoked_at IS NULL ORDER BY issued_at DESC"
            ).fetchall()
        candidates = [FamilyDeviceCredentialRow.from_row(row) for row in rows]
        matched: FamilyDeviceCredentialRow | None = None
        for candidate in candidates:
            # ``compare_digest`` over equal-length hex keeps the work per
            # candidate constant regardless of where the first mismatch is.
            if hmac.compare_digest(candidate.token_hash, token_hash):
                matched = candidate
        return matched

    def mark_stale_devices_offline(
        self, *, heartbeat_timeout_seconds: int, now: int | None = None,
    ) -> int:
        """Flip devices to ``OFFLINE`` once ``last_seen`` falls behind the
        heartbeat timeout.

        Runs in the background so online state does not depend on someone
        opening the dashboard. Only ``ONLINE`` / ``BUSY`` rows are
        touched; ``REVOKED`` and ``DISABLED`` are terminal and must not be
        resurrected into an ``OFFLINE`` state that looks recoverable.
        """
        timestamp = now_ts() if now is None else now
        cutoff = timestamp - heartbeat_timeout_seconds
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_devices SET status = 'OFFLINE', updated_at = ? "
                "WHERE status IN ('ONLINE', 'BUSY') "
                "  AND (last_seen IS NULL OR last_seen <= ?)",
                (timestamp, cutoff),
            )
        return int(cursor.rowcount or 0)

    def revoke_credential(self, credential_id: str) -> FamilyDeviceCredentialRow | None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_device_credentials SET revoked_at = ? "
                "WHERE credential_id = ? AND revoked_at IS NULL",
                (ts, credential_id),
            )
        return self.get_credential(credential_id)

    def revoke_all_credentials_for_device(self, device_id: str) -> int:
        ts = now_ts()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_device_credentials SET revoked_at = ? "
                "WHERE device_id = ? AND revoked_at IS NULL",
                (ts, device_id),
            )
        return int(cursor.rowcount or 0)

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
        is_unsafe: bool = False,
        initial_status: str = "PENDING",
    ) -> FamilyDeviceCommandRow:
        command_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_device_commands(command_id, family_id, device_id, "
                "capability, payload_json, requested_by, transaction_id, expires_at, "
                "status, is_unsafe, retry_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    command_id,
                    family_id,
                    device_id,
                    capability,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    requested_by,
                    transaction_id,
                    expires_at,
                    initial_status,
                    1 if is_unsafe else 0,
                    ts,
                    ts,
                ),
            )
        return self.get_command(command_id)  # type: ignore[return-value]

    def get_command(self, command_id: str) -> FamilyDeviceCommandRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_device_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return FamilyDeviceCommandRow.from_row(row) if row else None

    def list_commands(
        self,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[FamilyDeviceCommandRow]:
        clauses = ["device_id = ?"]
        params: list[object] = [device_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_device_commands WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyDeviceCommandRow)

    def transition_command(
        self,
        command_id: str,
        *,
        from_status: str | tuple[str, ...],
        to_status: str,
        result_json: str | None = None,
        error: str | None = None,
        lease_owner: str | None = None,
        lease_expires_at: int | None = None,
        clear_lease: bool = False,
        approved_by: int | None = None,
        increment_retry: bool = False,
    ) -> FamilyDeviceCommandRow | None:
        ts = now_ts()
        from_clause = (
            f"status = '{from_status}'"
            if isinstance(from_status, str)
            else "status IN (" + ", ".join(f"'{s}'" for s in from_status) + ")"
        )
        sets = ["status = ?", "updated_at = ?"]
        params: list[object] = [to_status, ts]
        if result_json is not None:
            sets.append("result_json = ?")
            params.append(result_json)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if lease_owner is not None:
            sets.append("lease_owner = ?")
            params.append(lease_owner)
        if lease_expires_at is not None:
            sets.append("lease_expires_at = ?")
            params.append(lease_expires_at)
        if clear_lease:
            sets.append("lease_owner = NULL")
            sets.append("lease_expires_at = NULL")
        if approved_by is not None:
            sets.append("approved_at = ?")
            sets.append("approved_by = ?")
            params.extend((ts, approved_by))
        if increment_retry:
            sets.append("retry_count = retry_count + 1")
        params.append(command_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE homemind_device_commands SET {', '.join(sets)} "
                f"WHERE command_id = ? AND {from_clause}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_command(command_id)

    def claim_next_command(
        self,
        device_id: str,
        *,
        lease_owner: str,
        lease_ttl_seconds: int,
        now: int | None = None,
        max_retries: int = 3,
    ) -> FamilyDeviceCommandRow | None:
        """Atomically claim the next dispatchable command for ``device_id``.

        Eligible rows are:
        * ``PENDING`` and not yet expired, or
        * ``DISPATCHED`` / ``RUNNING`` whose lease expired — an abandoned
          claim another runtime is allowed to pick up.

        Reclaiming is refused for ``is_unsafe`` commands: replaying a
        delete or a "format disk" is not idempotent, so a stale lease must
        escalate to a human instead of silently re-running. This only
        applies to the *reclaim* path — an unsafe command that a manager
        approved still dispatches normally. ``max_retries`` additionally
        bounds automatic reclaim so a poison command cannot loop forever.

        The CAS ``UPDATE ... WHERE status = <observed>`` is what makes two
        concurrent runtimes safe: only the writer whose status guard still
        matches gets a row back, and the loser moves on to the next
        candidate.
        """
        timestamp = now_ts() if now is None else now
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_device_commands "
                "WHERE device_id = ? AND ("
                "  (status = 'PENDING' AND expires_at > ?) OR"
                "  (status IN ('DISPATCHED', 'RUNNING') "
                "   AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)"
                ") ORDER BY created_at ASC, id ASC",
                (device_id, timestamp, timestamp),
            ).fetchall()
        for row in rows:
            command = FamilyDeviceCommandRow.from_row(row)
            is_reclaim = command.status in {"DISPATCHED", "RUNNING"}
            if is_reclaim and command.is_unsafe:
                continue
            if is_reclaim and command.retry_count >= max_retries:
                continue
            claimed = self.transition_command(
                command.id,
                from_status=command.status,
                to_status="DISPATCHED",
                lease_owner=lease_owner,
                lease_expires_at=timestamp + lease_ttl_seconds,
                increment_retry=is_reclaim,
            )
            if claimed is not None:
                return claimed
        return None

    def list_reclaimable_commands(
        self, device_id: str, *, now: int | None = None,
    ) -> list[FamilyDeviceCommandRow]:
        """Commands whose lease expired and may still be reclaimed."""
        timestamp = now_ts() if now is None else now
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_device_commands "
                "WHERE device_id = ? AND status IN ('DISPATCHED', 'RUNNING') "
                "  AND lease_expires_at IS NOT NULL AND lease_expires_at <= ? "
                "ORDER BY created_at ASC, id ASC",
                (device_id, timestamp),
            ).fetchall()
        return map_rows(rows, FamilyDeviceCommandRow)


__all__ = [
    "FamilyDeviceCommandRow",
    "FamilyDeviceCredentialRow",
    "FamilyDeviceRepo",
    "FamilyDeviceRow",
]
