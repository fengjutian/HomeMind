-- Schema v21: server-managed upload sessions for resumable large-file upload.

CREATE TABLE IF NOT EXISTS upload_sessions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  upload_id         TEXT NOT NULL UNIQUE,
  owner_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id          TEXT,
  family_id         TEXT,
  purpose           TEXT NOT NULL,
  filename          TEXT NOT NULL,
  relative_target   TEXT,
  mime_type         TEXT,
  total_bytes       INTEGER NOT NULL,
  chunk_size        INTEGER NOT NULL,
  expected_sha256   TEXT,
  received_bytes    INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'OPEN',
  expires_at        INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  completed_at      INTEGER,
  final_resource_id TEXT,
  last_error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_owner
  ON upload_sessions(owner_user_id, status);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_expiry
  ON upload_sessions(status, expires_at);

-- Child rows key on the parent's public id, never the integer surrogate.
-- ``byte_offset`` avoids the reserved ``OFFSET`` keyword in both dialects.
CREATE TABLE IF NOT EXISTS upload_parts (
  upload_id   TEXT NOT NULL REFERENCES upload_sessions(upload_id) ON DELETE CASCADE,
  part_number INTEGER NOT NULL,
  byte_offset INTEGER NOT NULL,
  size        INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (upload_id, part_number)
);

UPDATE _schema_version SET version = 21;