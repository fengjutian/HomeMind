-- HomeMind schema v23: the index behind the mobile cursor contract
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. The cursor is decoded server-side and
-- compared numerically, so the index is on the raw columns.

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_cursor
  ON homemind_family_notifications(user_id, created_at DESC, id DESC);

UPDATE _homemind_schema_version SET version = 23;