-- HomeMind schema v16: face-match candidates awaiting a human decision
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. The state vocabulary is the same on both
-- dialects: a face match is a suggestion until a manager confirms it, and
-- no biometric data is stored in this table.

CREATE TABLE IF NOT EXISTS homemind_family_face_candidates (
  id              BIGSERIAL PRIMARY KEY,
  candidate_id    TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id        TEXT NOT NULL REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  member_id       TEXT NOT NULL REFERENCES homemind_family_members(member_id) ON DELETE CASCADE,
  confidence      DOUBLE PRECISION NOT NULL,
  status          TEXT NOT NULL DEFAULT 'PENDING',
  decided_by      BIGINT REFERENCES users(id) ON DELETE RESTRICT,
  decided_at      BIGINT,
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_face_candidates_review
  ON homemind_family_face_candidates(family_id, status, created_at);

-- Idempotence for a re-run job: one candidate per (asset, member).
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_face_candidates_pair
  ON homemind_family_face_candidates(asset_id, member_id);

UPDATE _homemind_schema_version SET version = 16;
