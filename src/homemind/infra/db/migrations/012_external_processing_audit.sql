-- HomeMind schema v12: external processing + MCP call audit.
--
-- Stage 10 requires every MCP call to be auditable, and Stage 12
-- requires every outbound provider call (vision / embedding /
-- geocoding) to leave a record of *what left the house*. One table
-- serves both: the MCP server writes an audit row per tool call, and
-- the external-processing guard writes one per provider call.
--
-- Deliberately excluded: API keys, tokens, raw image bytes, and the
-- file path of any original asset. ``data_categories`` records *that*
-- a photo was analysed, never its contents or location.

CREATE TABLE IF NOT EXISTS homemind_external_processing_audit (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_id        TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL DEFAULT '',
  user_id         INTEGER NOT NULL,
  asset_id        TEXT,
  provider_id     TEXT NOT NULL,
  model           TEXT NOT NULL DEFAULT '',
  operation       TEXT NOT NULL,
  data_categories TEXT NOT NULL DEFAULT '[]',
  result          TEXT NOT NULL DEFAULT '{}',
  transaction_id  TEXT,
  created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_external_audit_family
  ON homemind_external_processing_audit(family_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_homemind_external_audit_asset
  ON homemind_external_processing_audit(asset_id);

-- Audit writes are frequent (one per MCP call, one per provider
-- call), so the family/user axis is indexed for the dashboard's
-- "what left my family" view.
CREATE INDEX IF NOT EXISTS idx_homemind_external_audit_user
  ON homemind_external_processing_audit(user_id, created_at DESC);

UPDATE _homemind_schema_version SET version = 12;
