-- HomeMind schema v23: the index behind the mobile cursor contract.
--
-- A phone asking for "my notifications" must not receive every one a
-- household ever generated, and offset pagination cannot express two
-- things well: it gets slower the deeper you go, and a row inserted
-- while a client is paging shifts everything after it, so an item is
-- silently skipped or shown twice.
--
-- The cursor is an opaque token carrying ``(created_at, id)``, decoded
-- server-side and compared *numerically* — so the index below is on the
-- raw columns rather than on an encoded copy of them. Storing the
-- encoded form too would imply base64 preserves ordering, which it does
-- not, and would add a column whose only job is to be out of date.

CREATE INDEX IF NOT EXISTS idx_homemind_family_notifications_cursor
  ON homemind_family_notifications(user_id, created_at DESC, id DESC);

UPDATE _homemind_schema_version SET version = 23;