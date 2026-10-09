"""Bridge connection rows — per-user links to remote Octop instances."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts


@dataclass(frozen=True)
class BridgeConnectionRow:
    pk: int
    connection_id: str
    owner_user_id: int
    peer_base_url: str
    peer_username: str
    display_name: str
    notes: str | None
    icon_name: str | None
    credential_blob: bytes | None
    access_token_blob: bytes | None
    token_expires_at: int | None
    status: str
    last_error: str | None
    last_seen_at: int | None
    auto_reconnect: bool
    #: Plan phase 13 state machine. ``status`` above is the legacy column,
    #: kept in step by :meth:`set_state` for older readers. Defaults keep rows
    #: built directly by callers working on a pre-v22 shape.
    state: str = "DISCONNECTED"
    state_changed_at: int | None = None
    state_detail: str | None = None
    peer_protocol: str | None = None
    peer_instance_id: str | None = None
    connected_since: int | None = None
    reconnect_attempts: int = 0
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, r: DbRow) -> BridgeConnectionRow:
        cred = r["credential_blob"]
        token = r["access_token_blob"]
        keys = set(r.keys()) if hasattr(r, "keys") else set()
        raw_notes = r["notes"]
        raw_icon = r["icon_name"] if "icon_name" in keys else None
        auto_raw = r["auto_reconnect"] if "auto_reconnect" in keys else 1
        return cls(
            pk=int(r["id"]),
            connection_id=str(r["connection_id"]),
            owner_user_id=int(r["owner_user_id"]),
            peer_base_url=str(r["peer_base_url"]),
            peer_username=str(r["peer_username"]),
            display_name=str(r["display_name"] or ""),
            notes=(str(raw_notes) if raw_notes is not None and str(raw_notes).strip() else None),
            icon_name=(
                str(raw_icon).strip()
                if isinstance(raw_icon, str) and str(raw_icon).strip()
                else None
            ),
            credential_blob=bytes(cred) if cred is not None else None,
            access_token_blob=bytes(token) if token is not None else None,
            token_expires_at=r["token_expires_at"],
            status=str(r["status"] or "disconnected"),
            last_error=r["last_error"],
            last_seen_at=r["last_seen_at"],
            auto_reconnect=bool(int(auto_raw or 0)),
            # New columns are read defensively: a test or a restore may present
            # a pre-v22 row, and ``sqlite3.Row`` has no ``.get()``.
            state=str(r["state"]) if "state" in keys else "DISCONNECTED",
            state_changed_at=(
                int(r["state_changed_at"])
                if "state_changed_at" in keys and r["state_changed_at"] is not None
                else None
            ),
            state_detail=r["state_detail"] if "state_detail" in keys else None,
            peer_protocol=r["peer_protocol"] if "peer_protocol" in keys else None,
            peer_instance_id=(
                r["peer_instance_id"] if "peer_instance_id" in keys else None
            ),
            connected_since=(
                int(r["connected_since"])
                if "connected_since" in keys and r["connected_since"] is not None
                else None
            ),
            reconnect_attempts=(
                int(r["reconnect_attempts"] or 0)
                if "reconnect_attempts" in keys
                else 0
            ),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class BridgeConnectionRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        peer_base_url: str,
        peer_username: str,
        display_name: str,
        notes: str | None = None,
        icon_name: str | None = None,
        credential_blob: bytes | None = None,
        access_token_blob: bytes | None = None,
        token_expires_at: int | None = None,
        status: str = "disconnected",
        auto_reconnect: bool = True,
    ) -> BridgeConnectionRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO bridge_connections("
                "connection_id, owner_user_id, peer_base_url, peer_username, display_name, "
                "notes, icon_name, credential_blob, access_token_blob, token_expires_at, status, "
                "auto_reconnect, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    connection_id,
                    owner_user_id,
                    peer_base_url,
                    peer_username,
                    display_name,
                    notes,
                    (icon_name or "").strip() or None,
                    credential_blob,
                    access_token_blob,
                    token_expires_at,
                    status,
                    1 if auto_reconnect else 0,
                    ts,
                    ts,
                ),
            )
        row = self.get(connection_id)
        assert row is not None  # noqa: S101
        return row

    def get(self, connection_id: str) -> BridgeConnectionRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM bridge_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        return BridgeConnectionRow.from_row(r) if r else None

    def get_for_owner(self, connection_id: str, owner_user_id: int) -> BridgeConnectionRow | None:
        row = self.get(connection_id)
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def find_by_display_name(
        self, owner_user_id: int, display_name: str
    ) -> BridgeConnectionRow | None:
        name = display_name.strip()
        if not name:
            return None
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM bridge_connections WHERE owner_user_id = ? AND display_name = ?",
                (owner_user_id, name),
            ).fetchone()
        return BridgeConnectionRow.from_row(r) if r else None

    def list_for_owner(self, owner_user_id: int) -> list[BridgeConnectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bridge_connections WHERE owner_user_id = ? "
                "ORDER BY updated_at DESC, id DESC",
                (owner_user_id,),
            ).fetchall()
        return map_rows(rows, BridgeConnectionRow)

    def list_auto_reconnect(self) -> list[BridgeConnectionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bridge_connections WHERE auto_reconnect = 1 ORDER BY id ASC"
            ).fetchall()
        return map_rows(rows, BridgeConnectionRow)

    def update_credentials(
        self,
        connection_id: str,
        *,
        credential_blob: bytes | None = None,
        access_token_blob: bytes | None = None,
        token_expires_at: int | None = None,
    ) -> None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bridge_connections SET "
                "credential_blob = COALESCE(?, credential_blob), "
                "access_token_blob = COALESCE(?, access_token_blob), "
                "token_expires_at = COALESCE(?, token_expires_at), "
                "updated_at = ? WHERE connection_id = ?",
                (credential_blob, access_token_blob, token_expires_at, ts, connection_id),
            )

    def set_state(
        self,
        connection_id: str,
        state: str,
        *,
        legacy_status: str,
        detail: str | None = None,
        clear_detail: bool = False,
        peer_protocol: str | None = None,
        peer_instance_id: str | None = None,
    ) -> BridgeConnectionRow | None:
        """Move a connection to ``state`` and keep the legacy column in step.

        ``legacy_status`` is passed in rather than derived here: this layer is
        SQL only, and the new→legacy mapping belongs to the bridge domain
        (``bridge.states``). Two independent maps would drift.
        """
        ts = now_ts()
        connected_since = ts if state == "ONLINE" else None
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bridge_connections SET state = ?, status = ?, state_changed_at = ?, "
                "state_detail = ?, peer_protocol = COALESCE(?, peer_protocol), "
                "peer_instance_id = COALESCE(?, peer_instance_id), "
                "connected_since = ?, updated_at = ? WHERE connection_id = ?",
                (
                    state,
                    legacy_status,
                    ts,
                    None if clear_detail else detail,
                    peer_protocol,
                    peer_instance_id,
                    connected_since,
                    ts,
                    connection_id,
                ),
            )
        return self.get(connection_id)

    def update_status(
        self,
        connection_id: str,
        *,
        status: str,
        last_error: str | None = None,
        touch_seen: bool = False,
    ) -> None:
        ts = now_ts()
        seen = ts if touch_seen else None
        with self._db.transaction() as conn:
            if seen is not None:
                conn.execute(
                    "UPDATE bridge_connections SET status = ?, last_error = ?, "
                    "last_seen_at = ?, updated_at = ? WHERE connection_id = ?",
                    (status, last_error, seen, ts, connection_id),
                )
            else:
                conn.execute(
                    "UPDATE bridge_connections SET status = ?, last_error = ?, "
                    "updated_at = ? WHERE connection_id = ?",
                    (status, last_error, ts, connection_id),
                )

    def set_auto_reconnect(self, connection_id: str, enabled: bool) -> None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bridge_connections SET auto_reconnect = ?, updated_at = ? "
                "WHERE connection_id = ?",
                (1 if enabled else 0, ts, connection_id),
            )

    def update_meta(
        self,
        connection_id: str,
        *,
        display_name: str,
        notes: str | None,
    ) -> BridgeConnectionRow | None:
        """Overwrite display_name and notes for a connection."""
        return self.update_settings(
            connection_id,
            display_name=display_name,
            notes=notes,
        )

    def update_settings(
        self,
        connection_id: str,
        *,
        display_name: str,
        notes: str | None,
        icon_name: str | None = None,
        peer_base_url: str | None = None,
        peer_username: str | None = None,
        credential_blob: bytes | None = None,
        access_token_blob: bytes | None = None,
        token_expires_at: int | None = None,
        update_credentials: bool = False,
    ) -> BridgeConnectionRow | None:
        """Overwrite identity / endpoint fields; optionally refresh encrypted creds."""
        name = display_name.strip()
        if not name:
            raise ValueError("display_name required")
        note = (notes or "").strip() or None
        icon = (icon_name or "").strip() or None
        ts = now_ts()
        with self._db.transaction() as conn:
            if peer_base_url is not None and peer_username is not None and update_credentials:
                conn.execute(
                    "UPDATE bridge_connections SET "
                    "display_name = ?, notes = ?, icon_name = ?, "
                    "peer_base_url = ?, peer_username = ?, "
                    "credential_blob = ?, access_token_blob = ?, token_expires_at = ?, "
                    "updated_at = ? WHERE connection_id = ?",
                    (
                        name,
                        note,
                        icon,
                        peer_base_url,
                        peer_username,
                        credential_blob,
                        access_token_blob,
                        token_expires_at,
                        ts,
                        connection_id,
                    ),
                )
            elif peer_base_url is not None and peer_username is not None:
                conn.execute(
                    "UPDATE bridge_connections SET "
                    "display_name = ?, notes = ?, icon_name = ?, "
                    "peer_base_url = ?, peer_username = ?, updated_at = ? "
                    "WHERE connection_id = ?",
                    (name, note, icon, peer_base_url, peer_username, ts, connection_id),
                )
            else:
                conn.execute(
                    "UPDATE bridge_connections SET display_name = ?, notes = ?, icon_name = ?, "
                    "updated_at = ? WHERE connection_id = ?",
                    (name, note, icon, ts, connection_id),
                )
        return self.get(connection_id)

    def upsert_reverse(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        peer_base_url: str,
        peer_username: str,
        display_name: str,
        notes: str | None = None,
        status: str = "connected",
    ) -> BridgeConnectionRow:
        existing = self.get(connection_id)
        ts = now_ts()
        if existing is None:
            return self.create(
                connection_id=connection_id,
                owner_user_id=owner_user_id,
                peer_base_url=peer_base_url,
                peer_username=peer_username,
                display_name=display_name,
                notes=notes,
                status=status,
                auto_reconnect=False,
            )
        with self._db.transaction() as conn:
            # Keep the owner's chosen display_name / notes on reverse reconnect.
            # Reverse rows cannot dial out — never enable auto-reconnect.
            conn.execute(
                "UPDATE bridge_connections SET "
                "owner_user_id = ?, peer_base_url = ?, peer_username = ?, "
                "status = ?, last_error = NULL, last_seen_at = ?, updated_at = ?, "
                "auto_reconnect = 0 "
                "WHERE connection_id = ?",
                (
                    owner_user_id,
                    peer_base_url,
                    peer_username,
                    status,
                    ts,
                    ts,
                    connection_id,
                ),
            )
        row = self.get(connection_id)
        assert row is not None  # noqa: S101
        return row

    def delete(self, connection_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM bridge_connections WHERE connection_id = ?",
                (connection_id,),
            )
            return int(getattr(cur, "rowcount", 0) or 0) > 0
