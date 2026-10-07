-- HomeMind schema v18: family calendars, calendar events and reminders
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. Occurrences stay computed, never
-- materialised, and reminders are leased rather than held in memory so a
-- restart does not lose or duplicate one.

CREATE TABLE IF NOT EXISTS homemind_family_calendars (
  id            BIGSERIAL PRIMARY KEY,
  calendar_id   TEXT NOT NULL UNIQUE,
  family_id     TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  color         TEXT,
  timezone      TEXT,
  visibility    TEXT NOT NULL DEFAULT 'FAMILY',
  space_id      TEXT REFERENCES homemind_family_spaces(space_id) ON DELETE SET NULL,
  created_by    INTEGER NOT NULL,
  created_at    BIGINT NOT NULL,
  updated_at    BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendars_family
  ON homemind_family_calendars(family_id, name);

CREATE TABLE IF NOT EXISTS homemind_family_calendar_events (
  id                BIGSERIAL PRIMARY KEY,
  event_id          TEXT NOT NULL UNIQUE,
  calendar_id       TEXT NOT NULL REFERENCES homemind_family_calendars(calendar_id) ON DELETE CASCADE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  location          TEXT,
  starts_at         BIGINT NOT NULL,
  ends_at           BIGINT NOT NULL,
  all_day           INTEGER NOT NULL DEFAULT 0,
  timezone          TEXT NOT NULL,
  recurrence_rule   TEXT,
  recurrence_until  BIGINT,
  source_type       TEXT NOT NULL DEFAULT 'MANUAL',
  source_id         TEXT,
  status            TEXT NOT NULL DEFAULT 'CONFIRMED',
  version           INTEGER NOT NULL DEFAULT 1,
  created_by        INTEGER NOT NULL,
  created_at        BIGINT NOT NULL,
  updated_at        BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_family_start
  ON homemind_family_calendar_events(family_id, starts_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_calendar
  ON homemind_family_calendar_events(calendar_id, starts_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_calendar_events_source
  ON homemind_family_calendar_events(source_type, source_id);

CREATE TABLE IF NOT EXISTS homemind_family_reminders (
  id                  BIGSERIAL PRIMARY KEY,
  reminder_id         TEXT NOT NULL UNIQUE,
  family_id           TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  target_type         TEXT NOT NULL,
  target_id           TEXT NOT NULL,
  recipient_member_id TEXT REFERENCES homemind_family_members(member_id) ON DELETE CASCADE,
  recipient_user_id   INTEGER,
  remind_at           BIGINT NOT NULL,
  channel             TEXT NOT NULL DEFAULT 'IN_APP',
  status              TEXT NOT NULL DEFAULT 'PENDING',
  lease_owner         TEXT,
  lease_expires_at    BIGINT,
  attempt_count       INTEGER NOT NULL DEFAULT 0,
  last_error          TEXT,
  dedupe_key          TEXT NOT NULL UNIQUE,
  occurrence_key      TEXT,
  created_at          BIGINT NOT NULL,
  updated_at          BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_due
  ON homemind_family_reminders(status, remind_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_target
  ON homemind_family_reminders(target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_reminders_family
  ON homemind_family_reminders(family_id, recipient_member_id);

UPDATE _homemind_schema_version SET version = 18;