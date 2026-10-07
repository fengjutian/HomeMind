-- HomeMind schema v15: record the runtime version a device reports
-- (PostgreSQL).
--
-- Mirror of the SQLite migration.

ALTER TABLE homemind_family_devices
  ADD COLUMN IF NOT EXISTS runtime_version TEXT;

UPDATE _homemind_schema_version SET version = 15;
