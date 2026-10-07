-- HomeMind schema v20: schedulable, agent-executable family tasks.
--
-- Stage 8 gave the family a to-do list. This makes it a *system*: a
-- task can be scheduled for a time, recur, be owned by an agent rather
-- than a person, declare what it depends on, and carry the lease that
-- lets a worker claim it exactly once.
--
-- Three additions:
--   * ``homemind_family_tasks`` gains the scheduling columns. Existing
--     rows default to ``MANUAL`` and are otherwise untouched — a task
--     created before this migration is still a hand-written to-do, not
--     a job that suddenly needs a scheduler.
--   * ``homemind_family_task_dependencies`` is a real table rather than
--     a JSON blob, so "does B depend on A" is answerable by the
--     database instead of by scanning every task's payload.
--   * ``homemind_family_task_attempts`` records one row per execution
--     attempt. ``result_summary`` on the task holds only the latest
--     line; the trajectory lives here and in Octop's own history.

ALTER TABLE homemind_family_tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE homemind_family_tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_tasks ADD COLUMN schedule_at INTEGER;
ALTER TABLE homemind_family_tasks ADD COLUMN recurrence_rule TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN agent_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN transaction_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE homemind_family_tasks ADD COLUMN lease_owner TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE homemind_family_tasks ADD COLUMN started_at INTEGER;
ALTER TABLE homemind_family_tasks ADD COLUMN completed_at INTEGER;
ALTER TABLE homemind_family_tasks ADD COLUMN result_summary TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN last_error TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- The scheduler's hot path: "which MANUAL/AGENT tasks are due and
-- unleased, highest priority first".
CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_schedule
  ON homemind_family_tasks(status, schedule_at, priority);

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_lease
  ON homemind_family_tasks(lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_agent
  ON homemind_family_tasks(agent_id);

CREATE TABLE IF NOT EXISTS homemind_family_task_dependencies (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  dependency_id     TEXT NOT NULL UNIQUE,
  task_id           TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  depends_on_task_id TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  created_at        INTEGER NOT NULL
);

-- A task depends on a given predecessor at most once, which is also
-- what makes a duplicate edge a no-op rather than a double count.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_task_dependencies
  ON homemind_family_task_dependencies(task_id, depends_on_task_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_dependencies_task
  ON homemind_family_task_dependencies(task_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_dependencies_upstream
  ON homemind_family_task_dependencies(depends_on_task_id);

CREATE TABLE IF NOT EXISTS homemind_family_task_attempts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id      TEXT NOT NULL UNIQUE,
  task_id         TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  attempt_number  INTEGER NOT NULL,
  status          TEXT NOT NULL,
  summary         TEXT,
  error           TEXT,
  lease_owner     TEXT,
  started_at      INTEGER NOT NULL,
  finished_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_attempts_task
  ON homemind_family_task_attempts(task_id, attempt_number);

UPDATE _homemind_schema_version SET version = 20;