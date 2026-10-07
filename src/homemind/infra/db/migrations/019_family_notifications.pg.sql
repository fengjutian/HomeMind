-- HomeMind schema v19: the persistent notification centre and its
-- per-user preferences (PostgreSQL).
--
-- Mirror of the SQLite migration. The database, not the WebSocket, is
-- the source of truth for "what did I miss".

CREATE TABLE IF NOT EXISTS homemind_family_notifications (
  id              BIGSERIAL PRIMARY KEY,
  notification_id TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type            TEXT NOT NULL,
  title_key       TEXT NOT NULL,
  body_key        TEXT NOT NULL,
  params_json     TEXT NOT NULL DEFAULT '{}',
  target_type     TEXT,
  target_id       TEXT,
  severity        TEXT NOT NULL DEFAULT 'INFO',
  dedupe_key      TEXT NOT NULL,
  read_at         BIGINT,
  created_at      BIGINT NOT NULL,
  expires_at      BIGINT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_notifications_dedupe
  ON homemind_family_notifications(dedupe_key);

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_inbox
  ON homemind_family_notifications(user_id, read_at, created_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_expiry
  ON homemind_family_notifications(expires_at);

CREATE TABLE IF NOT EXISTS homemind_family_notification_prefs (
  id                  BIGSERIAL PRIMARY KEY,
  family_id           TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  disabled_types_json TEXT NOT NULL DEFAULT '[]',
  quiet_hours_start   BIGINT,
  quiet_hours_end     BIGINT,
  timezone            TEXT,
  created_at          BIGINT NOT NULL,
  updated_at          BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_notification_prefs
  ON homemind_family_notification_prefs(family_id, user_id);

UPDATE _homemind_schema_version SET version = 19;