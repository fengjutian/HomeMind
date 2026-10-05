import { request } from "../request";

export interface HomeMindFamily {
  id: string;
  name: string;
  avatar: string | null;
  owner_user_id: number;
  timezone: string;
  locale: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyMember {
  id: string;
  family_id: string;
  user_id: number | null;
  display_name: string;
  role: string;
  avatar: string | null;
  birthday: string | null;
  status: string;
}

export interface FamilyAlbum {
  id: string;
  family_id: string;
  name: string;
  description: string;
  cover_asset_id: string | null;
  created_at: number;
}

export interface FamilyTask {
  id: string;
  family_id: string;
  title: string;
  description: string;
  status: string;
  assigned_member_id: string | null;
  due_at: number | null;
  created_at: number;
}

export interface FamilyMemory {
  id: string;
  family_id: string;
  content: string;
  memory_type: string;
  importance: number;
  visibility: string;
  status: string;
  created_at: number;
}

export interface FamilySearchResult {
  kind: string;
  id: string;
  title: string;
  snippet: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface FamilyRelationship {
  id: string;
  family_id: string;
  from_member_id: string;
  to_member_id: string;
  relationship_type: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyEvent {
  id: string;
  family_id: string;
  event_type: string;
  title: string;
  start_at: number;
  end_at: number;
  location: string | null;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyAsset {
  id: string;
  family_id: string;
  asset_type: string;
  name: string;
  uri: string;
  mime_type: string;
  size_bytes: number;
  captured_at: number | null;
  status: string;
}

const root = "/homemind/families";
const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});
const mutate = (method: "PATCH" | "DELETE", body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export const homeMindFamilyApi = {
  listFamilies: () => request<HomeMindFamily[]>(root),
  createFamily: (body: { name: string; timezone?: string; locale?: string }) =>
    request<HomeMindFamily>(root, json(body)),
  updateFamily: (
    familyId: string,
    body: Partial<Pick<HomeMindFamily, "name" | "timezone" | "locale">>,
  ) => request<HomeMindFamily>(`${root}/${familyId}`, mutate("PATCH", body)),
  deleteFamily: (familyId: string) =>
    request<void>(`${root}/${familyId}`, mutate("DELETE")),
  listMembers: (familyId: string) =>
    request<FamilyMember[]>(`${root}/${familyId}/members`),
  createMember: (familyId: string, body: { display_name: string; role: string }) =>
    request<FamilyMember>(`${root}/${familyId}/members`, json(body)),
  updateMember: (
    familyId: string,
    memberId: string,
    body: Partial<Pick<FamilyMember, "display_name" | "role" | "birthday" | "status">>,
  ) =>
    request<FamilyMember>(
      `${root}/${familyId}/members/${memberId}`,
      mutate("PATCH", body),
    ),
  deleteMember: (familyId: string, memberId: string) =>
    request<void>(
      `${root}/${familyId}/members/${memberId}`,
      mutate("DELETE"),
    ),
  listRelationships: (familyId: string) =>
    request<FamilyRelationship[]>(`${root}/${familyId}/relationships`),
  createRelationship: (
    familyId: string,
    body: Pick<
      FamilyRelationship,
      "from_member_id" | "to_member_id" | "relationship_type"
    >,
  ) =>
    request<FamilyRelationship>(`${root}/${familyId}/relationships`, json(body)),
  deleteRelationship: (familyId: string, relationshipId: string) =>
    request<void>(
      `${root}/${familyId}/relationships/${relationshipId}`,
      mutate("DELETE"),
    ),
  listAlbums: (familyId: string) =>
    request<FamilyAlbum[]>(`${root}/${familyId}/albums`),
  createAlbum: (familyId: string, body: { name: string; description?: string }) =>
    request<FamilyAlbum>(`${root}/${familyId}/albums`, json(body)),
  listAlbumAssets: (familyId: string, albumId: string) =>
    request<string[]>(`${root}/${familyId}/albums/${albumId}/assets`),
  addAlbumAsset: (familyId: string, albumId: string, assetId: string) =>
    request<void>(
      `${root}/${familyId}/albums/${albumId}/assets`,
      json({ asset_id: assetId }),
    ),
  removeAlbumAsset: (familyId: string, albumId: string, assetId: string) =>
    request<void>(
      `${root}/${familyId}/albums/${albumId}/assets/${assetId}`,
      mutate("DELETE"),
    ),
  listAssets: (familyId: string) =>
    request<FamilyAsset[]>(`${root}/${familyId}/assets`),
  listTasks: (familyId: string) =>
    request<FamilyTask[]>(`${root}/${familyId}/tasks`),
  createTask: (familyId: string, body: { title: string; description?: string }) =>
    request<FamilyTask>(`${root}/${familyId}/tasks`, json(body)),
  listEvents: (familyId: string) =>
    request<FamilyEvent[]>(`${root}/${familyId}/events`),
  createEvent: (
    familyId: string,
    body: {
      event_type: string;
      title: string;
      start_at: number;
      end_at: number;
      location?: string;
      description?: string;
    },
  ) => request<FamilyEvent>(`${root}/${familyId}/events`, json(body)),
  deleteEvent: (familyId: string, eventId: string) =>
    request<void>(`${root}/${familyId}/events/${eventId}`, mutate("DELETE")),
  listMemories: (familyId: string, query = "") =>
    request<FamilyMemory[]>(
      `${root}/${familyId}/memories${query ? `?query=${encodeURIComponent(query)}` : ""}`,
    ),
  createMemory: (familyId: string, content: string) =>
    request<FamilyMemory>(
      `${root}/${familyId}/memories`,
      json({
        subject_type: "FAMILY",
        content,
        memory_type: "FACT",
        importance: 0.5,
        confidence: 0.8,
        visibility: "FAMILY",
        source_type: "MANUAL",
      }),
    ),
  search: (familyId: string, query: string) =>
    request<FamilySearchResult[]>(
      `${root}/${familyId}/search?query=${encodeURIComponent(query)}`,
    ),
};
