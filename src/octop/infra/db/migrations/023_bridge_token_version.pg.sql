-- Schema v23: compare-and-swap guard for Bridge token writes (plan phase 14).
--
-- ``update_credentials`` was an unconditional COALESCE overwrite. With two
-- connections to the same peer (or a reconnect racing a manual edit), the
-- slower write could land last and clobber a newer token with a stale one —
-- silently re-authenticating the connection against a token that may already
-- be expired.
--
-- ``token_version`` gives writers something to compare against. A writer reads
-- the version, does the slow work (an encrypted login round trip), and then
-- only commits if the version is still what it read.

ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

-- Writes also bump the version, so a plain ``update_credentials`` still moves
-- the counter and cannot race a CAS writer unnoticed.
ALTER TABLE bridge_connections
  ADD COLUMN IF NOT EXISTS credentials_updated_at INTEGER;

UPDATE _schema_version SET version = 23;