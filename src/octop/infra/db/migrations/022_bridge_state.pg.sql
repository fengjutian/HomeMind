-- Schema v22: Bridge connection state machine (plan phase 13).
--
-- Phase 0 found four free-text status values with no enum and no CHECK:
-- ``disconnected`` / ``connecting`` / ``connected`` / ``error``. That cannot
-- express the states the plan needs — a peer that needs re-authentication,
-- a link that is up but degraded, a version mismatch that will never resolve
-- on retry, or an operator-disabled connection. All four collapse into
-- ``error``, so the UI and the diagnostics summary could not tell "retry
-- and it will work" from "this will never work until a human intervenes".
--
-- ``state`` is the new canonical column; ``status`` stays for existing
-- readers and is kept in step by the repo, so an older build reading this
-- table still sees something sensible.

ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'DISCONNECTED';
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS state_changed_at INTEGER;
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS state_detail TEXT;
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS peer_protocol TEXT;
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS peer_instance_id TEXT;
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS connected_since INTEGER;
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS reconnect_attempts INTEGER NOT NULL DEFAULT 0;

-- The dashboard lists connections by state; without this it is a table scan
-- on the one query the operator runs when something is wrong.
CREATE INDEX IF NOT EXISTS idx_bridge_connections_state
  ON bridge_connections(owner_user_id, state);

-- Backfill: map the legacy free-text values onto the new vocabulary. Guarded
-- so a re-run cannot undo a state the manager has since written.
UPDATE bridge_connections SET state = 'ONLINE'       WHERE status = 'connected'     AND state = 'DISCONNECTED';
UPDATE bridge_connections SET state = 'CONNECTING'   WHERE status = 'connecting'    AND state = 'DISCONNECTED';
UPDATE bridge_connections SET state = 'DEGRADED'     WHERE status = 'error'         AND state = 'DISCONNECTED';
UPDATE bridge_connections SET state = 'DISCONNECTED' WHERE status = 'disconnected'  AND state = 'DISCONNECTED';

UPDATE _schema_version SET version = 22;