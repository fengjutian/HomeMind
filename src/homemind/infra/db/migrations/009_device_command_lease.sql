-- HomeMind schema v9: device command lease, approval gate, and retry guard.
--
-- Stage 5 requires a runtime command state machine that survives two
-- concurrent runtimes and one abandoned lease:
--
--   PENDING → WAITING_APPROVAL → DISPATCHED → RUNNING → SUCCEEDED / FAILED
--                                              ↘ CANCELLED / EXPIRED
--
-- ``lease_owner`` + ``lease_expires_at`` let a crashed runtime's command
-- be reclaimed safely. ``is_unsafe`` marks capabilities that must never
-- be auto-replayed after a lease expiry (deleting a file twice is not
-- idempotent), so reclaim refuses them and requires a human.
--
-- ``retry_count`` bounds automatic reclaim so a poison command cannot
-- spin forever.

ALTER TABLE homemind_device_commands ADD COLUMN is_unsafe INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_device_commands ADD COLUMN lease_owner TEXT;
ALTER TABLE homemind_device_commands ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE homemind_device_commands ADD COLUMN approved_at INTEGER;
ALTER TABLE homemind_device_commands ADD COLUMN approved_by INTEGER;
ALTER TABLE homemind_device_commands ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

-- Dispatch lookup is the hot path: PENDING rows ordered by age, plus
-- expired leases on DISPATCHED / RUNNING rows that may be reclaimed.
CREATE INDEX IF NOT EXISTS idx_homemind_device_commands_dispatch
  ON homemind_device_commands(device_id, status, expires_at, created_at);

-- The daily sweep marks devices OFFLINE when last_seen falls behind the
-- heartbeat timeout, so it needs a (status, last_seen) index.
CREATE INDEX IF NOT EXISTS idx_homemind_family_devices_liveness
  ON homemind_family_devices(status, last_seen);

UPDATE _homemind_schema_version SET version = 9;
