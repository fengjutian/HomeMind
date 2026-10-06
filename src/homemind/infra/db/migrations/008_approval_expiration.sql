-- HomeMind schema v8: approval expiration window.
--
-- Add ``approval_expires_at`` to ``homemind_family_approvals`` so the
-- daily cron can mark stale ``PENDING`` rows ``EXPIRED`` without a
-- human intervention. Existing rows backfill to ``NULL`` so the
-- migration is safe to apply on a populated database — the daily
-- sweep treats NULL as "no expiry configured".

ALTER TABLE homemind_family_approvals ADD COLUMN approval_expires_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_homemind_family_approvals_expiry
  ON homemind_family_approvals(status, approval_expires_at);

UPDATE _homemind_schema_version SET version = 8;
