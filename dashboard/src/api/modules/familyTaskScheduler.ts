/**
 * Family task API (Stage 4).
 *
 * Two surfaces on one table:
 * - the legacy `/tasks` routes, unchanged, for a hand-written to-do;
 * - `/task-scheduler`, for tasks the system can claim and run.
 *
 * They share the same rows — a task created here still appears on the
 * plain tasks list — because they are the same product surface seen
 * from two angles, not two separate lists.
 */
import { request } from "../request";

const root = "/homemind/families";

export type ScheduledTaskStatus =
  | "TODO"
  | "SCHEDULED"
  | "IN_PROGRESS"
  | "WAITING_APPROVAL"
  | "DONE"
  | "FAILED"
  | "BLOCKED"
  | "CANCELLED";

export type ScheduledTaskType = "MANUAL" | "AGENT" | "DEVICE";

export interface ScheduledTask {
  id: string;
  family_id: string;
  title: string;
  description: string;
  status: ScheduledTaskStatus;
  task_type: ScheduledTaskType;
  priority: number;
  assigned_member_id: string | null;
  due_at: number | null;
  schedule_at: number | null;
  recurrence_rule: string | null;
  agent_id: string | null;
  transaction_id: string | null;
  parent_task_id: string | null;
  attempt_count: number;
  max_attempts: number;
  lease_owner: string | null;
  lease_expires_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  result_summary: string | null;
  last_error: string | null;
  version: number;
  created_at: number;
  updated_at: number;
}

export interface TaskAttempt {
  id: string;
  task_id: string;
  attempt_number: number;
  status: string;
  summary: string | null;
  error: string | null;
  started_at: number;
  finished_at: number | null;
}

export interface TaskDependency {
  id: string;
  task_id: string;
  depends_on_task_id: string;
  created_at: number;
}

export interface TaskBoard {
  todo: ScheduledTask[];
  scheduled: ScheduledTask[];
  in_progress: ScheduledTask[];
  waiting_approval: ScheduledTask[];
  done: ScheduledTask[];
  failed: ScheduledTask[];
  blocked: ScheduledTask[];
}

export const familyTaskSchedulerApi = {
  create: (
    familyId: string,
    body: {
      title: string;
      description?: string;
      task_type: ScheduledTaskType;
      agent_id?: string | null;
      schedule_at?: number | null;
      recurrence_rule?: string | null;
      parent_task_id?: string | null;
      depends_on?: string[];
      priority?: number;
      max_attempts?: number;
    },
  ) => request<ScheduledTask>(`${root}/${familyId}/task-scheduler/tasks`, {
    method: "POST",
    body: JSON.stringify(body),
  }),

  board: (familyId: string) =>
    request<TaskBoard>(`${root}/${familyId}/task-scheduler/board`),

  schedule: (familyId: string, taskId: string, scheduleAt: number | null) =>
    request<ScheduledTask>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/schedule?schedule_at=${scheduleAt ?? ""}`,
      { method: "POST" },
    ),

  cancel: (familyId: string, taskId: string) =>
    request<ScheduledTask>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/cancel`,
      { method: "POST" },
    ),

  retry: (familyId: string, taskId: string) =>
    request<ScheduledTask>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/retry`,
      { method: "POST" },
    ),

  addDependency: (familyId: string, taskId: string, dependsOnTaskId: string) =>
    request<TaskDependency>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/dependencies`,
      {
        method: "POST",
        body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
      },
    ),

  listDependencies: (familyId: string, taskId: string) =>
    request<TaskDependency[]>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/dependencies`,
    ),

  removeDependency: (familyId: string, taskId: string, dependsOnTaskId: string) =>
    request<void>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/dependencies/${dependsOnTaskId}`,
      { method: "DELETE" },
    ),

  listAttempts: (familyId: string, taskId: string) =>
    request<TaskAttempt[]>(
      `${root}/${familyId}/task-scheduler/tasks/${taskId}/attempts`,
    ),
};