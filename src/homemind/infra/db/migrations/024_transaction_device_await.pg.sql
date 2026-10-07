-- HomeMind schema v24: transactions that wait on a device
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. An approved device command parks in
-- AWAITING_DEVICE and is resumed by the device's result report, not by
-- a worker holding a lease open until it expires.

ALTER TABLE homemind_family_transactions
  ADD COLUMN device_command_id TEXT;

ALTER TABLE homemind_family_transactions
  ADD COLUMN device_deadline_at BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_transactions_device_command
  ON homemind_family_transactions(device_command_id)
  WHERE device_command_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_homemind_family_transactions_device_deadline
  ON homemind_family_transactions(status, device_deadline_at);

UPDATE _homemind_schema_version SET version = 24;