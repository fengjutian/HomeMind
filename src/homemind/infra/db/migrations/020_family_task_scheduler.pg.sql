-- HomeMind schema v20: schedulable, agent-executable family tasks
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. Existing task rows keep their
-- meaning: `MANUAL`, due immediately, never claimed by a scheduler
-- unless someone schedules it.

ALTER TABLE homemind_family_tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE homemind_family_tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_tasks ADD COLUMN schedule_at BIGINT;
ALTER TABLE homemind_family_tasks ADD COLUMN recurrence_rule TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN agent_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN transaction_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE homemind_family_tasks ADD COLUMN lease_owner TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN lease_expires_at BIGINT;
ALTER TABLE homemind_family_tasks ADD COLUMN started_at BIGINT;
ALTER TABLE homemind_family_tasks ADD COLUMN completed_at BIGINT;
ALTER TABLE homemind_family_tasks ADD COLUMN result_summary TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN last_error TEXT;
ALTER TABLE homemind_family_tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_schedule
  ON homemind_family_tasks(status, schedule_at, priority);

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_lease
  ON homemind_family_tasks(lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_homemind_family_tasks_agent
  ON homemind_family_tasks(agent_id);

CREATE TABLE IF NOT EXISTS homemind_family_task_dependencies (
  id                BIGSERIAL PRIMARY KEY,
  dependency_id     TEXT NOT NULL UNIQUE,
  task_id           TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  depends_on_task_id TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  created_at        BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_family_task_dependencies
  ON homemind_family_task_dependencies(task_id, depends_on_task_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_dependencies_task
  ON homemind_family_task_dependencies(task_id);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_dependencies_upstream
  ON homemind_family_task_dependencies(depends_on_task_id);

CREATE TABLE IF NOT EXISTS homemind_family_task_attempts (
  id              BIGSERIAL PRIMARY KEY,
  attempt_id      TEXT NOT NULL UNIQUE,
  task_id         TEXT NOT NULL REFERENCES homemind_family_tasks(task_id) ON DELETE CASCADE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  attempt_number  INTEGER NOT NULL,
  status          TEXT NOT NULL,
  summary         TEXT,
  error           TEXT,
  lease_owner     TEXT,
  started_at      BIGINT NOT NULL,
  finished_at     BIGINT
);

CREATE INDEX IF NOT EXISTS idx_homemind_family_task_attempts_task
  ON homemind_family_task_attempts(task_id, attempt_number);

UPDATE _homemind_schema_version SET version = 20;