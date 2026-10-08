"""SQL access for smart-home providers and their mapped entities.

Two things this file is careful about:

* **Credentials are references, not values.** ``secret_ref`` names a
  row in the secret store; the token itself never enters this table, a
  log line, or an audit row.
* **An external id is not a primary key.** ``light.living_room`` belongs
  to whatever system issued it. HomeMind keys on its own ULID so a
  rename upstream cannot collide with, or renumber, its own rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, sql_in_placeholders
from octop.infra.utils.ulid import new_ulid

PROVIDER_KIND_HOME_ASSISTANT = "HOME_ASSISTANT"
PROVIDER_KIND_MQTT = "MQTT"
PROVIDER_KINDS: frozenset[str] = frozenset({PROVIDER_KIND_HOME_ASSISTANT, PROVIDER_KIND_MQTT})


def _row_data(row: DbRow) -> dict[str, Any]:
    return (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )


def _loads(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class SmartProviderRow:
    id: str
    pk: int
    family_id: str
    kind: str
    name: str
    base_url: str | None
    #: Name of a row in the secret store — never the credential itself.
    secret_ref: str | None
    topic_allowlist: list[str]
    enabled: bool
    last_seen_at: int | None
    last_error: str | None
    #: Plan phase 5 health fields. ``last_seen_at`` is written on every probe
    #: (including failures), so it cannot answer "when did this last work" —
    #: ``last_success_at`` can.
    last_sync_at: int | None
    last_success_at: int | None
    last_error_code: str | None
    reauth_required: bool
    rate_limited_until: int | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> SmartProviderRow:
        data = _row_data(row)
        topics = _loads(data.get("topic_allowlist_json"), [])
        return cls(
            id=str(data["provider_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            kind=str(data["kind"]),
            name=str(data["name"]),
            base_url=data["base_url"],
            secret_ref=data["secret_ref"],
            topic_allowlist=[str(t) for t in topics] if isinstance(topics, list) else [],
            enabled=bool(data["enabled"]),
            last_seen_at=(int(data["last_seen_at"]) if data["last_seen_at"] is not None else None),
            last_error=data["last_error"],
            last_sync_at=(
                int(data["last_sync_at"]) if data.get("last_sync_at") is not None else None
            ),
            last_success_at=(
                int(data["last_success_at"]) if data.get("last_success_at") is not None else None
            ),
            last_error_code=data.get("last_error_code"),
            reauth_required=bool(data.get("reauth_required") or 0),
            rate_limited_until=(
                int(data["rate_limited_until"])
                if data.get("rate_limited_until") is not None
                else None
            ),
            created_by=int(data["created_by"]),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


@dataclass(frozen=True)
class SmartEntityRow:
    id: str
    pk: int
    family_id: str
    provider_id: str
    external_entity_id: str
    domain: str
    name: str
    capabilities: list[str]
    state: dict[str, Any]
    #: Plan phase 5: stable composite key of the *physical* device, so the
    #: entities one lamp exposes group together and a rename cannot fork it.
    device_key: str | None
    capabilities_typed: list[str]
    last_state_at: int | None
    last_changed_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> SmartEntityRow:
        data = _row_data(row)
        capabilities = _loads(data.get("capabilities_json"), [])
        typed = _loads(data.get("capabilities_typed_json"), [])
        state = _loads(data.get("state_json"), {})
        return cls(
            id=str(data["entity_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            provider_id=str(data["provider_id"]),
            external_entity_id=str(data["external_entity_id"]),
            domain=str(data["domain"]),
            name=str(data["name"]),
            capabilities=[str(c) for c in capabilities] if isinstance(capabilities, list) else [],
            state=state if isinstance(state, dict) else {},
            device_key=data.get("device_key"),
            capabilities_typed=[str(c) for c in typed] if isinstance(typed, list) else [],
            last_state_at=(
                int(data["last_state_at"]) if data["last_state_at"] is not None else None
            ),
            last_changed_at=(
                int(data["last_changed_at"]) if data["last_changed_at"] is not None else None
            ),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class SmartHomeRepo:
    """SQL access for the smart-home tables."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @property
    def db(self) -> DatabasePool:
        return self._db

    # ------------------------------------------------------------- providers

    def create_provider(
        self,
        family_id: str,
        *,
        kind: str,
        name: str,
        created_by: int,
        base_url: str | None = None,
        secret_ref: str | None = None,
        topic_allowlist: list[str] | None = None,
    ) -> SmartProviderRow:
        provider_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_smart_providers(provider_id, family_id, kind, "
                "name, base_url, secret_ref, topic_allowlist_json, enabled, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    provider_id,
                    family_id,
                    kind,
                    name,
                    base_url,
                    secret_ref,
                    json.dumps(topic_allowlist or [], ensure_ascii=False),
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_provider(provider_id)  # type: ignore[return-value]

    def get_provider(self, provider_id: str) -> SmartProviderRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_smart_providers WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return SmartProviderRow.from_row(row) if row else None

    def list_providers(self, family_id: str) -> list[SmartProviderRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_smart_providers WHERE family_id = ? "
                "ORDER BY name ASC",
                (family_id,),
            ).fetchall()
        return map_rows(rows, SmartProviderRow)

    def update_provider(self, provider_id: str, **values: object) -> SmartProviderRow | None:
        allowed = {
            "name",
            "base_url",
            "secret_ref",
            "enabled",
            "last_seen_at",
            "last_error",
        }
        fields = [key for key in values if key in allowed]
        if not fields:
            return self.get_provider(provider_id)
        params: list[Any] = [1 if values[key] is True else values[key] for key in fields]
        params.extend((now_ts(), provider_id))
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE homemind_family_smart_providers SET "
                f"{', '.join(f'{key} = ?' for key in fields)}, updated_at = ? "
                "WHERE provider_id = ?",
                params,
            )
        return self.get_provider(provider_id)

    def set_topic_allowlist(self, provider_id: str, topics: list[str]) -> SmartProviderRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_smart_providers SET topic_allowlist_json = ?, "
                "updated_at = ? WHERE provider_id = ?",
                (json.dumps(topics, ensure_ascii=False), now_ts(), provider_id),
            )
        return self.get_provider(provider_id)

    def record_probe(
        self, provider_id: str, *, ok: bool, error: str | None = None
    ) -> SmartProviderRow | None:
        """Record the outcome of a probe.

        Only the exception's *type* is stored, never its message: a
        connection error can carry a URL, and a URL can carry a host
        name the family would not want in a status field.
        """
        return self.update_provider(
            provider_id,
            last_seen_at=now_ts(),
            last_error=None if ok else (error or "probe failed"),
        )

    def record_sync_result(
        self,
        provider_id: str,
        *,
        ok: bool,
        error: str | None = None,
        error_code: str | None = None,
        reauth_required: bool = False,
        rate_limited_until: int | None = None,
    ) -> SmartProviderRow | None:
        """Record a full sync attempt, not just a liveness probe.

        Unlike :meth:`record_probe` this distinguishes *seen* from *worked*: a
        failed sync still bumps ``last_sync_at`` but leaves ``last_success_at``
        alone, so the UI can say "never worked" instead of implying freshness.
        """
        ts = now_ts()
        values: dict[str, object] = {
            "last_sync_at": ts,
            "last_error": None if ok else (error or "sync failed"),
            "last_error_code": None if ok else error_code,
            "reauth_required": 1 if (reauth_required and not ok) else 0,
            "rate_limited_until": rate_limited_until,
        }
        if ok:
            values["last_success_at"] = ts
        return self.update_provider(provider_id, **values)

    def delete_provider(self, provider_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_smart_providers WHERE provider_id = ?",
                (provider_id,),
            )
        return bool(cursor.rowcount > 0)

    # -------------------------------------------------------------- entities

    def upsert_entity(
        self,
        family_id: str,
        *,
        provider_id: str,
        external_entity_id: str,
        domain: str,
        name: str,
        capabilities: list[str] | None = None,
        state: dict[str, Any] | None = None,
        changed_at: int | None = None,
        device_key: str | None = None,
        capabilities_typed: list[str] | None = None,
    ) -> SmartEntityRow:
        """Record one entity, replacing its previous row's state.

        Upsert rather than insert so a re-sync updates state instead of
        failing on the unique index; the caller decides which entities
        to surface, and a second sync of the same entity is not a
        second entity.
        """
        entity_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT entity_id FROM homemind_family_smart_entities "
                "WHERE provider_id = ? AND external_entity_id = ?",
                (provider_id, external_entity_id),
            ).fetchone()
            if existing is not None:
                entity_id = str(_row_data(existing)["entity_id"])
                conn.execute(
                    "UPDATE homemind_family_smart_entities SET name = ?, domain = ?, "
                    "capabilities_json = ?, state_json = ?, last_state_at = ?, "
                    "device_key = COALESCE(?, device_key), "
                    "capabilities_typed_json = ?, "
                    "last_changed_at = COALESCE(?, last_changed_at), updated_at = ? "
                    "WHERE entity_id = ?",
                    (
                        name,
                        domain,
                        json.dumps(capabilities or [], ensure_ascii=False),
                        json.dumps(state or {}, ensure_ascii=False, sort_keys=True),
                        timestamp,
                        device_key,
                        json.dumps(capabilities_typed or [], ensure_ascii=False),
                        changed_at,
                        timestamp,
                        entity_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO homemind_family_smart_entities(entity_id, family_id, "
                    "provider_id, external_entity_id, domain, name, capabilities_json, "
                    "state_json, device_key, capabilities_typed_json, "
                    "last_state_at, last_changed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        family_id,
                        provider_id,
                        external_entity_id,
                        domain,
                        name,
                        json.dumps(capabilities or [], ensure_ascii=False),
                        json.dumps(state or {}, ensure_ascii=False, sort_keys=True),
                        device_key,
                        json.dumps(capabilities_typed or [], ensure_ascii=False),
                        timestamp,
                        changed_at,
                        timestamp,
                        timestamp,
                    ),
                )
        return self.get_entity(entity_id)  # type: ignore[return-value]

    def group_entities_by_device(
        self, provider_id: str, family_id: str
    ) -> dict[str, list[SmartEntityRow]]:
        """Entities of one provider grouped by physical device key.

        Plan phase 5 acceptance: "the several HA entities of one physical
        device aggregate into a single HomeMind device". Entities saved before
        this column existed have a NULL key and fall back to their own id so
        they still group one-per-key instead of collapsing together.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_smart_entities "
                "WHERE provider_id = ? AND family_id = ? ORDER BY name ASC",
                (provider_id, family_id),
            ).fetchall()
        grouped: dict[str, list[SmartEntityRow]] = {}
        for row in rows:
            entity = SmartEntityRow.from_row(row)
            key = entity.device_key or f"{provider_id}:entity:{entity.external_entity_id}"
            grouped.setdefault(key, []).append(entity)
        return grouped

    def get_entity(self, entity_id: str) -> SmartEntityRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_smart_entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
        return SmartEntityRow.from_row(row) if row else None

    def get_entity_by_external(
        self, provider_id: str, external_entity_id: str
    ) -> SmartEntityRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_smart_entities "
                "WHERE provider_id = ? AND external_entity_id = ?",
                (provider_id, external_entity_id),
            ).fetchone()
        return SmartEntityRow.from_row(row) if row else None

    def list_entities(
        self, family_id: str, *, provider_id: str | None = None, domain: str | None = None
    ) -> list[SmartEntityRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if provider_id is not None:
            clauses.append("provider_id = ?")
            params.append(provider_id)
        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_smart_entities WHERE "
                + " AND ".join(clauses)
                + " ORDER BY domain ASC, name ASC",
                params,
            ).fetchall()
        return map_rows(rows, SmartEntityRow)

    def set_entity_state(
        self, entity_id: str, state: dict[str, Any], *, changed_at: int | None = None
    ) -> SmartEntityRow | None:
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_smart_entities SET state_json = ?, last_state_at = ?, "
                "last_changed_at = COALESCE(?, last_changed_at), updated_at = ? "
                "WHERE entity_id = ?",
                (
                    json.dumps(state, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    changed_at,
                    timestamp,
                    entity_id,
                ),
            )
        return self.get_entity(entity_id)

    def delete_entity(self, entity_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_smart_entities WHERE entity_id = ?", (entity_id,)
            )
        return bool(cursor.rowcount > 0)

    def prune_missing_entities(
        self, family_id: str, provider_id: str, seen_external_ids: list[str]
    ) -> list[str]:
        """Delete rows for entities the provider no longer reports.

        A device removed in Home Assistant otherwise stays in this table
        forever, so the dashboard keeps showing a ghost that can never be
        reached. Only rows for this family+provider are considered, and a
        provider that reports nothing is treated as "we do not know" rather
        than "everything is gone" — an empty snapshot must not wipe the map.
        """
        if not seen_external_ids:
            return []
        placeholders = sql_in_placeholders(len(seen_external_ids))
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_smart_entities "
                f"WHERE family_id = ? AND provider_id = ? "
                f"AND external_entity_id NOT IN ({placeholders})",
                (family_id, provider_id, *seen_external_ids),
            )
            removed = int(getattr(cursor, "rowcount", 0) or 0)
        return ["deleted"] * removed

    def delete_entities_for_provider(self, provider_id: str) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_smart_entities WHERE provider_id = ?",
                (provider_id,),
            )
        return int(cursor.rowcount or 0)


__all__ = [
    "PROVIDER_KINDS",
    "PROVIDER_KIND_HOME_ASSISTANT",
    "PROVIDER_KIND_MQTT",
    "SmartEntityRow",
    "SmartHomeRepo",
    "SmartProviderRow",
]
