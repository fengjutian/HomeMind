-- HomeMind schema v15: record the runtime version a device reports and
-- persist the non-sensitive configuration a persistent asset job needs
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. ``config_json`` is separate from
-- ``cursor_json`` on purpose: the cursor is worker-owned scan progress,
-- the config is the immutable requester-supplied job description that must
-- survive a restart. It carries public provider / model identifiers only,
-- never a credential.

ALTER TABLE homemind_family_devices
  ADD COLUMN IF NOT EXISTS runtime_version TEXT;

ALTER TABLE homemind_asset_jobs
  ADD COLUMN IF NOT EXISTS config_json TEXT NOT NULL DEFAULT '{}';

UPDATE _homemind_schema_version SET version = 15;
