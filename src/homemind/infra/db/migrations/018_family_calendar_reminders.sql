-- HomeMind schema v18: family calendars, calendar events and reminders.
--
-- A household's shared commitments ("grandma's appointment Tuesday
-- 09:00", "swim class every Monday") are neither a memory nor an asset
-- event: they have a time, a place and a recurrence rule. Keeping them
-- out of ``homemind_family_events`` is deliberate -- that table is the
-- timeline narrative an agent reads, and a recurring dentist appointment
-- must not read like a family fact.
--
-- Three tables:
--   * ``..._calendars``      -- a named, coloured, optionally
--                               space-scoped container. ``timezone`` is
--                               NULL by default, meaning "inherit the
--                               server timezone"; a family that travels
--                               can override per calendar.
--   * ``..._calendar_events``-- the events themselves. ``starts_at`` /
--                               ``ends_at`` are UTC epoch seconds, the
--                               only unambiguous storage for a moment in
--                               time; ``timezone`` is kept alongside so a
--                               recurrence rule can be re-evaluated in
--                               the wall clock the user actually typed.
--                               Occurrences are *not* materialised -- a
--                               daily event for a decade is one row.
--   * ``..._reminders``      -- "tell the family about this", claimed by
--                               a lease-holding runner. ``dedupe_key``
--                               is the uniqueness that makes "exactly
--                               one in-app reminder per occurrence"
--                               true even with two runners racing.

CREATE TABLE IF NOT EXISTS homemind_family_calendars (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_id   TEXT NOT NULL UNIQUE,
  family_id     TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  color         TEXT,
  timezone      TEXT,
  visibility    TEXT NOT NULL DEFAULT 'FAMILY',
  space_id      TEXT REFERENCES homemind_family_spaces(space_id) ON DELETE SET NULL,
  created_by    INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendars_family
  ON homemind_family_calendars(family_id, name);

CREATE TABLE IF NOT EXISTS homemind_family_calendar_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id          TEXT NOT NULL UNIQUE,
  calendar_id       TEXT NOT NULL REFERENCES homemind_family_calendars(calendar_id) ON DELETE CASCADE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  location          TEXT,
  starts_at         INTEGER NOT NULL,
  ends_at           INTEGER NOT NULL,
  all_day           INTEGER NOT NULL DEFAULT 0,
  timezone          TEXT NOT NULL,
  recurrence_rule   TEXT,
  recurrence_until  INTEGER,
  source_type       TEXT NOT NULL DEFAULT 'MANUAL',
  source_id         TEXT,
  status            TEXT NOT NULL DEFAULT 'CONFIRMED',
  version           INTEGER NOT NULL DEFAULT 1,
  created_by        INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

-- The occurrence query always filters by family and window on start
-- time, so index that pair rather than the primary key.
CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_family_start
  ON homemind_family_calendar_events(family_id, starts_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_calendar
  ON homemind_family_calendar_events(calendar_id, starts_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_source
  ON homemind_family_calendar_events(source_type, source_id);

CREATE TABLE IF NOT EXISTS homemind_family_reminders (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  reminder_id         TEXT NOT NULL UNIQUE,
  family_id           TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  target_type         TEXT NOT NULL,
  target_id           TEXT NOT NULL,
  recipient_member_id TEXT REFERENCES homemind_family_members(member_id) ON DELETE CASCADE,
  recipient_user_id   INTEGER,
  remind_at           INTEGER NOT NULL,
  channel             TEXT NOT NULL DEFAULT 'IN_APP',
  status              TEXT NOT NULL DEFAULT 'PENDING',
  lease_owner         TEXT,
  lease_expires_at    INTEGER,
  attempt_count       INTEGER NOT NULL DEFAULT 0,
  last_error          TEXT,
  dedupe_key          TEXT NOT NULL UNIQUE,
  occurrence_key      TEXT,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

-- The runner's hot path: "which PENDING reminders are due and unleased".
CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_due
  ON homemind_family_reminders(status, remind_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_target
  ON homemind_family_reminders(target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_family
  ON homemind_family_reminders(family_id, recipient_member_id);

UPDATE _homemind_schema_version SET version = 18;