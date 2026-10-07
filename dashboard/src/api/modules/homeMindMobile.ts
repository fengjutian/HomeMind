/**
 * HomeMind mobile API client (Stage 7).
 *
 * The contract a phone codes against: the same JWT auth as the
 * dashboard, list endpoints that page with an opaque cursor, and an
 * explicit statement of "there is no more" so a client cannot loop.
 *
 * `next_cursor` is `null` on the last page — not an empty string — so
 * a caller testing it for truthiness stops rather than requesting page
 * one forever.
 */
import { request } from "../request";
import type { FamilyNotification } from "./homeMindNotifications";

const root = "/homemind/families";

/** One page of a cursor-paginated list. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

/** A response with no paging — a detail view, not a list. */
export interface MobileUnreadCount {
  unread: number;
}

export interface MobileTimelineEntry {
  id: string;
  kind: string;
  title: string;
  occurred_at: number;
  summary?: string;
}

export interface MobileApprovalSummary {
  id: string;
  action: string;
  status: string;
  created_at: number;
  preview: Record<string, unknown>;
}

/**
 * Pull one page, following `next_cursor` until the server says stop.
 *
 * `maxPages` is a hard stop rather than a nicety: a server bug that
 * keeps returning the same cursor would otherwise spin a phone's
 * battery flat. The caller gets whatever was collected.
 */
export async function collectPages<T>(
  fetchPage: (cursor: string | null) => Promise<CursorPage<T>>,
  options: { maxPages?: number; pageSize?: number } = {},
): Promise<T[]> {
  const maxPages = options.maxPages ?? 10;
  const collected: T[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < maxPages; page += 1) {
    const result = await fetchPage(cursor);
    collected.push(...result.items);
    if (!result.has_more || result.next_cursor === null) break;
    cursor = result.next_cursor;
  }
  return collected;
}

export const homeMindMobileApi = {
  notificationsPage: (
    familyId: string,
    options: { cursor?: string | null; limit?: number; unreadOnly?: boolean } = {},
  ) => {
    const params = new URLSearchParams();
    if (options.cursor) params.set("cursor", options.cursor);
    params.set("limit", String(options.limit ?? 20));
    if (options.unreadOnly) params.set("unread_only", "true");
    return request<CursorPage<FamilyNotification>>(
      `${root}/${familyId}/notifications/page?${params.toString()}`,
    );
  },

  unreadCount: (familyId: string) =>
    request<MobileUnreadCount>(`${root}/${familyId}/notifications/unread-count`),

  markRead: (familyId: string, notificationId: string) =>
    request<FamilyNotification>(
      `${root}/${familyId}/notifications/${notificationId}/read`,
      { method: "POST" },
    ),

  markAllRead: (familyId: string) =>
    request<{ marked: number }>(`${root}/${familyId}/notifications/read-all`, {
      method: "POST",
    }),

  /** Today plus the next `days` of calendar occurrences, as one list. */
  occurrences: (familyId: string, from: number, to: number) =>
    request<MobileOccurrence[]>(
      `${root}/${familyId}/calendar-occurrences?from=${from}&to=${to}`,
    ),

  approvals: (familyId: string) =>
    request<MobileApprovalSummary[]>(`${root}/${familyId}/approvals`),
};

export interface MobileOccurrence {
  event_id: string;
  title: string;
  starts_at: number;
  ends_at: number;
  all_day: boolean;
  timezone: string;
  occurrence_key: string;
  is_recurring: boolean;
}

/** Re-exported so a mobile caller need not import two modules. */
export type { FamilyNotification };