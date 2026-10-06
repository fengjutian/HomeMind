-- HomeMind schema v9: device command lease, approval gate, and retry guard
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. ``BIGINT`` replaces ``INTEGER`` for the
-- epoch columns to match the rest of the HomeMind PostgreSQL schema, and
-- ``ADD COLUMN IF NOT EXISTS`` keeps the migration idempotent on databases
-- that recorded the version out of band.

ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS is_unsafe BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS lease_expires_at BIGINT;
ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS approved_at BIGINT;
ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS approved_by BIGINT;
ALTER TABLE homemind_device_commands
  ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_homemind_device_commands_dispatch
  ON homemind_device_commands(device_id, status, expires_at, created_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_devices_liveness
  ON homemind_family_devices(status, last_seen);

UPDATE _homemind_schema_version SET version = 9;
