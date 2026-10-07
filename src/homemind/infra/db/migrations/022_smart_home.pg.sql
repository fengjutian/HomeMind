-- HomeMind schema v22: smart-home entity mapping (PostgreSQL).
--
-- Mirror of the SQLite migration. Credentials live in the secret store;
-- this table holds only a reference to them.

CREATE TABLE IF NOT EXISTS homemind_family_smart_providers (
  id                BIGSERIAL PRIMARY KEY,
  provider_id       TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  kind              TEXT NOT NULL,
  name              TEXT NOT NULL,
  base_url          TEXT,
  secret_ref        TEXT,
  topic_allowlist_json TEXT NOT NULL DEFAULT '[]',
  enabled           INTEGER NOT NULL DEFAULT 1,
  last_seen_at      BIGINT,
  last_error        TEXT,
  created_by        INTEGER NOT NULL,
  created_at        BIGINT NOT NULL,
  updated_at        BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_providers_family
  ON homemind_family_smart_providers(family_id, kind);

CREATE TABLE IF NOT EXISTS homemind_family_smart_entities (
  id              BIGSERIAL PRIMARY KEY,
  entity_id       TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  provider_id     TEXT NOT NULL REFERENCES homemind_family_smart_providers(provider_id) ON DELETE CASCADE,
  external_entity_id TEXT NOT NULL,
  domain          TEXT NOT NULL,
  name            TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  state_json      TEXT NOT NULL DEFAULT '{}',
  last_state_at   BIGINT,
  last_changed_at BIGINT,
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_smart_entities_external
  ON homemind_family_smart_entities(provider_id, external_entity_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_entities_family
  ON homemind_family_smart_entities(family_id, domain);

UPDATE _homemind_schema_version SET version = 22;