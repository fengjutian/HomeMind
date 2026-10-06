-- HomeMind schema v4: transaction state machine, lease, idempotency.

ALTER TABLE homemind_family_transactions ADD COLUMN idempotency_key TEXT;
ALTER TABLE homemind_family_transactions ADD COLUMN preview_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE homemind_family_transactions ADD COLUMN verification_json TEXT;
ALTER TABLE homemind_family_transactions ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_transactions ADD COLUMN lease_owner TEXT;
ALTER TABLE homemind_family_transactions ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE homemind_family_transactions ADD COLUMN approved_at INTEGER;
ALTER TABLE homemind_family_transactions ADD COLUMN executed_at INTEGER;
ALTER TABLE homemind_family_transactions ADD COLUMN verified_at INTEGER;
ALTER TABLE homemind_family_transactions ADD COLUMN cancelled_at INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS idx_homemind_family_transactions_idempotency
  ON homemind_family_transactions(family_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_homemind_family_transactions_status_lease
  ON homemind_family_transactions(status, lease_expires_at);

UPDATE _homemind_schema_version SET version = 4;