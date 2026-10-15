import { request } from "../request";

export type AuditKind =
  | "message"
  | "model_call"
  | "tool_call"
  | "trajectory"
  | "workspace_change";

export interface ThreadAuditItem {
  id: string;
  kind: AuditKind;
  ts: number;
  status?: string | null;
  turn_id?: string | null;
  tool_call_id?: string | null;
  payload: Record<string, unknown>;
}

export interface ThreadAuditSummary {
  model?: string | null;
  status: string;
  has_errors: boolean;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  model_calls: number;
}

export interface ThreadAuditPage {
  thread_id: string;
  agent_id: string;
  summary: ThreadAuditSummary;
  timeline: ThreadAuditItem[];
  next_before_ts?: number | null;
  has_more: boolean;
}

export interface ThreadAuditFilters {
  kinds?: AuditKind[];
  sinceTs?: number;
  beforeTs?: number;
  turnId?: string;
  toolCallId?: string;
  status?: string;
  limit?: number;
}

export const auditApi = {
  getThreadAudit: (threadId: string, filters: ThreadAuditFilters = {}) => {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 50) });
    if (filters.kinds?.length) params.set("kinds", filters.kinds.join(","));
    if (filters.sinceTs != null)
      params.set("since_ts", String(filters.sinceTs));
    if (filters.beforeTs != null)
      params.set("before_ts", String(filters.beforeTs));
    if (filters.turnId) params.set("turn_id", filters.turnId);
    if (filters.toolCallId) params.set("tool_call_id", filters.toolCallId);
    if (filters.status) params.set("status", filters.status);
    return request<ThreadAuditPage>(`/threads/${threadId}/audit?${params}`);
  },
};
