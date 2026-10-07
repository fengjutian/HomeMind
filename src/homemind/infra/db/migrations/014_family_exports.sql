-- HomeMind schema v14: family export / import jobs.
--
-- Stage 13 exports a family as a portable JSON bundle and restores it
-- into another install. Two rules drive the schema:
--
--   * An export is a *snapshot of relationships and metadata*, not a
--     copy of the family's photos. Original asset bytes are opt-in and
--     stored separately, so a default export never moves a byte of
--     the family's actual media.
--   * An import is never applied in place. It is staged as a row a
--     manager reviews and approves, so a malformed or hostile bundle
--     cannot overwrite a live family.

CREATE TABLE IF NOT EXISTS homemind_family_exports (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  export_id             TEXT NOT NULL UNIQUE,
  family_id             TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  status                TEXT NOT NULL DEFAULT 'PENDING',
  format_version        INTEGER NOT NULL DEFAULT 1,
  includes_original_assets INTEGER NOT NULL DEFAULT 0,
  manifest_json         TEXT NOT NULL DEFAULT '{}',
  file_path             TEXT,
  byte_size             INTEGER NOT NULL DEFAULT 0,
  checksum              TEXT,
  error_summary         TEXT,
  requested_by          INTEGER NOT NULL,
  created_at            INTEGER NOT NULL,
  started_at            INTEGER,
  finished_at           INTEGER,
  expires_at            INTEGER,
  lease_owner           TEXT,
  lease_expires_at      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_exports_family
  ON homemind_family_exports(family_id, status, created_at DESC);

-- Export downloads are authorised for a short window, so the expiry
-- is indexed for the cleanup sweep.
CREATE INDEX IF NOT EXISTS idx_homemind_family_exports_expiry
  ON homemind_family_exports(expires_at);

CREATE TABLE IF NOT EXISTS homemind_family_imports (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id       TEXT NOT NULL UNIQUE,
  target_family_id TEXT REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  status          TEXT NOT NULL DEFAULT 'STAGED',
  manifest_json   TEXT NOT NULL DEFAULT '{}',
  dry_run         INTEGER NOT NULL DEFAULT 1,
  conflict_report TEXT NOT NULL DEFAULT '{}',
  error_summary   TEXT,
  requested_by    INTEGER NOT NULL,
  decided_by      INTEGER,
  decided_at      INTEGER,
  created_at      INTEGER NOT NULL,
  finished_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_imports_target
  ON homemind_family_imports(target_family_id, status, created_at DESC);

UPDATE _homemind_schema_version SET version = 14;
