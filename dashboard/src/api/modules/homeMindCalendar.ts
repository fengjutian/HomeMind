/**
 * HomeMind calendar and reminder API (Stage 1).
 *
 * Timestamps cross the wire as UTC epoch seconds, exactly as the
 * backend stores them. Converting to the family's clock happens at
 * render time using the server timezone, never by mutating the value —
 * an occurrence at "09:00 Asia/Shanghai" and one at "09:00 UTC" are the
 * same number on the wire and different moments on screen.
 */
import { request } from "../request";

const root = "/homemind/families";

/** Request options for a JSON body, mirroring `homeMindFamily.ts`. */
const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});
const mutate = (method: "PATCH" | "DELETE", body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export interface FamilyCalendar {
  id: string;
  family_id: string;
  name: string;
  description: string;
  color: string | null;
  /** null means the server timezone applies. */
  timezone: string | null;
  visibility: string;
  space_id: string | null;
  created_by: number;
  created_at: number;
  updated_at: number;
}

export interface FamilyCalendarEvent {
  id: string;
  calendar_id: string;
  family_id: string;
  title: string;
  description: string;
  location: string | null;
  starts_at: number;
  ends_at: number;
  all_day: boolean;
  timezone: string;
  /** Normalised RFC 5545 RRULE, or null. */
  recurrence_rule: string | null;
  recurrence_until: number | null;
  source_type: string;
  source_id: string | null;
  status: "CONFIRMED" | "CANCELLED";
  version: number;
  created_by: number;
  created_at: number;
  updated_at: number;
}

export interface FamilyCalendarOccurrence {
  event_id: string;
  calendar_id: string;
  family_id: string;
  title: string;
  description: string;
  location: string | null;
  starts_at: number;
  ends_at: number;
  all_day: boolean;
  timezone: string;
  status: string;
  source_type: string;
  source_id: string | null;
  /** Identifies one instance of a repeating event. */
  occurrence_key: string;
  is_recurring: boolean;
  /** The stored RRULE this occurrence came from, or null. */
  recurrence_rule: string | null;
}

export interface FamilyReminder {
  id: string;
  family_id: string;
  target_type: "TASK" | "CALENDAR_EVENT" | "APPROVAL" | "DEVICE";
  target_id: string;
  recipient_member_id: string | null;
  recipient_user_id: number | null;
  remind_at: number;
  channel: string;
  status: "PENDING" | "CLAIMED" | "SENT" | "FAILED" | "CANCELLED";
  attempt_count: number;
  last_error: string | null;
  occurrence_key: string | null;
  created_at: number;
  updated_at: number;
}

export interface CalendarCreateInput {
  name: string;
  description?: string;
  color?: string | null;
  timezone?: string | null;
  visibility?: "FAMILY" | "PRIVATE" | "PUBLIC";
  space_id?: string | null;
}

export interface CalendarEventCreateInput {
  calendar_id: string;
  title: string;
  starts_at: number;
  ends_at: number;
  description?: string;
  location?: string | null;
  all_day?: boolean;
  timezone?: string | null;
  recurrence_rule?: string | null;
  recurrence_until?: number | null;
}

export interface CalendarEventUpdateInput {
  title?: string;
  starts_at?: number;
  ends_at?: number;
  description?: string;
  location?: string | null;
  all_day?: boolean;
  timezone?: string;
  recurrence_rule?: string | null;
  /** Optimistic lock; a 409 means someone else edited first. */
  expected_version?: number;
}

export const homeMindCalendarApi = {
  listCalendars: (familyId: string) =>
    request<FamilyCalendar[]>(`${root}/${familyId}/calendars`),
  createCalendar: (familyId: string, body: CalendarCreateInput) =>
    request<FamilyCalendar>(`${root}/${familyId}/calendars`, json(body)),
  updateCalendar: (
    familyId: string,
    calendarId: string,
    body: Partial<CalendarCreateInput>,
  ) =>
    request<FamilyCalendar>(
      `${root}/${familyId}/calendars/${calendarId}`,
      mutate("PATCH", body),
    ),
  deleteCalendar: (familyId: string, calendarId: string) =>
    request<void>(`${root}/${familyId}/calendars/${calendarId}`, mutate("DELETE")),

  listEvents: (familyId: string, calendarId?: string) =>
    request<FamilyCalendarEvent[]>(
      `${root}/${familyId}/calendar-events${calendarId ? `?calendar_id=${encodeURIComponent(calendarId)}` : ""}`,
    ),
  createEvent: (familyId: string, body: CalendarEventCreateInput) =>
    request<FamilyCalendarEvent>(`${root}/${familyId}/calendar-events`, json(body)),
  updateEvent: (
    familyId: string,
    eventId: string,
    body: CalendarEventUpdateInput,
  ) =>
    request<FamilyCalendarEvent>(
      `${root}/${familyId}/calendar-events/${eventId}`,
      mutate("PATCH", body),
    ),
  cancelEvent: (familyId: string, eventId: string) =>
    request<FamilyCalendarEvent>(
      `${root}/${familyId}/calendar-events/${eventId}/cancel`,
      json({}),
    ),
  deleteEvent: (familyId: string, eventId: string) =>
    request<void>(`${root}/${familyId}/calendar-events/${eventId}`, mutate("DELETE")),

  /**
   * Occurrences overlapping a window. The backend caps both the window
   * length and the result count, so a caller that needs more has to
   * page by month rather than asking for "everything".
   */
  listOccurrences: (
    familyId: string,
    from: number,
    to: number,
    calendarId?: string,
  ) =>
    request<FamilyCalendarOccurrence[]>(
      `${root}/${familyId}/calendar-occurrences?from=${from}&to=${to}${
        calendarId ? `&calendar_id=${encodeURIComponent(calendarId)}` : ""
      }`,
    ),

  listReminders: (familyId: string, status?: string) =>
    request<FamilyReminder[]>(
      `${root}/${familyId}/reminders${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  createReminder: (
    familyId: string,
    body: {
      target_type: FamilyReminder["target_type"];
      target_id: string;
      remind_at: number;
      recipient_member_id?: string | null;
      occurrence_key?: string | null;
      lead_seconds?: number;
    },
  ) => request<FamilyReminder>(`${root}/${familyId}/reminders`, json(body)),
  cancelReminder: (familyId: string, reminderId: string) =>
    request<FamilyReminder>(
      `${root}/${familyId}/reminders/${reminderId}/cancel`,
      json({}),
    ),
  reminderSummary: (familyId: string) =>
    request<{ counts: Record<string, number> }>(`${root}/${familyId}/reminders/summary`),
};