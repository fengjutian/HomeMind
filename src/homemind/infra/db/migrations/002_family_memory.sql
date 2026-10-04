-- HomeMind schema v2: family memory.

CREATE TABLE IF NOT EXISTS homemind_family_memories (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id     TEXT NOT NULL UNIQUE,
  family_id     TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  subject_type  TEXT NOT NULL,
  subject_id    TEXT,
  content       TEXT NOT NULL,
  memory_type   TEXT NOT NULL,
  importance    REAL NOT NULL DEFAULT 0.5,
  confidence    REAL NOT NULL DEFAULT 0.5,
  visibility    TEXT NOT NULL DEFAULT 'FAMILY',
  source_type   TEXT NOT NULL,
  source_id     TEXT,
  created_by    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  expires_at    INTEGER,
  status        TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_memories_family
  ON homemind_family_memories(family_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_homemind_family_memories_subject
  ON homemind_family_memories(family_id, subject_type, subject_id);

CREATE TABLE IF NOT EXISTS homemind_family_tasks (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id            TEXT NOT NULL UNIQUE,
  family_id          TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  title              TEXT NOT NULL,
  description        TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL DEFAULT 'TODO',
  assigned_member_id TEXT REFERENCES homemind_family_members(member_id) ON DELETE SET NULL,
  due_at             INTEGER,
  created_by         INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_family
  ON homemind_family_tasks(family_id, status, due_at);

CREATE TABLE IF NOT EXISTS homemind_family_devices (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id    TEXT NOT NULL UNIQUE,
  family_id    TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  device_type  TEXT NOT NULL,
  platform     TEXT,
  status       TEXT NOT NULL DEFAULT 'OFFLINE',
  address      TEXT,
  capabilities TEXT NOT NULL DEFAULT '[]',
  last_seen    INTEGER,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_devices_family
  ON homemind_family_devices(family_id, status);

CREATE TABLE IF NOT EXISTS homemind_family_transactions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  transaction_id TEXT NOT NULL UNIQUE,
  family_id      TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  requested_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  action         TEXT NOT NULL,
  payload_json   TEXT NOT NULL,
  status         TEXT NOT NULL,
  result_json    TEXT,
  error          TEXT,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_transactions_family
  ON homemind_family_transactions(family_id, status, created_at);

CREATE TABLE IF NOT EXISTS homemind_family_approvals (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  approval_id    TEXT NOT NULL UNIQUE,
  transaction_id TEXT NOT NULL UNIQUE REFERENCES homemind_family_transactions(transaction_id)
                   ON DELETE CASCADE,
  family_id      TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  status         TEXT NOT NULL DEFAULT 'PENDING',
  requested_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  decided_by     INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  reason         TEXT,
  created_at     INTEGER NOT NULL,
  decided_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_approvals_family
  ON homemind_family_approvals(family_id, status, created_at);

CREATE TABLE IF NOT EXISTS homemind_family_audit_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_id       TEXT NOT NULL UNIQUE,
  family_id      TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  transaction_id TEXT REFERENCES homemind_family_transactions(transaction_id) ON DELETE SET NULL,
  action         TEXT NOT NULL,
  target         TEXT,
  result         TEXT NOT NULL,
  approval       TEXT,
  detail_json    TEXT NOT NULL DEFAULT '{}',
  created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_audit_family
  ON homemind_family_audit_log(family_id, created_at);

CREATE TABLE IF NOT EXISTS homemind_family_albums (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  album_id    TEXT NOT NULL UNIQUE,
  family_id   TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  cover_asset_id TEXT REFERENCES homemind_family_assets(asset_id) ON DELETE SET NULL,
  created_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE(family_id, name)
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_albums_family
  ON homemind_family_albums(family_id, updated_at);

CREATE TABLE IF NOT EXISTS homemind_family_album_assets (
  album_id TEXT NOT NULL REFERENCES homemind_family_albums(album_id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  added_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  added_at INTEGER NOT NULL,
  PRIMARY KEY(album_id, asset_id)
);

CREATE TABLE IF NOT EXISTS homemind_family_organization_plans (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     TEXT NOT NULL UNIQUE,
  family_id   TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  strategy    TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'PLANNED',
  groups_json TEXT NOT NULL,
  created_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at  INTEGER NOT NULL,
  applied_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_organization_plans_family
  ON homemind_family_organization_plans(family_id, status, created_at);

CREATE TABLE IF NOT EXISTS homemind_family_photo_intelligence (
  asset_id        TEXT PRIMARY KEY REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  description     TEXT NOT NULL DEFAULT '',
  objects_json    TEXT NOT NULL DEFAULT '[]',
  scenes_json     TEXT NOT NULL DEFAULT '[]',
  faces_json      TEXT NOT NULL DEFAULT '[]',
  location_name   TEXT,
  perceptual_hash TEXT,
  embedding_json  TEXT,
  vision_provider TEXT,
  embedding_provider TEXT,
  analyzed_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_photo_intelligence_family
  ON homemind_family_photo_intelligence(family_id, analyzed_at);

UPDATE _homemind_schema_version SET version = 2;
