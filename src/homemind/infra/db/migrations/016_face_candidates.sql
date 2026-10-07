-- HomeMind schema v16: face-match candidates awaiting a human decision.
--
-- Face recognition is a suggestion, never a verdict. The provider returns
-- a confidence, and anything below the family's threshold must sit in a
-- review queue rather than silently becoming a family-member label — an
-- automated "this is your son" that turns out to be wrong is the kind of
-- error a family cannot audit or undo.
--
-- Three states, deliberately explicit rather than encoded in a free-text
-- description:
--   PENDING   - matched, awaiting a manager's confirmation
--   CONFIRMED - a manager said yes; usable as a label
--   REJECTED  - a manager said no; never re-offered automatically
--
-- No biometric data is stored here. Only the member id, the confidence
-- the provider reported, and who decided — the face itself lives in the
-- provider's response and in the reference photos.

CREATE TABLE IF NOT EXISTS homemind_family_face_candidates (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id    TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id        TEXT NOT NULL REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  member_id       TEXT NOT NULL REFERENCES homemind_family_members(member_id) ON DELETE CASCADE,
  confidence      REAL NOT NULL,
  status          TEXT NOT NULL DEFAULT 'PENDING',
  decided_by      INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  decided_at      INTEGER,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- The review queue: pending candidates for one family, newest first.
CREATE INDEX IF NOT EXISTS idx_homemind_family_face_candidates_review
  ON homemind_family_face_candidates(family_id, status, created_at);

-- Idempotence for a re-run job: one candidate per (asset, member).
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_face_candidates_pair
  ON homemind_family_face_candidates(asset_id, member_id);

UPDATE _homemind_schema_version SET version = 16;
