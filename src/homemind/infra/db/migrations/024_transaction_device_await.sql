-- HomeMind schema v24: transactions that wait on a device.
--
-- ``device.command`` submits a command and returns; it does not block
-- until the household tablet wakes up. Blocking would hold the
-- transaction's lease open until it expired, and a lease expiry reads
-- as "timed out" -- which is exactly how a command that *did* run gets
-- mislabelled as one that did not.
--
-- So an approved device command parks here instead. The device reports
-- back through its existing result endpoint, which resumes the verify
-- step; only then does the transaction leave this state.
--
-- ``device_command_id`` is the link that makes that resume possible
-- without scanning payloads. ``device_deadline_at`` bounds the wait:
-- a command whose device never answers is escalated to
-- FAILED_REQUIRES_REVIEW rather than sitting here forever.

ALTER TABLE homemind_family_transactions
  ADD COLUMN device_command_id TEXT;

ALTER TABLE homemind_family_transactions
  ADD COLUMN device_deadline_at BIGINT;

-- One transaction per device command. Enforced so a retried approval
-- cannot attach two transactions to the same command and verify it
-- twice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_transactions_device_command
  ON homemind_family_transactions(device_command_id)
  WHERE device_command_id IS NOT NULL;

-- Boot recovery scans this state for rows whose deadline has passed.
CREATE INDEX IF NOT EXISTS idx_homemind_family_transactions_device_deadline
  ON homemind_family_transactions(status, device_deadline_at);

UPDATE _homemind_schema_version SET version = 24;