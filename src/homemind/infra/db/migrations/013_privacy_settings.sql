-- HomeMind schema v13: family privacy settings + external provider policy.
--
-- Stage 12 makes "may this family send data to a third party?" an
-- explicit, per-family, server-side decision instead of an implicit
-- consequence of which providers happen to be configured.
--
-- ``processing_mode`` is the coarse switch:
--   LOCAL_ONLY     — nothing leaves the house, period
--   ASK_EACH_TIME  — each outbound call needs a human decision
--   ALLOW_EXTERNAL — allowed, but only for the listed providers and
--                    only for assets the caller may already read
--
-- ``allowed_provider_ids`` is a JSON array of provider ids. An empty
-- array means "none", not "all" — fail closed.

CREATE TABLE IF NOT EXISTS homemind_family_privacy_settings (
  id                             INTEGER PRIMARY KEY AUTOINCREMENT,
  family_id                      TEXT NOT NULL UNIQUE REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  processing_mode                TEXT NOT NULL DEFAULT 'ASK_EACH_TIME',
  allow_external_vision          INTEGER NOT NULL DEFAULT 0,
  allow_external_embedding       INTEGER NOT NULL DEFAULT 0,
  allow_external_geocoding       INTEGER NOT NULL DEFAULT 0,
  allow_sensitive_external       INTEGER NOT NULL DEFAULT 0,
  allowed_provider_ids           TEXT NOT NULL DEFAULT '[]',
  updated_at                     INTEGER NOT NULL,
  updated_by                     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS homemind_external_processing_requests (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id        TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id          TEXT,
  user_id           INTEGER NOT NULL,
  operation         TEXT NOT NULL,
  provider_id       TEXT NOT NULL,
  model             TEXT NOT NULL DEFAULT '',
  data_categories   TEXT NOT NULL DEFAULT '[]',
  status            TEXT NOT NULL DEFAULT 'PENDING',
  decided_by        INTEGER,
  decided_at        INTEGER,
  transaction_id    TEXT,
  created_at        INTEGER NOT NULL
);

-- A pending approval blocks the outbound call; the index serves the
-- "what is waiting for a decision" dashboard query.
CREATE INDEX IF NOT EXISTS idx_homemind_external_requests_status
  ON homemind_external_processing_requests(family_id, status, created_at DESC);

UPDATE _homemind_schema_version SET version = 13;
