-- HomeMind schema v6: family invites.
--
-- A manager creates a one-shot invite; the redeemer exchanges the
-- token for a membership row bound to their user account. Tokens are
-- stored as hashes so the dashboard never persists plaintext.

CREATE TABLE IF NOT EXISTS homemind_family_invites (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_id      TEXT NOT NULL UNIQUE,
  family_id      TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  role           TEXT NOT NULL DEFAULT 'MEMBER',
  display_name   TEXT NOT NULL,
  token_hash     TEXT NOT NULL UNIQUE,
  created_by     INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at     INTEGER NOT NULL,
  expires_at     INTEGER NOT NULL,
  redeemed_at    INTEGER,
  redeemed_by    INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_invites_family
  ON homemind_family_invites(family_id, redeemed_at);

UPDATE _homemind_schema_version SET version = 6;
