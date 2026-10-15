-- Persist desktop notifications until the desktop client accepts them.

CREATE TABLE IF NOT EXISTS desktop_notifications (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id  TEXT NOT NULL UNIQUE,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id          TEXT NOT NULL,
  thread_id         TEXT NOT NULL,
  agent_name        TEXT NOT NULL,
  body              TEXT NOT NULL,
  created_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_desktop_notifications_user_created
  ON desktop_notifications(user_id, created_at);

-- Append-only audit trail for mutations to agent workspace content.
CREATE TABLE IF NOT EXISTS workspace_change_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  change_id       TEXT NOT NULL UNIQUE,
  ts              INTEGER NOT NULL,
  actor_type      TEXT NOT NULL,
  actor_id        TEXT,
  user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
  agent_id        TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  thread_id       TEXT,
  turn_id         TEXT,
  tool_call_id    TEXT,
  source          TEXT NOT NULL,
  operation       TEXT NOT NULL,
  path            TEXT NOT NULL,
  destination     TEXT,
  before_exists   INTEGER NOT NULL,
  after_exists    INTEGER NOT NULL,
  before_size     INTEGER,
  after_size      INTEGER,
  before_hash     TEXT,
  after_hash      TEXT,
  diff_kind       TEXT,
  diff_text       TEXT,
  status          TEXT NOT NULL,
  error_code      TEXT,
  error_message   TEXT,
  request_id      TEXT,
  correlation_id  TEXT,
  metadata_json   TEXT NOT NULL DEFAULT '{}',
  integrity_hash  TEXT NOT NULL,
  schema_version  INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_workspace_change_agent_ts
  ON workspace_change_log(agent_id, ts);
CREATE INDEX IF NOT EXISTS idx_workspace_change_thread_ts
  ON workspace_change_log(thread_id, ts);
CREATE INDEX IF NOT EXISTS idx_workspace_change_tool_call
  ON workspace_change_log(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_workspace_change_path_ts
  ON workspace_change_log(agent_id, path, ts);

UPDATE _schema_version SET version = 24;
