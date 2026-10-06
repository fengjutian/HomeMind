-- HomeMind schema v8: approval expiration window (PostgreSQL).
--
-- Mirror of the SQLite migration: add ``approval_expires_at`` to
-- ``homemind_family_approvals`` plus an index that lets the daily
-- sweep pick stale ``PENDING`` rows in O(log n). NULL values are
-- preserved on existing rows so backfill is safe.

ALTER TABLE homemind_family_approvals
  ADD COLUMN IF NOT EXISTS approval_expires_at BIGINT;

CREATE INDEX IF NOT EXISTS idx_homemind_family_approvals_expiry
  ON homemind_family_approvals(status, approval_expires_at);

UPDATE _homemind_schema_version SET version = 8;
