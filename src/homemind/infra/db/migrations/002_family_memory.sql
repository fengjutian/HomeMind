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

UPDATE _homemind_schema_version SET version = 2;
