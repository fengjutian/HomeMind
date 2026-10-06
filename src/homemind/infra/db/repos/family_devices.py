"""Read/write access for registered HomeMind family devices, credentials, and commands."""

from __future__ import annotations

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

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceRow:
        return cls(
            id=str(row["device_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            name=str(row["name"]),
            device_type=str(row["device_type"]),
            platform=row["platform"],
            status=str(row["status"]),
            address=row["address"],
            capabilities=[str(value) for value in json.loads(str(row["capabilities"]))],
            last_seen=int(row["last_seen"]) if row["last_seen"] is not None else None,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
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
        return cls(
            id=str(row["credential_id"]),
            pk=int(row["id"]),
            device_id=str(row["device_id"]),
            family_id=str(row["family_id"]),
            token_hash=str(row["token_hash"]),
            issued_at=int(row["issued_at"]),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
            rotated_from=row["rotated_from"],
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

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceCommandRow:
        return cls(
            id=str(row["command_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            device_id=str(row["device_id"]),
            capability=str(row["capability"]),
            payload_json=str(row["payload_json"]),
            requested_by=int(row["requested_by"]),
            transaction_id=row["transaction_id"],
            expires_at=int(row["expires_at"]),
            status=str(row["status"]),
            result_json=row["result_json"],
            error=row["error"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
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

    def list(self, family_id: str) -> list[FamilyDeviceRow]:
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
    ) -> FamilyDeviceRow:
        device_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_devices(device_id, family_id, name, "
                "device_type, platform, status, address, capabilities, last_seen, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'OFFLINE', ?, ?, NULL, ?, ?)",
                (
                    device_id,
                    family_id,
                    name.strip(),
                    device_type,
                    platform,
                    address,
                    json.dumps(capabilities, ensure_ascii=False, sort_keys=True),
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
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_device_credentials "
                "WHERE token_hash = ? AND revoked_at IS NULL "
                "ORDER BY issued_at DESC LIMIT 1",
                (token_hash,),
            ).fetchone()
        return FamilyDeviceCredentialRow.from_row(row) if row else None

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
    ) -> FamilyDeviceCommandRow:
        command_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_device_commands(command_id, family_id, device_id, "
                "capability, payload_json, requested_by, transaction_id, expires_at, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (
                    command_id,
                    family_id,
                    device_id,
                    capability,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    requested_by,
                    transaction_id,
                    expires_at,
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


__all__ = [
    "FamilyDeviceCommandRow",
    "FamilyDeviceCredentialRow",
    "FamilyDeviceRepo",
    "FamilyDeviceRow",
]