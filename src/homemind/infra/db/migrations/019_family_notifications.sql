-- HomeMind schema v19: the persistent notification centre and its
-- per-user preferences.
--
-- Stage 1 delivered reminders over a WebSocket. A socket is an
-- acceleration path, not a record: a family member whose dashboard was
-- closed when a reminder fired must still find out afterwards. This
-- migration makes the database the source of truth so "what did I miss"
-- is answerable after a restart, a reconnect, or a week offline.
--
-- Two tables:
--   * ``..._notifications``       -- one row per thing a user was told
--                                     about. The copy is stored as
--                                     ``title_key`` / ``body_key`` plus a
--                                     parameter bag, never as rendered
--                                     text: the same row has to read as
--                                     Chinese for one member and English
--                                     for another, and freezing one
--                                     language into the table would make
--                                     the other impossible.
--   * ``..._notification_prefs``  -- one row per (user, family): which
--                                     types are on, and the quiet-hours
--                                     window in which nothing is
--                                     delivered at all.

CREATE TABLE IF NOT EXISTS homemind_family_notifications (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type            TEXT NOT NULL,
  title_key       TEXT NOT NULL,
  body_key        TEXT NOT NULL,
  params_json     TEXT NOT NULL DEFAULT '{}',
  target_type     TEXT,
  target_id       TEXT,
  severity        TEXT NOT NULL DEFAULT 'INFO',
  read_at         INTEGER,
  created_at      INTEGER NOT NULL,
  expires_at      INTEGER
);

-- "Exactly one notification for this thing" is what makes repeated
-- triggers idempotent; a retried job or a double-clicked approve must
-- not stack two unread bells.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_notifications_dedupe
  ON homemind_family_notifications(family_id, user_id, type, dedupe_key_placeholder);

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_inbox
  ON homemind_family_notifications(user_id, read_at, created_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_expiry
  ON homemind_family_notifications(expires_at);

CREATE TABLE IF NOT EXISTS homemind_family_notification_prefs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  family_id           TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  disabled_types_json TEXT NOT NULL DEFAULT '[]',
  quiet_hours_start   INTEGER,
  quiet_hours_end     INTEGER,
  timezone            TEXT,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

-- One preference row per (family, user); the upsert target for PATCH.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_notification_prefs
  ON homemind_family_notification_prefs(family_id, user_id);

UPDATE _homemind_schema_version SET version = 19;