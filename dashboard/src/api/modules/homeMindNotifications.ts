/**
 * Family notification API (Stage 2).
 *
 * `title_key` / `body_key` plus `params` are what the server stored.
 * The copy is resolved client-side in the reader's locale, so one stored
 * row reads correctly for a Chinese and an English member — which is why
 * these fields are keys and not strings.
 */
import { request } from "../request";

const root = "/homemind/families";

export interface FamilyNotification {
  id: string;
  family_id: string;
  user_id: number;
  type: string;
  title_key: string;
  body_key: string;
  params: Record<string, unknown>;
  target_type: string | null;
  target_id: string | null;
  severity: "INFO" | "SUCCESS" | "WARNING" | "CRITICAL";
  read_at: number | null;
  created_at: number;
  expires_at: number | null;
}

export interface NotificationPrefs {
  family_id: string;
  user_id: number;
  disabled_types: string[];
  /** Minutes from local midnight, 0-1439. */
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
  timezone: string | null;
}

export const homeMindNotificationApi = {
  list: (
    familyId: string,
    options: { type?: string; unreadOnly?: boolean; limit?: number; offset?: number } = {},
  ) => {
    const params = new URLSearchParams();
    if (options.type) params.set("type", options.type);
    if (options.unreadOnly) params.set("unread_only", "true");
    if (options.limit) params.set("limit", String(options.limit));
    if (options.offset) params.set("offset", String(options.offset));
    const query = params.toString();
    return request<FamilyNotification[]>(
      `${root}/${familyId}/notifications${query ? `?${query}` : ""}`,
    );
  },

  unreadCount: (familyId: string) =>
    request<{ unread: number }>(`${root}/${familyId}/notifications/unread-count`),

  markRead: (familyId: string, notificationId: string) =>
    request<FamilyNotification>(
      `${root}/${familyId}/notifications/${notificationId}/read`,
      { method: "POST" },
    ),

  markAllRead: (familyId: string) =>
    request<{ marked: number }>(`${root}/${familyId}/notifications/read-all`, {
      method: "POST",
    }),

  purgeExpired: (familyId: string) =>
    request<{ removed: number }>(`${root}/${familyId}/notifications/expired`, {
      method: "DELETE",
    }),

  getPreferences: (familyId: string) =>
    request<NotificationPrefs>(`${root}/${familyId}/notifications/preferences`),

  savePreferences: (
    familyId: string,
    body: {
      disabled_types: string[];
      quiet_hours_start: number | null;
      quiet_hours_end: number | null;
      timezone?: string | null;
    },
  ) =>
    request<NotificationPrefs>(`${root}/${familyId}/notifications/preferences`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};

/** Notification types, mirroring the backend enum. */
export const NOTIFICATION_TYPES = [
  "TASK_DUE",
  "TASK_DONE",
  "APPROVAL_WAITING",
  "APPROVAL_DECIDED",
  "DEVICE_OFFLINE",
  "ASSET_JOB_DONE",
  "ASSET_JOB_FAILED",
  "KNOWLEDGE_INDEX_FAILED",
  "CALENDAR_REMINDER",
] as const;