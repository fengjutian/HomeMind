-- HomeMind schema v22: smart-home entity mapping.
--
-- A household already owns its lights and locks somewhere else — Home
-- Assistant, or an MQTT broker on the same network. HomeMind does not
-- become the source of truth for the home; it *reads* state from the
-- system that already is, and writes back only through a reviewed
-- transaction.
--
-- Two tables:
--   * ``..._smart_entities`` -- one row per external entity a family
--                               has chosen to surface. The external id
--                               (``light.living_room``) is stored but is
--                               deliberately not the primary key: an
--                               external id is something another system
--                               owns, and HomeMind must not let a
--                               rename over there collide with its own
--                               rows.
--   * ``..._smart_providers`` -- the connection per family, holding a
--                               *reference* to a secret rather than the
--                               secret itself. Credentials never enter
--                               this table, a log line, or an audit row.

CREATE TABLE IF NOT EXISTS homemind_family_smart_providers (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id       TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  kind              TEXT NOT NULL,
  name              TEXT NOT NULL,
  base_url          TEXT,
  secret_ref        TEXT,
  topic_allowlist_json TEXT NOT NULL DEFAULT '[]',
  enabled           INTEGER NOT NULL DEFAULT 1,
  last_seen_at      INTEGER,
  last_error        TEXT,
  created_by        INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_providers_family
  ON homemind_family_smart_providers(family_id, kind);

CREATE TABLE IF NOT EXISTS homemind_family_smart_entities (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id       TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  provider_id     TEXT NOT NULL REFERENCES homemind_family_smart_providers(provider_id) ON DELETE CASCADE,
  external_entity_id TEXT NOT NULL,
  domain          TEXT NOT NULL,
  name            TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  state_json      TEXT NOT NULL DEFAULT '{}',
  last_state_at   INTEGER,
  last_changed_at INTEGER,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- State polling reads by provider; the external id is unique only
-- *within* a provider, since two brokers may use the same name.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_smart_entities_external
  ON homemind_family_smart_entities(provider_id, external_entity_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_entities_family
  ON homemind_family_smart_entities(family_id, domain);

UPDATE _homemind_schema_version SET version = 22;