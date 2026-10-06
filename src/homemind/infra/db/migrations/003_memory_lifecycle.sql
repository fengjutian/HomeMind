-- HomeMind schema v3: memory candidate lifecycle.

CREATE TABLE IF NOT EXISTS homemind_memory_candidates (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id    TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  subject_type    TEXT NOT NULL,
  subject_id      TEXT,
  content         TEXT NOT NULL,
  memory_type     TEXT NOT NULL,
  importance      REAL NOT NULL DEFAULT 0.5,
  confidence      REAL NOT NULL DEFAULT 0.5,
  visibility      TEXT NOT NULL DEFAULT 'FAMILY',
  source_type     TEXT NOT NULL,
  source_id       TEXT,
  status          TEXT NOT NULL DEFAULT 'PENDING',
  created_by      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at      INTEGER NOT NULL,
  reviewed_by     INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  reviewed_at     INTEGER,
  rejection_reason TEXT,
  merged_into     TEXT REFERENCES homemind_family_memories(memory_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_memory_candidates_family
  ON homemind_memory_candidates(family_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_homemind_memory_candidates_subject
  ON homemind_memory_candidates(family_id, subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_homemind_memory_candidates_status
  ON homemind_memory_candidates(status, created_at);

CREATE TABLE IF NOT EXISTS homemind_memory_evidence (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  evidence_id     TEXT NOT NULL UNIQUE,
  candidate_id    TEXT NOT NULL REFERENCES homemind_memory_candidates(candidate_id) ON DELETE CASCADE,
  memory_id       TEXT REFERENCES homemind_family_memories(memory_id) ON DELETE SET NULL,
  source_type     TEXT NOT NULL,
  source_id       TEXT,
  content_hash    TEXT NOT NULL,
  observed_at     INTEGER NOT NULL,
  confidence_delta REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_homemind_memory_evidence_candidate
  ON homemind_memory_evidence(candidate_id);
CREATE INDEX IF NOT EXISTS idx_homemind_memory_evidence_memory
  ON homemind_memory_evidence(memory_id);

UPDATE _homemind_schema_version SET version = 3;