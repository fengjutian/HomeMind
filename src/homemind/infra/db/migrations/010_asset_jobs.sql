-- HomeMind schema v10: persistent asset jobs.
--
-- Stage 6 replaces the in-memory periodic scan loop with a database-backed
-- job queue so work survives a restart, can be paused / resumed / retried,
-- and reports progress per item instead of per sweep.
--
-- Two tables:
--   * ``homemind_asset_jobs``      — one row per job, holds the cursor and
--                                    the aggregate counters,
--   * ``homemind_asset_job_items`` — one row per unit of work, so a single
--                                    bad photo cannot abort the batch and a
--                                    retry can target just the failures.
--
-- ``lease_owner`` + ``lease_expires_at`` make claiming safe across
-- processes: a crashed worker's job becomes claimable again once its lease
-- lapses, and the CAS on ``status`` guarantees only one worker advances it.

CREATE TABLE IF NOT EXISTS homemind_asset_jobs (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id            TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  source_id         TEXT,
  job_type          TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'PENDING',
  cursor_json       TEXT NOT NULL DEFAULT '{}',
  total_items       INTEGER NOT NULL DEFAULT 0,
  processed_items   INTEGER NOT NULL DEFAULT 0,
  succeeded_items   INTEGER NOT NULL DEFAULT 0,
  skipped_items     INTEGER NOT NULL DEFAULT 0,
  failed_items      INTEGER NOT NULL DEFAULT 0,
  error_summary     TEXT,
  requested_by      INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  started_at        INTEGER,
  updated_at        INTEGER NOT NULL,
  finished_at       INTEGER,
  lease_owner       TEXT,
  lease_expires_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_asset_jobs_family
  ON homemind_asset_jobs(family_id, status, created_at);

-- The worker claims the oldest runnable job, so this is the hot path.
CREATE INDEX IF NOT EXISTS idx_homemind_asset_jobs_claim
  ON homemind_asset_jobs(status, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS homemind_asset_job_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id       TEXT NOT NULL UNIQUE,
  job_id        TEXT NOT NULL REFERENCES homemind_asset_jobs(job_id) ON DELETE CASCADE,
  asset_id      TEXT,
  source_path   TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'PENDING',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_asset_job_items_job
  ON homemind_asset_job_items(job_id, status, id);

CREATE INDEX IF NOT EXISTS idx_homemind_asset_job_items_retry
  ON homemind_asset_job_items(status, attempt_count);

UPDATE _homemind_schema_version SET version = 10;
